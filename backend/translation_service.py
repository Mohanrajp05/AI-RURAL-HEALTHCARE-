
import re
import threading
import os
import time
import torch


_SLASH_WORD_RE = re.compile(r"([A-Za-z])/([A-Za-z])")


def _normalize_for_translation(text: str) -> str:
    return _SLASH_WORD_RE.sub(r"\1 / \2", text)

INDIC2EN_MODEL = os.environ.get("IT2_INDIC_EN_MODEL", "prajdabre/rotary-indictrans2-indic-en-dist-200M")
EN2INDIC_MODEL = os.environ.get("IT2_EN_INDIC_MODEL", "prajdabre/rotary-indictrans2-en-indic-dist-200M")

# GPU when available (huge win: these models otherwise run fp32 on CPU for
# every non-English turn). fp16 only on CUDA -- fp16 matmul on CPU is
# unsupported/slow, so CPU stays fp32.
_DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
_DTYPE = torch.float16 if _DEVICE == "cuda" else torch.float32

_LANG_TO_ISO = {
    "eng_Latn": "en",
    "hin_Deva": "hi",
    "kan_Knda": "kn",
    "tam_Taml": "ta",
    "tel_Telu": "te",
}

_unicode_transliterator = None
_tt_lock = threading.Lock()


def _get_transliterator():
    global _unicode_transliterator
    if _unicode_transliterator is None:
        with _tt_lock:
            if _unicode_transliterator is None:
                from indicnlp.transliterate.unicode_transliterate import (
                    UnicodeIndicTransliterator,
                )
                _unicode_transliterator = UnicodeIndicTransliterator
    return _unicode_transliterator


def _to_devanagari(text: str, lang: str) -> str:
    """Transliterate native-script Indic text to Devanagari (model's canonical script)."""
    if lang in ("eng_Latn", "hin_Deva"):
        return text
    iso = _LANG_TO_ISO.get(lang)
    if not iso:
        return text
    try:
        return _get_transliterator().transliterate(text, iso, "hi")
    except Exception:
        return text


def _from_devanagari(text: str, lang: str) -> str:
    """Transliterate Devanagari model output back to the target Indic script."""
    if lang in ("eng_Latn", "hin_Deva"):
        return text
    iso = _LANG_TO_ISO.get(lang)
    if not iso:
        return text
    try:
        return _get_transliterator().transliterate(text, "hi", iso)
    except Exception:
        return text

_it2_lock = threading.Lock()

TRANSLATE_LOCK = threading.Lock()

_it2_tokenizer = None
_it2_indic_en_model = None
_it2_en_indic_tokenizer = None
_it2_en_indic_model = None
_it2_translate_text = None
_it2_broken = False


def _get_nllb_fallback():
    global _it2_translate_text
    if _it2_translate_text is None:
        from chatbot_pipeline import nllb_translate as _fn
        _it2_translate_text = _fn
    return _it2_translate_text


def _load_models():
    global _it2_tokenizer, _it2_indic_en_model, _it2_en_indic_tokenizer, _it2_en_indic_model
    if _it2_tokenizer is not None:
        return

    with _it2_lock:
        if _it2_tokenizer is not None:
            return

        from transformers import AutoTokenizer, AutoModelForSeq2SeqLM

        _it2_tokenizer = AutoTokenizer.from_pretrained(
            INDIC2EN_MODEL, trust_remote_code=True, local_files_only=True
        )
        _it2_indic_en_model = AutoModelForSeq2SeqLM.from_pretrained(
            INDIC2EN_MODEL, trust_remote_code=True, local_files_only=True, dtype=_DTYPE
        )
        _it2_indic_en_model.eval()
        _it2_indic_en_model.to(_DEVICE)
        # NOTE: use_cache=False is NOT a perf oversight -- IndicTrans2's
        # custom remote-code decoder crashes under transformers 4.57.1 with
        # KV caching on ("'NoneType' object has no attribute 'shape'" in
        # modeling_rotary_indictrans.py), confirmed by testing use_cache=True
        # directly. Leave disabled; the GPU move below is what actually
        # fixes the latency, since it parallelizes the recompute instead of
        # avoiding it.
        _it2_indic_en_model.config.use_cache = False

        _it2_en_indic_tokenizer = AutoTokenizer.from_pretrained(
            EN2INDIC_MODEL, trust_remote_code=True, local_files_only=True
        )
        _it2_en_indic_model = AutoModelForSeq2SeqLM.from_pretrained(
            EN2INDIC_MODEL, trust_remote_code=True, local_files_only=True, dtype=_DTYPE
        )
        _it2_en_indic_model.eval()
        _it2_en_indic_model.to(_DEVICE)
        _it2_en_indic_model.config.use_cache = False

        print(f"[Translate] Model loaded once at startup (device={_DEVICE}, dtype={_DTYPE})", flush=True)


