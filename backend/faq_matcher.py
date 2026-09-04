"""
FAQ Matcher
===========

Zero-cost, zero-latency answer source for questions about the app itself
(what it does, how to use it, what diseases it covers, how each feature
works) -- matched against the 59 curated Q&A pairs in
knowledge_base/faq_index.json (built by build_faq_index.py) with no LLM
call at all.

Checked in chatbot_pipeline.process_query() before disease detection and
before any LLM tier, so a hit here never touches Ollama/Portkey.
"""

import difflib
import json
import os
import re

print("[faq_matcher-import] importing sklearn (TfidfVectorizer/cosine_similarity)", flush=True)
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
print("[faq_matcher-import] sklearn imported", flush=True)

# Render's free tier (512MB RAM) gets OOM-killed once torch + transformers
# + sklearn are all resident. The TF-IDF/difflib matching below (this
# file's actual job) is pure sklearn and unaffected either way -- only the
# OPTIONAL semantic-similarity guard (_get_semantic_model() below) pulls in
# sentence-transformers -> torch, purely to refine/double-check a TF-IDF
# hit. It already degrades gracefully to "TF-IDF/difflib only" on any
# failure, so SKIP_LOCAL_ML=true (set in Render's env, NOT local .env)
# simply skips ever attempting it.
SKIP_LOCAL_ML = os.environ.get("SKIP_LOCAL_ML", "false").strip().lower() == "true"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FAQ_INDEX_PATH = os.path.join(BASE_DIR, "knowledge_base", "faq_index.json")


FUZZY_CUTOFF = 0.72

# difflib's ratio is a character-sequence match with no notion of word
# meaning, so a short "what is X" query shares its entire "what is "
# prefix with every other "what is Y" FAQ question -- that alone inflates
# the ratio to ~0.70-0.78 against a COMPLETELY unrelated Y (empirically:
# "what is migraine" -> "what is creatinine" at 0.706; "what is stroke"
# -> "what is cholesterol" at 0.727), both comfortably clearing the
# general FUZZY_CUTOFF=0.72 and returning a confidently wrong answer.
# A genuine typo of a term that IS in the FAQ scores far higher --
# "what iz cholesterol"/"what is cholestrol" -> "what is cholesterol" at
# 0.947/0.973 -- so short queries get a much stricter cutoff sitting in
# the wide gap between those two bands (measured false-positive ceiling
# 0.783, genuine-match floor 0.947), without touching TF-IDF at all
# (TF-IDF already correctly scores both "migraine" and "stroke" at 0.000
# -- it was never the problem; tightening ITS threshold for short queries
# would instead have broken "what is WBC", which legitimately scores
# 0.553, barely above TFIDF_CUTOFF).
SHORT_QUERY_MAX_WORDS = 3
SHORT_QUERY_FUZZY_CUTOFF = 0.85


TFIDF_CUTOFF = 0.55

SEMANTIC_GUARD_CUTOFF = 0.40


