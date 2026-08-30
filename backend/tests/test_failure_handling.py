"""Regression tests for chunk-failure handling in translation_service.translate().

Pure unit tests: `_load_models` and `_translate_chunk` are monkeypatched so
these run fast and deterministically, with no dependency on the real
IndicTrans2 checkpoints being installed. They protect exactly the behavior
called out as a hard requirement while diagnosing the original bug:

  "Do not silently skip failed chunks. If any chunk fails, report:
  'CHUNK X FAILED'"

and the NLLB-fallback / original-text-preserving behavior that follows it.
"""
import translation_service as ts

SRC = "eng_Latn"
TGT = "hin_Deva"


def _patch_load_models_noop(monkeypatch):
    """translate() calls _load_models() first; make it a no-op so this
    test never touches the real (possibly-unavailable) model files."""
    monkeypatch.setattr(ts, "_load_models", lambda: None)
    monkeypatch.setattr(ts, "_it2_broken", False)


def test_chunk_failure_is_reported_and_not_silently_skipped(monkeypatch, capsys):
    """One chunk's _translate_chunk raises; every other chunk succeeds."""
    _patch_load_models_noop(monkeypatch)

    calls = []

    def fake_translate_chunk(text, src_lang, tgt_lang):
        calls.append(text)
        if "SECOND" in text:
            raise RuntimeError("simulated IndicTrans2 inference failure")
        return f"[TRANSLATED:{text}]"

    def fake_nllb_fallback():
        def _fallback(text, src_lang, tgt_lang):
            return f"[NLLB-FALLBACK:{text}]"
        return _fallback

    monkeypatch.setattr(ts, "_translate_chunk", fake_translate_chunk)
    monkeypatch.setattr(ts, "_get_nllb_fallback", fake_nllb_fallback)

    text = "FIRST chunk here.\nSECOND chunk here.\nTHIRD chunk here."
    result = ts.translate(text, SRC, TGT)

    captured = capsys.readouterr()

    # 1. The failure is detected and reported exactly as specified.
    assert "CHUNK 2 FAILED" in captured.out

    # 2. The failed chunk is NOT silently skipped -- its NLLB fallback
    #    output is present in the final result, not blank/missing.
    assert "[NLLB-FALLBACK:SECOND chunk here.]" in result

    # 3. The chunks that succeeded are untouched and still present.
    assert "[TRANSLATED:FIRST chunk here.]" in result
    assert "[TRANSLATED:THIRD chunk here.]" in result

    # 4. Chunk order is preserved in the combined output.
    first_pos = result.index("FIRST")
    second_pos = result.index("SECOND")
    third_pos = result.index("THIRD")
    assert first_pos < second_pos < third_pos

    # 5. All 3 source chunks were actually attempted (none skipped upstream).
    assert len(calls) == 3


def test_chunk_failure_falls_back_to_original_text_when_nllb_also_fails(monkeypatch, capsys):
    """Both IT2 and the NLLB fallback fail for one chunk -- the ORIGINAL
    untranslated text for that chunk must still appear in the output
    (never silently dropped to nothing), per the original-text-preserving
    requirement."""
    _patch_load_models_noop(monkeypatch)

    def fake_translate_chunk(text, src_lang, tgt_lang):
        if "SECOND" in text:
            raise RuntimeError("simulated IT2 failure")
        return f"[TRANSLATED:{text}]"

    def fake_nllb_fallback():
        def _fallback(text, src_lang, tgt_lang):
            raise RuntimeError("simulated NLLB failure too")
        return _fallback

    monkeypatch.setattr(ts, "_translate_chunk", fake_translate_chunk)
    monkeypatch.setattr(ts, "_get_nllb_fallback", fake_nllb_fallback)

    text = "FIRST chunk here.\nSECOND chunk here.\nTHIRD chunk here."
    result = ts.translate(text, SRC, TGT)
    captured = capsys.readouterr()

    assert "CHUNK 2 FAILED" in captured.out
    # Original (untranslated) text for the failed chunk survives verbatim.
    assert "SECOND chunk here." in result
    assert "[TRANSLATED:FIRST chunk here.]" in result
    assert "[TRANSLATED:THIRD chunk here.]" in result


def test_api_does_not_falsely_report_full_success_when_a_chunk_failed(monkeypatch, capsys):
    """The stdout log for a request with a failed chunk must be
    distinguishable from a fully-successful request -- callers/observability
    tooling watching the logs should never see a clean run when a chunk
    actually failed."""
    _patch_load_models_noop(monkeypatch)

    def fake_translate_chunk(text, src_lang, tgt_lang):
        if "BAD" in text:
            raise RuntimeError("simulated failure")
        return f"[OK:{text}]"

    monkeypatch.setattr(ts, "_translate_chunk", fake_translate_chunk)
    monkeypatch.setattr(ts, "_get_nllb_fallback", lambda: (lambda t, s, g: f"[FB:{t}]"))

    ts.translate("GOOD chunk one.\nBAD chunk two.", SRC, TGT)
    captured = capsys.readouterr()
    assert "CHUNK 2 FAILED" in captured.out

    # Contrast: an all-success run must NOT print any "CHUNK X FAILED".
    ts.translate("GOOD chunk one.\nGOOD chunk two.", SRC, TGT)
    captured2 = capsys.readouterr()
    assert "FAILED" not in captured2.out