def preload():
    """Load the IndicTrans2 models once at server startup (never per-request)."""
    try:
        _load_models()
    except Exception as exc:
        print(f"[Translate] Startup preload failed ({exc!r}); will fall back to NLLB on demand.")


# IndicTrans2's 200M distilled checkpoint is tuned for sentence-scale
# inputs. Feeding it a full multi-sentence reply in one shot causes two
# distinct failures, both confirmed empirically on real 8-line replies:
# (1) it hits the generation length cap and cuts off mid-sentence, dropping
# the tail of the reply, and (2) well before that, it degrades into
# repetition loops (e.g. "slowly slowly slowly...") instead of translating
# cleanly -- a well-known collapse mode for small seq2seq models pushed
# outside their training-length distribution. Splitting into line/sentence
# chunks keeps every call short enough to avoid both.
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?।])\s+")
_CHUNK_MAX_WORDS = 40

# Markdown bullet/bold markers ("* **Heading:** rest of line") confuse the
# translation models if fed through raw: "**" gets mangled into stray "* *"
# and the model can hallucinate/duplicate the wrapped heading text -- both
# confirmed live on real bullet-list replies. Strip the markup out, translate
# only the plain-text pieces, and reapply the markers structurally instead.
_MD_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_MD_BULLET_RE = re.compile(r"^(\s*[-*•]\s+)")


def _translate_segment(segment: str, translate_fn) -> str:
    """translate_fn strips its output, which loses a segment's original
    leading/trailing whitespace (e.g. the space after a "**...**" span
    before the next word) -- restore it so words don't get jammed together."""
    if not segment.strip():
        return segment
    translated = translate_fn(segment)
    if segment[:1].isspace() and not translated[:1].isspace():
        translated = " " + translated
    if segment[-1:].isspace() and not translated[-1:].isspace():
        translated = translated + " "
    return translated


def translate_preserving_markdown(content: str, translate_fn) -> str:
    """Translate `content` via `translate_fn(plain_text) -> plain_text`
    while leaving a leading bullet marker and any "**bold**" spans intact."""
    bullet_match = _MD_BULLET_RE.match(content)
    prefix = bullet_match.group(1) if bullet_match else ""
    body = content[len(prefix):]

    if "**" not in body:
        return prefix + _translate_segment(body, translate_fn)

    pieces = []
    last_end = 0
    for m in _MD_BOLD_RE.finditer(body):
        if m.start() > last_end:
            pieces.append(_translate_segment(body[last_end:m.start()], translate_fn))
        inner = m.group(1)
        pieces.append(f"**{_translate_segment(inner, translate_fn).strip()}**")
        last_end = m.end()
    if last_end < len(body):
        pieces.append(_translate_segment(body[last_end:], translate_fn))

    return prefix + "".join(pieces)


def _split_words_by_count(span: str, max_words: int):
    """Yield `span` in <= max_words pieces, breaking ONLY on whitespace
    (never mid-word). Last-resort fallback for a "sentence" that is still
    too long after _SENTENCE_SPLIT_RE -- i.e. a long run of text with no
    sentence-ending punctuation (`. ! ? ।`) at all, which real user input
    (mobile typing, dictated symptom descriptions) hits often. Without this,
    such a paragraph became ONE oversized chunk fed whole to a model tuned
    for sentence-scale input, which is what caused incomplete/hallucinated/
    meaning-changed translations on long inputs -- confirmed by reproducing
    it directly: a 244-word punctuation-free paragraph came back as a
    garbled, incomplete 125-word translation."""
    words = span.split()
    for i in range(0, len(words), max_words):
        yield " ".join(words[i:i + max_words])


