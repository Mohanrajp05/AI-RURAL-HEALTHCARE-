"""
Four-tier LLM fallback router for the AI chat assistant.

  Tier 1 (primary):   Portkey AI Gateway -- Model Catalog provider slugs
                      (all targets from .env) with automatic failover
                      (cloud first, fixes mid-stream breaks)
  Tier 2 (fallback):  Biomistral via local Ollama (free, private)
  Tier 3 (fallback):  tinyllama (lighter, faster, still local and free)
  Tier 4 (last):      keyword/difflib knowledge-base lookup only (no LLM)

The pipeline calls `get_response()`; it returns a tuple of
(response_text, model_used, tier) so callers can surface which tier
answered (e.g. for a subtle UI badge) without exposing raw internals.

KB grounding: `user_message` carries the KB-grounded context composed
upstream (chatbot_pipeline), so every tier -- including Portkey -- answers
from the same verified information instead of inventing facts.
"""

import difflib
import os
import re

print("[llm_router-import] importing requests", flush=True)
import requests
print("[llm_router-import] requests imported", flush=True)

# `from portkey_ai import Portkey` used to sit here, at module top. On
# Render this import was observed taking a very long time (minutes+,
# vs. ~2.5s locally) -- almost certainly CPU/memory contention from
# torch/transformers already being loaded in the same process by the time
# chatbot_pipeline -> llm_router gets imported. Since llm_router is itself
# imported synchronously inside the load_heavy_resources() background
# thread (see app.py), a slow portkey_ai import there delayed
# CHAT_MODULES_READY (and therefore /chat) by however long it took,
# with zero visibility into whether it was progressing or truly stuck.
# The Portkey class is only ever instantiated in call_portkey() below, so
# the import is deferred there instead -- llm_router's own import becomes
# fast and unconditional, and the (still slow) portkey_ai import happens
# lazily on the first real Portkey-tier call, off the startup critical
# path, where a stall no longer blocks chat entirely (Tier 2/3 Ollama and
# Tier 4 KB-lookup fallbacks still work without it).

from doc_chunker import retrieve_relevant_chunks

