"""Portkey Gateway LLM integration.

Routes chat completions through the Portkey AI Gateway when Model Catalog
provider slugs are configured (see portkey_config.py), falling back to
local Ollama otherwise. This module shares its client config with
llm_router.py -- both build the request from PORTKEY_CONFIG/PORTKEY_API_KEY
in portkey_config.py, so there is exactly one place that knows how to talk
to Portkey (Model Catalog "@slug/model" targets, no virtual keys).

Configuration lives in backend/.env and is documented in portkey_config.py:
    PORTKEY_GATEWAY_KEY / PORTKEY_API_KEY   - Portkey gateway API key
    PORTKEY_SLUGS + PORTKEY_MODEL           - comma-separated slugs, shared model
    PORTKEY_<NAME>_SLUG + PORTKEY_<NAME>_MODEL - per-provider pairs (optional)
"""

from env_loader import load_env_file

load_env_file()

import portkey_circuit  # noqa: E402
from portkey_config import PORTKEY_API_KEY, PORTKEY_MODELS  # noqa: E402

DEFAULT_OLLAMA_MODEL = "cniongolo/biomistral:latest"

# Some Model Catalog targets (e.g. gemini-3.6-flash) are reasoning models
# that spend part of max_tokens on invisible reasoning before emitting
# visible text. Callers here (translation, symptom-mapping) pass small
# budgets (80/150) tuned for local Ollama, which is enough for Ollama but
# leaves Gemini with nothing left to answer with -- it then returns a
# truncated-but-non-empty reply (e.g. "Mal") that silently passes the
# `if content:` check. Floor only the Portkey request's budget so those
# callers keep their original, smaller num_predict for the Ollama branch.
#
# Measured live: a trivial one-sentence question burned 354-375 reasoning
# tokens on gemini-3.6-flash before any visible text -- 400 still truncated
# (MAX_TOKENS, no visible answer beyond a few words). 1200 left enough
# headroom for a full short answer (finish_reason STOP). Harder prompts may
# burn more reasoning tokens and could still truncate; raise this further
# (or disable Gemini's thinking budget via provider-specific params) if
# that shows up in practice.
_PORTKEY_MIN_MAX_TOKENS = 1200


def portkey_enabled() -> bool:
    return bool(PORTKEY_API_KEY and PORTKEY_MODELS)


def call_portkey(
    messages,
    *,
    temperature: float = 0.3,
    max_tokens: int = 150,
    timeout: float = 15.0,
) -> str | None:
    """Call the LLM via the Portkey AI Gateway. Returns text content or None on failure.

    Uses Model Catalog provider slugs (PORTKEY_MODELS, each "@slug/model")
    -- no virtual_key, no per-provider headers. This account blocks inline
    `config` payloads, so targets are tried one at a time client-side
    (same approach as llm_router.call_portkey) instead of relying on
    Portkey's server-side fallback strategy.
    """
    if not portkey_enabled():
        return None
    if portkey_circuit.circuit_is_open():
        # Gateway was unreachable/exhausted recently -- skip straight to
        # Ollama instead of hanging on a doomed sweep again.
        print("[PORTKEY] breaker OPEN, skipping to local fallback")
        return None
    from portkey_ai import Portkey

    client = Portkey(api_key=PORTKEY_API_KEY)
    effective_max_tokens = max(max_tokens, _PORTKEY_MIN_MAX_TOKENS)
    for model in portkey_circuit.pick_targets(PORTKEY_MODELS):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                max_tokens=effective_max_tokens,
                temperature=temperature,
                timeout=timeout,
            )
            content = str(response.choices[0].message.content or "").strip()
            if content:
                portkey_circuit.record_success()
                return content
        except Exception as exc:
            print(f"[PORTKEY] target {model} failed: {type(exc).__name__}: {exc}")
            continue
    portkey_circuit.record_all_failed()
    return None


def chat(
    messages,
    *,
    ollama_model: str = DEFAULT_OLLAMA_MODEL,
    temperature: float = 0.3,
    num_predict: int = 80,
    timeout: float = 30.0,
) -> tuple:
    """Get a completion via Portkey Gateway when configured, else Ollama.

    Returns (content: str | None, source: str). source is "portkey" or "ollama".
    """
    if portkey_enabled():
        content = call_portkey(
            messages,
            temperature=temperature,
            max_tokens=num_predict,
            timeout=timeout,
        )
        if content:
            return content, "portkey"

    try:
        import ollama

        response = ollama.chat(
            model=ollama_model,
            messages=messages,
            stream=False,
            options={"num_predict": num_predict, "temperature": temperature},
            timeout=timeout,
        )
    except Exception as exc:
        print(f"[LLM] Ollama chat failed: {exc}")
        return None, "ollama"

    content = ""
    if isinstance(response, dict):
        msg = response.get("message")
        if isinstance(msg, dict):
            content = str(msg.get("content") or "")
        elif msg is not None and hasattr(msg, "content"):
            content = str(getattr(msg, "content") or "")
        if not content:
            content = str(response.get("response") or "")
    else:
        msg = getattr(response, "message", None)
        if msg is not None:
            content = str(getattr(msg, "content", "") or "")
    return (content.strip() or None), "ollama"
