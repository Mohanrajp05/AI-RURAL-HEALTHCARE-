"""
Production-ready healthcare chatbot pipeline.
Strict, deterministic, no hallucinations.
"""

import re
import json
import time
import difflib
from functools import lru_cache
from typing import Optional, Dict, Any
from pathlib import Path
import ollama
import torch
from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

import portkey_llm
import llm_router
from faq_matcher import match_faq



KB_PATH = Path(__file__).parent / "disease_knowledge_base.json"
TXT_KB_DIR = Path(__file__).parent / "knowledge_base"
OLLAMA_MODEL = "cniongolo/biomistral:latest"
OLLAMA_TEMPERATURE = 0.3
OLLAMA_NUM_PREDICT = 80
NLLB_MODEL_NAME = "facebook/nllb-200-distilled-600M"

# FAISS semantic retrieval (RAG) configuration
FAISS_INDEX_PATH = "faiss_disease_index"
FAISS_TOP_K = 3
EMBEDDING_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
# Embeddings are L2-normalized (normalize_embeddings=True below), so FAISS's
# L2 distance score is a direct proxy for cosine distance: 0 = identical,
# 2 = opposite. Empirically calibrated (2026-08-24 pipeline audit): genuinely
# relevant chunks score 0.44-0.98 for on-topic disease questions; completely
# unrelated questions ("what is the purpose of Rural Healthcare", "what is
# the weather like today") score 1.22-1.52 against the SAME disease index --
# nothing in it is actually about them, but similarity_search() has no floor
# and always returns its top-k nearest neighbors regardless of how irrelevant
# they are, so an off-topic question would silently get "grounded" in
# whichever disease happened to be least-far-away (observed: Migraine) and
# the LLM would confidently answer from that wrong context. This threshold
# is the fix: below it, a chunk is treated as not actually relevant.
FAISS_MAX_RELEVANCE_SCORE = 1.1

LANGUAGE_CODES = {
    "eng_Latn": "English",
    "hin_Deva": "Hindi",
    "kan_Knda": "Kannada",
    "tam_Taml": "Tamil",
    "tel_Telu": "Telugu",
}

# Modular automatic language detection + translation routing.
# English is skipped; other languages go through the NLLB translation service.
from language_service import detect_language as detect_language_service
from language_service import translate_if_needed as route_translation
from translation_service import translate as translate_text
from translation_service import TRANSLATE_LOCK
from translation_service import _normalize_for_translation, _split_into_chunks, translate_preserving_markdown

_translation_tokenizer = None
_translation_model = None


def limit_response_words(text: str, max_words: int = 350) -> str:
    if not text:
        return text
    words = str(text).split()
    if len(words) <= max_words:
        return text
    truncated = " ".join(words[:max_words])
    last_period = max(truncated.rfind("."), truncated.rfind("!"), truncated.rfind("?"))
    if last_period != -1:
        return truncated[: last_period + 1]
    else:
        return truncated + "."


# Session storage: {session_id: {"disease": str, "timestamp": datetime}}
_SESSIONS: Dict[str, Dict[str, Any]] = {}

# ============================================================
# 1. NORMALIZATION
# ============================================================

FILLER_WORDS = {
    "disease", "condition", "illness", "health", "medical", "problem",
    "symptom", "symptoms", "sign", "signs", "about", "the", "a", "is"
}

PUNCTUATION_PATTERN = re.compile(r'[^\w\s]')


def normalize(text: str) -> str:
    """
    Normalize text: lowercase, remove punctuation, strip filler words.
    
    Args:
        text: Raw text to normalize
        
    Returns:
        Normalized text for matching
    """
    if not text:
        return ""
    
    # Lowercase
    text = text.lower().strip()
    
    # Remove punctuation
    text = PUNCTUATION_PATTERN.sub('', text)
    
    # Remove extra whitespace
    text = ' '.join(text.split())
    
    # Remove filler words
    words = [w for w in text.split() if w not in FILLER_WORDS]
    
    return ' '.join(words)


# ============================================================
# 2. KNOWLEDGE BASE LOADER
# ============================================================

_KB_CACHE = None


def load_kb(force_reload: bool = False) -> Optional[Dict[str, Dict[str, str]]]:
    """Load disease knowledge base from JSON (cached after first load)."""
    global _KB_CACHE
    if _KB_CACHE is not None and not force_reload:
        return _KB_CACHE
    try:
        if not KB_PATH.exists():
            return None

        with open(KB_PATH, 'r', encoding='utf-8') as f:
            kb = json.load(f)

        _KB_CACHE = kb if isinstance(kb, dict) else None
        return _KB_CACHE
    except Exception as e:
        print(f"Error loading KB: {e}")
        return None


# ============================================================
# 2b. FAISS SEMANTIC RETRIEVAL (RAG)
# ============================================================

_faiss_store = None
_faiss_embeddings = None
_faiss_error = None


def init_rag() -> bool:
    """
    Load the FAISS index and embedding model ONCE (cached for the process
    lifetime). Returns True when FAISS is ready, False otherwise. Safe to
    call repeatedly; on failure it remembers the error and never retries.
    """
    global _faiss_store, _faiss_embeddings, _faiss_error

    if _faiss_store is not None:
        return True
    if _faiss_error is not None:
        return False

    try:
        from langchain_community.vectorstores import FAISS
        from langchain_huggingface import HuggingFaceEmbeddings

        index_dir = Path(__file__).parent / FAISS_INDEX_PATH
        if not index_dir.exists():
            raise FileNotFoundError(f"FAISS index not found at {index_dir}")

        _faiss_embeddings = HuggingFaceEmbeddings(
            model_name=EMBEDDING_MODEL_NAME,
            model_kwargs={"device": "cpu"},
            encode_kwargs={"normalize_embeddings": True},
        )
        _faiss_store = FAISS.load_local(
            str(index_dir),
            embeddings=_faiss_embeddings,
            allow_dangerous_deserialization=True,
        )
        print(f"[RAG] FAISS index loaded: {_faiss_store.index.ntotal} vectors from {index_dir}")
        return True
    except Exception as e:
        _faiss_error = e
        print(f"[RAG] FAISS unavailable ({e}). Falling back to keyword/difflib KB retrieval.")
        return False


def retrieve_faiss_chunks(query: str, top_k: int = FAISS_TOP_K, diag: Optional[dict] = None) -> list:
    """
    Embed the query and return the top-k most relevant (source, content) chunks
    -- but ONLY those that actually score within FAISS_MAX_RELEVANCE_SCORE.
    Returns an empty list when FAISS is unavailable, the search fails, or
    nothing retrieved clears the relevance floor, so the caller correctly
    falls back to the generic/no-context path instead of being handed a
    "closest available" chunk from a completely unrelated disease.

    When `diag` (a dict) is supplied, it is updated in place with:
      retrieval_time_ms (float), retrieved (list of (source, content)).
    """
    if not query:
        return []
    if not init_rag():
        return []
    t0 = time.perf_counter()
    try:
        results = _faiss_store.similarity_search_with_score(query, k=top_k)
        chunks = []
        dropped = 0
        for r, score in results:
            if score > FAISS_MAX_RELEVANCE_SCORE:
                dropped += 1
                continue
            source = Path(str(r.metadata.get("source", "unknown"))).name
            content = str(r.page_content or "").strip()
            if content:
                chunks.append((source, content))
        if diag is not None:
            diag["retrieval_time_ms"] = (time.perf_counter() - t0) * 1000.0
            diag["retrieved"] = chunks
        print(f"[RAG] Retrieved {len(chunks)} chunk(s) for query '{query}' "
              f"({dropped} dropped as below relevance threshold): {[c[0] for c in chunks]}")
        return chunks
    except Exception as e:
        if diag is not None:
            diag["retrieval_time_ms"] = (time.perf_counter() - t0) * 1000.0
            diag["retrieved"] = []
        print(f"[RAG] Retrieval failed: {e}")
        return []


def _compose_faiss_context(chunks: list) -> str:
    """Join retrieved chunks into a single context block for the Ollama prompt."""
    blocks = []
    for i, (source, content) in enumerate(chunks, 1):
        blocks.append(f"[Source: {source}]\n{content}")
    return "\n\n".join(blocks).strip()