import portkey_circuit
from portkey_config import (
    PORTKEY_API_KEY,
    PORTKEY_MODELS,
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
KB_DIR = os.path.join(BASE_DIR, "knowledge_base")

OLLAMA_URL = "http://127.0.0.1:11434/api/chat"
PRIMARY_MODEL = "cniongolo/biomistral:latest"
FALLBACK_MODEL = "tinyllama"
TIMEOUT_SECONDS = 20
# Kept short: with portkey_circuit capping how many targets are tried per
# call, worst-case added latency is MAX_TARGETS_PER_CALL * this value.
PORTKEY_TIMEOUT_SECONDS = 10

# Groq fast-inference tier for file-grounded Q&A (OpenAI-compatible API,
# called directly -- raw speed is the point here, Portkey failover is not).
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_DEFAULT_MODEL = "llama-3.3-70b-versatile"

# Reasoning models (e.g. gemini-3.1-flash-lite) burn part of max_tokens on
# invisible reasoning before emitting visible text; a small budget can
# leave them with nothing left to answer with. Floor the Portkey budget
# (same value as portkey_llm._PORTKEY_MIN_MAX_TOKENS) so a short answer
# still fits under the cap.
_PORTKEY_MIN_MAX_TOKENS = 1200

_KB_INDEX_CACHE = None


def _build_messages(system_prompt, messages, user_message):
    """Shared payload assembly for every LLM tier.

    `user_message` already contains any KB-grounded context (composed
    upstream in chatbot_pipeline), so local Ollama and cloud Portkey calls
    are grounded identically -- no duplicated injection logic.
    """
    payload_messages = [{"role": "system", "content": system_prompt}]
    payload_messages.extend(messages[-10:])
    payload_messages.append({"role": "user", "content": user_message})
    return payload_messages


# ---------------------------------------------------------------------------
# Tier 1/2 -- Ollama chat via the HTTP API
# ---------------------------------------------------------------------------
def call_ollama(model, system_prompt, messages, user_message, timeout=TIMEOUT_SECONDS):
    """Call a single Ollama model. Returns text or None on any failure."""
    try:
        resp = requests.post(
            OLLAMA_URL,
            json={
                "model": model,
                "messages": _build_messages(system_prompt, messages, user_message),
                "stream": False,
                "options": {"temperature": 0.3, "num_predict": 500},
            },
            timeout=timeout,
        )
        if resp.status_code == 200:
            text = resp.json().get("message", {}).get("content", "").strip()
            return text if text else None
        return None
    except (requests.exceptions.Timeout, requests.exceptions.ConnectionError, Exception) as e:
        print(f"[llm_router] {model} failed: {e}")
        return None


# ---------------------------------------------------------------------------
# Tier 3 -- Portkey AI Gateway: slug-based targets, automatic failover
# ---------------------------------------------------------------------------
def call_portkey(system_prompt, messages, user_message, timeout=PORTKEY_TIMEOUT_SECONDS):
    """Call the cloud tier via the Portkey AI Gateway with client-side failover.

    Uses Model Catalog provider slugs: each target's model string is
    "@{slug}/{model}" (built in portkey_config from .env), so no
    virtual_key or per-provider keys are involved -- the gateway resolves
    the provider from the slug embedded in the model string.

    This account has Portkey's "block_inline_config" setting enabled, which
    rejects an inline `config` (strategy/targets) payload, so the fallback
    across targets is done here in Python instead of server-side: each
    target's model string is tried in turn (no `config` param sent at all)
    until one returns a non-empty response. Returns (text, model_used) or
    (None, None) if every target failed.
    """
    if not PORTKEY_MODELS or not PORTKEY_API_KEY:
        print("[llm_router] Portkey has no configured targets, skipping")
        return None, None
    if portkey_circuit.circuit_is_open():
        # Gateway was unreachable/exhausted recently -- skip straight to the
        # local Ollama tiers instead of hanging on a doomed sweep again.
        print("[llm_router] Portkey breaker OPEN, skipping to local fallback")
        return None, None
    # Deferred from module top -- see the comment above the imports at the
    # top of this file. First call pays the (possibly slow) import cost;
    # every call after that is a cached, instant re-import.
    from portkey_ai import Portkey
    client = Portkey(api_key=PORTKEY_API_KEY)
    payload_messages = _build_messages(system_prompt, messages, user_message)
    for model in portkey_circuit.pick_targets(PORTKEY_MODELS):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=payload_messages,
                max_tokens=_PORTKEY_MIN_MAX_TOKENS,
                temperature=0.3,  # matches the earlier hallucination fix
                timeout=timeout,
            )
            finish_reason = getattr(response.choices[0], "finish_reason", None)
            text = (response.choices[0].message.content or "").strip()
            if not text or finish_reason == "length":
                # A truncated reply (hit the token cap) is NOT a success --
                # treat it like a failure so the next target (e.g. DeepSeek)
                # actually gets tried instead of serving cut-off garbage.
                print(f"[llm_router] Portkey target {model} truncated "
                      f"(finish_reason={finish_reason}), trying next target")
                continue
            portkey_circuit.record_success()
            return text, getattr(response, "model", None) or model
        except Exception as e:
            print(f"[llm_router] Portkey target {model} failed: {type(e).__name__}: {e}")
            continue
    portkey_circuit.record_all_failed()
    return None, None