def _normalize(text: str) -> str:
    text = str(text or "").lower().strip()
    text = re.sub(r"[^\w\s]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _load_faq_entries() -> list:
    if not os.path.exists(FAQ_INDEX_PATH):
        print(f"[faq_matcher] WARNING: {FAQ_INDEX_PATH} not found -- run "
              f"build_faq_index.py. FAQ fast path will be unavailable.")
        return []
    try:
        with open(FAQ_INDEX_PATH, encoding="utf-8") as fh:
            entries = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[faq_matcher] WARNING: failed to load {FAQ_INDEX_PATH}: {exc}")
        return []
    return [e for e in entries if e.get("question") and e.get("answer")]


FAQ_ENTRIES = _load_faq_entries()
FAQ_LOOKUP = {_normalize(e["question"]): e["answer"] for e in FAQ_ENTRIES}
FAQ_QUESTIONS_NORMALIZED = list(FAQ_LOOKUP.keys())

# TF-IDF index over the FAQ questions, built once at import time. Word-based
# cosine similarity tolerates reordered / differently-worded phrasing that
# character-sequence matching (difflib) cannot see.
_faq_questions = [e["question"] for e in FAQ_ENTRIES]
_faq_vectorizer = None
_faq_vectors = None

print(f"[faq_matcher] Loaded {len(FAQ_ENTRIES)} FAQ entries")


def _ensure_tfidf():
    global _faq_vectorizer, _faq_vectors
    if _faq_vectors is None and _faq_questions:
        _faq_vectorizer = TfidfVectorizer(stop_words="english").fit(_faq_questions)
        _faq_vectors = _faq_vectorizer.transform(_faq_questions)
    return _faq_vectors

SEMANTIC_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
SEMANTIC_CUTOFF = 0.60

_semantic_model = None
_semantic_model_failed = False
_semantic_question_vectors = None


def _get_semantic_model():
    global _semantic_model, _semantic_model_failed
    if _semantic_model is not None or _semantic_model_failed:
        return _semantic_model
    if SKIP_LOCAL_ML:
        _semantic_model_failed = True
        print("[faq_matcher] SKIP_LOCAL_ML=true -- semantic guard disabled, "
              "FAQ matching uses TF-IDF/difflib only", flush=True)
        return None
    try:
        from sentence_transformers import SentenceTransformer
        _semantic_model = SentenceTransformer(SEMANTIC_MODEL_NAME)
    except Exception as exc:
        print(f"[faq_matcher] semantic fallback unavailable ({exc}); "
              f"FAQ matching will use difflib only.")
        _semantic_model_failed = True
    return _semantic_model


def _ensure_semantic_vectors():
    """Build the cached question embeddings once (lazy, thread-unsafe first
    call is guarded by the caller holding the GIL during encode)."""
    global _semantic_question_vectors
    if _semantic_question_vectors is None and _semantic_model is not None:
        import numpy as np
        _semantic_question_vectors = np.asarray(
            _semantic_model.encode(_faq_questions, normalize_embeddings=True)
        )
    return _semantic_question_vectors


def prewarm_semantic():
    """Load the semantic model + question vectors once at server startup
    (background thread), so the TF-IDF semantic guard never has to pay the
    one-time model load on a live chat request."""
    global _semantic_model_failed
    try:
        model = _get_semantic_model()
        if model is None:
            return
        _ensure_semantic_vectors()
        print("[faq_matcher] Semantic model loaded once at startup "
              f"({len(_semantic_question_vectors)} FAQ question vectors)")
    except Exception as exc:
        _semantic_model_failed = True
        print(f"[faq_matcher] Semantic prewarm failed ({exc!r}); "
              "TF-IDF matches will run without the semantic guard.")


def _semantic_guard_score(user_text: str, entry_idx: int):
    """Non-blocking semantic cosine for ONE FAQ entry.

    Returns the cosine similarity of `user_text` against the question at
    `entry_idx`, or None when the semantic model/vectors are not warm yet
    (caller must treat None as "guard unavailable" and accept the match).
    """
    if _semantic_model is None or _semantic_question_vectors is None:
        return None
    import numpy as np
    query_vec = np.asarray(_semantic_model.encode([user_text], normalize_embeddings=True)[0])
    sims = _semantic_question_vectors @ query_vec
    return float(sims[entry_idx])


def _semantic_match(user_text: str, cutoff: float = SEMANTIC_CUTOFF):
    if not FAQ_ENTRIES:
        return None
    model = _get_semantic_model()
    if not model:
        return None
    import numpy as np

    if _ensure_semantic_vectors() is None:
        return None
    query_vec = np.asarray(model.encode([user_text], normalize_embeddings=True)[0])
    sims = _semantic_question_vectors @ query_vec
    best_idx = int(np.argmax(sims))
    if float(sims[best_idx]) >= cutoff:
        return FAQ_ENTRIES[best_idx]["answer"]
    return None


def _tfidf_match(user_text: str):
    """Primary fuzzy matcher: TF-IDF + cosine similarity. Handles
    paraphrasing and reordered phrasing far better than character-sequence
    matching (all 3 real failing transcripts score 0.552-0.841 here)."""
    vectors = _ensure_tfidf()
    if vectors is None:
        return None
    user_vector = _faq_vectorizer.transform([user_text])
    scores = cosine_similarity(user_vector, vectors)[0]
    best_idx = int(scores.argmax())
    best_score = float(scores[best_idx])
    if best_score < TFIDF_CUTOFF:
        return None
    guard = _semantic_guard_score(user_text, best_idx)
    if guard is not None and guard < SEMANTIC_GUARD_CUTOFF:
        return None
    print(f"[faq_matcher] TF-IDF match {best_score:.3f} (sem {guard if guard is None else round(guard, 3)}) "
          f"-> {FAQ_ENTRIES[best_idx]['question']!r}")
    return FAQ_ENTRIES[best_idx]["answer"]


def match_faq(user_text: str, cutoff: float = FUZZY_CUTOFF):
    """Return the best-matching FAQ answer for `user_text`, or None.

    Tries, in order:
    1. Exact normalized match (zero cost).
    2. TF-IDF + cosine similarity (primary fuzzy matcher -- handles
       paraphrasing/reordering; guarded by a semantic-embedding check so
       medical/off-topic queries can't hijack an FAQ answer).
    3. difflib character-sequence match (secondary fallback -- catches
       near-identical typo'd phrasing TF-IDF's word-based view misses).
    4. Semantic embedding fallback (last resort for zero shared vocabulary).
    """
    norm = _normalize(user_text)
    if not norm:
        return None

    exact = FAQ_LOOKUP.get(norm)
    if exact:
        return exact

    tfidf_hit = _tfidf_match(user_text)
    if tfidf_hit:
        return tfidf_hit


    word_count = len(norm.split())
    effective_cutoff = SHORT_QUERY_FUZZY_CUTOFF if word_count <= SHORT_QUERY_MAX_WORDS else cutoff

    close = difflib.get_close_matches(norm, FAQ_QUESTIONS_NORMALIZED, n=1, cutoff=effective_cutoff)
    if close:
        idx = FAQ_QUESTIONS_NORMALIZED.index(close[0])
        guard = _semantic_guard_score(user_text, idx)
        if guard is not None and guard < SEMANTIC_GUARD_CUTOFF:
            print(f"[faq_matcher] difflib match REJECTED by semantic guard (sem={guard:.3f}) "
                  f"-> {FAQ_ENTRIES[idx]['question']!r} for query {user_text[:40]!r}", flush=True)
        else:
            ratio = difflib.SequenceMatcher(None, norm, close[0]).ratio()
            print(f"[faq_matcher] difflib match ratio={ratio:.3f} (cutoff={effective_cutoff}, "
                  f"words={word_count}) -> {FAQ_ENTRIES[idx]['question']!r} for query {user_text[:40]!r}", flush=True)
            return FAQ_LOOKUP[close[0]]
    elif word_count <= SHORT_QUERY_MAX_WORDS:
        # Visibility for the exact case this fix targets: show what the
        # OLD general cutoff would have matched, so a future threshold
        # tune isn't guesswork.
        loose = difflib.get_close_matches(norm, FAQ_QUESTIONS_NORMALIZED, n=1, cutoff=cutoff)
        if loose:
            ratio = difflib.SequenceMatcher(None, norm, loose[0]).ratio()
            print(f"[faq_matcher] short-query guard blocked ratio={ratio:.3f} "
                  f"(< strict cutoff {SHORT_QUERY_FUZZY_CUTOFF}) -> "
                  f"{FAQ_LOOKUP[loose[0]]!r:.60}... for query {user_text[:40]!r}", flush=True)

    return _semantic_match(user_text)
