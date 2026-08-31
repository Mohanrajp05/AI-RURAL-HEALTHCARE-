"""Text-to-Speech service backed by Meta's MMS-TTS (VITS).

Receives (text, language) and returns a generated WAV audio file (bytes).
Languages: English, Kannada, Hindi, Tamil, Telugu -- one dedicated
single-language checkpoint per language (~145MB each), selected by the
`language` argument (unlike the previous Indic-Mio engine, MMS-TTS does not
auto-detect language from text, so an accurate `language` matters here).

Replaces the earlier Indic-Mio (SPRINGLab/Indic-Mio, Qwen3-based) engine:
Indic-Mio is autoregressive -- it generates ~150-260 speech codec tokens one
at a time before decoding to audio, which is what made it take 35-85s per
sentence on this CPU-only machine (measured). MMS-TTS/VITS is a single
forward-pass (non-autoregressive) model, measured at ~1.6-2.8s per sentence
on the same machine -- a ~20-30x latency win, with a smaller combined
download too (~725MB for all 5 languages vs. Indic-Mio+codec's ~1.76GB).
"""
import io
import os
import re
import threading
import time
from typing import Optional, Tuple

# Render's free tier (512MB RAM) gets OOM-killed once torch + transformers
# + sklearn are all resident. Ollama isn't used in production, so
# SKIP_LOCAL_ML=true (set in Render's env, NOT local .env) disables local
# MMS-TTS entirely -- see synthesize() below, which raises a clear error
# before ever touching torch/transformers, instead of loading a ~145MB-per-
# language model on first request.
SKIP_LOCAL_ML = os.environ.get("SKIP_LOCAL_ML", "false").strip().lower() == "true"

if not SKIP_LOCAL_ML:
    import torch

    _DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
else:
    _DEVICE = "cpu"  # never read -- synthesize() raises before reaching it

SUPPORTED_LANGUAGES = {
    "en": "English",
    "kn": "Kannada",
    "hi": "Hindi",
    "ta": "Tamil",
    "te": "Telugu",
}

# facebook/mms-tts-<code> per language -- Meta's Massively Multilingual
# Speech TTS models (VITS), one dedicated checkpoint per language.
MMS_MODEL_REPO = {
    "en": os.environ.get("MMS_TTS_EN_MODEL", "facebook/mms-tts-eng"),
    "hi": os.environ.get("MMS_TTS_HI_MODEL", "facebook/mms-tts-hin"),
    "kn": os.environ.get("MMS_TTS_KN_MODEL", "facebook/mms-tts-kan"),
    "ta": os.environ.get("MMS_TTS_TA_MODEL", "facebook/mms-tts-tam"),
    "te": os.environ.get("MMS_TTS_TE_MODEL", "facebook/mms-tts-tel"),
}

# Accept both ISO-639-1 codes and NLLB-style codes.
LANGUAGE_ALIASES = {
    "en": "en", "eng": "en", "eng_Latn": "en", "english": "en",
    "kn": "kn", "kan": "kn", "kan_Knda": "kn", "kannada": "kn",
    "hi": "hi", "hin": "hi", "hin_Deva": "hi", "hindi": "hi",
    "ta": "ta", "tam": "ta", "tam_Taml": "ta", "tamil": "ta",
    "te": "te", "tel": "te", "tel_Telu": "te", "telugu": "te",
}

_lock = threading.Lock()
_tokenizers: dict = {}
_models: dict = {}
_load_attempted: dict = {}


def normalize_language(language: Optional[str]) -> str:
    """Map any accepted language label to its ISO-639-1 code (default: en)."""
    if language:
        key = str(language).strip().lower()
        if key in LANGUAGE_ALIASES:
            return LANGUAGE_ALIASES[key]
    return "en"


def supported_languages() -> dict:
    return dict(SUPPORTED_LANGUAGES)


def _load_resources(lang: str):
    """Lazily load the tokenizer+model for one language (each is its own
    checkpoint, so languages load independently -- a failure on one doesn't
    take down the others)."""
    if _load_attempted.get(lang):
        return
    with _lock:
        if _load_attempted.get(lang):
            return
        _load_attempted[lang] = True

        from transformers import AutoTokenizer, VitsModel

        repo = MMS_MODEL_REPO[lang]
        tok = AutoTokenizer.from_pretrained(repo)
        model = VitsModel.from_pretrained(repo)
        model.eval()
        model.to(_DEVICE)

        _tokenizers[lang] = tok
        _models[lang] = model
        print(f"[tts] MMS-TTS loaded for '{lang}' ({repo}), device={_DEVICE}", flush=True)