# ---------------------------------------------------------------------------
# Groq fast tier -- used ONLY for file-grounded Q&A (not regular chat)
# ---------------------------------------------------------------------------
def call_groq(system_prompt, context_chunks, user_message, timeout=10):
    """Call Groq's fast-inference API directly (OpenAI-compatible endpoint).

    No new dependency: `requests` is already used by the Ollama tier. The
    API key is read from the environment (GROQ_API_KEY) -- never hardcoded.
    Returns text or None on any failure (missing key, HTTP error, timeout).
    """
    api_key = os.environ.get("GROQ_API_KEY", "").strip()
    if not api_key:
        print("[llm_router] GROQ_API_KEY not set in .env, skipping Groq tier")
        return None
    model = os.environ.get("GROQ_MODEL", GROQ_DEFAULT_MODEL).strip() or GROQ_DEFAULT_MODEL
    prompt = f"Relevant excerpts:\n\n{context_chunks}\n\nQuestion: {user_message}"
    try:
        resp = requests.post(
            GROQ_API_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt},
                ],
                "max_tokens": 500,
                "temperature": 0.3,
            },
            timeout=timeout,
        )
        if resp.status_code == 200:
            data = resp.json()
            content = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
            return str(content or "").strip() or None
        print(f"[llm_router] Groq HTTP {resp.status_code}: {resp.text[:200]}")
        return None
    except Exception as exc:
        print(f"[llm_router] Groq call failed: {exc}")
        return None


def handle_file_question(user_message, chunks, system_prompt):
    """File-grounded Q&A: retrieve only the relevant chunks, then answer.

    Groq (fast inference) is tried FIRST for this use case -- raw speed is
    the bottleneck, not provider failover. If Groq is unavailable, the
    normal Ollama -> Portkey -> KB chain answers instead, still grounded on
    the retrieved (not full) context.

    Returns (response_text, tier) where tier is "groq" or one of
    get_response()'s tiers ("portkey" | "fallback" | "kb_only" | "failed").
    """
    relevant = retrieve_relevant_chunks(user_message, chunks)
    context = "\n\n---\n\n".join(relevant)
    result = call_groq(system_prompt, context, user_message)
    if result:
        print("[llm_router] FILE QUESTION answered by GROQ "
              f"({len(context)} chars of retrieved context)")
        return result, "groq"

    print("[llm_router] FILE QUESTION Groq unavailable -- falling back to tier chain")
    text, _model, tier = get_response(
        system_prompt,
        [],
        f"Context:\n{context}\n\nQuestion: {user_message}",
    )
    return text, tier


# ---------------------------------------------------------------------------
# Tier 4 -- knowledge_base/*.txt keyword/difflib lookup (no LLM)
# ---------------------------------------------------------------------------
def _kb_index():
    """Build {disease_name: {"path": ..., "slug": ...}} once for the process."""
    global _KB_INDEX_CACHE
    if _KB_INDEX_CACHE is not None:
        return _KB_INDEX_CACHE
    index = {}
    if os.path.isdir(KB_DIR):
        for fname in os.listdir(KB_DIR):
            if not fname.lower().endswith(".txt"):
                continue
            path = os.path.join(KB_DIR, fname)
            disease_name = None
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                    for line in fh:
                        m = re.match(r"^\s*#\s*(.+?)\s*$", line)
                        if m:
                            disease_name = m.group(1).strip()
                            break
            except OSError:
                continue
            if not disease_name:
                disease_name = os.path.splitext(fname)[0].replace("_", " ").strip().title()
            index[disease_name] = {"path": path, "slug": fname}
    _KB_INDEX_CACHE = index
    return index


_SECTION_RE = re.compile(r"^\s*##\s*(.+?)\s*$")
_KB_FIELDS = [
    "symptoms",
    "causes",
    "prevention",
    "treatment",
    "diet recommendations",
    "when to see a doctor",
]
_KB_FIELD_NAMES = {
    "symptoms": "Symptoms",
    "causes": "Causes",
    "prevention": "Prevention",
    "treatment": "Treatment",
    "diet recommendations": "Diet Recommendations",
    "when to see a doctor": "When to See a Doctor",
}


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]", " ", (text or "").lower()).strip()


def _pick_disease_file(user_message: str):
    """Return the disease name whose KB file best matches the user message."""
    index = _kb_index()
    if not index:
        return None
    query_norm = _normalize(user_message)
    disease_names = list(index.keys())
    norm_to_name = {_normalize(d): d for d in disease_names}

    close = difflib.get_close_matches(query_norm, list(norm_to_name.keys()), n=1, cutoff=0.45)
    if close:
        return norm_to_name[close[0]]

    # Token-level scoring: prefer longer, rarer tokens shared with the query.
    query_tokens = set(query_norm.split())
    if not query_tokens:
        return None
    best_name = None
    best_score = 0
    for norm, name in norm_to_name.items():
        name_tokens = set(norm.split())
        shared = query_tokens & name_tokens
        score = sum(len(tok) for tok in shared if len(tok) > 2)
        if score > best_score:
            best_score = score
            best_name = name
    if best_score >= 4:
        return best_name
    return None


