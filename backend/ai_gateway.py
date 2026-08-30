
import hashlib
import json
import os
import re
import time
import unicodedata
import uuid
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOGS_DIR = os.path.join(BASE_DIR, "logs")
LOG_FILE = os.path.join(LOGS_DIR, "chat_log.json")

# ---------------------------------------------------------------------------
# 3a. RATE LIMITING
# ---------------------------------------------------------------------------

MAX_PER_SESSION_PER_HOUR = 20
MAX_PER_IP_PER_MINUTE = 5
RATE_LIMIT_MESSAGE = "You have sent too many messages. Please wait a moment before asking again."

# Global kill-switch for rate limiting. When False, rate_limit_check always
# allows the request (chat is unlimited). Set True to re-enable the per-minute
# and per-hour limits below before any real deployment.
RATE_LIMITING_ENABLED = False

_ratelimit_store: dict = {}  # key -> list of timestamps (seconds)


def _prune_store(now: float) -> None:
    for key in list(_ratelimit_store):
        timestamps = [t for t in _ratelimit_store[key] if now - t < 3600]
        if timestamps:
            _ratelimit_store[key] = timestamps
        else:
            _ratelimit_store.pop(key, None)


def rate_limit_check(ip: str, _clock=None) -> dict:
    """Check whether an IP may send a chat request.

    Returns {"allowed": bool, "response": str | None}.
    `ip` is the client address (request.remote_addr); the same key is used for
    both the per-minute and the per-hour limits. `_clock` allows the evals to
    inject fake timestamps for deterministic testing.
    """
    if not RATE_LIMITING_ENABLED:
        return {"allowed": True, "response": None}

    now = float(_clock() if _clock else time.time())
    _prune_store(now)

    ip_key = f"ip:{ip}"
    per_minute = [t for t in _ratelimit_store.get(ip_key, []) if now - t < 60]
    per_hour = [t for t in _ratelimit_store.get(ip_key, []) if now - t < 3600]

    if len(per_minute) >= MAX_PER_IP_PER_MINUTE or len(per_hour) >= MAX_PER_SESSION_PER_HOUR:
        return {"allowed": False, "response": RATE_LIMIT_MESSAGE}

    _ratelimit_store.setdefault(ip_key, []).append(now)
    return {"allowed": True, "response": None}


# ---------------------------------------------------------------------------
# 3b. INPUT SANITIZATION
# ---------------------------------------------------------------------------

MAX_INPUT_CHARS = 1000
TRIM_NOTE = "Your message was trimmed to 1000 characters."

_HTML_TAG_RE = re.compile(r"<[^>]*>")
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def sanitize_input(raw_text: str) -> dict:
    """Clean user input before it reaches the LLM.

    Returns {"text": str, "trimmed": bool} — text has no HTML, no control
    characters, normalized unicode, and is at most 500 characters.
    """
    text = unicodedata.normalize("NFKC", str(raw_text or ""))
    text = _HTML_TAG_RE.sub(" ", text)
    text = _CONTROL_CHAR_RE.sub("", text)
    trimmed = len(text) > MAX_INPUT_CHARS
    if trimmed:
        text = text[:MAX_INPUT_CHARS].strip()
    return {"text": text, "trimmed": trimmed}


# ---------------------------------------------------------------------------
# 3c. OUTPUT SANITIZATION
# ---------------------------------------------------------------------------

MAX_OUTPUT_TOKENS = 1000

_SCRIPT_RE = re.compile(
    r"<script[^>]*>.*?</script>|<script[^>]*>|</script>|\beval\s*\(|\bexec\s*\(", re.I | re.S
)
_OUTPUT_HTML_RE = re.compile(r"<[^>]*>")


def sanitize_output(raw_text: str) -> str:
    """Clean LLM output before it is sent to the frontend."""
    text = str(raw_text or "")
    text = _SCRIPT_RE.sub(" ", text)
    text = _OUTPUT_HTML_RE.sub(" ", text)
    text = _CONTROL_CHAR_RE.sub("", text)
    words = text.split()
    if len(words) > MAX_OUTPUT_TOKENS:
        text = " ".join(words[:MAX_OUTPUT_TOKENS]) + "..."
    return text.strip()


# ---------------------------------------------------------------------------
# 3d. REQUEST LOGGING
# ---------------------------------------------------------------------------