# Load the FAISS index + embedding model once at startup (best-effort).
init_rag()


def load_translation_model():
    """Load and cache the NLLB-200 translation model (once per process)."""
    global _translation_tokenizer, _translation_model

    if _translation_tokenizer is not None and _translation_model is not None:
        return _translation_tokenizer, _translation_model

    tokenizer = AutoTokenizer.from_pretrained(NLLB_MODEL_NAME, local_files_only=True)
    model = AutoModelForSeq2SeqLM.from_pretrained(NLLB_MODEL_NAME, local_files_only=True)
    model.eval()

    _translation_tokenizer = tokenizer
    _translation_model = model
    print("[Translate] NLLB-200 fallback model loaded once at startup", flush=True)
    return tokenizer, model


def preload_translation():
    """Warm the NLLB-200 fallback model once at server startup."""
    try:
        load_translation_model()
    except Exception as exc:
        print(f"[Translate] NLLB preload failed ({exc!r}); will load on demand.")


def _nllb_translate_chunk(text: str, src_lang: str, tgt_lang: str) -> str:
    """Translate one sentence/line-scale chunk via NLLB. Raises on failure."""
    tokenizer, model = load_translation_model()
    tokenizer.src_lang = src_lang

    # _split_into_chunks() keeps every chunk sentence-scale, so this should
    # never actually engage -- but an explicit max_length (the tokenizer's
    # own reported limit, not a guess) plus a warning if it ever fires
    # means a truncation here is never silent.
    normalized_text = _normalize_for_translation(text)
    model_max_length = getattr(tokenizer, "model_max_length", None) or 1024
    untruncated_len = len(tokenizer(normalized_text)["input_ids"])
    if untruncated_len > model_max_length:
        print(f"[NLLB] WARNING: chunk of {untruncated_len} tokens exceeds "
              f"model_max_length={model_max_length}; truncating ({src_lang}->{tgt_lang}).")
    inputs = tokenizer(normalized_text, return_tensors="pt", truncation=True, max_length=model_max_length)
    inputs = {key: value.to(model.device) for key, value in inputs.items()}
    # transformers >= 5 removed `lang_code_to_id`; resolve the NLLB
    # language token via convert_tokens_to_ids (e.g. kan_Knda -> 256083).
    lang_code_map = getattr(tokenizer, "lang_code_to_id", None)
    forced_bos_token_id = (lang_code_map or {}).get(tgt_lang)
    if forced_bos_token_id is None:
        forced_bos_token_id = tokenizer.convert_tokens_to_ids(tgt_lang)
    if forced_bos_token_id is None or forced_bos_token_id == tokenizer.unk_token_id:
        print(f"[NLLB] Unknown target language code: {tgt_lang!r}")
        return text

    input_len = inputs["input_ids"].shape[1]
    max_new_tokens = min(600, max(256, int(input_len * 2.5)))

    # Same process-wide lock IndicTrans2 uses (see translation_service.py):
    # serializes this generate() call against every other translation call
    # so concurrent chat requests queue instead of thrashing against each
    # other.
    with TRANSLATE_LOCK, torch.inference_mode():
        generated_tokens = model.generate(
            **inputs,
            forced_bos_token_id=forced_bos_token_id,
            max_new_tokens=max_new_tokens,
            no_repeat_ngram_size=3,
        )

    return tokenizer.batch_decode(generated_tokens, skip_special_tokens=True)[0].strip()


def nllb_translate(text: str, src_lang: str, tgt_lang: str) -> str:
    """Translate text with NLLB-200 (used as fallback), chunked by
    line/sentence like translation_service.translate() -- same rationale:
    keeps each generate() call short enough to avoid mid-reply truncation
    on long, multi-line assistant replies."""
    if not text or src_lang == tgt_lang:
        return text

    _t0 = time.perf_counter()
    pieces = []
    n_chunks = 0
    for sep, content in _split_into_chunks(text):
        if not content.strip():
            pieces.append(sep + content)
            continue
        n_chunks += 1
        try:
            translated = translate_preserving_markdown(
                content, lambda t: _nllb_translate_chunk(t, src_lang, tgt_lang)
            )
            pieces.append(sep + translated)
        except Exception as exc:
            print(f"[NLLB] translation error on chunk ({src_lang}->{tgt_lang}): {exc!r}")
            pieces.append(sep + content)
    elapsed_ms = (time.perf_counter() - _t0) * 1000.0
    print(f"[Translate] NLLB {src_lang}->{tgt_lang} {n_chunks} chunk(s) took {elapsed_ms:.0f}ms", flush=True)
    return "".join(pieces)


TARGET_LANGUAGE_HINTS = {
    "hin_Deva": (
        r"\b(?:in|into|answer in|reply in|translate to|output in|respond in)\s+hindi\b",
        r"\bhindi\s+mein\b",
        r"\bhindi\s+me\b",
    ),
    "kan_Knda": (
        r"\b(?:in|into|answer in|reply in|translate to|output in|respond in)\s+kannada\b",
        r"\bkannada\s+mein\b",
        r"\bkannada\s+me\b",
    ),
    "tam_Taml": (
        r"\b(?:in|into|answer in|reply in|translate to|output in|respond in)\s+tamil\b",
        r"\btamil\s+(?:la|le|il)\b",
        r"\btamil\s+me\b",
    ),
    "tel_Telu": (
        r"\b(?:in|into|answer in|reply in|translate to|output in|respond in)\s+telugu\b",
        r"\btelugu\s+lo\b",
        r"\btelugu\s+me\b",
    ),
}


def extract_target_language(text: str) -> tuple[str, Optional[str]]:
    """Remove explicit language hints and return the cleaned text plus a target language."""
    if not text:
        return "", None

    cleaned_text = text
    requested_language: Optional[str] = None

    for language_code, patterns in TARGET_LANGUAGE_HINTS.items():
        for pattern in patterns:
            if re.search(pattern, cleaned_text, flags=re.IGNORECASE):
                requested_language = language_code
                cleaned_text = re.sub(pattern, " ", cleaned_text, flags=re.IGNORECASE)

    cleaned_text = re.sub(r"\s+", " ", cleaned_text).strip()
    return cleaned_text, requested_language


# ============================================================
# 3. DISEASE DETECTION
# ============================================================

def build_disease_index(kb: Dict[str, Dict[str, Any]]) -> Dict[str, str]:
    """Build a lookup index: name/alias (lowercase) -> canonical disease key."""
    index: Dict[str, str] = {}
    for disease_key, data in kb.items():
        index[str(disease_key).lower()] = disease_key
        aliases = data.get("aliases", []) if isinstance(data, dict) else []
        if isinstance(aliases, list):
            for alias in aliases:
                alias_norm = str(alias or "").strip().lower()
                if alias_norm:
                    index[alias_norm] = disease_key
    return index


def match_disease(user_text: str, index: Dict[str, str], cutoff: float = 0.78) -> Optional[str]:
    """Resolve a user message to a canonical disease key.

    - Exact word-boundary match first (handles "aids", "malaria" cleanly).
    - Fuzzy match against individual words/phrases for typos (e.g. "Diabities").
    Returns the canonical disease name or None.
    """
    if not user_text or not index:
        return None

    user_text_lower = user_text.lower()
    # Exact match first, anchored to word boundaries -- a plain substring
    # check let short abbreviation aliases (e.g. "uti", "tb", "cad") match
    # INSIDE unrelated words ("uti" hides inside "precautions"), silently
    # hijacking a follow-up question to the wrong disease and clobbering
    # the session's remembered disease in the process. \b ensures "uti"
    # only matches the standalone word "uti", not a substring of "precautions".
    for name, canonical in index.items():
        if name and re.search(rf"\b{re.escape(name)}\b", user_text_lower):
            return canonical

    # Fuzzy match against individual words/phrases for typos
    words = user_text_lower.split()
    candidates = list(index.keys())
    for n in range(min(4, len(words)), 0, -1):  # try 4-word, 3-word, ... phrases
        for i in range(len(words) - n + 1):
            phrase = " ".join(words[i:i + n])
            close = difflib.get_close_matches(phrase, candidates, n=1, cutoff=cutoff)
            if close:
                return index[close[0]]
    return None


