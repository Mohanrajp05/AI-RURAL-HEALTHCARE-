"""
Faster-Whisper Speech-to-Text Service
=====================================

Dedicated, lazily-loaded speech-to-text service backed by faster-whisper
(local, offline, no API key). The WhisperModel is loaded ONCE on the first
request and cached for the process lifetime, mirroring the lazy-load
pattern tts_service.py uses for the Indic-Mio TTS model so backend startup
stays fast. (Historical note: an earlier version of this project used
Kokoro for TTS; the live engine today is Indic-Mio -- see tts_service.py.)

Usage:
    from speech_service import transcribe_audio

    result = transcribe_audio(audio_bytes, filename="mic.webm")
    # -> {"text": "...", "language": "en", "confidence": 0.91}
"""

from __future__ import annotations

import io
import math
import os
import re
import tempfile
import threading
import time
from pathlib import Path

# Optional dependency detection: silero VAD needs onnxruntime.
try:
    import onnxruntime  # noqa: F401
    _VAD_AVAILABLE = True
except Exception:
    _VAD_AVAILABLE = False

_MODEL = None
_MODEL_LOCK = None
_MODEL_ERROR = None
_MODEL_SIZE = os.environ.get("ASR_MODEL_SIZE", "base")
_DEVICE = os.environ.get("ASR_DEVICE", "cpu")
_COMPUTE_TYPE = os.environ.get("ASR_COMPUTE_TYPE", "int8")
_USE_VAD = os.environ.get("ASR_VAD", "1") == "1" and _VAD_AVAILABLE

MAX_AUDIO_BYTES = 25 * 1024 * 1024  # 25 MB request cap

# Audio formats we accept from the browser / uploads.
ALLOWED_EXTENSIONS = {
    ".wav", ".mp3", ".m4a", ".webm", ".ogg", ".oga", ".flac", ".aac", ".opus",
}

# Native-script seeds for Indic languages. Faster-Whisper often transliterates
# Hindi/Kannada/Tamil/Telugu audio to Latin script; seeding the prompt with a
# line in the target script makes it return native-script text, which is what
# the multilingual pipeline needs for reliable translation routing.
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


def _contains_native_script(text: str) -> bool:
    """True when `text` contains any block of a supported Indic script."""
    return any(
        start <= ord(ch) <= end
        for (start, end) in _NATIVE_RANGES.values()
        for ch in str(text or "")
    )


def _contains_latin(text: str) -> bool:
    return bool(re.search(r"[A-Za-z]{2,}", str(text or "")))


def _get_model():
    """Lazy-load the WhisperModel once and cache it (thread-safe)."""
    global _MODEL, _MODEL_LOCK, _MODEL_ERROR
    if _MODEL_LOCK is None:
        _MODEL_LOCK = threading.Lock()
    if _MODEL is not None:
        return _MODEL
    if _MODEL_ERROR is not None:
        raise RuntimeError(_MODEL_ERROR)
    with _MODEL_LOCK:
        if _MODEL is not None:
            return _MODEL
        if _MODEL_ERROR is not None:
            raise RuntimeError(_MODEL_ERROR)
        try:
            from faster_whisper import WhisperModel
            print(f"[ASR] Loading Faster-Whisper model '{_MODEL_SIZE}' "
                  f"(device={_DEVICE}, compute={_COMPUTE_TYPE}, vad={_USE_VAD})...")
            _MODEL = WhisperModel(
                _MODEL_SIZE,
                device=_DEVICE,
                compute_type=_COMPUTE_TYPE,
            )
            print("[ASR] Whisper model loaded once at startup (cached for process lifetime)")
        except Exception as exc:
            _MODEL_ERROR = (
                f"Faster-Whisper model failed to load: {exc}. "
                "Install with: pip install faster-whisper"
            )
            print(f"[ASR] FAILED {_MODEL_ERROR}")
            raise RuntimeError(_MODEL_ERROR)
    return _MODEL


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


def _write_temp_audio(audio_bytes: bytes, filename: str) -> str:
    """Persist the upload to a temp file so PyAV can sniff the format."""
    ext = Path(filename or "audio.webm").suffix.lower() or ".wav"
    with tempfile.NamedTemporaryFile(
        suffix=ext, prefix="asr_", delete=False
    ) as tmp:
        tmp.write(audio_bytes)
        return tmp.name


