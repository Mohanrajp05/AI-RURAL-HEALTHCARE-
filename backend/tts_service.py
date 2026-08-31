"""Text-to-Speech service backed by Google Translate's TTS endpoint (gTTS).

Cloud-based, zero local memory: gTTS makes a plain HTTPS call per request and
returns MP3 bytes -- no local model, no torch, no transformers, so it needs
no SKIP_LOCAL_ML gate.

This is the automatic BACKEND FALLBACK for speakText() in
client/pages/AIAssistant.tsx: the frontend tries the browser's own built-in
Web Speech API (speechSynthesis) first -- free, instant, zero backend call --
and only calls this endpoint when the browser has no installed voice for the
selected language. In practice that's most often Kannada/Tamil/Telugu on
Windows Chrome/Edge, which ship English (and often Hindi) voices but not
those three; English/Hindi replies usually never reach this module at all.

(Historical note: this used to be Meta's MMS-TTS/VITS, one ~145MB local
checkpoint per language loaded via transformers+torch -- see speech_
service.py for the equivalent local-Whisper -> Groq-cloud swap on the ASR
side. That combined footprint is what OOM-crashed Render's free 512MB tier.)
"""
import io
import time
from typing import Optional, Tuple

from gtts import gTTS

SUPPORTED_LANGUAGES = {
    "en": "English",
    "kn": "Kannada",
    "hi": "Hindi",
    "ta": "Tamil",
    "te": "Telugu",
}

# Accept both ISO-639-1 codes and NLLB-style codes (matches the reply-language
# dropdown and translation_service.py's language handling).
LANGUAGE_ALIASES = {
    "en": "en", "eng": "en", "eng_Latn": "en", "english": "en",
    "kn": "kn", "kan": "kn", "kan_Knda": "kn", "kannada": "kn",
    "hi": "hi", "hin": "hi", "hin_Deva": "hi", "hindi": "hi",
    "ta": "ta", "tam": "ta", "tam_Taml": "ta", "tamil": "ta",
    "te": "te", "tel": "te", "tel_Telu": "te", "telugu": "te",
}

MAX_TTS_CHARS = 1000


def normalize_language(language: Optional[str]) -> str:
    """Map any accepted language label to its ISO-639-1 code (default: en)."""
    if language:
        key = str(language).strip().lower()
        if key in LANGUAGE_ALIASES:
            return LANGUAGE_ALIASES[key]
    return "en"


def supported_languages() -> dict:
    return dict(SUPPORTED_LANGUAGES)


def preload() -> None:
    """No-op: gTTS has no local model/weights to warm up -- kept only so
    app.py's startup sequence (which calls this like every other service's
    preload()) doesn't need a special case."""
    return


def synthesize(text: str, language: Optional[str] = None, max_new_tokens: int = 1024) -> Tuple[bytes, Optional[int]]:
    """Synthesize speech for `text` via gTTS and return (mp3_bytes, None).

    The second tuple element (sample rate) is kept for call-site compatibility
    with the previous MMS-TTS engine but is meaningless for an MP3 response --
    always None.

    `max_new_tokens` is accepted for interface compatibility with the
    previous (local, token-budgeted) engine but unused here.
    """
    text = str(text or "").strip()
    if not text:
        raise ValueError("text is required")
    if len(text) > MAX_TTS_CHARS:
        text = text[:MAX_TTS_CHARS]

    norm_lang = normalize_language(language)
    if norm_lang not in SUPPORTED_LANGUAGES:
        raise ValueError(f"unsupported language: {language}")

    # gTTS internally splits long text into <=100-char requests to Google's
    # endpoint and concatenates the MP3 frames -- no manual chunking needed
    # here (unlike the old VITS engine, which had no such built-in handling).
    try:
        t0 = time.time()
        buf = io.BytesIO()
        gTTS(text=text, lang=norm_lang).write_to_fp(buf)
        tts_ms = (time.time() - t0) * 1000.0
        print(f"[tts] gTTS synthesized {len(text)} chars in {tts_ms:.0f}ms "
              f"({SUPPORTED_LANGUAGES[norm_lang]})")
        print(f"[Pipeline] TTS: {tts_ms:.0f}ms | Total: {tts_ms:.0f}ms", flush=True)
        return buf.getvalue(), None
    except Exception as exc:
        raise RuntimeError(f"gTTS synthesis failed: {exc}") from exc