def preload():
    """Load every language's model once at server startup (never per-request),
    mirroring the Whisper/IndicTrans2 prewarm pattern."""
    if SKIP_LOCAL_ML:
        print("[tts] SKIP_LOCAL_ML=true -- skipping MMS-TTS preload, "
              "voice output is disabled in this deployment", flush=True)
        return
    for lang in SUPPORTED_LANGUAGES:
        try:
            _load_resources(lang)
        except Exception as exc:
            print(f"[tts] Startup preload failed for '{lang}' ({exc!r}); will load on demand.")


_TTS_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?।])\s+")
_TTS_CHUNK_MAX_CHARS = 400


def _chunk_text_for_tts(text: str) -> list:
    """Split `text` into sentence/line-scale pieces so a long reply becomes
    several short synth calls (natural pausing between sentences) instead of
    one very long forward pass."""
    units = []
    for line in text.split("\n"):
        if not line.strip():
            continue
        units.extend(s for s in _TTS_SENTENCE_SPLIT_RE.split(line) if s.strip())

    chunks = []
    current = ""
    for unit in units:
        candidate = f"{current} {unit}".strip() if current else unit
        if current and len(candidate) > _TTS_CHUNK_MAX_CHARS:
            chunks.append(current)
            current = unit
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks or [text]


def _synthesize_chunk(text: str, lang: str):
    """Generate one chunk's audio via a single VITS forward pass (no
    autoregressive loop) and return (wav_array, sample_rate)."""
    tok = _tokenizers[lang]
    model = _models[lang]

    inputs = tok(text, return_tensors="pt")
    inputs = {k: v.to(_DEVICE) for k, v in inputs.items()}

    with torch.inference_mode():
        output = model(**inputs).waveform

    wav = output.squeeze(0).float().cpu().numpy()
    return wav, int(model.config.sampling_rate)


def synthesize(text: str, language: Optional[str] = None, max_new_tokens: int = 1024) -> Tuple[bytes, int]:
    """Synthesize speech for `text` and return (wav_bytes, sample_rate).

    `max_new_tokens` is accepted for interface compatibility with the
    previous (autoregressive) engine but unused here -- VITS has no
    token-generation budget.
    """
    if SKIP_LOCAL_ML:
        raise RuntimeError(
            "Voice output isn't available in this deployment "
            "(SKIP_LOCAL_ML=true disables local MMS-TTS to stay within "
            "the host's memory limit)."
        )

    text = str(text or "").strip()
    if not text:
        raise ValueError("text is required")

    norm_lang = normalize_language(language)
    if norm_lang not in SUPPORTED_LANGUAGES:
        raise ValueError(f"unsupported language: {language}")

    _load_resources(norm_lang)
    if norm_lang not in _models:
        raise RuntimeError(f"MMS-TTS resources failed to load for '{norm_lang}'")

    with _lock:
        import numpy as np
        import soundfile as sf

        chunks = _chunk_text_for_tts(text)
        gap = np.zeros(0)
        wav_parts = []
        sample_rate = None

        t0 = time.time()
        for chunk in chunks:
            chunk_wav, sample_rate = _synthesize_chunk(chunk, norm_lang)
            if wav_parts:
                if gap.shape[0] == 0:
                    gap = np.zeros(int(sample_rate * 0.2), dtype=chunk_wav.dtype)
                wav_parts.append(gap)
            wav_parts.append(chunk_wav)

        wav = np.concatenate(wav_parts) if len(wav_parts) > 1 else wav_parts[0]
        buf = io.BytesIO()
        sf.write(buf, wav, sample_rate, format="WAV")
        buf.seek(0)
        tts_ms = (time.time() - t0) * 1000.0
        print(f"[tts] MMS-TTS synthesized {len(chunks)} chunk(s) in {tts_ms:.0f}ms "
              f"({SUPPORTED_LANGUAGES[norm_lang]})")
        print(f"[Pipeline] TTS: {tts_ms:.0f}ms | Total: {tts_ms:.0f}ms", flush=True)
        return buf.getvalue(), sample_rate


