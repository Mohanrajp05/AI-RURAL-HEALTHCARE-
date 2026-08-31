"""
Groq Cloud Whisper Speech-to-Text Service
==========================================

ASR backed by Groq's hosted whisper-large-v3 endpoint (OpenAI-compatible
`/audio/transcriptions` API), called directly via `requests` -- the same
"no new dependency, no SDK" pattern llm_router.call_groq() already uses for
chat. No local model, no torch, no ctranslate2, no memory cost: this is a
plain HTTPS call, so it needs no SKIP_LOCAL_ML gate and no lazy/preloaded
model.

(Historical note: this used to be a local faster-whisper model, lazily
loaded and cached for the process lifetime. That looked torch-free -- it's
built on ctranslate2 -- but empirically pulled in the full torch +
transformers stack too, which OOM-crashed Render's free 512MB tier. See
tts_service.py for the equivalent MMS-TTS -> browser-native swap on the
output side.)

Usage:
    from speech_service import transcribe_audio

    result = transcribe_audio(audio_bytes, filename="mic.webm", language="hi")
    # -> {"text": "...", "language": "hi", "confidence": 0.91}
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path

import requests

GROQ_API_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
GROQ_ASR_MODEL = os.environ.get("GROQ_ASR_MODEL", "whisper-large-v3")
GROQ_TIMEOUT_SECONDS = 30

MAX_AUDIO_BYTES = 25 * 1024 * 1024  # Groq free-tier request cap

# Audio formats we accept from the browser / uploads (also what Groq's
# Whisper endpoint accepts).
ALLOWED_EXTENSIONS = {
    ".wav", ".mp3", ".m4a", ".webm", ".ogg", ".oga", ".flac", ".aac", ".opus", ".mp4", ".mpeg", ".mpga",
}

# Native-script seeds for Indic languages. Whisper (local or cloud) often
# transliterates Hindi/Kannada/Tamil/Telugu audio to Latin script; seeding
# the `prompt` field with a line in the target script biases the model
# toward returning native-script text, which is what the multilingual
# pipeline needs for reliable translation routing.
_NATIVE_SCRIPT_SEEDS = {
    "hi": "यह पाठ हिंदी भाषा में लिखा गया है। बुखार सिरदर्द रोग लक्षण दर्द।",
    "kn": "ಈ ಪಠ್ಯವನ್ನು ಕನ್ನಡ ಭಾಷೆಯಲ್ಲಿ ಬರೆಯಲಾಗಿದೆ. ಜ್ವರ ತಲೆನೋವು ರೋಗ ಲಕ್ಷಣ ನೋವು.",
    "ta": "இந்த உரை தமிழ் மொழியில் எழுதப்பட்டது. காய்சல்கு வலி நோய் அறிகுறி.",
    "te": "ఈ వచనం తెలుగు భాషలో వ్రాయబడింది. జ్వరం తలనొప్పి వ్యాధి లక్షాలు నొప్పి.",
}
_NATIVE_RANGES = {
    "hi": (0x0900, 0x097F),  # Devanagari
    "kn": (0x0C80, 0x0CFF),  # Kannada
    "ta": (0x0B80, 0x0BFF),  # Tamil
    "te": (0x0C00, 0x0C7F),  # Telugu
}


# Groq's verbose_json response returns the detected language as a full
# English name (e.g. "english", "hindi"), not an ISO-639-1 code -- unlike
# the old local faster-whisper path, whose `info.language` was already a
# 2-letter code. Normalize back to ISO-639-1 so downstream code (the
# native-script seed lookup below, and the "language" field this module
# returns to callers) stays on the same contract regardless of ASR backend.
_LANGUAGE_NAME_TO_ISO = {
    "english": "en",
    "hindi": "hi",
    "kannada": "kn",
    "tamil": "ta",
    "telugu": "te",
}


def _normalize_language(value: str | None) -> str:
    key = str(value or "").strip().lower()
    if key in _LANGUAGE_NAME_TO_ISO:
        return _LANGUAGE_NAME_TO_ISO[key]
    if len(key) >= 2 and key[:2].isalpha():
        return key[:2]
    return "en"


def _contains_native_script(text: str) -> bool:
    """True when `text` contains any block of a supported Indic script."""
    return any(
        start <= ord(ch) <= end
        for (start, end) in _NATIVE_RANGES.values()
        for ch in str(text or "")
    )


def _contains_latin(text: str) -> bool:
    return bool(re.search(r"[A-Za-z]{2,}", str(text or "")))


def _validate_audio(audio_bytes: bytes, filename: str) -> None:
    if not audio_bytes:
        raise ValueError("Empty audio file received.")
    if len(audio_bytes) > MAX_AUDIO_BYTES:
        raise ValueError(
            f"Audio file too large ({len(audio_bytes) / 1024 / 1024:.1f} MB). "
            f"Maximum is {MAX_AUDIO_BYTES / 1024 / 1024:.0f} MB."
        )
    ext = Path(filename or "").suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError(
            f"Unsupported audio format '{ext or 'unknown'}'. "
            f"Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}"
        )


def _call_groq_transcribe(
    audio_bytes: bytes,
    filename: str,
    api_key: str,
    language: str | None,
    prompt: str | None,
) -> dict:
    """One HTTP call to Groq's Whisper endpoint. Returns the parsed JSON body.

    Raises RuntimeError on any transport/HTTP failure.
    """
    files = {"file": (filename or "audio.webm", audio_bytes)}
    data = {
        "model": GROQ_ASR_MODEL,
        "response_format": "verbose_json",
    }
    if language:
        data["language"] = language
    if prompt:
        data["prompt"] = prompt

    try:
        resp = requests.post(
            GROQ_API_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            files=files,
            data=data,
            timeout=GROQ_TIMEOUT_SECONDS,
        )
    except requests.exceptions.Timeout as exc:
        raise RuntimeError("Groq transcription timed out. Please try again.") from exc
    except requests.exceptions.RequestException as exc:
        raise RuntimeError(f"Could not reach Groq's transcription service: {exc}") from exc

    if resp.status_code != 200:
        detail = resp.text[:300]
        raise RuntimeError(f"Groq transcription failed (HTTP {resp.status_code}): {detail}")

    try:
        return resp.json()
    except ValueError as exc:
        raise RuntimeError("Groq returned a non-JSON response.") from exc


def transcribe_audio(
    audio_bytes: bytes,
    filename: str = "audio.webm",
    language: str | None = None,
) -> dict:
    """
    Transcribe an audio upload via Groq's cloud Whisper (whisper-large-v3).

    Args:
        audio_bytes: Raw audio file bytes (wav/mp3/webm/ogg/...).
        filename: Original filename, used to validate the format.
        language: Optional ISO-639-1 code (e.g. "en", "hi", "kn", "ta", "te")
                   to force; None auto-detects. Whisper's multilingual model
                   natively supports all 5 languages this app uses.

    Returns:
        {"text": str, "language": str, "confidence": float}

    Raises:
        ValueError: on invalid/empty/unsupported audio, or no speech detected.
        RuntimeError: when GROQ_API_KEY is missing or the Groq call fails.
    """
    _validate_audio(audio_bytes, filename)

    api_key = os.environ.get("GROQ_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError(
            "Voice input isn't configured: GROQ_API_KEY is not set. "
            "Add it to the environment (see backend/.env.example)."
        )

    lang = (str(language or "").strip().lower()[:2]) or None
    if lang and not lang.isalpha():
        lang = None

    _t0 = time.perf_counter()

    def _run(use_lang, seed):
        body = _call_groq_transcribe(audio_bytes, filename, api_key, use_lang, seed)
        text = str(body.get("text") or "").strip()
        detected_lang = _normalize_language(body.get("language") or use_lang)
        segments = body.get("segments") or []
        logprobs = [
            float(seg["avg_logprob"])
            for seg in segments
            if isinstance(seg, dict) and seg.get("avg_logprob") is not None
        ]
        return text, detected_lang, logprobs

    text, detected_lang, logprobs = _run(lang, None)

    # Second pass: Whisper often romanizes Indic audio to Latin. When the
    # detected language has a native-script seed and the transcript is still
    # Latin-only, re-run with that script seeded so the multilingual
    # pipeline can route it correctly.
    if (
        not _contains_native_script(text)
        and detected_lang in _NATIVE_SCRIPT_SEEDS
        and detected_lang != "en"
    ):
        seed = _NATIVE_SCRIPT_SEEDS[detected_lang]
        text2, _detected2, logprobs2 = _run(detected_lang, seed)
        if text2 and (_contains_native_script(text2) or not _contains_latin(text2)):
            text = text2
            logprobs = logprobs2

    if not text:
        raise ValueError(
            "No speech detected in the audio. "
            "Please speak clearly and try again."
        )

    elapsed_ms = (time.perf_counter() - _t0) * 1000.0
    print(f"[ASR] Groq transcription took {elapsed_ms:.0f}ms "
          f"(lang={detected_lang}, chars={len(text)})")
    print(f"[Pipeline] ASR: {elapsed_ms:.0f}ms | Total: {elapsed_ms:.0f}ms")

    # Confidence: normalize the mean segment log-probability to 0..1 (same
    # formula the old local-Whisper path used); Groq doesn't return an
    # overall language_probability, so default to a fixed high value when no
    # per-segment logprobs come back.
    if logprobs:
        import math
        mean_lp = sum(logprobs) / len(logprobs)
        confidence = min(1.0, max(0.0, math.exp(mean_lp)))
    else:
        confidence = 0.9
    confidence = round(confidence, 4)

    return {
        "text": text,
        "language": detected_lang,
        "confidence": confidence,
    }


def asr_status() -> dict:
    """Return a lightweight availability report (used for diagnostics)."""
    configured = bool(os.environ.get("GROQ_API_KEY", "").strip())
    return {
        "loaded": configured,  # no local model to "load" -- true once the key is set
        "model": GROQ_ASR_MODEL,
        "backend": "groq-cloud",
        "error": None if configured else "GROQ_API_KEY is not set",
    }