def _split_into_chunks(text: str):
    """Yield (separator, content) pairs; joining translated `content`
    values back with their `separator` reconstructs the original layout
    (lines rejoin on "\\n", split-out sentences/pieces rejoin on " ").

    Every yielded chunk is guaranteed to be <= _CHUNK_MAX_WORDS words: first
    split by line, then by sentence-ending punctuation, then -- only if a
    "sentence" is STILL too long (no punctuation to split on) -- by a plain
    word-count cut via _split_words_by_count. This is what keeps every
    model call within the sentence-scale range the translation models are
    tuned for, however the input is punctuated, so no chunk is ever
    silently oversized, no text is dropped, and original order is preserved
    throughout (this generator only ever walks forward through `text`)."""
    lines = text.split("\n")
    for i, line in enumerate(lines):
        line_sep = "" if i == 0 else "\n"
        if not line.strip() or len(line.split()) <= _CHUNK_MAX_WORDS:
            yield (line_sep, line)
            continue
        sentences = [s for s in _SENTENCE_SPLIT_RE.split(line) if s.strip()]
        first_on_line = True
        for sent in sentences:
            sent_sep = line_sep if first_on_line else " "
            first_on_line = False
            if len(sent.split()) <= _CHUNK_MAX_WORDS:
                yield (sent_sep, sent)
                continue
            first_piece = True
            for piece in _split_words_by_count(sent, _CHUNK_MAX_WORDS):
                yield (sent_sep if first_piece else " ", piece)
                first_piece = False


def _translate_chunk(text: str, src_lang: str, tgt_lang: str) -> str:
    """Translate one sentence/line-scale chunk via IndicTrans2 (no fallback --
    callers handle that). Raises on inference failure."""
    if src_lang == "eng_Latn":
        model = _it2_en_indic_model
        tok = _it2_en_indic_tokenizer
    else:
        model = _it2_indic_en_model
        tok = _it2_tokenizer
    inp = f"{src_lang} {tgt_lang} {_to_devanagari(_normalize_for_translation(text), src_lang)}"
    # _split_into_chunks() keeps every chunk sentence-scale, so this should
    # never actually engage -- but truncation=True + an explicit max_length
    # (the tokenizer's own reported limit, not a guess) is a defensive
    # safety net for the rare pathological chunk, and a warning is logged
    # if it ever fires so a truncation is never silent.
    model_max_length = getattr(tok, "model_max_length", None) or 8192
    untruncated_len = len(tok(inp)["input_ids"])
    if untruncated_len > model_max_length:
        print(f"[IT2] WARNING: chunk of {untruncated_len} tokens exceeds "
              f"model_max_length={model_max_length}; truncating ({src_lang}->{tgt_lang}).")
    enc = tok([inp], return_tensors="pt", truncation=True, max_length=model_max_length)
    enc = {k: v.to(_DEVICE) for k, v in enc.items()}

    input_len = enc["input_ids"].shape[1]
    max_new_tokens = min(600, max(256, int(input_len * 2.5)))
    with TRANSLATE_LOCK, torch.inference_mode():
        out = model.generate(
            **enc,
            num_beams=1,
            max_new_tokens=max_new_tokens,
            no_repeat_ngram_size=3,
            use_cache=False,
        )
    result = tok.batch_decode(out.cpu(), skip_special_tokens=True)[0].strip()
    return _from_devanagari(result, tgt_lang)


def translate(text: str, src_lang: str, tgt_lang: str) -> str:
    """Translate text via IndicTrans2, chunked by line/sentence (see
    _split_into_chunks). Falls back to NLLB -- per-chunk if IT2 errors on a
    specific chunk, or for the whole text once IT2 is unusable."""
    global _it2_broken
    if not text or not text.strip():
        return text
    if src_lang == tgt_lang:
        return text

    if _it2_broken:
        return _get_nllb_fallback()(text, src_lang, tgt_lang)

    try:
        _load_models()
    except Exception as exc:
        _it2_broken = True
        print(f"[IT2] IndicTrans2 unavailable (transformers 5.x?): {exc!r} - using NLLB")
        return _get_nllb_fallback()(text, src_lang, tgt_lang)

    _t0 = time.perf_counter()
    pieces = []
    n_chunks = 0
    chunk_num = 0
    for sep, content in _split_into_chunks(text):
        if not content.strip():
            pieces.append(sep + content)
            continue
        n_chunks += 1
        chunk_num += 1
        try:
            translated = translate_preserving_markdown(
                content, lambda t: _translate_chunk(t, src_lang, tgt_lang)
            )
            pieces.append(sep + translated)
        except Exception as exc:
            
            print(f"[IT2] IndicTrans2 inference error on chunk ({src_lang}->{tgt_lang}): {exc!r}")
            try:
                pieces.append(sep + _get_nllb_fallback()(content, src_lang, tgt_lang))
            except Exception as exc2:
                print(f"[IT2] NLLB fallback also failed ({src_lang}->{tgt_lang}): {exc2!r}")
                pieces.append(sep + content)
    elapsed_ms = (time.perf_counter() - _t0) * 1000.0
    print(f"[Translate] {src_lang}->{tgt_lang} {n_chunks} chunk(s) took {elapsed_ms:.0f}ms", flush=True)
    return "".join(pieces)
