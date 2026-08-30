"""
Automatic Language Detection & Translation Router
=================================================

Modular language service supporting **English**, **Kannada**, **Hindi**,
**Tamil** and **Telugu**.

- ``detect_language(text)`` -> NLLB-style language code.
- ``translate_if_needed(text, translate_fn)`` -> (text, language):
    * English input is returned UNTOUCHED (translation skipped).
    * Non-English input is routed to the injected ``translate_fn``.

The translation function is injected as a callable
``translate_fn(text, src_lang, tgt_lang) -> str`` so this module stays fully
decoupled from the NLLB implementation and can be unit-tested in isolation.

Usage::

    from language_service import detect_language, translate_if_needed

    lang = detect_language("எனக்கு தலைவலி")              # -> tam_Taml
    out, lang = translate_if_needed(text, translate_fn=translate_text)
"""

from __future__ import annotations

from typing import Callable, Optional, Tuple

# NLLB-200 style language codes this service understands.
SUPPORTED_LANGUAGES: dict[str, str] = {
    "eng_Latn": "English",
    "kan_Knda": "Kannada",
    "hin_Deva": "Hindi",
    "tam_Taml": "Tamil",
    "tel_Telu": "Telugu",
}

# Unicode script blocks used for strongest-signal detection.
_SCRIPT_BLOCKS: dict[str, Tuple[int, int]] = {
    "hin_Deva": (0x0900, 0x097F),  # Devanagari (Hindi)
    "tam_Taml": (0x0B80, 0x0BFF),  # Tamil
    "tel_Telu": (0x0C00, 0x0C7F),  # Telugu
    "kan_Knda": (0x0C80, 0x0CFF),  # Kannada
}

# English keywords that indicate a request specifically in another language.
_LANGUAGE_KEYWORDS: dict[str, Tuple[str, ...]] = {
    "kan_Knda": (
        "kannada", "in kannada", "into kannada",
        "kannada alli", "kannada dalli", "kannadadalli",
    ),
    "hin_Deva": (
        "hindi", "in hindi", "into hindi",
        "hindi me", "hindi mein", "hindi ma",
    ),
    "tam_Taml": (
        "tamil", "in tamil", "into tamil", "tamil la", "tamil le",
        "tamilil", "tamizh", "thamil",
    ),
    "tel_Telu": (
        "telugu", "in telugu", "into telugu", "telugu lo", "telugu loo",
        "telugulo", "teluguloni",
    ),
}


def detect_language(text: str) -> str:
    """Detect the input language among the supported set.

    Returns an NLLB-style language code:
    ``eng_Latn``, ``kan_Knda``, ``hin_Deva``, ``tam_Taml`` or ``tel_Telu``.

    Detection order:
      1. Unicode script blocks (strongest, most reliable signal).
      2. English keywords naming the target language ("in tamil", ...).
      3. Defaults to English.
    """
    raw = str(text or "")
    if not raw:
        return "eng_Latn"

    # 1. Script-based detection (native-script input).
    for code, (start, end) in _SCRIPT_BLOCKS.items():
        if any(start <= ord(ch) <= end for ch in raw):
            return code

    # 2. Keyword-based detection (Latin-script requests written in English).
    lowered = raw.lower()
    for code, keywords in _LANGUAGE_KEYWORDS.items():
        if any(kw in lowered for kw in keywords):
            return code

    # 3. Default to English.
    return "eng_Latn"


def is_english(text: str) -> bool:
    """True when the input is (detected as) English."""
    return detect_language(text) == "eng_Latn"


TranslateFn = Callable[[str, str, str], str]


def translate_if_needed(
    text: str,
    translate_fn: Optional[TranslateFn] = None,
    language: Optional[str] = None,
) -> Tuple[str, str]:
    """
    Route text to the translation service ONLY when it is not English.

    Args:
        text: Input text to classify / translate.
        translate_fn: Callable ``(text, src_lang, tgt_lang) -> translated``.
        language: Optional pre-detected language code; when omitted it is
                  detected automatically with :func:`detect_language`.

    Returns:
        ``(text_or_translation, detected_language)``.
        - English input is returned UNTOUCHED (translation skipped).
        - Non-English input is translated to English via ``translate_fn``
          (or returned unchanged when ``translate_fn`` is ``None``).
    """
    text = str(text or "")
    if not text:
        return "", language or detect_language(text)

    lang = language or detect_language(text)

    if lang == "eng_Latn":
        return text, lang

    if translate_fn is None:
        return text, lang

    translated = translate_fn(text, lang, "eng_Latn")
    return translated or text, lang


def display_name(lang_code: str) -> str:
    """Human-readable language name for a code (falls back to the code)."""
    return SUPPORTED_LANGUAGES.get(lang_code, lang_code)