# Only carry over the previous turn's disease when the current message is
# CLEARLY a pronoun-based follow-up (not merely because a fuzzy match failed).
FOLLOWUP_PRONOUNS = {"it", "this", "that", "the disease", "this disease"}


def resolve_disease(user_text: str, index: Dict[str, str], last_disease: Optional[str]):
    """Resolve the disease for a message, using session memory only for
    pronoun-based follow-ups. Returns (disease, status) where status is one of
    "matched", "followup", or "not_found"."""
    matched = match_disease(user_text, index)
    if matched:
        return matched, "matched"
    words = set(str(user_text or "").lower().split())
    if last_disease and (words & FOLLOWUP_PRONOUNS):
        return last_disease, "followup"
    return None, "not_found"


# Module-level disease index, built once at import time (fast direct lookups).
DISEASE_KB = load_kb() or {}
DISEASE_INDEX = build_disease_index(DISEASE_KB)
print(f"[KB] Loaded {len(DISEASE_KB)} diseases, {len(DISEASE_INDEX)} name/alias lookup entries")


def _extract_candidate_term(query: str) -> Optional[str]:
    """Extract the disease-like term from a 'what is X'-style question.

    Used only for the deterministic not-found reply, so the user sees exactly
    which term we could not match (e.g. "GRED").
    """
    raw = re.sub(r"\s+", " ", str(query or "").strip())
    m = re.search(
        r"(?:what is|what are|what's|about|define|tell me about|explain)\s+"
        r"([a-zA-Z0-9'\- ]{1,40})",
        raw, re.IGNORECASE)
    if not m:
        return None
    term = m.group(1).strip().strip('?".,;!')
    term = re.sub(
        r"\s+(the|a|an|disease|illness|condition|please)\s*$",
        "", term, flags=re.IGNORECASE).strip()
    return term or None


# Words that make a candidate phrase look like GENERAL phrasing rather than a
# specific disease/term name. Used to decide whether the "not a commonly known
# medical term" reply is appropriate (reserved for real named terms like "GRED").
_GENERAL_PHRASE_WORDS = {
    "the", "a", "an", "early", "late", "best", "about", "what", "is", "are",
    "how", "to", "when", "why", "should", "do", "does", "for", "during", "in",
    "of", "me", "please", "tell",
    # field / intent phrasing
    "prevent", "prevention", "preventions", "precaution", "precautions", "avoid", "food", "foods", "diet",
    "eat", "eating", "nutrition", "needs", "need", "meal", "symptom",
    "symptoms", "signs", "sign", "cause", "causes", "treatment", "treat",
    "control", "manage", "medicine", "medication", "exercise", "yoga",
    "doctor", "hospital", "emergency", "urgent", "stage", "stages",
    # common general / symptom terms that are NOT disease names
    "fever", "cough", "cold", "headache", "pain", "aches", "vomiting",
    "nausea", "diarrhea", "diarrhoea", "rash", "fatigue", "weakness",
    "tiredness", "dizziness", "infection", "virus", "viral", "bacteria",
    "bacterial", "disease", "diseases", "condition", "illness",
    # pronouns / fillers -- "tell me about it", "what should I do about
    # this" -- these are NOT disease names but were being captured by the
    # "about X" regex in _extract_candidate_term and then misclassified as
    # a real named term, which dead-ended the reply before ever reaching
    # FAISS retrieval or the LLM tier (Portkey/Ollama).
    "it", "that", "this", "these", "those", "them", "him", "her",
    "he", "she", "they", "we", "us", "you", "i", "one",
    "something", "anything", "everything", "nothing", "someone", "anyone",
}


def _looks_like_named_term(term: Optional[str]) -> bool:
    """True when the extracted candidate looks like a specific disease/term name
    (e.g. "GRED"), as opposed to general phrasing (e.g. "the early preventions"
    or "the food needs to avoid")."""
    if not term:
        return False
    words = str(term).lower().split()
    while words and words[0] in {"the", "a", "an"}:
        words = words[1:]
    if not words:
        return False
    if any(w in _GENERAL_PHRASE_WORDS for w in words):
        return False
    return True


def detect_disease_from_query(query: str, kb: Dict[str, Dict[str, str]]) -> Optional[str]:
    """Detect disease from query using exact/alias/fuzzy matching.

    Args:
        query: User query
        kb: Knowledge base

    Returns:
        Disease name if found, None otherwise
    """
    if not query or not kb:
        return None
    index = build_disease_index(kb)
    return match_disease(query, index)


def detect_disease(query: str, kb: Dict[str, Dict[str, str]]) -> Optional[str]:
    """Compatibility alias for disease detection."""
    return detect_disease_from_query(query, kb)


# ============================================================
# 4. INTENT DETECTION
# ============================================================

INTENT_KEYWORDS = {
    "What is the disease?": [
        "what is", "about", "explain", "tell me", "definition", "describe"
    ],
    "What are the symptoms?": [
        "symptom", "symptoms", "sign", "signs", "show", "indicate", "present"
    ],
    "What causes the disease?": [
        "cause", "causes", "why", "reason", "trigger", "origin", "source"
    ],
    "Prevention": [
        "prevent", "prevention", "avoid", "avoid", "reduce", "lower", "protect"
    ],
    "Food": [
        "food", "diet", "eat", "eating", "nutrition", "what to eat", "what not to eat"
    ],
    "How to control it?": [
        "treat", "treatment", "control", "manage", "cure", "yoga", "exercise", "remedy"
    ],
    "When to see a doctor?": [
        "doctor", "consult", "when", "see a doctor", "hospital", "emergency", "urgent"
    ]
}

ADVANCED_KEYWORDS = [
    "pathophysiology", "mechanism", "molecular", "complication", "complications",
    "differential diagnosis", "prognosis", "guideline", "evidence", "evidence-based",
    "contraindication", "pharmacology", "pharmacokinetics", "comorbidity", "co-morbidity",
    "renal adjustment", "dose adjustment", "risk stratification", "clinical reasoning",
    "advanced management", "in detail", "detailed", "comprehensive",
]

INTERMEDIATE_KEYWORDS = [
    "how long", "duration", "recover", "recovery", "stages", "stage", "monitor",
    "home care", "warning signs", "warning sign", "when to worry", "safe medicine",
    "what should i do", "what to do", "can i", "is it serious", "is it dangerous",
    "diet plan", "exercise plan", "daily routine", "management plan", "daily care",
    "manage", "at home", "when to see doctor", "when should i see doctor",
]

FOLLOWUP_HINTS = [
    "it", "this", "that", "this disease", "that disease", "for this", "for it",
    "what about", "can it", "is it", "does it", "how to prevent", "what to eat",
]


def classify_question_level(query: str) -> str:
    """Classify query into basic/intermediate/advanced for response routing."""
    q = normalize(query)
    if not q:
        return "basic"

    if any(marker in q for marker in ADVANCED_KEYWORDS):
        return "advanced"

    if any(marker in q for marker in INTERMEDIATE_KEYWORDS):
        return "intermediate"

    # Longer multi-part clinical questions are treated as advanced.
    if len(str(query or "").split()) >= 25:
        return "advanced"
    if len(str(query or "").split()) >= 14:
        return "intermediate"
    return "basic"


def _should_use_session_disease(query: str) -> bool:
    """Use remembered disease only for clear pronoun-based follow-ups.

    Word-set intersection on FOLLOWUP_PRONOUNS only, so substrings like "it"
    inside "diet" can never trigger a false follow-up.
    """
    if not query:
        return False
    words = set(str(query).lower().split())
    return bool(words & FOLLOWUP_PRONOUNS)