def _hash_ip(ip: str) -> str:
    return hashlib.sha256(str(ip or "").encode("utf-8")).hexdigest()


def log_request(
    *,
    ip: str,
    message_length: int,
    guardrail_triggered: bool,
    guardrail_type: str | None,
    response_time_ms: float,
    was_blocked: bool,
    cache_hit: bool = False,
    model_tier: str | None = None,
    model_used: str | None = None,
) -> None:
    """Append a privacy-safe entry to backend/logs/chat_log.json.

    Never logs the message content or the response content.
    `model_tier`/`model_used` are optional metadata recording which fallback
    tier and model actually answered ("portkey", "fallback", "kb_only",
    "failed", ...).
    """
    try:
        os.makedirs(LOGS_DIR, exist_ok=True)
        entry = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "ip_hash": _hash_ip(ip),
            "message_length": int(message_length),
            "guardrail_triggered": bool(guardrail_triggered),
            "guardrail_type": guardrail_type,
            "response_time_ms": round(float(response_time_ms), 2),
            "was_blocked": bool(was_blocked),
            "cache_hit": bool(cache_hit),
            "model_tier": model_tier,
            "model_used": model_used,
        }
        entries: list = []
        if os.path.exists(LOG_FILE):
            try:
                with open(LOG_FILE, "r", encoding="utf-8") as handle:
                    entries = json.load(handle)
            except (json.JSONDecodeError, OSError):
                entries = []
        entries.append(entry)
        with open(LOG_FILE, "w", encoding="utf-8") as handle:
            json.dump(entries, handle, indent=2)
    except Exception as exc:  # noqa: BLE001 — logging must never crash the chat
        print(f"[AI-GATEWAY] log_request failed: {exc}")


# ---------------------------------------------------------------------------
# 3e. SESSION MANAGEMENT
# ---------------------------------------------------------------------------

SESSION_IDLE_SECONDS = 30 * 60
SESSION_CONTEXT_WINDOW = 10

_sessions: dict = {}  # session_id -> {"messages": [...], "last_active": float}


def resolve_session(session_id: str | None) -> str:
    """Return the given session_id, or create a fresh UUID session."""
    sid = str(session_id or "").strip()
    if not sid:
        sid = str(uuid.uuid4())
    if sid not in _sessions:
        _sessions[sid] = {"messages": [], "last_active": time.time()}
    return sid


def expire_idle_sessions(_clock=None) -> int:
    """Clear contexts idle for more than 30 minutes. Returns count cleared."""
    now = float(_clock() if _clock else time.time())
    expired = [sid for sid, data in _sessions.items() if now - data["last_active"] > SESSION_IDLE_SECONDS]
    for sid in expired:
        _sessions.pop(sid, None)
    return len(expired)


def push_session_message(session_id: str, user_message: str, reply: str) -> None:
    """Keep the 10 most recent (user, assistant) turns for the session."""
    data = _sessions.setdefault(session_id, {"messages": [], "last_active": time.time()})
    data["messages"].append({"user": user_message, "assistant": reply})
    data["messages"] = data["messages"][-SESSION_CONTEXT_WINDOW:]
    data["last_active"] = time.time()


def get_session_context(session_id: str) -> list:
    data = _sessions.get(session_id)
    return list(data["messages"]) if data else []


def session_stats() -> dict:
    now = time.time()
    return {
        "active_sessions": len(_sessions),
        "idle_cleared": expire_idle_sessions(),
        "oldest_idle_minutes": (
            round((now - min(d["last_active"] for d in _sessions.values())) / 60.0, 1)
            if _sessions
            else 0
        ),
    }


if __name__ == "__main__":
    print("ai_gateway standalone sanity test")
    print("-" * 50)
    # Rate limit: 5/min -> 6th call blocked
    for i in range(7):
        result = rate_limit_check("127.0.0.1")
        print(f"  message {i + 1}: {'allowed' if result['allowed'] else 'BLOCKED'}")
    clean = sanitize_input("  <b>Hello</b>  123-45-6789\x00\u03bb   ")
    print("sanitized:", clean)
    out = sanitize_output("<script>alert(1)</script> Safe advice.")
    print("output:", repr(out))
    sid = resolve_session(None)
    print("new session:", sid)
    print("session stats:", session_stats())
    print("log path:", LOG_FILE)