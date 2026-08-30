"""Shared fixtures for the translation regression test suite.

Design notes (see backend/tests/test_translation_long_text.py for the full
writeup of what each test covers):

- `unit` tests never touch the real ML model or app.py's heavy imports --
  they exercise translation_service's pure chunking/formatting functions
  directly, or use monkeypatching to simulate model behavior. These always
  run, in any environment, in well under a second each.

- `integration` tests exercise the REAL IndicTrans2 models via
  translation_service.translate(). The models are loaded exactly once for
  the whole test session (see `real_translator` below) and reused by every
  integration test, instead of paying the load cost per test. If the local
  model checkpoints aren't available in the current environment, these
  tests are SKIPPED (not failed/errored) so the unit suite still gives a
  clean signal on machines without the model files.

- `e2e` is reserved for tests that exercise behavior across module
  boundaries (currently: the static source-scan of app.py's translation
  branch). Nothing here imports app.py itself -- app.py eagerly loads
  several unrelated heavy subsystems at import time (disease-prediction
  models, FAQ/RAG index, etc.), which would make the suite slow and would
  pull unrelated components into a "translation regression" test's
  failure surface. See test_response_limit_regression.py for how the
  app.py-specific checks are done safely (source inspection) instead.
"""
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import translation_service  # noqa: E402


def pytest_configure(config):
    config.addinivalue_line("markers", "unit: fast, deterministic, no ML model or DB required")
    config.addinivalue_line("markers", "integration: exercises the real IndicTrans2 model (session-scoped load)")
    config.addinivalue_line("markers", "e2e: end-to-end behavioral checks")


@pytest.fixture(scope="session")
def real_translator():
    """Load the real IndicTrans2 models ONCE for the whole test session.

    Every integration test that needs actual translation should depend on
    this fixture and then call `translation_service.translate(...)`
    directly -- the model is already warm after the first test that uses
    it. Skips (doesn't fail) when the local checkpoints aren't available,
    so this suite degrades gracefully on a machine that only has the unit
    tests' dependencies installed.
    """
    try:
        translation_service._load_models()
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"IndicTrans2 models unavailable in this environment: {exc!r}")
    return translation_service
