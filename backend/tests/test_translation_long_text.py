"""Permanent regression suite for the long-text translation pipeline.

Background: a real bug shipped where long AI replies, when translated,
came back truncated to the frontend. Root-caused to TWO independent
issues (see git history around 2026-08-22):

  1. `translation_service.translate()` chunking -- long/punctuation-free
     input could collapse into one oversized chunk fed whole to a model
     tuned for sentence-scale input, producing incomplete/garbled output.
     Fixed by `_split_into_chunks()` + `_split_words_by_count()`'s
     word-count fallback.
  2. `app.py`'s translation-command branch called
     `limit_response_words(reply, 300)` AFTER translation was already
     complete, silently discarding everything past word 300. Verified
     live: a 727-word input produced a 971-word complete Hindi
     translation, which the cap then cut to exactly 300 words. Fixed by
     removing that call from the translation branch (see
     test_response_limit_regression.py for the dedicated guard).

This file implements the 7 required regression cases (TEST 1-7). Cases
that need actual translation quality/completeness are marked
`@pytest.mark.integration` and share ONE session-scoped model load (see
`real_translator` in conftest.py) -- they are automatically SKIPPED, not
failed, on a machine without the local IndicTrans2 checkpoints, so this
file still gives a signal in a lightweight environment. Pure chunking
behavior (no model required) is asserted directly alongside, per-test,
using `translation_service._split_into_chunks` -- chunking correctness is
never inferred only from the final translation (see also
test_chunking_unit.py for the standalone chunking-only suite).

Run just this file:  pytest tests/test_translation_long_text.py -v
Run only what's fast (no model): pytest -m "not integration" -v
"""
import re

import pytest

import translation_service as ts
from sample_texts import (
    LONG_PARAGRAPH_EN_FOR_KN,
    LONG_PARAGRAPH_KN,
    MEDICAL_PARAGRAPH_EN,
    MEDIUM_PARAGRAPH_EN,
    NO_PUNCTUATION_PARAGRAPH_EN,
    NUMBERS_MEDICAL_PARAGRAPH_EN,
    REQUIRED_NUMERIC_TOKENS,
    SHORT_PARAGRAPH_EN,
)

EN = "eng_Latn"
HI = "hin_Deva"
KN = "kan_Knda"

REQUIRED_MEDICAL_TERMS = [
    "hypertension", "diabetes", "myocardial infarction", "blood pressure",
    "heart rate", "symptoms", "diagnosis", "treatment", "medication",
    "dosage", "liver", "kidney", "infection",
]


def _real_chunk_count(text: str) -> int:
    return sum(1 for _sep, content in ts._split_into_chunks(text) if content.strip())


def _run_and_count_failures(text, src, tgt, capsys):
    """Translate `text` and return (result, total_chunks, failed_chunks)
    by cross-referencing the deterministic chunk count against any
    "CHUNK X FAILED" lines the run printed -- gives the log/report numbers
    the spec asks for without needing production code to carry permanent
    diagnostic instrumentation."""
    total_chunks = _real_chunk_count(text)
    result = ts.translate(text, src, tgt)
    failed = len(re.findall(r"CHUNK \d+ FAILED", capsys.readouterr().out))
    return result, total_chunks, failed


# ===========================================================================
# TEST 1 -- short paragraph (~50-100 words)
# ===========================================================================
@pytest.mark.integration
def test_1_short_paragraph_translates_successfully(real_translator, capsys):
    assert 50 <= len(SHORT_PARAGRAPH_EN.split()) <= 100

    result, total_chunks, failed = _run_and_count_failures(SHORT_PARAGRAPH_EN, EN, HI, capsys)

    assert failed == 0
    assert result.strip() != ""
    # Not unexpectedly truncated: short input should complete in a small
    # number of chunks, all successful, with a non-trivial output.
    assert total_chunks >= 1
    assert len(result.split()) >= 5


