"""Ollama-based healthcare chatbot response generator with NLLB-only translation."""

from __future__ import annotations

import re
from functools import lru_cache

print("[cr-import] a. importing ollama", flush=True)
import ollama
print("[cr-import] b. importing torch", flush=True)
import torch
print("[cr-import] c. importing transformers (AutoModelForSeq2SeqLM/AutoTokenizer)", flush=True)
from transformers import AutoModelForSeq2SeqLM, AutoTokenizer
print("[cr-import] d. transformers imported", flush=True)

print("[cr-import] e. importing portkey_llm", flush=True)
import portkey_llm
print("[cr-import] f. portkey_llm imported", flush=True)

OLLAMA_MODEL = "cniongolo/biomistral:latest"
NLLB_MODEL_NAME = "facebook/nllb-200-distilled-600M"

_translation_tokenizer = None
_translation_model = None
last_response = ""

# Session Management
_SESSION_LAST_DISEASE = {}
_SESSION_LAST_RESPONSE = {}
_SESSION_HISTORY = {}

def _session_key(session_id: str | None) -> str:
    """Generate a session key from session_id."""
    return str(session_id or "default")

def _set_session_disease(session_id: str | None, disease: str) -> None:
    """Store disease in session."""
    key = _session_key(session_id)
    if key not in _SESSION_LAST_DISEASE:
        _SESSION_LAST_DISEASE[key] = {}
    _SESSION_LAST_DISEASE[key]["disease"] = disease

def _get_session_disease(session_id: str | None) -> str:
    """Retrieve disease from session."""
    key = _session_key(session_id)
    return _SESSION_LAST_DISEASE.get(key, {}).get("disease", "")

def clear_chat_session(session_id: str | None = None) -> None:
    """Clear session data."""
    key = _session_key(session_id)
    _SESSION_LAST_DISEASE.pop(key, None)
    _SESSION_LAST_RESPONSE.pop(key, None)
    _SESSION_HISTORY.pop(key, None)

def update_disease_context(session_id: str | None, new_disease: str) -> None:
    """Update disease context and clear history if disease changes."""
    key = _session_key(session_id)
    old_disease = _SESSION_LAST_DISEASE.get(key, {}).get("disease", "")
    
    if old_disease and old_disease.lower() != new_disease.lower():
        _SESSION_HISTORY[key] = []
        _SESSION_LAST_RESPONSE[key] = ""
    
    _SESSION_LAST_DISEASE[key] = {
        "disease": new_disease,
        "timestamp": None
    }


def limit_response_words(text: str, max_words: int = 300) -> str:
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


def _detect_input_language(text: str) -> str:
    raw = str(text or "")
    if re.search(r"[\u0C80-\u0CFF]", raw):
        return "kan_Knda"
    if re.search(r"[\u0900-\u097F]", raw):
        return "hin_Deva"
    if re.search(r"[\u0B80-\u0BFF]", raw):
        return "tam_Taml"
    if re.search(r"[\u0C00-\u0C7F]", raw):
        return "tel_Telu"
    return "eng_Latn"


def is_translation_query(query: str) -> bool:
    return "translate" in str(query or "").lower()


def get_target_language(query: str) -> str:
    q = str(query or "").lower()
    if "kannada" in q or "ಕನ್ನಡ" in q:
        return "kan_Knda"
    if "hindi" in q or "हिंदी" in q or "हिन्दी" in q:
        return "hin_Deva"
    if "tamil" in q or "தமிழ்" in q:
        return "tam_Taml"
    if "telugu" in q or "తెలుగు" in q:
        return "tel_Telu"
    return "eng_Latn"


@lru_cache(maxsize=1)
def load_translation_model():
    global _translation_tokenizer, _translation_model

    if _translation_tokenizer is not None and _translation_model is not None:
        return _translation_tokenizer, _translation_model

    tokenizer = AutoTokenizer.from_pretrained(NLLB_MODEL_NAME, local_files_only=True)
    model = AutoModelForSeq2SeqLM.from_pretrained(NLLB_MODEL_NAME, local_files_only=True)
    model.eval()

    _translation_tokenizer = tokenizer
    _translation_model = model
    return tokenizer, model