def transcribe_audio(
    audio_bytes: bytes,
    filename: str = "audio.webm",
    language: str | None = None,
) -> dict:
    """
    Transcribe an audio upload with Faster-Whisper.

    Args:
        audio_bytes: Raw audio file bytes (wav/mp3/webm/ogg/...).
        filename: Original filename, used to validate the format.
        language: Optional ISO-639-1 code (e.g. "en", "hi", "kn") to force;
                  None auto-detects.

    Returns:
        {"text": str, "language": str, "confidence": float}

    Raises:
        ValueError: on invalid/empty/unsupported audio.
        RuntimeError: when the Whisper model cannot be loaded/run.
    """
    _validate_audio(audio_bytes, filename)
    model = _get_model()

    tmp_path = None
    _t0 = time.perf_counter()
    try:
        tmp_path = _write_temp_audio(audio_bytes, filename)
        lang = (str(language or "").strip().lower()[:2]) or None
        if lang and not lang.isalpha():
            lang = None

        def _run(use_lang: str | None, seed: str | None):
            return model.transcribe(
                tmp_path,
                language=use_lang,
                beam_size=1,
                vad_filter=_USE_VAD,
                vad_parameters=(
                    {"min_silence_duration_ms": 500} if _USE_VAD else None
                ),
                initial_prompt=seed,
            )

        try:
            segments_iter, info = _run(lang, None)

            pieces: list[str] = []
            logprobs: list[float] = []
            for segment in segments_iter:
                pieces.append(segment.text or "")
                if segment.avg_logprob is not None:
                    logprobs.append(float(segment.avg_logprob))
        except Exception as exc:
            # faster-whisper decodes the upload via PyAV/FFmpeg internally --
            # an empty, truncated, or header-only recording (e.g. the mic
            # was stopped almost immediately, or the browser produced a
            # WebM blob with no actual audio frames) surfaces here as a raw
            # decoder error, e.g. "[Errno 541478725] End of file: '...webm'"
            # (541478725 is FFmpeg's AVERROR_EOF, packed as ASCII "EOF ").
            # That string used to leak straight to the frontend as a 500.
            # Re-raise as the same ValueError the "no speech" path below
            # already uses, so the UI shows one clear, actionable message
            # instead of an internal decoder error -- the real exception is
            # still logged server-side for diagnosis.
            print(f"[ASR] audio decode failed for '{filename}': {type(exc).__name__}: {exc}")
            raise ValueError(
                "Couldn't read the recording -- it may have been too short or empty. "
                "Please hold the mic button, speak for a moment, then stop."
            ) from exc

        text = "".join(pieces).strip()
        detected_lang = str(getattr(info, "language", "") or "en")

        # Second pass: faster-whisper often romanizes Indic audio to Latin. When
        # the detected language has a native-script seed and the transcript is
        # still Latin-only, re-run the model with that script seeded so the
        # multilingual pipeline can route it correctly.
        if (
            not _contains_native_script(text)
            and detected_lang in _NATIVE_SCRIPT_SEEDS
            and detected_lang != "en"
        ):
            seed = _NATIVE_SCRIPT_SEEDS[detected_lang]
            segments_iter2, _info2 = _run(detected_lang, seed)
            pieces2: list[str] = []
            logprobs2: list[float] = []
            for segment in segments_iter2:
                pieces2.append(segment.text or "")
                if segment.avg_logprob is not None:
                    logprobs2.append(float(segment.avg_logprob))
            text2 = "".join(pieces2).strip()
            if text2 and (_contains_native_script(text2) or not _contains_latin(text2)):
                text = text2
                logprobs = logprobs2

        if not text:
            raise ValueError(
                "No speech detected in the audio. "
                "Please speak clearly and try again."
            )

        elapsed_ms = (time.perf_counter() - _t0) * 1000.0
        print(f"[ASR] Transcription took {elapsed_ms:.0f}ms "
              f"(lang={detected_lang}, chars={len(text)})")
        print(f"[Pipeline] ASR: {elapsed_ms:.0f}ms | Total: {elapsed_ms:.0f}ms")

        # Confidence: normalize the mean token log-probability to 0..1.
        if logprobs:
            mean_lp = sum(logprobs) / len(logprobs)
            confidence = min(1.0, max(0.0, math.exp(mean_lp)))
        else:
            confidence = float(info.language_probability or 0.0)
        confidence = round(confidence, 4)

        return {
            "text": text,
            "language": detected_lang,
            "confidence": confidence,
        }
    finally:
        if tmp_path:
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def asr_status() -> dict:
    """Return a lightweight availability report (used for diagnostics)."""
    if _MODEL is None and _MODEL_ERROR is None:
        return {"loaded": False, "model": _MODEL_SIZE, "vad": _USE_VAD}
    return {
        "loaded": _MODEL is not None,
        "model": _MODEL_SIZE,
        "vad": _USE_VAD,
        "error": _MODEL_ERROR,
    }