# ===========================================================================
# TEST 2 -- 500-1000 word paragraph
# ===========================================================================
@pytest.mark.integration
def test_2_medium_paragraph_full_pipeline(real_translator, capsys):
    input_words = len(MEDIUM_PARAGRAPH_EN.split())
    assert 500 <= input_words <= 1000

    # Chunking-level assertions FIRST (independent of translation quality).
    chunks = [(s, c) for s, c in ts._split_into_chunks(MEDIUM_PARAGRAPH_EN) if c.strip()]
    assert len(chunks) > 1, "500-1000 word input must be split into multiple chunks"
    for _sep, content in chunks:
        assert len(content.split()) <= ts._CHUNK_MAX_WORDS
    # Chunk order preserved + full coverage (nothing dropped at the split stage).
    assert "".join(sep + content for sep, content in ts._split_into_chunks(MEDIUM_PARAGRAPH_EN)) == MEDIUM_PARAGRAPH_EN

    result, total_chunks, failed = _run_and_count_failures(MEDIUM_PARAGRAPH_EN, EN, HI, capsys)
    successful = total_chunks - failed
    output_words = len(result.split())

    print(f"\n[TEST 2 REPORT] input_words={input_words} chunks={total_chunks} "
          f"successful={successful} failed={failed} output_words={output_words}")

    assert total_chunks == len(chunks)  # every chunk from the split stage was processed
    assert failed == 0
    assert successful == total_chunks  # every chunk accounted for -- none silently skipped
    assert result.strip() != ""
    # The regression this case exists for: NOT capped to (or near) 300
    # words. translation_service.translate() never applies any word cap,
    # so a 500-1000 word input's translation should clear 300 words --
    # this is the same shape of check that would have caught the original
    # bug if it had lived at this layer instead of in app.py.
    assert output_words > 300


# ===========================================================================
# TEST 3 -- long paragraph WITHOUT sentence punctuation (the original bug)
# ===========================================================================
def test_3_no_punctuation_chunking_does_not_collapse(capsys):
    """Pure chunking check -- no model required, so this fails immediately
    (in milliseconds) if the old bug returns, independent of whether the
    translation model happens to be installed in this environment."""
    assert len(NO_PUNCTUATION_PARAGRAPH_EN.split()) >= 500
    assert not re.search(r"[.!?।]", NO_PUNCTUATION_PARAGRAPH_EN)

    chunks = [(s, c) for s, c in ts._split_into_chunks(NO_PUNCTUATION_PARAGRAPH_EN) if c.strip()]
    assert len(chunks) > 1, "punctuation-free input must NOT collapse into one giant chunk"
    for _sep, content in chunks:
        assert len(content.split()) <= ts._CHUNK_MAX_WORDS
    # Full coverage, order preserved.
    total_words = sum(len(c.split()) for _s, c in chunks)
    assert total_words == len(NO_PUNCTUATION_PARAGRAPH_EN.split())


@pytest.mark.integration
def test_3_no_punctuation_paragraph_translates_completely(real_translator, capsys):
    result, total_chunks, failed = _run_and_count_failures(NO_PUNCTUATION_PARAGRAPH_EN, EN, HI, capsys)
    successful = total_chunks - failed

    assert total_chunks > 1
    assert failed == 0
    assert successful == total_chunks
    assert result.strip() != ""
    assert len(result.split()) > 100  # not collapsed/garbled into a near-empty reply


# ===========================================================================
# TEST 4 -- long medical paragraph, required terminology
# ===========================================================================
@pytest.mark.integration
def test_4_medical_paragraph_terminology_preserved(real_translator, capsys):
    for term in REQUIRED_MEDICAL_TERMS:
        assert term in MEDICAL_PARAGRAPH_EN.lower()

    input_words = len(MEDICAL_PARAGRAPH_EN.split())
    result, total_chunks, failed = _run_and_count_failures(MEDICAL_PARAGRAPH_EN, EN, HI, capsys)
    successful = total_chunks - failed
    output_words = len(result.split())

    print(f"\n[TEST 4 REPORT] input_words={input_words} chunks={total_chunks} "
          f"successful={successful} failed={failed} output_words={output_words}")

    assert failed == 0
    assert successful == total_chunks  # no chunk (and so no medical term inside it) silently skipped
    assert result.strip() != ""
    # Deliberately NOT a word-for-word/back-translation check (legitimate
    # translations reorder and change word count) -- instead a loose
    # length-ratio sanity bound that still catches gross truncation or a
    # collapse to near-empty output without being flaky.
    assert 0.5 * input_words <= output_words <= 3 * input_words