def translate_text(text: str, src_lang: str, tgt_lang: str) -> str:
    """Translate text using IndicTrans2 with NLLB fallback."""
    from translation_service import translate as _it2_translate
    return _it2_translate(text, src_lang, tgt_lang)


def enforce_language(text: str, lang: str) -> str:
    """Remove non-native script characters to prevent mixed-language output.

    Covers all four supported Indic scripts (previously only Kannada/Hindi
    were stripped here; Tamil/Telugu fell through unfiltered).
    """
    if lang == "kan_Knda":
        return "".join(re.findall(r'[\u0C80-\u0CFF\s.,!?]', str(text or "")))
    if lang == "hin_Deva":
        return "".join(re.findall(r'[\u0900-\u097F\s.,!?]', str(text or "")))
    if lang == "tam_Taml":
        return "".join(re.findall(r'[\u0B80-\u0BFF\s.,!?]', str(text or "")))
    if lang == "tel_Telu":
        return "".join(re.findall(r'[\u0C00-\u0C7F\s.,!?]', str(text or "")))
    return str(text or "")


def _finalize_response_text(english_response: str, target_language: str) -> str:
    base_response = str(english_response or "").strip()
    if not base_response:
        return base_response

    if target_language == "eng_Latn":
        return limit_response_words(base_response, 300)

    translated = translate_text(base_response, "eng_Latn", target_language)
    cleaned = enforce_language(translated, target_language).strip()
    final = cleaned or translated.strip() or base_response
    return limit_response_words(final, 300)


def chatbot_response(user_query: str) -> str:
    if not user_query or not user_query.strip():
        return "Please ask me a question about your health concerns."

    global last_response

    source_language = _detect_input_language(user_query)
    target_language = get_target_language(user_query)
    if target_language == "eng_Latn" and source_language != "eng_Latn":
        target_language = source_language

    english_query = user_query.strip()
    if source_language != "eng_Latn":
        english_query = translate_text(english_query, source_language, "eng_Latn").strip() or english_query

    if is_translation_query(user_query):
        if not last_response.strip():
            return _finalize_response_text("No previous response available to translate.", target_language)
        return _finalize_response_text(last_response, target_language)

    system_prompt = """You are a healthcare assistant.

Answer the user's question clearly and simply.

Rules:

* Keep answer SHORT (2-4 lines only)
* Answer ONLY what is asked
* Do NOT add extra sections like prevention, food, exercise unless asked
* Do NOT over-explain
* Use simple English only
* Never translate into another language
* Answer in maximum 3 sentences only. Keep response strictly under 300 words. Be direct and concise.

User question: {user_query}"""

    formatted_prompt = system_prompt.format(user_query=english_query)

    try:
        answer, _source = portkey_llm.chat(
            [{"role": "user", "content": formatted_prompt}],
            ollama_model=OLLAMA_MODEL,
            temperature=0.3,
            num_predict=80,
        )
        answer = (answer or "").strip()
        if not answer:
            return _finalize_response_text("I couldn't generate a response. Please try asking again.", target_language)

        # Enforce Mistral word limit (300 words)
        answer = limit_response_words(answer, 300)
        last_response = answer
        return _finalize_response_text(answer, target_language)

    except Exception as exc:
        error_msg = str(exc).lower()

        if "connection refused" in error_msg or "failed to connect" in error_msg:
            return _finalize_response_text(
                "Healthcare assistant is temporarily unavailable. Please ensure Ollama is running on localhost:11434.",
                target_language,
            )

        if "model not found" in error_msg or "biomistral" in error_msg:
            return _finalize_response_text(
                "The biomistral model is not loaded. Please run: ollama pull cniongolo/biomistral:latest",
                target_language,
            )

        return _finalize_response_text(
            "I encountered an error processing your question. Please try again.",
            target_language,
        )


__all__ = [
    "chatbot_response",
    "enforce_language",
    "get_target_language",
    "is_translation_query",
    "last_response",
    "load_translation_model",
    "translate_text",
]