def _read_kb_sections(path: str) -> dict:
    """Parse a KB .txt file into {field_key: text} for the standard fields."""
    sections = {}
    current = None
    current_lines = []
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as fh:
            lines = fh.readlines()
    except OSError:
        return sections

    def flush():
        if current and current_lines:
            body = " ".join(ln.strip() for ln in current_lines if ln.strip())
            sections[current] = body

    for line in lines:
        m = _SECTION_RE.match(line)
        if m:
            flush()
            heading = _normalize(m.group(1))
            matched = next((f for f in _KB_FIELDS if f in heading), None)
            current = matched if matched else None
            current_lines = []
        elif current:
            current_lines.append(line)
    flush()
    return sections


def search_knowledge_base(user_message):
    """Return a plain-text KB excerpt for the user message, or None."""
    disease = _pick_disease_file(user_message)
    if not disease:
        return None
    path = _kb_index()[disease]["path"]
    sections = _read_kb_sections(path)
    if not sections:
        return None

    lines = [f"{disease}"]
    for field in _KB_FIELDS:
        body = sections.get(field, "").strip()
        if body and "information not found" not in body.lower():
            lines.append("")
            lines.append(f"{_KB_FIELD_NAMES[field]}:")
            lines.append(body)
    if len(lines) == 1:
        return None
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------
def get_response(system_prompt, messages, user_message, kb_query=None):
    """
    Returns (response_text, model_used, tier).
    tier: "portkey" | "fallback" | "kb_only" | "failed"

    `kb_query` is the raw user question used for the Tier 4 KB lookup when
    `user_message` is a full prompt (history+context+question) rather than
    the bare question.
    """
    # Tier 1 -- PRIMARY: Portkey gateway, Gemini 3.1 Flash Lite -> DeepSeek R1 8B.
    # Cloud first with built-in provider failover (fixes mid-stream breaks);
    # skipped silently until real API keys are set in backend/.env.
    result, model_used = call_portkey(system_prompt, messages, user_message)
    if result:
        print(f"[llm_router] TIER 1 (portkey) answered, model={model_used}")
        return result, model_used or "portkey", "portkey"

    # Tier 2 -- local Biomistral fallback (free, private, KB-grounded)
    result = call_ollama(PRIMARY_MODEL, system_prompt, messages, user_message)
    if result:
        print(f"[llm_router] TIER 2 (biomistral) answered, model={PRIMARY_MODEL}")
        return result, PRIMARY_MODEL, "fallback"

    # Tier 3 -- lighter local fallback model
    result = call_ollama(FALLBACK_MODEL, system_prompt, messages, user_message, timeout=15)
    if result:
        print(f"[llm_router] TIER 3 (tinyllama) answered, model={FALLBACK_MODEL}")
        return result, FALLBACK_MODEL, "fallback"

    # Tier 4 -- KB-only, no LLM
    kb_result = search_knowledge_base(kb_query if kb_query else user_message)
    if kb_result:
        return (
            "I couldn't reach my AI engine, but here's what I found in "
            "my health knowledge base:\n\n" + kb_result,
            "knowledge_base",
            "kb_only",
        )

    # All 4 tiers failed
    return (
        "I'm having trouble connecting to my AI engine right now, and "
        "couldn't find a matching entry in my health knowledge base. "
        "Please try again shortly, or contact support if this continues.",
        None,
        "failed",
    )


if __name__ == "__main__":
    import sys

    q = sys.argv[1] if len(sys.argv) > 1 else "what are the symptoms of malaria"
    text, model, tier = get_response(
        "You are a helpful rural healthcare assistant. Answer concisely and safely.",
        [],
        q,
    )
    print(f"TIER: {tier}  MODEL: {model}\n")
    print(text)
