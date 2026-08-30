"""Regression guard for the actual bug that caused long translations to be
truncated: app.py's translation-command branch called
`limit_response_words(reply, 300)` AFTER translate_text() had already
produced a complete translation, silently discarding everything past word
300 (a 971-word verified-complete Hindi translation was cut to exactly 300
words).

Deliberately does NOT `import app` -- app.py eagerly loads several
unrelated heavy subsystems at import time (disease-prediction models, the
FAQ/RAG index, etc. via chatbot_response/chatbot_pipeline/
predict_disease_guarded), so pulling the whole module in just to reach two
small pure functions would make this suite slow and would drag unrelated
components into a "translation regression" test's failure surface.

Instead:
  - The wiring check (branch does/doesn't CALL limit_response_words) parses
    app.py with `ast` and looks for an actual `Call` node inside the
    translation branch's line range -- not a substring search, which would
    false-positive on comments that merely *mention* the function name
    (this file's own explanatory comment in app.py does exactly that: "...
    deliberately NOT calling limit_response_words() here"). Comments never
    produce AST nodes, so this only fires on a real reintroduced call --
    exactly the regression named in the requirements ("the test should
    fail if somebody accidentally reintroduces limit_response_words(reply,
    300) into the translation branch").
  - The *behavior* of limit_response_words/enforce_language is verified by
    extracting their exact current function bodies out of app.py via `ast`
    and exec'ing just those (still the real, current source -- not a
    hand-copied duplicate that could drift out of sync) in an isolated
    namespace seeded with the handful of stdlib names they use.
"""
import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

BACKEND_DIR = Path(__file__).resolve().parent.parent
APP_PY = BACKEND_DIR / "app.py"

START_MARKER = "STEP 1: DETECT TRANSLATION QUERY"
END_MARKER = "STEP 1B: HOSPITAL SEARCH INTENT"


def _app_source() -> str:
    return APP_PY.read_text(encoding="utf-8")


def _translation_branch_line_range(source: str) -> tuple[int, int]:
    """1-indexed [start_line, end_line) covering the translation-query
    branch, located via the same section-comment markers app.py already
    uses to delimit it."""
    lines = source.splitlines()
    start_line = next(i for i, ln in enumerate(lines) if START_MARKER in ln) + 1
    end_line = next(i for i, ln in enumerate(lines) if END_MARKER in ln) + 1
    return start_line, end_line


def _find_calls_to(source: str, func_name: str, line_lo: int = 0, line_hi: int = 10**9):
    """Return line numbers of every AST Call to `func_name` (as a bare name
    or an attribute access) within [line_lo, line_hi). AST-based, so
    comments and docstrings mentioning the name are correctly ignored."""
    tree = ast.parse(source)
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
        if name == func_name and line_lo <= node.lineno < line_hi:
            hits.append(node.lineno)
    return hits


def _extract_function(source: str, name: str):
    """Return a live callable for function `name`, built by exec'ing just
    that function's exact source text pulled out of `source` via `ast`.
    Avoids importing the (heavy) module the source lives in. Seeded with
    `re` since both extracted functions (enforce_language,
    limit_response_words) rely on the module-level `import re` in app.py,
    which this isolated namespace doesn't otherwise have."""
    import re as _re

    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            func_src = ast.get_source_segment(source, node)
            namespace: dict = {"re": _re}
            exec(compile(func_src, filename=f"<extracted:{name}>", mode="exec"), namespace)
            return namespace[name]
    raise AssertionError(f"function {name!r} not found in source")


# ---------------------------------------------------------------------------
# The critical wiring guard: this MUST fail if limit_response_words(reply,
# 300) is ever reintroduced into the translation branch.
# ---------------------------------------------------------------------------
def test_translation_branch_does_not_apply_the_normal_word_limit():
    source = _app_source()
    line_lo, line_hi = _translation_branch_line_range(source)
    calls = _find_calls_to(source, "limit_response_words", line_lo, line_hi)
    assert calls == [], (
        f"limit_response_words() is called at line(s) {calls} inside the "
        "translation-query branch of app.py (between the "
        f"{START_MARKER!r} and {END_MARKER!r} markers, lines {line_lo}-"
        f"{line_hi}) -- calling it there re-truncates an already-complete "
        "translation. This is the exact regression that caused a 971-word "
        "verified-complete Hindi translation to be cut to 300 words. If "
        "you intentionally moved this branch, update START_MARKER/"
        "END_MARKER in this test to match, but do NOT reintroduce the "
        "word-cap call itself."
    )


def test_normal_chatbot_replies_still_use_the_word_limit_elsewhere():
    """The cap wasn't globally removed -- it's still called by the normal
    (non-translation) KB-style answer paths, outside the branch."""
    source = _app_source()
    line_lo, line_hi = _translation_branch_line_range(source)
    all_calls = _find_calls_to(source, "limit_response_words")
    outside_branch = [ln for ln in all_calls if not (line_lo <= ln < line_hi)]
    assert len(outside_branch) >= 4, (
        f"expected limit_response_words() to still be called by normal "
        f"reply paths outside the translation branch, found calls at "
        f"{outside_branch}"
    )


# ---------------------------------------------------------------------------
# Behavioral check of the real (extracted, not duplicated) functions.
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def limit_response_words():
    return _extract_function(_app_source(), "limit_response_words")


@pytest.fixture(scope="module")
def enforce_language():
    return _extract_function(_app_source(), "enforce_language")


def test_limit_response_words_caps_normal_replies_at_300(limit_response_words):
    text = " ".join(["word"] * 500) + "."
    capped = limit_response_words(text, 300)
    assert len(capped.split()) <= 300


def test_limit_response_words_leaves_short_replies_untouched(limit_response_words):
    text = "Please drink plenty of water and rest."
    assert limit_response_words(text, 300) == text


def test_enforce_language_keeps_devanagari_and_strips_stray_latin(enforce_language):
    mixed = "स्वास्थ्य अच्छा रखें garbage123 धन्यवाद"
    cleaned = enforce_language(mixed, "hin_Deva")
    assert "स्वास्थ्य" in cleaned
    assert "धन्यवाद" in cleaned
    assert "garbage123" not in cleaned


# ---------------------------------------------------------------------------
# End-to-end shape check: translate_text() -> enforce_language() (no cap)
# must be able to carry a >300-word payload through unchanged, proving the
# combination the translation branch actually runs does not truncate.
# ---------------------------------------------------------------------------
def test_translation_pipeline_functions_do_not_truncate_long_text(enforce_language):
    long_translated_text = ("शब्द " * 400).strip()  # 400 Devanagari "words"
    result = enforce_language(long_translated_text, "hin_Deva")
    # enforce_language only filters characters/scripts -- it must not cut
    # a long string down by length.
    assert len(result.split()) >= 390