def _compose_context_by_level(qa: Dict[str, str], intent: str, level: str) -> str:
    """Build context window based on question complexity level."""
    primary = str(qa.get(intent, "")).strip()
    about = str(qa.get("What is the disease?", "")).strip()
    symptoms = str(qa.get("What are the symptoms?", "")).strip()
    causes = str(qa.get("What causes the disease?", "")).strip()
    prevention = str(qa.get("Prevention", "")).strip()
    control = str(qa.get("How to control it?", "")).strip()
    food = str(qa.get("Food", "")).strip()
    exercise = str(qa.get("Exercise", "")).strip()
    emergency = str(qa.get("When to see a doctor?", "")).strip()

    if level == "basic":
        return primary or about or symptoms

    if level == "intermediate":
        blocks = [
            primary,
            f"Symptoms: {symptoms}" if symptoms else "",
            f"Cause: {causes}" if causes else "",
            f"Care: {control}" if control else "",
            f"When to see doctor: {emergency}" if emergency else "",
        ]
        return "\n".join([b for b in blocks if b]).strip()

    blocks = [
        primary,
        f"About: {about}" if about else "",
        f"Symptoms: {symptoms}" if symptoms else "",
        f"Cause: {causes}" if causes else "",
        f"Prevention: {prevention}" if prevention else "",
        f"Food: {food}" if food else "",
        f"Exercise: {exercise}" if exercise else "",
        f"Control: {control}" if control else "",
        f"When to see doctor: {emergency}" if emergency else "",
    ]
    return "\n".join([b for b in blocks if b]).strip()


def _general_case_context() -> str:
    return (
        "Use safe rural primary-care guidance only. "
        "Provide home-care steps, hydration advice when relevant, warning signs, "
        "and when to seek urgent doctor/hospital care. "
        "Do not prescribe unsafe doses or unsupported claims."
    )


def _fallback_general_medical_reply(query: str) -> str:
    q = normalize(query)
    if any(k in q for k in ["dehydration", "ors", "vomiting", "loose motion", "diarrhea"]):
        return (
            "For dehydration risk: give frequent ORS sips, continue fluids, and monitor urine output. "
            "Seek urgent care for persistent vomiting, no urine for 6 to 8 hours, lethargy, blood in stool, "
            "or inability to drink."
        )
    if any(k in q for k in ["fever", "child", "infant", "baby"]):
        return (
            "For a child with fever, keep hydration adequate, use age-appropriate fever medicine only as advised, "
            "and monitor breathing, urine, and alertness. Visit a doctor urgently if fever is high and persistent, "
            "breathing is difficult, the child is very drowsy, or fluid intake is poor."
        )
    return (
        "Please share the suspected disease name for a precise answer. "
        "If symptoms are severe or worsening, seek in-person medical care immediately."
    )


def _structured_kb_reply(disease: str, qa: Dict[str, str], level: str) -> str:
    about = str(qa.get("What is the disease?", "")).strip()
    symptoms = str(qa.get("What are the symptoms?", "")).strip()
    causes = str(qa.get("What causes the disease?", "")).strip()
    control = str(qa.get("How to control it?", "")).strip()
    prevention = str(qa.get("Prevention", "")).strip()
    food = str(qa.get("Food", "")).strip()
    exercise = str(qa.get("Exercise", "")).strip()
    emergency = str(qa.get("When to see a doctor?", "")).strip()
    emergency_signs = str(qa.get("Emergency signs", "")).strip()
    danger = str(qa.get("Is it dangerous?", "")).strip()

    if level == "intermediate":
        lines = [
            f"- Problem: {disease}. {about}".strip(),
            f"- Symptoms to monitor: {symptoms}" if symptoms else "",
            f"- Home care: {control}" if control else "",
            f"- Food/fluids: {food}" if food else "",
            f"- When to see a doctor: {emergency or 'If symptoms worsen or danger signs appear.'}",
        ]
        return "\n".join([ln for ln in lines if ln])

    lines = [
        f"1. Condition: {disease}. {about}".strip(),
        f"2. Symptoms: {symptoms}" if symptoms else "",
        f"3. Cause/Mechanism: {causes}" if causes else "",
        f"4. Complication risk: {danger or emergency_signs or 'Watch for deterioration and red-flag symptoms.'}",
        f"5. Management plan: {control}" if control else "",
        f"6. Prevention: {prevention}" if prevention else "",
        f"7. Food and activity: {food} {exercise}".strip() if food or exercise else "",
        f"8. Urgent care trigger: {emergency or 'Seek urgent care if severe symptoms develop.'}",
    ]
    return "\n".join([ln for ln in lines if ln])


def _is_low_quality_reply(answer: str, min_words: int = 35) -> bool:
    text = str(answer or "").strip()
    if not text:
        return True
    return len(text.split()) < min_words


# Ordered field matchers. FIRST match wins, so "food to avoid" maps to Food
# (not Prevention), even though "avoid" also appears in the Prevention pattern.
FIELD_PATTERNS = [
    ("Food", re.compile(r"\b(food|foods|food needs|foods? to avoid|what food|diet|diet plan|eat|eating|nutrition|meal|meal plan|what to eat|what not to eat)\b", re.IGNORECASE)),
    ("When to see a doctor?", re.compile(r"\b(see a doctor|when to see|when should i|consult|hospital|emergency|urgent|doctor)\b", re.IGNORECASE)),
    ("What are the symptoms?", re.compile(r"\b(symptom|symptoms|signs|sign|show|indicate|present)\b", re.IGNORECASE)),
    ("What causes the disease?", re.compile(r"\b(causes?|why do|why is|risk factor|risk factors|origin|trigger)\b", re.IGNORECASE)),
    ("Stages", re.compile(r"\b(stages|stage|progression|phases|phase)\b", re.IGNORECASE)),
    ("Exercise", re.compile(r"\b(exercise|yoga|physical activity|workout)\b", re.IGNORECASE)),
    ("How to control it?", re.compile(r"\b(treat|treatment|control|manage|management|cure|medicine|medication|remedy)\b", re.IGNORECASE)),
    ("Prevention", re.compile(r"\b(prevent|preventions?|precautions?|early prevention|how to avoid|avoid|reduce|lower|protect)\b", re.IGNORECASE)),
    ("What is the disease?", re.compile(r"\b(what is|what are|about|explain|tell me|definition|describe)\b", re.IGNORECASE)),
]


def detect_intent(query: str) -> str:
    """
    Detect user intent and map to KB field. First matching pattern wins.
    Defaults to "What is the disease?" when nothing matches.
    """
    if not query:
        return "What is the disease?"

    for field, pattern in FIELD_PATTERNS:
        if pattern.search(query):
            return field
    return "What is the disease?"


# ============================================================
# 4b. SCRAPED-TXT KB FALLBACK (zero LLM cost when a field is missing from
# disease_knowledge_base.json but present in the scraped WHO/CDC/NHS/Mayo
# knowledge_base/*.txt files built by scrape_medical_kb.py)
# ============================================================

# Maps a disease_knowledge_base.json field name (the `intent` values
# detect_intent() returns) to the matching "## <Heading>" section in the
# scraped .txt files. Only fields that exist in both places are listed --
# json-only fields (e.g. "What is the disease?", "Stages", "Exercise")
# have no scraped equivalent and simply have no fallback.
_JSON_FIELD_TO_TXT_SECTION = {
    "What are the symptoms?": "symptoms",
    "What causes the disease?": "causes",
    "Prevention": "prevention",
    "How to control it?": "treatment",
    "Food": "diet recommendations",
    "When to see a doctor?": "when to see a doctor",
}

# disease_knowledge_base.json's disease name doesn't always slugify to the
# same filename scrape_medical_kb.py used (it keys diseases independently
# for a different, overlapping-but-not-identical disease list). Only real
# mismatches need an entry here; everything else slugifies consistently.
_TXT_KB_DISEASE_ALIASES = {
    "chickenpox": "chicken_pox",
    # scrape_medical_kb.py's DISEASES dict now keys these three by the exact
    # (typo'd) canonical CSV disease name so its slug matches the ML model's
    # label 1:1; disease_knowledge_base.json keeps the clean, readable name
    # instead, so the two slugs diverge and need an explicit bridge here.
    "vertigo": "vertigo_paroymsal_positional_vertigo",
    "dimorphic_hemorrhoids": "dimorphic_hemmorhoids_piles",
    "paralysis": "paralysis_brain_hemorrhage",
}

_TXT_SECTION_NOT_FOUND = "information not found on the source page for this field."