# ===========================================================================
# TEST 5 -- long Kannada -> English
# ===========================================================================
@pytest.mark.integration
def test_5_kannada_to_english_long_text(real_translator, capsys):
    from language_service import detect_language

    input_words = len(LONG_PARAGRAPH_KN.split())
    assert 300 <= input_words <= 800

    # Correct source language selected (script-based detection).
    assert detect_language(LONG_PARAGRAPH_KN) == KN

    chunks = [(s, c) for s, c in ts._split_into_chunks(LONG_PARAGRAPH_KN) if c.strip()]
    assert len(chunks) > 1

    result, total_chunks, failed = _run_and_count_failures(LONG_PARAGRAPH_KN, KN, EN, capsys)
    successful = total_chunks - failed
    output_words = len(result.split())

    print(f"\n[TEST 5 REPORT] input_words={input_words} chunks={total_chunks} "
          f"successful={successful} failed={failed} output_words={output_words}")

    assert failed == 0
    assert successful == total_chunks  # no Kannada section silently dropped
    assert result.strip() != ""
    # Correct target language: output should be predominantly Latin-script
    # English, not left in Kannada script.
    kannada_chars = sum(1 for ch in result if "ಀ" <= ch <= "೿")
    assert kannada_chars < 0.1 * max(len(result), 1)
    # Not artificially capped: with 300-800 Kannada words in, output
    # shouldn't be suspiciously truncated to a sliver of the input.
    assert output_words >= 0.5 * input_words


# ===========================================================================
# TEST 6 -- long English -> Kannada
# ===========================================================================
@pytest.mark.integration
def test_6_english_to_kannada_long_text(real_translator, capsys):
    from language_service import detect_language

    input_words = len(LONG_PARAGRAPH_EN_FOR_KN.split())
    assert 300 <= input_words <= 800
    assert detect_language(LONG_PARAGRAPH_EN_FOR_KN) == EN

    chunks = [(s, c) for s, c in ts._split_into_chunks(LONG_PARAGRAPH_EN_FOR_KN) if c.strip()]
    assert len(chunks) > 1

    result, total_chunks, failed = _run_and_count_failures(LONG_PARAGRAPH_EN_FOR_KN, EN, KN, capsys)
    successful = total_chunks - failed
    output_words = len(result.split())

    print(f"\n[TEST 6 REPORT] input_words={input_words} chunks={total_chunks} "
          f"successful={successful} failed={failed} output_words={output_words}")

    assert failed == 0
    assert successful == total_chunks
    assert result.strip() != ""
    # Kannada output actually returned (correct target script), not left
    # as English or some other script.
    kannada_chars = sum(1 for ch in result if "ಀ" <= ch <= "೿")
    assert kannada_chars > 0.3 * max(len(result.replace(" ", "")), 1)
    assert output_words >= 0.5 * input_words


# ===========================================================================
# TEST 7 -- numbers, dates, dosages, units embedded in a long medical text
# ===========================================================================
@pytest.mark.integration
def test_7_numbers_dates_and_dosages_survive_translation(real_translator, capsys):
    for token in REQUIRED_NUMERIC_TOKENS:
        assert token in NUMBERS_MEDICAL_PARAGRAPH_EN  # sanity-check the fixture itself

    input_words = len(NUMBERS_MEDICAL_PARAGRAPH_EN.split())
    result, total_chunks, failed = _run_and_count_failures(NUMBERS_MEDICAL_PARAGRAPH_EN, EN, HI, capsys)
    successful = total_chunks - failed

    assert failed == 0
    assert successful == total_chunks  # every chunk -- and so every value inside it -- was processed
    assert result.strip() != ""

    missing = [tok for tok in REQUIRED_NUMERIC_TOKENS if tok not in result]
    print(f"\n[TEST 7 REPORT] input_words={input_words} chunks={total_chunks} "
          f"successful={successful} failed={failed} output_words={len(result.split())} "
          f"missing_numeric_tokens={missing}")

    # Do NOT require exact word-count parity (translation legitimately
    # changes word count) -- but numbers/dates/dosages/units are not
    # translatable content and must survive verbatim. Allow a small margin
    # (transliteration engines occasionally reformat a stray value) rather
    # than demanding 100% of a dozen values with zero tolerance.
    assert len(missing) <= 1, f"too many numeric/date/dosage values dropped: {missing}"
