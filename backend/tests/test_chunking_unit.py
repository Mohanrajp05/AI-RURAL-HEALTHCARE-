"""Chunking assertions for translation_service's chunking layer.

Pure unit tests: no ML model, no network, no app.py import. These exercise
`_split_into_chunks` / `_split_words_by_count` / `translate_preserving_markdown`
directly and are the fastest, most direct guard against the chunking
regression -- they must fail immediately (in milliseconds) if the
"one giant chunk" bug (see translation_service._split_words_by_count's
docstring) ever comes back, without needing the translation model to be
installed at all.
"""
import pytest

import translation_service as ts
from sample_texts import (
    MEDICAL_PARAGRAPH_EN,
    MEDIUM_PARAGRAPH_EN,
    NO_PUNCTUATION_PARAGRAPH_EN,
    NUMBERS_MEDICAL_PARAGRAPH_EN,
    SHORT_PARAGRAPH_EN,
)

pytestmark = pytest.mark.unit


def _chunks(text):
    return list(ts._split_into_chunks(text))


def _real_chunks(text):
    """Chunks with actual content (drop blank/separator-only pieces)."""
    return [(sep, content) for sep, content in _chunks(text) if content.strip()]


# ---------------------------------------------------------------------------
# Round-trip fidelity: no text lost, no text reordered.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "text",
    [
        SHORT_PARAGRAPH_EN,
        MEDIUM_PARAGRAPH_EN,
        NO_PUNCTUATION_PARAGRAPH_EN,
        MEDICAL_PARAGRAPH_EN,
        NUMBERS_MEDICAL_PARAGRAPH_EN,
        "",
        "   ",
        "one two three",
        "Line one\nLine two\nLine three",
    ],
)
def test_chunks_reconstruct_the_original_text_exactly(text):
    """Concatenating (separator + content) for every yielded chunk, in
    order, must reproduce the source text byte-for-byte. This is the
    strongest possible guarantee that chunking preserves order and drops
    nothing -- it doesn't rely on the translation step at all."""
    reconstructed = "".join(sep + content for sep, content in _chunks(text))
    assert reconstructed == text


# ---------------------------------------------------------------------------
# No chunk may exceed the configured safe word limit.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "text",
    [MEDIUM_PARAGRAPH_EN, NO_PUNCTUATION_PARAGRAPH_EN, MEDICAL_PARAGRAPH_EN, NUMBERS_MEDICAL_PARAGRAPH_EN],
)
def test_no_chunk_exceeds_configured_max_words(text):
    for _sep, content in _real_chunks(text):
        word_count = len(content.split())
        assert word_count <= ts._CHUNK_MAX_WORDS, (
            f"chunk exceeded _CHUNK_MAX_WORDS={ts._CHUNK_MAX_WORDS}: "
            f"{word_count} words -- {content[:80]!r}..."
        )


# ---------------------------------------------------------------------------
# Long text must produce more than one chunk.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "text", [MEDIUM_PARAGRAPH_EN, NO_PUNCTUATION_PARAGRAPH_EN, MEDICAL_PARAGRAPH_EN, NUMBERS_MEDICAL_PARAGRAPH_EN]
)
def test_long_text_produces_multiple_chunks(text):
    assert len(_real_chunks(text)) > 1


# ---------------------------------------------------------------------------
# TEST 3 core assertion -- the actual regression this suite must catch.
# ---------------------------------------------------------------------------
def test_no_punctuation_paragraph_does_not_collapse_to_one_giant_chunk():
    """This is the exact bug this whole test file exists to prevent: a long
    run of text with no sentence-ending punctuation (`. ! ? ।`) at all used
    to become ONE oversized chunk fed whole to a model tuned for
    sentence-scale input (see translation_service._split_words_by_count's
    docstring -- this reproduced a real incomplete/garbled translation on a
    244-word punctuation-free paragraph). If _split_words_by_count's
    fallback ever regresses, this test fails immediately without needing
    the model."""
    assert "." not in NO_PUNCTUATION_PARAGRAPH_EN
    assert "!" not in NO_PUNCTUATION_PARAGRAPH_EN
    assert "?" not in NO_PUNCTUATION_PARAGRAPH_EN
    assert "।" not in NO_PUNCTUATION_PARAGRAPH_EN  # danda (।)
    assert len(NO_PUNCTUATION_PARAGRAPH_EN.split()) >= 500

    chunks = _real_chunks(NO_PUNCTUATION_PARAGRAPH_EN)

    # The old bug: exactly one chunk containing the entire input.
    assert len(chunks) > 1, "punctuation-free input collapsed into a single chunk -- the old bug is back"
    for _sep, content in chunks:
        assert len(content.split()) <= ts._CHUNK_MAX_WORDS

    # And still nothing lost/reordered.
    total_words_in_chunks = sum(len(c.split()) for _s, c in chunks)
    assert total_words_in_chunks == len(NO_PUNCTUATION_PARAGRAPH_EN.split())


def test_split_words_by_count_never_drops_or_reorders_words():
    words = [f"word{i}" for i in range(137)]
    span = " ".join(words)
    pieces = list(ts._split_words_by_count(span, max_words=40))

    assert all(len(p.split()) <= 40 for p in pieces)
    assert " ".join(pieces) == span  # order preserved, nothing dropped
    assert len(pieces) == 4  # 137 words / 40 per piece -> ceil(137/40) == 4


def test_split_words_by_count_never_breaks_mid_word():
    span = " ".join(f"symptomword{i}" for i in range(90))
    for piece in ts._split_words_by_count(span, max_words=40):
        for w in piece.split():
            assert w in span.split()  # every emitted token is a whole original word


# ---------------------------------------------------------------------------
# Markdown-preserving translation wrapper -- pure, no model needed (fake
# translate_fn).
# ---------------------------------------------------------------------------
def test_translate_preserving_markdown_keeps_bullet_and_bold_structure():
    fake_translate = lambda t: t.upper()  # noqa: E731 -- deterministic stand-in for a real model
    content = "- **Warning:** seek care immediately"
    result = ts.translate_preserving_markdown(content, fake_translate)
    assert result.startswith("- ")
    assert "**WARNING:**" in result
    assert "SEEK CARE IMMEDIATELY" in result