def _txt_kb_slug(disease_name: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", str(disease_name or "")).strip("_").lower()
    return _TXT_KB_DISEASE_ALIASES.get(slug, slug)


@lru_cache(maxsize=64)
def _read_txt_kb_file(slug: str) -> str:
    path = TXT_KB_DIR / f"{slug}.txt"
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def read_txt_kb_section(disease_name: str, intent: str) -> Optional[str]:
    """Return the scraped-KB section text for `disease_name`/`intent`, or None.

    `intent` is a disease_knowledge_base.json field name (as returned by
    detect_intent()); it's mapped to the scraped .txt file's "## Heading"
    via _JSON_FIELD_TO_TXT_SECTION. Returns None when there's no scraped
    file for this disease, no mapped section for this intent, or the
    scraper recorded "not found" for that section on the source page.
    """
    txt_field = _JSON_FIELD_TO_TXT_SECTION.get(intent)
    if not txt_field:
        return None
    content = _read_txt_kb_file(_txt_kb_slug(disease_name))
    if not content:
        return None
    pattern = re.compile(
        rf"^##\s*{re.escape(txt_field.title())}\s*\n(.+?)(?=\n##\s|\Z)",
        re.IGNORECASE | re.DOTALL | re.MULTILINE,
    )
    match = pattern.search(content)
    if not match:
        return None
    text = match.group(1).strip()
    if not text or text.lower().strip(".") == _TXT_SECTION_NOT_FOUND.strip("."):
        return None
    return text


# ============================================================
# 5. KB FIELD RETRIEVAL
# ============================================================

def get_kb_field(kb: Dict[str, Dict[str, str]], disease: str, intent: str) -> str:
    """
    Get specific field from KB for a disease.
    
    Args:
        kb: Knowledge base
        disease: Disease name
        intent: Intent/field key
        
    Returns:
        KB field content, empty string if not found
    """
    if not kb or not disease or not intent:
        return ""
    
    disease_data = kb.get(disease)
    if not isinstance(disease_data, dict):
        return ""
    
    field_value = disease_data.get(intent, "")
    
    return str(field_value).strip() if field_value else ""


# ============================================================
# 6. SESSION MANAGEMENT
# ============================================================

def set_session_disease(session_id: str, disease: str) -> None:
    """Store detected disease in session."""
    if not session_id or not disease:
        return
    
    from datetime import datetime
    
    if session_id not in _SESSIONS:
        _SESSIONS[session_id] = {}
    
    _SESSIONS[session_id]["disease"] = disease
    _SESSIONS[session_id]["timestamp"] = datetime.now()


def get_session_disease(session_id: str) -> Optional[str]:
    """Retrieve last detected disease from session."""
    if not session_id or session_id not in _SESSIONS:
        return None
    
    return _SESSIONS[session_id].get("disease")


def clear_session(session_id: str) -> None:
    """Clear session data."""
    _SESSIONS.pop(session_id, None)


# ============================================================
# 7. OLLAMA (MISTRAL) CALL
# ============================================================

OLLAMA_HOST = "127.0.0.1"
OLLAMA_PORT = 11434
OLLAMA_READY_CACHE: dict = {"ts": 0.0, "ok": False}


def ollama_ready(cache_secs: float = 5.0) -> bool:
    """Return True when the Ollama server is reachable on 127.0.0.1:11434.

    Result is cached for `cache_secs` so hot chat requests do not pay a
    socket connect each time, while a server that just started is picked up
    within a few seconds.
    """
    import socket as _socket

    now = time.time()
    if now - OLLAMA_READY_CACHE["ts"] < cache_secs:
        return OLLAMA_READY_CACHE["ok"]
    ok = False
    try:
        sock = _socket.create_connection((OLLAMA_HOST, OLLAMA_PORT), timeout=1)
        sock.close()
        ok = True
    except OSError:
        ok = False
    OLLAMA_READY_CACHE["ts"] = now
    OLLAMA_READY_CACHE["ok"] = ok
    return ok


OLLAMA_UNAVAILABLE_MESSAGE = (
    "I'm having trouble connecting to my AI engine (Ollama). "
    "Please make sure Ollama is running on localhost:11434 "
    "(start it with: ollama serve) and try again."
)

SYSTEM_PROMPT = """
You are Rural Healthcare AI Assistant. 

Greeting: When a user first opens the chat, greet them with:
"Hi! I am Rural Healthcare AI. I am here to help you related to 
predicting diseases and answering your healthcare questions. 
Feel free to ask whatever you want related to healthcare!"

Your role:
- Help users understand disease symptoms, precautions, and general 
  health information
- Explain prediction results from the assessment form in simple language
- Answer questions about the 41 diseases covered by this system
- Provide general health and wellness advice
- Guide users to seek professional medical care when needed

Always:
- Respond in simple, clear language suitable for rural communities
- End serious health queries with: "Please consult a qualified 
  doctor for proper diagnosis and treatment"
- Be empathetic, patient, and non-judgmental

Never:
- Provide specific drug dosages or prescribe medication
- Replace professional medical advice
- Discuss topics unrelated to healthcare

Grounding:
- Only state medical facts you are confident about. If you are unsure, say
  "I don't have reliable information about that — please consult a doctor."
- Never invent drug names, dosages, statistics, or study results.
- Prefer saying less over guessing.

India emergency numbers (state these exactly, do not guess a different one):
- 108 is the primary, free number for general medical emergencies —
  accidents, heart attacks, breathing difficulty, unconsciousness, etc.
  Always give this as the main number for an urgent medical situation.
- 102 is the National Ambulance Service, dedicated to maternal and child
  health (pregnancy, delivery, infant care) — not the general emergency
  number, mention it only for that specific use.
- 112 is India's unified emergency number (police/fire/medical combined),
  useful if 108 doesn't connect.
"""


def call_ollama(
    query: str,
    context: str,
    diag: Optional[dict] = None,
    history: Optional[list] = None,
) -> Optional[str]:
    """
    Call the LLM via the 3-tier fallback router (llm_router.get_response):
    Tier 1 Biomistral -> Tier 2 tinyllama -> Tier 3 knowledge-base lookup.
    
    Args:
        query: User question
        context: KB context to use
        diag: Optional dict updated in place with llm_time_ms (float),
              model_used (str) and model_tier (str) for RAG logging.
        history: Optional list of {role, content} dicts (recent conversation).
        
    Returns:
        Formatted response, or None if every tier failed.
    """
    if not query or not context:
        return None

    history_text = ""
    if history:
        lines = []
        for msg in history[-20:]:
            role = str(msg.get("role", "user") or "user").capitalize()
            content = str(msg.get("content", "") or "")[:400]
            if content:
                lines.append(f"{role}: {content}")
        if lines:
            history_text = "Recent conversation:\n" + "\n".join(lines) + "\n\n"

    prompt = f"""{history_text}Context:
{context}

User question:
{query}

Answer:"""
    
    t0 = time.perf_counter()
    try:
        answer, model_used, tier = llm_router.get_response(
            SYSTEM_PROMPT,
            history or [],
            prompt,
            kb_query=query,
        )
    except Exception as exc:
        print(f"[LLM] fallback router failed: {exc}")
        answer, model_used, tier = None, OLLAMA_MODEL, "failed"

    if diag is not None:
        diag["llm_time_ms"] = (time.perf_counter() - t0) * 1000.0
        diag["model_used"] = model_used or OLLAMA_MODEL
        diag["model_tier"] = tier or "failed"

    answer = (answer or "").strip()
    if not answer:
        return None
    # Echo-guard: the base BioMistral model sometimes echoes the prompt
    # (e.g. "Context:" or the system prompt text) instead of answering.
    # Treat echoed output as a failure so callers fall back to canned text.
    lowered = answer.lower()
    system_prefix = SYSTEM_PROMPT.strip().lower()[:80]
    if (
        lowered.startswith("context:")
        or lowered.startswith("user question:")
        or lowered.startswith("answer:")
        or (system_prefix and lowered.startswith(system_prefix))
    ):
        return None
    # Enforce word limit (300 words)
    return limit_response_words(answer, 300)



_SMALL_TALK_GREETING = (
    "Hello. I am Rural Healthcare AI. I am here to help you related to "
    "predicting diseases and answering your healthcare questions. "
    "Feel free to ask whatever you want related to healthcare."
)

_SMALL_TALK_FINE = (
    "I am doing great, thank you for asking. How can I help you today? "
    "You can ask me about disease symptoms, precautions, or any healthcare question."
)

_SMALL_TALK_THANKS = (
    "You are welcome. Feel free to ask me anything about your health anytime."
)


def _small_talk_reply(query: str) -> Optional[str]:
    """Return a canned friendly reply for pure small talk (no LLM call)."""
    q = normalize(query)
    greeting_phrases = {
        "hi", "hii", "hiii", "hello", "helo", "hey", "heyy", "hai",
        "good morning", "good afternoon", "good evening",
    }
    words = q.split()
    if q in greeting_phrases or (len(words) <= 2 and q.startswith(("hi", "hello", "hey", "hai"))):
        return _SMALL_TALK_GREETING
    if "how are you" in q or "how r u" in q or "how are u" in q:
        return _SMALL_TALK_FINE
    if any(k in q for k in ("thank you", "thanks", "tq", "thx")):
        return _SMALL_TALK_THANKS
    return None


def _process_file_query(
    query: str,
    file_chunks: Optional[list] = None,
    session_id: str = "default",
    diag: Optional[dict] = None,
    history: Optional[list] = None,
) -> str:
    """File-grounded answer path: retrieve only the relevant chunks of the
    attached file and answer from them (Groq fast tier first, then the
    normal Ollama -> Portkey -> KB chain). Disease detection, FAQ and
    small-talk routing are skipped so the file text can never be
    mis-routed to a KB disease answer."""
    chunks = list(file_chunks) if file_chunks else []
    if not chunks and query:
        chunks = [query]
    try:
        answer, tier = llm_router.handle_file_question(query, chunks, SYSTEM_PROMPT)
    except Exception as exc:
        print(f"[FILE-LLM] file question failed: {exc}")
        answer, tier = None, "failed"
    if diag is not None:
        diag["model_used"] = "groq" if tier == "groq" else str(diag.get("model_used", "") or "")
        diag["model_tier"] = tier or "failed"
    if answer:
        return answer
    if not ollama_ready():
        return OLLAMA_UNAVAILABLE_MESSAGE
    return (
        "I couldn't generate an answer about this file right now. "
        "Please try again, or ask a health question directly."
    )


# ============================================================
# BULK/META DISEASE REQUESTS ("explain each disease", "all 41", ...)
# ============================================================
# Bug: a bulk request like "give me the explanation for each disease" was
# never a single-disease name, so resolve_disease() correctly finds nothing
# -- but before it even got that far, the FAQ fast path's TF-IDF+semantic
# fuzzy matcher (faq_matcher.py) was matching it to the UNRELATED FAQ
# entry "What if my disease is not in the list of 41?" (TF-IDF 0.558,
# semantic 0.421 -- both just over their 0.55/0.40 cutoffs, since both
# texts are dense with the words "disease"/"41"). That produced the
# nonsensical "outside the 41 diseases... consult a doctor" refusal for a
# question that IS about the 41 diseases. Detecting bulk requests here,
# before the FAQ fast path runs, fixes it at the actual point of failure.
BULK_REQUEST_PATTERN = re.compile(
    r"\b(each|all|every)\b.*\b(diseases?|conditions?)\b|"
    r"\bexplain (?:all|each|every)\b|"
    r"\ball 41\b|\ball diseases\b",
    re.IGNORECASE,
)

BULK_SUMMARY_FOLLOWUP_PATTERN = re.compile(
    r"\b(one.?line|short|quick|brief)\b[^.?!]*\bsummary\b|"
    r"\bsummary\b[^.?!]*\b(all|each|every|41)\b|"
    r"^\s*(yes|yeah|yep|sure|ok|okay)\b[^.?!]*\bsummary\b",
    re.IGNORECASE,
)

_BULK_DISEASE_PROMPT = (
    "That's a lot to cover in one message! I can give you a one-line "
    "summary of all 41, or a full explanation of any specific one you "
    "pick — which would you prefer? You can also just ask me about one "
    "disease at a time, e.g. 'what is malaria'."
)


def is_bulk_disease_request(text: str) -> bool:
    return bool(BULK_REQUEST_PATTERN.search(str(text or "")))


def is_bulk_summary_followup(text: str) -> bool:
    return bool(BULK_SUMMARY_FOLLOWUP_PATTERN.search(str(text or "")))


@lru_cache(maxsize=1)
def _load_41_disease_names() -> tuple:
    """The 41 canonical disease names the prediction model covers (the
    same source cache.py's should_cache() reads), NOT the 66-entry
    disease_knowledge_base.json (which also holds extra KB-only terms like
    'Breast Cancer' that aren't among the model's 41)."""
    import csv
    csv_path = Path(__file__).parent / "Disease_precaution.csv"
    if not csv_path.exists():
        return ()
    with open(csv_path, encoding="utf-8") as fh:
        return tuple(row["Disease"].strip() for row in csv.DictReader(fh) if row.get("Disease", "").strip())


@lru_cache(maxsize=1)
def _build_one_line_disease_summaries() -> str:
    """Cheap (no LLM call), built once: the first sentence of each of the
    41 diseases' 'What is the disease?' field, as a compact list -- what
    1c asks for when the user follows up on the bulk-request prompt above."""
    lines = []
    for csv_name in _load_41_disease_names():
        kb_key = match_disease(csv_name, DISEASE_INDEX) or csv_name
        entry = DISEASE_KB.get(kb_key, {})
        what_is = str(entry.get("What is the disease?", "")).strip()
        first_sentence = re.split(r"(?<=[.!?])\s+", what_is)[0].strip() if what_is else "No summary available."
        lines.append(f"- {csv_name}: {first_sentence}")
    return "\n".join(lines)


def process_query(
    query: str,
    session_id: str = "default",
    predicted_disease: Optional[str] = None,
    diag: Optional[dict] = None,
    history: Optional[list] = None,
    file_attached: bool = False,
    file_chunks: Optional[list] = None,
) -> str:
    """
    Main chatbot pipeline: strict, deterministic, no hallucinations.
    
    Args:
        query: User question
        session_id: Session ID for memory
        predicted_disease: Optional disease from ML model
        history: Optional list of {role, content} dicts (recent conversation).
        file_attached: True when `query` targets an attached file. Such
            queries bypass disease detection/KB routing entirely and are
            answered from the file's chunks (see _process_file_query).
        file_chunks: Pre-chunked extracted text of the attached file,
            cached once per upload by /ai-chat.
        
    Returns:
        Safe, KB-only response
    """
    
    # Load KB
    kb = load_kb()
    if not kb:
        return "Knowledge base unavailable. Please try again later."

    # ============================================================
    # FILE ATTACHMENT FAST PATH (no disease detection, no KB routing)
    # ============================================================
    # A file-attached question is answered from the file's chunks only.
    # Running disease detection over the file text causes spurious fuzzy
    # matches (e.g. an emotion-detection paper matching "Urinary Tract
    # Infection"), so file queries must never touch KB disease routing.
    if file_attached:
        return _process_file_query(query, file_chunks, session_id, diag, history)
    
    # ============================================================
    # SMALL TALK FAST PATH (no LLM call)
    # ============================================================
    small_talk_reply = _small_talk_reply(query)
    if small_talk_reply:
        return small_talk_reply

    # ============================================================
    # BULK/META DISEASE REQUEST FAST PATH -- must run before the FAQ fast
    # path (see BULK_REQUEST_PATTERN above): a request like "explain each
    # disease" names no single disease, and was previously falling through
    # to the FAQ fuzzy matcher, which mis-matched it to an unrelated FAQ
    # entry. Checked in order: an explicit "one-line summary" request (or a
    # short "yes" reply following our own bulk-request prompt) returns the
    # actual compact list; anything else that reads as a bulk/meta request
    # gets the clarifying prompt instead of a wrong single-disease lookup.
    # ============================================================
    if is_bulk_summary_followup(query):
        if diag is not None:
            diag["model_tier"] = "bulk_summary"
        return _build_one_line_disease_summaries()
    if is_bulk_disease_request(query):
        if diag is not None:
            diag["model_tier"] = "bulk_prompt"
        return _BULK_DISEASE_PROMPT

    # ============================================================
    # ACTIVE KB FOLLOW-UP GUARD -- must run before the FAQ fast path.
    # A bare follow-up like "what are the symptoms" or "what are the
    # precautions" right after asking about a disease shares a word
    # (e.g. "symptoms") with unrelated app-usage FAQ questions (e.g.
    # "Can I type symptoms in my own words?"), which score high enough
    # on FAQ's TF-IDF/semantic fuzzy match (0.577, above its 0.55 cutoff)
    # to hijack the reply -- the user asks about Heart Attack, then a
    # plain follow-up question returns a generic app-FAQ or "not in my
    # scope" answer instead of the remembered disease's info, which reads
    # exactly like session memory being lost. Deterministic session-anchored
    # KB routing is a far stronger signal than a fuzzy FAQ score, so when a
    # remembered disease has real content for the field this message is
    # asking about, and the message doesn't itself name a (possibly
    # different) disease, skip the FAQ fast path entirely and let disease
    # resolution below (CASE B) answer from the KB.
    # ============================================================
    followup_disease = None
    remembered_for_followup = get_session_disease(session_id)
    if remembered_for_followup and remembered_for_followup in kb and not match_disease(query, DISEASE_INDEX):
        followup_field = detect_intent(query)
        if followup_field and kb.get(remembered_for_followup, {}).get(followup_field):
            followup_disease = remembered_for_followup

    # ============================================================
    # FAQ FAST PATH (no LLM call) -- curated Q&A about the app itself
    # (what it does, how to use it, what diseases it covers, how each
    # feature works), matched from knowledge_base/faq_index.json before
    # any disease detection or LLM tier is touched. Skipped for an active
    # KB follow-up (see guard above).
    # ============================================================
    if not followup_disease:
        faq_reply = match_faq(query)
        if faq_reply:
            if diag is not None:
                diag["model_tier"] = "faq"
            return faq_reply

    # ============================================================
    # STEP 1: DETECT DISEASE
    # ============================================================
    
    level = classify_question_level(query)
    explicit_disease, match_status = resolve_disease(query, DISEASE_INDEX, get_session_disease(session_id))
    remembered_disease = get_session_disease(session_id)

    # Deterministic, KB-driven disease resolution. No silent substitution of a
    # different disease when the fuzzy match fails.
    disease = predicted_disease or explicit_disease or (remembered_disease if match_status == "followup" else None)

    if not disease:
        field = detect_intent(query)
        candidate = _extract_candidate_term(query)

        # CASE D: user named a specific disease/term that is not in the KB.
        # Reserved ONLY for real named terms (e.g. "GRED"); general phrasing
        # like "what are the early preventions" must NEVER hit this reply.
        if candidate and _looks_like_named_term(candidate):
            return f"'{candidate}' is not a commonly known medical term in my knowledge base. Could you rephrase or check the spelling?"

        # CASE B: no NEW disease named, but one is active in session memory AND
        # the message clearly asks about a field -> answer from the KB using the
        # active disease (e.g. "what are the early preventions" after Dengue).
        if remembered_disease and field and remembered_disease in kb and kb.get(remembered_disease, {}).get(field):
            disease = remembered_disease
        else:
            # CASE C: fall through to Biomistral — a failed KB lookup never
            # dead-ends the conversation here.
            faiss_chunks = retrieve_faiss_chunks(query, diag=diag)
            general_context = _compose_faiss_context(faiss_chunks) or _general_case_context()
            general_reply = call_ollama(query, general_context, diag=diag, history=history)
            if general_reply:
                max_words = 300 if level == "advanced" else 220
                return limit_response_words(general_reply, max_words)
            # LLM failed — fail loud and clear when Ollama itself is down.
            if not ollama_ready():
                return OLLAMA_UNAVAILABLE_MESSAGE
            return limit_response_words(_fallback_general_medical_reply(query), 220)

    if explicit_disease or predicted_disease:
        set_session_disease(session_id, disease)

    if disease not in kb:
        return f"I don't have information about '{disease}' in my database. Please consult a doctor."

    qa = kb.get(disease, {})
    intent = detect_intent(query)

    # RAG: embed the question and retrieve the top-k most relevant FAISS chunks
    # (logged per question so retrieval can be verified). The disease name is
    # already known at this point (explicit match or remembered from session),
    # but a bare follow-up like "when should I see a doctor" carries no disease
    # name itself -- searching on the raw query alone lets FAISS return the
    # closest-worded chunk from ANY disease's page (observed: a Heart Attack
    # follow-up pulling in Peptic Ulcer Disease content), which the LLM then
    # answers from instead of the disease actually being discussed. Prefixing
    # the disease name grounds retrieval in the right disease's chunks.
    faiss_chunks = retrieve_faiss_chunks(f"{disease} {query}", diag=diag)
    rag_context = _compose_faiss_context(faiss_chunks) if faiss_chunks else ""

    # Basic questions should prefer direct deterministic KB output (unchanged).
    if level == "basic":
        context = _compose_context_by_level(qa, intent, level)
        if not context:
            # json has no answer for this field -- try the scraped WHO/CDC/
            # NHS/Mayo txt KB before giving up (still zero LLM cost).
            context = read_txt_kb_section(disease, intent)
            if context and diag is not None:
                diag["model_tier"] = "kb_txt"
        if not context:
            return f"Information about '{intent.lower()}' for {disease} is not available. Please consult a doctor."
        return limit_response_words(f"{disease}: {context}", 140)

    # Advanced disease questions should be deterministic from KB for consistency and safety (unchanged).
    if level == "advanced":
        return limit_response_words(_structured_kb_reply(disease, qa, "advanced"), 320)

    # Intermediate: ground the LLM prompt in this disease's own verified KB
    # content FIRST, then layer FAISS semantic context on top as extra
    # reference. `rag_context or kb_field_context` (the old behavior) let a
    # same-worded chunk from a DIFFERENT disease silently replace the
    # correct, already-known content whenever FAISS returned anything at
    # all (observed: a Heart Attack follow-up answered from Peptic Ulcer
    # Disease / Hypertension chunks) -- putting the right disease's KB text
    # first anchors the LLM so RAG can only add detail, not swap diseases.
    kb_field_context = _compose_context_by_level(qa, intent, level)
    if kb_field_context and rag_context:
        context = f"About {disease}:\n{kb_field_context}\n\nAdditional reference:\n{rag_context}"
    else:
        context = kb_field_context or rag_context

    # When the requested field is missing from the disease entry, try the
    # scraped WHO/CDC/NHS/Mayo txt KB directly first -- zero LLM cost, and
    # it's real sourced content rather than the LLM inferring from
    # unrelated fields. Only fall back to LLM-grounded generation if the
    # txt KB doesn't have this section either (e.g. no scraped file for
    # this disease, or the scraper found nothing for this field).
    if not qa.get(intent):
        txt_answer = read_txt_kb_section(disease, intent)
        if txt_answer:
            if diag is not None:
                diag["model_tier"] = "kb_txt"
            return limit_response_words(f"{disease}: {txt_answer}", 220)

        kb_context = json.dumps(qa, ensure_ascii=False)
        context = (
            "Use ONLY the following verified medical information to answer. "
            "If the answer is not in this information, say you don't have "
            "that specific detail and recommend consulting a doctor. "
            "Do not invent facts.\n\n"
            f"VERIFIED INFO:\n{kb_context}"
        )

    if not context:
        return f"Information about '{intent.lower()}' for {disease} is not available. Please consult a doctor."

    response = call_ollama(query, context, diag=diag, history=history)

    # Tier 1 - Knowledge base fallback
    if not response:
        if level in {"intermediate", "advanced"}:
            return limit_response_words(_structured_kb_reply(disease, qa, level), 320)
        kb_reply = f"{disease}: {context}"
        return limit_response_words(kb_reply, 350)

    # If intermediate LLM output is too thin, fall back to structured KB answer.
    if level == "intermediate" and _is_low_quality_reply(response, min_words=22):
        return limit_response_words(_structured_kb_reply(disease, qa, "intermediate"), 260)

    # Detect follow-up (short question heuristic) and emergency intent
    is_followup = len(str(query or "").split()) <= 14
    intent_lower = str(intent or "").lower()
    is_emergency = "emergency" in intent_lower or "when to see a doctor" in intent_lower

    # Apply level-aware limits
    if is_emergency:
        response = limit_response_words(response, 150)
        if not response.strip().endswith("Please visit a doctor immediately."):
            response = response.strip() + " Please visit a doctor immediately."
    elif level == "advanced":
        response = limit_response_words(response, 300)
    elif level == "intermediate":
        response = limit_response_words(response, 220)
    elif is_followup:
        response = limit_response_words(response, 200)
    else:
        response = limit_response_words(response, 200)

    return response


_NLLB_TO_KB_LANG = {
    "kan_Knda": "kannada",
    "hin_Deva": "hindi",
    # Tamil/Telugu are wired to the same pre-written-translation shortcut as
    # Hindi/Kannada (was previously hard-coded to None, so tam/tel queries
    # ALWAYS paid the full ~10-20s model translation even on the rare disease
    # entries that do have a pre-written KB answer -- Hindi/Kannada could
    # skip straight to it). No tamil/telugu content exists in the KB yet, so
    # this is currently a no-op until that content is added, but it removes
    # the artificial exclusion.
    "tam_Taml": "tamil",
    "tel_Telu": "telugu",
}


def _prewritten_field_translation(
    english_input: str,
    english_response: str,
    target_language: str,
) -> Optional[str]:
    """Return a pre-written KB translation for the matched disease+field, or None.

    Only replaces the plain deterministic "{Disease}: {field}" response; falls
    back to the NLLB service for anything else (never crashes, never empty).
    """
    lang_key = _NLLB_TO_KB_LANG.get(target_language)
    if not lang_key:
        return None
    disease, status = resolve_disease(english_input, DISEASE_INDEX, None)
    if status != "matched" or not disease or disease not in DISEASE_KB:
        return None
    field = detect_intent(english_input)
    translations = (DISEASE_KB.get(disease) or {}).get("translations") or {}
    lang_block = translations.get(lang_key) or {}
    translated_field = lang_block.get(field)
    if not translated_field:
        return None
    prefix = f"{disease}: "
    if english_response.strip().startswith(prefix):
        return f"{disease}: {translated_field}"
    return None


def multilingual_chatbot(
    user_input: Optional[str] = None,
    session_id: str = "default",
    query: Optional[str] = None,
    predicted_disease: Optional[str] = None,
    diag: Optional[dict] = None,
    history: Optional[list] = None,
    target_language: Optional[str] = None,
    file_attached: bool = False,
    file_chunks: Optional[list] = None,
) -> str:
    """
    Multilingual wrapper around the strict English pipeline.

    Flow:
    - detect language
    - translate non-English input to English
    - call the existing ML -> KB -> Mistral pipeline
    - translate the response back to the requested language when needed
      (pre-written KB translations are used when available, otherwise NLLB)

    `target_language` (NLLB-style code) is an explicit override from the UI
    language dropdown, used when the user did not ask inline (e.g. no
    "explain it in kannada" in the message).

    When `diag` (a dict) is passed it is filled in place with retrieval/LLM
    timing and retrieved chunks for RAG interaction logging.

    `file_attached` forwards the /ai-chat file-attachment flag so the query
    is routed straight to the LLM tier chain, never through disease/KB
    routing (see process_query).
    """
    incoming_text = user_input if user_input is not None else query
    if incoming_text is None:
        return "Please specify the disease (e.g., 'symptoms of dengue')."

    cleaned_input, requested_target_language = extract_target_language(incoming_text)
    # Automatic language detection (English, Kannada, Hindi, Tamil, Telugu).
    original_language = detect_language_service(cleaned_input)
    # Precedence: inline request ("explain it in kannada") > UI dropdown > detected.
    target_language = requested_target_language or target_language or original_language

    # Per-stage pipeline timing so latency can be diagnosed from logs alone
    # (ASR and TTS stages are logged by their own services).
    _pipeline_t0 = time.perf_counter()

    # Modular routing: English input is SKIPPED (nothing to translate);
    # non-English input is translated to English via the NLLB service.
    english_input, original_language = route_translation(
        cleaned_input,
        translate_fn=translate_text,
        language=original_language,
    )
    _translate_in_ms = (time.perf_counter() - _pipeline_t0) * 1000.0

    english_response = process_query(
        english_input,
        session_id=session_id,
        predicted_disease=predicted_disease,
        diag=diag,
        history=history,
        file_attached=file_attached,
        file_chunks=file_chunks,
    )
    _kb_llm_ms = (time.perf_counter() - _pipeline_t0) * 1000.0

    translated_response = english_response
    _translate_out_ms = 0.0
    if target_language != "eng_Latn" and english_response:
        prewritten = _prewritten_field_translation(english_input, english_response, target_language)
        if prewritten:
            translated_response = prewritten
        else:
            translated_response = translate_text(english_response, "eng_Latn", target_language)
        _translate_out_ms = (time.perf_counter() - _pipeline_t0) * 1000.0 - _kb_llm_ms

    _pipeline_total_ms = (time.perf_counter() - _pipeline_t0) * 1000.0
    print(f"[Pipeline] Translate-in: {_translate_in_ms:.0f}ms | "
          f"LLM/KB: {_kb_llm_ms - _translate_in_ms:.0f}ms | "
          f"Translate-out: {_translate_out_ms:.0f}ms | "
          f"Total: {_pipeline_total_ms:.0f}ms", flush=True)

    return translated_response


def new_diag() -> dict:
    """Return a fresh, empty RAG diagnostics dict for one chat interaction."""
    return {
        "retrieval_time_ms": 0.0,
        "retrieved": [],
        "llm_time_ms": 0.0,
        "model_used": OLLAMA_MODEL,
        "model_tier": "primary",
    }


# ============================================================
# STARTUP SELF-TEST -- catches FAQ/KB fast-path regressions the moment
# they're introduced, instead of silently shipping and only surfacing as
# a Portkey-quota-burning production incident. This exact category of bug
# (FAQ/KB matching quietly stops firing, or starts firing on the wrong
# entry) has recurred multiple times across this project's history; this
# runs automatically on every backend startup, not on demand.
# ============================================================
def run_startup_selftest() -> bool:
    """Exercise a handful of known exact-match FAQ/KB questions. Prints a
    loud CRITICAL banner (impossible to miss in the startup log) if any
    fail, and returns False -- callers should treat that as "do not trust
    the fast path in this build" rather than crash the server outright, so
    a broken selftest is loud but not itself an outage.

    Deliberately does NOT check the LLM fallback tiers (Ollama/Portkey) --
    those are external services that can be legitimately down without the
    KB/FAQ code itself being broken; this only guards the deterministic,
    zero-network fast path.
    """
    test_cases = [
        ("faq", "What is Rural Healthcare?"),
        ("faq", "Is Rural Healthcare free to use?"),
        ("kb", "what is malaria"),
        ("kb", "what is heart attack"),
    ]
    failures = []
    for kind, question in test_cases:
        try:
            if kind == "faq":
                result = match_faq(question)
            else:
                result, _status = resolve_disease(question, DISEASE_INDEX, None)
        except Exception as exc:
            result = None
            print(f"[selftest] {kind} check for {question!r} raised {type(exc).__name__}: {exc}")
        if not result:
            failures.append((kind, question))

    if failures:
        print("=" * 60)
        print("CRITICAL: Startup self-test FAILED for:")
        for kind, q in failures:
            print(f"  [{kind}] {q!r}")
        print("FAQ/KB matching may be broken. Check before relying on this build.")
        print("=" * 60)
        return False

    print("[selftest] All FAQ/KB self-tests passed.")
    return True


# ============================================================
# TESTING & DEBUG
# ============================================================

if __name__ == "__main__":
    # Test normalize
    print("=== TEST: normalize ===")
    print(normalize("What is the disease about COVID"))
    print()
    
    # Test KB load
    print("=== TEST: load_kb ===")
    kb = load_kb()
    if kb:
        print(f"KB loaded: {len(kb)} diseases")
    print()
    
    # Test detect_intent
    print("=== TEST: detect_intent ===")
    print(detect_intent("What are the symptoms?"))
    print(detect_intent("Tell me about food"))
    print(detect_intent("When should I see a doctor?"))
    print()
    
    # Test main pipeline
    print("=== TEST: process_query ===")
    result = process_query("What are the symptoms of malaria?")
    print(result)
