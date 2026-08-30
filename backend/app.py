import os
import sys
import time
import smtplib
import tempfile
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import json
import hashlib
import pickle
import uuid
import difflib
from datetime import datetime, timedelta
import pandas as pd
from collections import deque
import re
import warnings
from pathlib import Path
from flask import Flask, request, jsonify, Response
from flask_cors import CORS

import psutil
import time
import threading
from threading import Timer
try:
    from sendgrid import SendGridAPIClient
    from sendgrid.helpers.mail import Mail
except Exception:
    SendGridAPIClient = None
    Mail = None

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from chatbot_response import (
    chatbot_response,
    clear_chat_session,
    _set_session_disease,
    _get_session_disease,
    _SESSION_HISTORY,
    update_disease_context,
)
from chatbot_pipeline import multilingual_chatbot as pipeline_process_query
from chatbot_pipeline import new_diag as _new_pipeline_diag
from chatbot_pipeline import run_startup_selftest as _run_pipeline_selftest

# NEW guarded disease-prediction pipeline (40-symptom MultiLabelBinarizer +
# RF/NB/SVM ensemble). Imports are lazy-safe: appliance only loads the
# .joblib files when the pipeline is first invoked.
from predict_disease_guarded import (
    predict_guarded as _new_predict_guarded,
    CHECKBOX_TO_SYMPTOMS as _NEW_CHECKBOX_TO_SYMPTOMS,
    TOKEN_TO_CHECKBOX as _NEW_TOKEN_TO_CHECKBOX,
    load_models as _new_load_models,
)

# Risk-level classification: disease clinical severity x model confidence.
from risk_classification import compute_risk_level as _compute_risk_level

# Chat security & quality layers: guardrails (input blocking), AI gateway
# (rate limiting, sanitization, logging, sessions) and response caching.
import guardrails
import ai_gateway
import cache as chat_cache
import portkey_llm

from file_extractor import (
    extract_text as _extract_file_text,
    resolve_file_type as _resolve_file_type,
    FileExtractionError as _FileExtractionError,
)

# Hospital search (OpenStreetMap: Overpass API + Nominatim): explicit
# "nearest hospital" requests and the high-risk-assessment-upload offer.
# See /hospitals/nearby below.
import hospital_search

# MySQL is the only database this app talks to (MongoDB was removed --
# patients, legacy_users, chat_conversations/messages/usage, rag_chat_log
# and feedback all live here). See mysql_store.py for the full table list.
# Registered-user info (Admin Dashboard) is NOT stored here -- it's read
# straight from Supabase Auth via supabase_admin.py (see /api/users).
import mysql_store
import supabase_admin

# Uploaded chat attachments are stored temporarily under backend/uploads/
# (deleted automatically after 24 hours; only the extracted text persists in
# chat history).
UPLOAD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploads")
UPLOAD_MAX_BYTES = 10 * 1024 * 1024  # 10 MB
UPLOAD_MAX_AGE_SECONDS = 24 * 60 * 60

os.makedirs(UPLOAD_DIR, exist_ok=True)

# .env has always used SMTP_EMAIL/SMTP_HOST (see Sender/SMTP provider
# settings), but this used to read SMTP_USER/SMTP_SERVER -- names that were
# never actually set anywhere, so SMTP_USER silently fell back to the
# "your_email@gmail.com" placeholder below and the guard at the mail-send
# call site (which explicitly checks for that placeholder) kept the whole
# feedback-email feature disabled even with real, correct credentials in
# .env. SMTP_USER/SMTP_SERVER are still checked second, in case anything
# was relying on those names instead.
SMTP_USER = os.environ.get("SMTP_EMAIL") or os.environ.get("SMTP_USER", "your_email@gmail.com")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "your_app_password")
FEEDBACK_TO_EMAIL = os.environ.get("FEEDBACK_TO_EMAIL", "info@ruralhealthcare.com")
SMTP_SERVER = os.environ.get("SMTP_HOST") or os.environ.get("SMTP_SERVER", "smtp.gmail.com")
try:
    SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
except ValueError:
    SMTP_PORT = 587

app = Flask(__name__)
CORS(app)

SENDGRID_API_KEY = os.environ.get("SENDGRID_API_KEY")


def limit_response_words(text: str, max_words: int = 300) -> str:
    if not text:
        return text
    words = str(text).split()
    if len(words) <= max_words:
        return text
    truncated = " ".join(words[:max_words])
    last_period = max(truncated.rfind('.'), truncated.rfind('!'), truncated.rfind('?'))
    if last_period != -1:
        return truncated[: last_period + 1]
    else:
        return truncated + '.'

# ===== MEMORY MANAGEMENT & CRASH PREVENTION =====
MAX_SESSIONS = 100  # Limit total sessions to prevent unbounded memory growth

def cleanup_old_sessions():
    """Remove sessions that haven't been used in 30 minutes."""
    from chatbot_response import _SESSION_LAST_DISEASE
    now = datetime.now()
    expired = [
        sid for sid, data in _SESSION_LAST_DISEASE.items()
        if (now - data.get("timestamp", now)).seconds > 1800
    ]
    for sid in expired:
        _SESSION_LAST_DISEASE.pop(sid, None)
        _SESSION_HISTORY.pop(sid, None)
        _SESSION_LAST_RESPONSE.pop(sid, None)
        _MISTRAL_LAST_CALL.pop(sid, None)
        _SESSION_HOSPITAL_OFFER_SHOWN.discard(sid)
    if expired:
        print(f"[Memory] Cleaned up {len(expired)} expired sessions. Active: {len(_SESSION_LAST_DISEASE)}")

def schedule_cleanup():
    """Schedule cleanup to run every 15 minutes."""
    cleanup_old_sessions()
    Timer(900, schedule_cleanup).daemon = True
    Timer(900, schedule_cleanup).start()

# Start cleanup scheduler at startup
schedule_cleanup()

MAX_INPUT_CHARS = int(os.getenv("MAX_INPUT_CHARS", "500"))

# Per-user (logged-in email -- same Gmail account) response quota: up to
# RESPONSE_LIMIT_PER_WINDOW replies, then a COOLDOWN_HOURS-long cooling-off
# window before the count resets and they can chat again. Anonymous users
# (no email) are never capped -- there's no reliable identity to key a
# quota on. The per-IP rate guardrail (ai_gateway.rate_limit_check) is a
# separate, still-disabled burst/spam guard -- this is the actual usage
# quota requested.
RESPONSE_LIMIT_PER_WINDOW = 30
COOLDOWN_HOURS = 5

_USER_MESSAGE_USAGE: dict = {}  # {user_email: {"count": int, "window_start": iso str}} — in-memory mirror


def _usage_window_expired(window_start_iso: str | None, now: datetime) -> bool:
    if not window_start_iso:
        return True
    try:
        started = datetime.fromisoformat(window_start_iso)
    except (TypeError, ValueError):
        return True
    return (now - started) >= timedelta(hours=COOLDOWN_HOURS)


def _get_usage_entry(user_email: str) -> dict:
    """Return {"count": int, "window_start": iso str} for a user, reading
    through to the persistent store on first access this process."""
    entry = _USER_MESSAGE_USAGE.get(user_email)
    if entry is not None:
        return entry
    entry = _chat_read_usage(user_email)
    _USER_MESSAGE_USAGE[user_email] = entry
    return entry


def _user_response_count(user_email: str) -> int:
    """Current response count within the active window (0 if a previous
    window's cooldown has already elapsed -- checked, but NOT persisted,
    so a read-only status check never itself starts a new window)."""
    entry = _get_usage_entry(user_email)
    if _usage_window_expired(entry.get("window_start"), datetime.now()):
        return 0
    return int(entry.get("count", 0))


def _response_limit_state(user_email: str) -> dict:
    """Read-only: is `user_email` currently blocked by the cooldown, and if
    so, exactly when does it end? Returns
    {"limit_reached": bool, "retry_after_iso": str|None, "count": int}."""
    if not user_email:
        return {"limit_reached": False, "retry_after_iso": None, "count": 0}
    now = datetime.now()
    entry = _get_usage_entry(user_email)
    if _usage_window_expired(entry.get("window_start"), now):
        return {"limit_reached": False, "retry_after_iso": None, "count": 0}
    count = int(entry.get("count", 0))
    if count < RESPONSE_LIMIT_PER_WINDOW:
        return {"limit_reached": False, "retry_after_iso": None, "count": count}
    started = datetime.fromisoformat(entry["window_start"])
    retry_at = started + timedelta(hours=COOLDOWN_HOURS)
    return {"limit_reached": True, "retry_after_iso": retry_at.isoformat(timespec="seconds"), "count": count}


def _increment_user_response_count(user_email: str) -> int:
    """Increment the user's response count and persist it, rolling over to
    a fresh window first if the previous cooldown has already elapsed."""
    now = datetime.now()
    entry = _get_usage_entry(user_email)
    if _usage_window_expired(entry.get("window_start"), now):
        entry = {"count": 0, "window_start": now.isoformat(timespec="seconds")}
    entry["count"] = int(entry.get("count", 0)) + 1
    _USER_MESSAGE_USAGE[user_email] = entry
    _chat_write_usage(user_email, entry)
    return entry["count"]


def _trim_to_word_boundary(text: str, max_chars: int) -> str:
    """Trim text to a max length without cutting in the middle of a word."""
    text = str(text or "")
    if len(text) <= max_chars:
        return text
    clipped = text[:max_chars]
    boundary = re.match(r"^([\s\S]*?)\s+\S*$", clipped)
    return (boundary.group(1) if boundary else clipped).rstrip()

# Mistral rate limiter - prevent overwhelming the API
_MISTRAL_LAST_CALL = {}
MISTRAL_MIN_INTERVAL = 2  # Minimum 2 seconds between Mistral calls

def can_call_mistral(session_id: str) -> bool:
    """Check if enough time has passed since last Mistral call for this session."""
    now = time.time()
    last = _MISTRAL_LAST_CALL.get(session_id, 0)
    if now - last < MISTRAL_MIN_INTERVAL:
        return False
    _MISTRAL_LAST_CALL[session_id] = now
    return True

def call_mistral_with_timeout(prompt: str, timeout_secs: int = 15) -> str:
    """Call Mistral with timeout protection to prevent hanging."""
    try:
        content, _source = portkey_llm.chat(
            [{"role": "user", "content": prompt}],
            temperature=0,
            num_predict=150,
            timeout=timeout_secs,
        )
        return content or "I recommend consulting a nearby doctor or health worker."
    except Exception as e:
        print(f"[Mistral] Error: {e}")
        return "I recommend consulting a nearby doctor or health worker."

# ===== CONVERSATION HISTORY MANAGEMENT =====
MAX_HISTORY = 20  # Keep last 10 user+assistant message pairs (20 messages)

def get_limited_history(session_id: str) -> list:
    """Get only the last MAX_HISTORY exchanges from session history."""
    history = _SESSION_HISTORY.get(session_id, [])
    return history[-MAX_HISTORY:]

def update_history(session_id: str, user_msg: str, bot_msg: str) -> None:
    """Add user and bot messages to history and keep only last MAX_HISTORY exchanges."""
    if session_id not in _SESSION_HISTORY:
        _SESSION_HISTORY[session_id] = []
    _SESSION_HISTORY[session_id].append({"role": "user", "content": user_msg})
    _SESSION_HISTORY[session_id].append({"role": "assistant", "content": bot_msg})
    # Keep only last MAX_HISTORY messages
    _SESSION_HISTORY[session_id] = _SESSION_HISTORY[session_id][-MAX_HISTORY:]

# Last response per session, used by the /ai-chat "translate my last answer" flow.
_SESSION_LAST_RESPONSE = {}  # Store last response per session for translation

# Sessions that have already been shown the high-risk hospital offer
# (Trigger 2) -- asked at most once per conversation regardless of how many
# more files are uploaded or questions asked afterward (spec step 5d).
_SESSION_HOSPITAL_OFFER_SHOWN: set = set()

def detect_language(query: str) -> str:
    """Detect input language (English, Kannada, Hindi, Tamil, Telugu).

    Delegates to the modular :mod:`language_service` so detection logic has a
    single source of truth. Returns an NLLB-style language code.
    """
    from language_service import detect_language as _ls_detect_language
    return _ls_detect_language(query)


def is_translation_query(query: str) -> bool:
    keywords = [
        "translate", "in hindi", "into hindi",
        "hindi me", "hindi mein", "in kannada",
        "into kannada", "kannada alli", "kannadadalli",
        "in tamil", "into tamil", "tamil la", "tamilil",
        "in telugu", "into telugu", "telugu lo", "telugulo",
    ]
    return any(k in str(query or "").lower() for k in keywords)


# ===== HOSPITAL SEARCH (Trigger 1): explicit "find nearby hospitals" =====
# Exact phrases that signal hospital-search intent on their own, without
# needing a separate proximity word (e.g. "find a hospital" already implies
# "near me").
HOSPITAL_SEARCH_KEYWORDS = [
    "find a hospital", "find hospital", "find me a hospital",
    "where can i get treated", "where can i get treatment",
]

# Fallback: a facility word (hospital/clinic/ER) anywhere near a proximity
# word (near/nearby/nearest/...) catches phrasing the exact-phrase list
# above can't anticipate, e.g. "list the hospitals which are near me" or
# "show me clinics around here". Word-boundaried so "er" doesn't match
# inside words like "prefer", and "near" doesn't match "nearly".
_HOSPITAL_FACILITY_RE = re.compile(r"\b(hospitals?|clinics?|emergency room|er)\b")
_HOSPITAL_PROXIMITY_RE = re.compile(
    r"\b(near|nearby|nearest|closest|close|around)\b|in my area|around here|close to me"
)
_HOSPITAL_FACILITY_WORDS = ("hospital", "hospitals", "clinic", "clinics")


def _has_hospital_facility_word(q: str) -> bool:
    if _HOSPITAL_FACILITY_RE.search(q):
        return True
    # Typo-tolerant fallback -- seen live: "what are the nearest hospitsals
    # near me" (transposed letters) missed the exact-word regex entirely
    # and fell through to the generic LLM, which has no location access and
    # just apologizes instead of running the real search. difflib catches
    # transpositions/drops like "hospitsals", "hopsital", "clinik" while a
    # 6-char floor keeps short unrelated words from matching by coincidence.
    return any(
        difflib.get_close_matches(w, _HOSPITAL_FACILITY_WORDS, n=1, cutoff=0.8)
        for w in re.findall(r"[a-z]+", q)
        if len(w) >= 6
    )


def is_hospital_search_query(query: str) -> bool:
    q = str(query or "").lower()
    if any(k in q for k in HOSPITAL_SEARCH_KEYWORDS):
        return True
    return _has_hospital_facility_word(q) and bool(_HOSPITAL_PROXIMITY_RE.search(q))


# Purpose-built medical-urgency keywords for the hospital-search trigger.
# Deliberately NOT guardrails.EMERGENCY_KEYWORDS: that list is specifically
# self-harm/suicide crisis language (see guardrails.py's docstring) and
# contains none of "chest pain", "can't breathe", etc. -- reusing it here
# would silently fail to flag genuine physical emergencies as urgent.
MEDICAL_URGENCY_KEYWORDS = [
    "emergency", "urgent", "severe pain", "unbearable pain",
    "can't breathe", "cannot breathe", "difficulty breathing",
    "chest pain", "severe bleeding", "bleeding heavily", "heavy bleeding",
    "unconscious", "not breathing", "severe injury", "heart attack",
    "stroke", "can't move", "cannot move", "losing consciousness",
]


def is_medical_urgency(query: str) -> bool:
    return any(k in str(query or "").lower() for k in MEDICAL_URGENCY_KEYWORDS)

def translate_text(text: str, src_lang: str, tgt_lang: str) -> str:
    """Translate text using IndicTrans2 with NLLB fallback."""
    from translation_service import translate as _it2_translate
    return _it2_translate(text, src_lang, tgt_lang)

def enforce_language(text: str, lang: str) -> str:
    """Remove non-native script characters to prevent mixed-language output."""
    if lang == "kan_Knda":
        # Keep ONLY Kannada Unicode range U+0C80–U+0CFF
        cleaned = "".join(re.findall(r'[\u0C80-\u0CFF\s.,!?()]', text))
        return cleaned.strip()
    elif lang == "hin_Deva":
        # Keep ONLY Devanagari Unicode range U+0900–U+097F
        cleaned = "".join(re.findall(r'[\u0900-\u097F\s.,!?()]', text))
        return cleaned.strip()
    elif lang == "tam_Taml":
        # Keep ONLY Tamil Unicode range U+0B80–U+0BFF
        cleaned = "".join(re.findall(r'[\u0B80-\u0BFF\s.,!?()]', text))
        return cleaned.strip()
    elif lang == "tel_Telu":
        # Keep ONLY Telugu Unicode range U+0C00–U+0C7F
        cleaned = "".join(re.findall(r'[\u0C00-\u0C7F\s.,!?()]', text))
        return cleaned.strip()
    return text  # English - return as is


def _save_rag_chat_log(entry: dict) -> None:
    """Persist one RAG chat interaction to MySQL. Never raises."""
    try:
        if not mysql_store.rag_log_insert(entry):
            print("[RAG-LOG] MySQL unavailable - chat log not saved")
    except Exception as exc:
        print(f"[RAG-LOG] Failed to save chat log: {exc}")


# ===== CHAT CONVERSATION STORE (persistent, survives restarts) =====
# Conversations and messages are saved to MySQL (chat_conversations /
# chat_messages / chat_usage tables) when it's reachable and to a local
# JSON file otherwise. Nothing in this store is ever auto-expired, cleaned
# up, or deleted except through the explicit DELETE endpoint.
CHAT_STORE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chat_store.json")
_chat_store_lock = threading.Lock()


def _chat_store_load() -> dict:
    """Read the JSON chat store; returns a fresh dict when missing/corrupt."""
    try:
        with open(CHAT_STORE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return {
                "conversations": data.get("conversations") or [],
                "messages": data.get("messages") or [],
                "usage": data.get("usage") or {},
            }
    except Exception:
        pass
    return {"conversations": [], "messages": [], "usage": {}}


def _chat_store_save(data: dict) -> None:
    """Write the JSON chat store atomically under a lock. Never raises.

    Retries the final rename a few times on a transient Windows "Access is
    denied" (WinError 5) -- this repo lives under OneDrive, whose sync
    agent briefly opens/scans a file right after it's written, which can
    make a same-instant os.replace() fail. Without a retry, that failure
    was silently swallowed and the whole write dropped -- for the response-
    quota/chat-cap counters added here, a silently dropped increment could
    let a user's count under-record and exceed the intended limit, so this
    is worth being resilient to rather than accepting on the first failure.
    """
    tmp_path = CHAT_STORE_FILE + ".tmp"
    with _chat_store_lock:
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as exc:
            print(f"[CHAT-STORE] failed to write temp file: {exc}")
            return
        last_exc = None
        for attempt in range(4):
            try:
                os.replace(tmp_path, CHAT_STORE_FILE)
                return
            except OSError as exc:
                last_exc = exc
                if attempt < 3:
                    time.sleep(0.05 * (attempt + 1))
        print(f"[CHAT-STORE] failed to persist chat store after retries: {last_exc}")


def _chat_now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


# Each signed-in user (Supabase user_id -- one per account/Gmail login) may
# keep at most this many saved conversations. Enforced by BLOCKING the
# creation of an 11th (see _would_exceed_conversation_cap, checked upfront
# in ai_chat() before any reply is generated) rather than silently evicting
# an old one -- the user is told plainly to delete a chat first. Guests
# (empty user_id) are never capped here since their conversations aren't
# associated with any listable per-user set anyway (see list_conversations()).
MAX_CONVERSATIONS_PER_USER = 10

CONVERSATION_CAP_MESSAGE = (
    f"You've reached the limit of {MAX_CONVERSATIONS_PER_USER} saved chats per "
    "user. Please delete an old chat from your chat history before starting "
    "a new one. Thank you."
)


def _would_exceed_conversation_cap(session_id: str, user_id: str) -> bool:
    """True when `session_id` would create an (MAX_CONVERSATIONS_PER_USER+1)th
    conversation for `user_id` -- i.e. it's not already one of their
    existing conversations (continuing an existing chat is always allowed)
    and they're already at the cap. Always False for guests."""
    if not user_id:
        return False
    data = _chat_store_load()
    owned = [c for c in data["conversations"] if str(c.get("user_id", "")) == user_id]
    if any(c.get("session_id") == session_id for c in owned):
        return False
    return len(owned) >= MAX_CONVERSATIONS_PER_USER


def _chat_upsert_conversation(
    session_id: str,
    user_id: str,
    user_email: str,
    title: str,
    message_count: int,
) -> dict:
    """Create the conversation for a session if it does not exist, then
    update its updated_at + message_count. Returns the conversation dict."""
    title = (title or "New chat").strip()[:200]
    now = _chat_now_iso()
    conv = None
    data = _chat_store_load()
    for existing in data["conversations"]:
        if existing.get("session_id") == session_id:
            conv = existing
            break
    if conv is None:
        # The per-user cap (MAX_CONVERSATIONS_PER_USER) is enforced upfront
        # in ai_chat() via _would_exceed_conversation_cap(), before a reply
        # is ever generated -- by the time this function runs, the request
        # has already been allowed through, so no cap check is needed here.
        conv = {
            "id": session_id,
            "session_id": session_id,
            "user_id": user_id,
            "user_email": user_email or "",
            "title": title,
            "created_at": now,
            "updated_at": now,
            "message_count": 0,
        }
        data["conversations"].append(conv)
    else:
        # A custom title set via the rename endpoint is never overwritten by
        # the auto-generated title; only auto-titled (or untitled) chats are.
        if not conv.get("custom_title") and (not conv.get("title") or conv.get("title") == "New chat"):
            conv["title"] = title
        conv["updated_at"] = now
    conv["message_count"] = int(conv.get("message_count", 0)) + message_count
    _chat_store_save(data)
    try:
        mysql_store.chat_conversation_upsert(
            session_id=session_id,
            user_id=user_id,
            user_email=user_email or "",
            title=conv["title"],
            custom_title=False,
            created_at=now,
            updated_at=now,
            message_count=conv["message_count"],
        )
    except Exception as exc:
        print(f"[CHAT-STORE] mysql conversation mirror failed: {exc}")
    return conv


def _chat_add_message(
    conversation_id: str,
    sender: str,
    message_text: str,
    kind: str = "normal",
    timestamp: str | None = None,
    file_name: str | None = None,
    file_content: str | None = None,
) -> None:
    """Append one message to a conversation (JSON file + MySQL mirror).

    `file_name` / `file_content` (extracted text, NOT the raw file) are
    stored with the message so a conversation still makes sense after the
    temporary upload is cleaned up.
    """
    ts = timestamp or _chat_now_iso()
    msg = {
        "id": str(uuid.uuid4()),
        "conversation_id": conversation_id,
        "sender": "user" if sender == "user" else "assistant",
        "message_text": message_text,
        "timestamp": ts,
        "kind": kind or "normal",
    }
    if file_name:
        msg["file_name"] = str(file_name)[:255]
    if file_content:
        msg["file_content"] = str(file_content)
    data = _chat_store_load()
    data["messages"].append(msg)
    _chat_store_save(data)
    try:
        mysql_store.chat_message_insert(msg)
    except Exception as exc:
        print(f"[CHAT-STORE] mysql message mirror failed: {exc}")


def _chat_save_exchange(
    session_id: str,
    user_id: str,
    user_email: str,
    user_message: str,
    assistant_reply: str,
    kind: str = "normal",
    file_name: str | None = None,
    file_content: str | None = None,
) -> None:
    """Persist one user+assistant exchange under a session's conversation.

    The conversation is created on the first exchange with a title generated
    from the first user message (truncated to ~30 characters).
    """
    try:
        title = (user_message or "").strip().replace("\n", " ")
        if len(title) > 30:
            title = title[:30].rstrip() + "..."
        _chat_upsert_conversation(session_id, user_id, user_email, title, 2)
        now = _chat_now_iso()
        _chat_add_message(session_id, "user", user_message, kind, now, file_name, file_content)
        _chat_add_message(session_id, "assistant", assistant_reply, kind, now)
    except Exception as exc:
        print(f"[CHAT-STORE] failed to save exchange: {exc}")


def _chat_list_conversations(user_id: str) -> list:
    """All conversations for one user, newest first (JSON fallback path)."""
    data = _chat_store_load()
    rows = [
        c for c in data["conversations"]
        if str(c.get("user_id", "")) == user_id
    ]
    rows.sort(key=lambda c: c.get("updated_at", "") or "", reverse=True)
    return rows


def _chat_get_messages(conversation_id: str) -> list:
    """All messages for one conversation, oldest first (JSON fallback path)."""
    data = _chat_store_load()
    rows = [m for m in data["messages"] if m.get("conversation_id") == conversation_id]
    rows.sort(key=lambda m: m.get("timestamp", "") or "")
    return rows


def _chat_delete_conversation(conversation_id: str, user_id: str) -> bool:
    """Permanently delete a conversation + its messages, owner-checked."""
    data = _chat_store_load()
    conv = next(
        (c for c in data["conversations"] if c.get("session_id") == conversation_id),
        None,
    )
    if conv is None:
        return False
    if user_id and str(conv.get("user_id", "")) != user_id:
        return False
    data["conversations"] = [
        c for c in data["conversations"] if c.get("session_id") != conversation_id
    ]
    data["messages"] = [
        m for m in data["messages"] if m.get("conversation_id") != conversation_id
    ]
    _chat_store_save(data)
    try:
        mysql_store.chat_conversation_delete(conversation_id)
    except Exception as exc:
        print(f"[CHAT-STORE] mysql delete mirror failed: {exc}")
    return True


def _chat_rename_conversation(conversation_id: str, user_id: str, title: str) -> dict | None:
    """Set a custom title for a conversation, owner-checked.

    Only the user who owns the conversation may rename it (same ownership
    rule as deletion). Returns the updated conversation dict, or None when
    the conversation does not exist / is not owned by the user / the title
    is blank after trimming.
    """
    title = (title or "").strip()[:50]
    if not title:
        return None
    data = _chat_store_load()
    conv = next(
        (c for c in data["conversations"] if c.get("session_id") == conversation_id),
        None,
    )
    if conv is None:
        return None
    if user_id and str(conv.get("user_id", "")) != user_id:
        return None
    conv["title"] = title
    conv["custom_title"] = True
    _chat_store_save(data)
    try:
        mysql_store.chat_conversation_rename(conversation_id, title)
    except Exception as exc:
        print(f"[CHAT-STORE] mysql rename mirror failed: {exc}")
    return dict(conv)


def _normalize_usage_record(raw) -> dict:
    """Coerce a stored usage value into {"count": int, "window_start": str|None}.
    Tolerates the older plain-int format (from before the cooldown window
    was added) by treating it as a count with no known window start --
    _usage_window_expired() then correctly treats that as "expired", so an
    old count never wrongly blocks anyone under the new scheme."""
    if isinstance(raw, dict):
        return {"count": int(raw.get("count", 0) or 0), "window_start": raw.get("window_start")}
    return {"count": int(raw or 0), "window_start": None}


def _chat_read_usage(user_email: str) -> dict:
    """Read a user's response-quota record ({count, window_start}) from the
    persistent store."""
    try:
        record = mysql_store.chat_usage_get(user_email)
        if record is not None:
            return _normalize_usage_record(record)
    except Exception as exc:
        print(f"[CHAT-STORE] mysql usage read failed: {exc}")
    data = _chat_store_load()
    try:
        return _normalize_usage_record(data["usage"].get(user_email))
    except Exception:
        return {"count": 0, "window_start": None}


def _chat_write_usage(user_email: str, entry: dict) -> None:
    """Persist a user's response-quota record ({count, window_start})."""
    data = _chat_store_load()
    data["usage"][user_email] = dict(entry)
    _chat_store_save(data)
    try:
        mysql_store.chat_usage_set(
            user_email,
            int(entry.get("count", 0)),
            entry.get("window_start"),
            _chat_now_iso(),
        )
    except Exception as exc:
        print(f"[CHAT-STORE] mysql usage mirror failed: {exc}")


def migrate_chat_store_json_to_mysql() -> None:
    """One-time backfill of existing chat_store.json conversations/messages/
    usage into MySQL (skipped once chat_conversations already has rows --
    idempotent, safe to call every startup)."""
    if not mysql_store.is_available() or not mysql_store.chat_store_is_empty():
        return
    data = _chat_store_load()
    if not data["conversations"] and not data["messages"]:
        return
    migrated_conversations = 0
    for conv in data["conversations"]:
        ok = mysql_store.chat_conversation_upsert(
            session_id=conv.get("session_id", ""),
            user_id=conv.get("user_id", ""),
            user_email=conv.get("user_email", ""),
            title=conv.get("title", "New chat"),
            custom_title=bool(conv.get("custom_title")),
            created_at=conv.get("created_at", ""),
            updated_at=conv.get("updated_at", ""),
            message_count=int(conv.get("message_count", 0) or 0),
        )
        if ok:
            migrated_conversations += 1
    migrated_messages = 0
    for msg in data["messages"]:
        if mysql_store.chat_message_insert(msg):
            migrated_messages += 1
    migrated_usage = 0
    for user_email, entry in data.get("usage", {}).items():
        normalized = _normalize_usage_record(entry)
        if mysql_store.chat_usage_set(user_email, normalized["count"], normalized["window_start"], _chat_now_iso()):
            migrated_usage += 1
    print(
        f"SUCCESS Migrated chat_store.json -> MySQL: {migrated_conversations} conversation(s), "
        f"{migrated_messages} message(s), {migrated_usage} usage record(s)"
    )


@app.route('/chat-history', methods=['GET'])
def chat_history():
    """Return the most recent RAG chat logs (optionally for one session)."""
    try:
        session_id = str(request.args.get("session_id", "")).strip()
        limit = min(int(request.args.get("limit", 20)), 100)

        logs = mysql_store.rag_log_recent(session_id=session_id, limit=limit)
        if logs is None:
            return jsonify({"success": False, "error": "MySQL unavailable"}), 503

        return jsonify({"success": True, "count": len(logs), "logs": logs}), 200
    except Exception as exc:
        print(f"[RAG-LOG] /chat-history error: {exc}")
        return jsonify({"success": False, "error": str(exc)}), 500


@app.route('/rag-stats', methods=['GET'])
def rag_stats():
    """Return aggregate RAG usage statistics across all chat logs."""
    try:
        stats = mysql_store.rag_log_stats()
        if stats is None:
            return jsonify({"success": False, "error": "MySQL not available"}), 503
        return jsonify({"success": True, **stats}), 200
    except Exception as exc:
        print(f"[RAG-LOG] /rag-stats error: {exc}")
        return jsonify({"success": False, "error": str(exc)}), 500


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# --- LEGACY single-pickle model loading -------------------------------
# The app now uses predict_disease_guarded.predict_guarded(), which loads the
# expanded_form_*.joblib ensemble + Disease_precaution.csv itself.
# Kept commented out (not deleted) so it can be diffed in case of regression.
# MODEL_CANDIDATES = [
#     os.path.join(BASE_DIR, "disease_prediction_model.pkl"),
#     os.path.join(BASE_DIR, "model.pkl"),
#     os.path.join(BASE_DIR, "..", "Disease dataset", "disease_prediction_model.pkl"),
# ]
DISEASE_KB_PATH = os.path.join(BASE_DIR, "disease_knowledge_base.json")
# DISEASE_CSV_PATH = os.path.join(BASE_DIR, "..", "Disease dataset", "DiseaseAndSymptoms.csv")
# FEATURE_COLUMNS = []
# model = None


# def build_feature_columns():
#     """Build the exact get_dummies column schema the model was trained on.
#
#     The model was trained via pd.get_dummies() over the Symptom_1..Symptom_17
#     columns of DiseaseAndSymptoms.csv, producing 394 positional one-hot
#     features (e.g. 'Symptom_3_high_fever'). We reproduce that schema here and
#     store the column names so /predict can build correctly-shaped vectors.
#     """
#     global FEATURE_COLUMNS
#     try:
#         if not os.path.exists(DISEASE_CSV_PATH):
#             print(f"WARNING Disease CSV not found: {DISEASE_CSV_PATH}")
#             FEATURE_COLUMNS = []
#             return FEATURE_COLUMNS
#         df = pd.read_csv(DISEASE_CSV_PATH)
#         symptom_cols = [f"Symptom_{i}" for i in range(1, 18)]
#         dummies = pd.get_dummies(df[symptom_cols], prefix_sep="_")
#         FEATURE_COLUMNS = list(dummies.columns)
#         print(f"SUCCESS Built FEATURE_COLUMNS ({len(FEATURE_COLUMNS)} columns) from {DISEASE_CSV_PATH}")
#     except Exception as exc:
#         print(f"WARNING Failed to build FEATURE_COLUMNS: {exc}")
#         FEATURE_COLUMNS = []
#     return FEATURE_COLUMNS


# # Map the frontend's plain-English symptom labels to the CSV symptom tokens.
# # The model only knows the dataset vocabulary (e.g. 'diarrhoea', 'polyuria',
# # 'breathlessness'), so aliases let user selections actually reach the features.
# _UI_SYMPTOM_ALIASES = {
#     "Fever": ["high_fever", "mild_fever"],
#     "Cough": ["cough"],
#     "Runny Nose": ["runny_nose"],
#     "Shortness of Breath": ["breathlessness"],
#     "Fatigue": ["fatigue"],
#     "Headache": ["headache"],
#     "Body Aches": ["muscle_pain", "malaise"],
#     "Sore Throat": ["throat_irritation"],
#     "Nausea": ["nausea"],
#     "Vomiting": ["vomiting"],
#     "Diarrhea": ["diarrhoea"],
#     "Loss of Appetite": ["loss_of_appetite"],
#     "Chest Pain": ["chest_pain"],
#     "Chills": ["chills", "shivering"],
#     "Dizziness": ["dizziness"],
#     "Joint Pain": ["joint_pain", "knee_pain"],
#     "Muscle Pain": ["muscle_pain"],
#     "Skin Rash": ["skin_rash"],
#     "Frequent Urination": ["polyuria"],
#     "Increased Thirst": ["increased_appetite"],
#     "Blurred Vision": ["blurred_and_distorted_vision"],
#     "High Blood Pressure": ["headache", "chest_pain", "dizziness"], # mapped to the model's Hypertension features
# }

# ---------------------------------------------------------------------
# NEW guarded pipeline: legacy checkbox names -> the exact strings
# predict_disease_guarded.CHECKBOX_TO_SYMPTOMS expects. 'Increased Thirst'
# and 'High Blood Pressure' have NO equivalent in the new 40-symptom
# vocabulary, so they are dropped (not silently mis-mapped like before).
# ---------------------------------------------------------------------
OLD_UI_TO_NEW = {
    "Fever": ["Mild Fever", "High Fever"],
    "Cough": ["Cough"],
    "Runny Nose": ["Runny Nose"],
    "Shortness of Breath": ["Breathlessness"],
    "Fatigue": ["Fatigue"],
    "Headache": ["Headache"],
    "Body Aches": ["Muscle Pain"],
    "Sore Throat": ["Throat Irritation"],
    "Nausea": ["Nausea"],
    "Vomiting": ["Vomiting"],
    "Diarrhea": ["Diarrhoea"],
    "Loss of Appetite": ["Loss of Appetite"],
    "Chest Pain": ["Chest Pain"],
    "Chills": ["Chills"],
    "Dizziness": ["Dizziness"],
    "Joint Pain": ["Joint Pain"],
    "Muscle Pain": ["Muscle Pain"],
    "Skin Rash": ["Skin Rash"],
    "Frequent Urination": ["Polyuria"],
    "Blurred Vision": ["Blurred and Distorted Vision"],
}


def canonicalize_checkboxes(raw_items):
    """Map any incoming symptom string (new checkbox label, legacy UI label,
    or model token e.g. from /map-symptoms) to the EXACT labels
    predict_guarded() understands. Returns (canonical_labels, dropped)."""
    canonical, dropped = [], []
    for item in raw_items:
        s = str(item or "").strip()
        if not s:
            continue
        if s in _NEW_CHECKBOX_TO_SYMPTOMS:
            canonical.append(s)
            continue
        mapped = OLD_UI_TO_NEW.get(s)
        if mapped:
            canonical.extend(mapped)
            continue
        token = re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")
        label = _NEW_TOKEN_TO_CHECKBOX.get(token)
        if label:
            canonical.append(label)
            continue
        dropped.append(s)
    seen, out = set(), []
    for s in canonical:
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out, dropped


# ---------------------------------------------------------------------
# LEGACY helpers (single-pickle get_dummies pipeline). Commented out — they
# reference FEATURE_COLUMNS / build_feature_columns / _UI_SYMPTOM_ALIASES
# which were removed when the app switched to predict_disease_guarded.py's
# 40-symptom MultiLabelBinarizer ensemble. Kept for diff/rollback.
# ---------------------------------------------------------------------
# def _symptom_tokens(symptom: str) -> list:
#     """Resolve a user-selected symptom into CSV symptom tokens."""
#     name = str(symptom or "").strip()
#     if not name:
#         return []
#     aliases = _UI_SYMPTOM_ALIASES.get(name)
#     if aliases:
#         return list(aliases)
#     token = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
#     return [token] if token else []


# def validate_symptom_alias_mappings():
#     """Boot-time guard: assert that every UI symptom alias maps to at least one real
#     get_dummies feature column. Any token that produces zero column hits would
#     be silently dropped by the predictor (the root cause of the 'only 5-6
#     diseases' bug), so it is flagged loudly here to prevent silent regressions."""
#     global FEATURE_COLUMNS
#     if not FEATURE_COLUMNS:
#         build_feature_columns()

#     if not FEATURE_COLUMNS:
#         print("[ALIAS] WARNING FEATURE_COLUMNS unavailable - cannot validate alias mappings")
#         return []

#     broken = []
#     for label, tokens in _UI_SYMPTOM_ALIASES.items():
#         for token in tokens:
#             hits = sum(1 for c in FEATURE_COLUMNS if c.lower().endswith(token))
#             if hits == 0:
#                 broken.append((label, token))
#                 print(f"[ALIAS] WARNING '{label}' -> '{token}' produces ZERO feature-column hits "
#                       f"and will be silently ignored by the predictor.")
#             else:
#                 print(f"[ALIAS] OK '{label}' -> '{token}' matches {hits} feature column(s)")

#     if broken:
#         print(f"[ALIAS] FAILED {len(broken)} alias mapping(s) produce zero feature-column hits: {broken}")
#     else:
#         print("[ALIAS] OK all UI symptom aliases map to valid feature columns")
#     return broken


# =====================================================================
# NATURAL-LANGUAGE SYMPTOM MAPPING (BioMistral -> model symptom tokens)
# =====================================================================
BIOMISTRAL_MODEL = "cniongolo/biomistral:latest"
MAP_SYMPTOMS_TIMEOUT = 30          # seconds for BioMistral
MAP_SYMPTOMS_MAX_TEXT = 300        # character cap on free-text input

_model_symptom_tokens_cache: list[str] = []


def _normalize_symptom_token(token) -> str:
    """Normalize a symptom token to the canonical underscore form, tolerating
    the whitespace quirks present in the DiseaseAndSymptoms.csv (e.g. leading
    spaces, 'foul_smell_of urine')."""
    return re.sub(r"[^a-z0-9]+", "_", str(token or "").lower().strip()).strip("_")


def _model_symptom_tokens() -> list:
    """Return the canonical model symptom tokens using the NEW 40-symptom
    MultiLabelBinarizer vocabulary (the only vocabulary the guarded pipeline
    can predict on). The legacy get_dummies-derived 131-token list is gone —
    it referred to the deleted FEATURE_COLUMNS/CSV pipeline."""
    global _model_symptom_tokens_cache
    if _model_symptom_tokens_cache:
        return _model_symptom_tokens_cache

    try:
        new_ok, _ = _new_load_models()
        if new_ok:
            _model_symptom_tokens_cache = sorted({
                t for ts in _NEW_CHECKBOX_TO_SYMPTOMS.values() for t in ts
            })
            return _model_symptom_tokens_cache
    except Exception as exc:
        print(f"[model tokens] new pipeline unavailable: {exc}")

    # No legacy CSV vocabulary exists anymore; degrade to the checkbox labels
    # that canonicalize_checkboxes() still understands.
    _model_symptom_tokens_cache = sorted(_NEW_CHECKBOX_TO_SYMPTOMS.keys())
    return _model_symptom_tokens_cache


def _call_biomistral(prompt: str, timeout_secs: int = MAP_SYMPTOMS_TIMEOUT) -> str:
    """Call BioMistral via Portkey Gateway when configured, else Ollama. Returns raw text or ''."""
    content, _source = portkey_llm.chat(
        [{"role": "user", "content": prompt}],
        ollama_model=BIOMISTRAL_MODEL,
        temperature=0,
        num_predict=150,
        timeout=timeout_secs,
    )
    return content or ""


def _build_symptom_map_prompt(text: str, tokens: list) -> str:
    token_list = ", ".join(tokens)
    return (
        "You are a medical symptom mapper. Given this patient description, "
        "extract and map ONLY to symptoms from this exact list: "
        f"[{token_list}] "
        "Return ONLY a JSON array of matching token strings, nothing else. "
        f"Patient says: {text}"
    )


def _extract_json_array(raw: str) -> list:
    """Return a JSON array parsed from BioMistral output, or [] if invalid."""
    if not raw:
        return []
    cleaned = re.sub(r"```(?:json)?", "", raw, flags=re.IGNORECASE).strip().strip("`").strip()
    try:
        val = json.loads(cleaned)
        return val if isinstance(val, list) else []
    except Exception:
        pass
    m = re.search(r"\[.*\]", cleaned, re.DOTALL)
    if m:
        try:
            val = json.loads(m.group(0))
            return val if isinstance(val, list) else []
        except Exception:
            return []
    return []


def _parse_biomistral_tokens(raw: str, valid_set: set) -> list:
    """Validate/normalize the array BioMistral returned, dropping any token not
    in the known model-symptom list (guards against hallucinated tokens)."""
    results: list[str] = []
    for item in _extract_json_array(raw):
        norm = _normalize_symptom_token(item)
        if norm and norm in valid_set:
            results.append(norm)
    return list(dict.fromkeys(results))


def _keyword_match_symptoms(text: str) -> list:
    """Simple keyword fallback against the canonical model-token list, used when
    BioMistral returns invalid JSON or fails/times out."""
    hay = str(text or "").lower()
    hay_norm = re.sub(r"[^a-z0-9]+", " ", hay)
    results: list[str] = []
    for token in _model_symptom_tokens():
        phrase = token.replace("_", " ")
        if phrase in hay_norm or token in hay:
            if token not in results:
                results.append(token)
    return results


@app.route('/map-symptoms', methods=['POST'])
def map_symptoms():
    """Map natural-language free text to the model's 131 symptom tokens.

    Request:  { "text": "I have stomach pain and my eyes are turning yellow" }
    Response: { "tokens": ["stomach_pain", "yellowing_of_eyes", ...],
                "matched": 3, "source": "biomistral" | "keyword" }
    """
    data = request.get_json(silent=True) or {}
    text = str(data.get("text", "")).strip()
    if not text:
        return jsonify({"tokens": [], "matched": 0, "source": "keyword", "error": "text is required"}), 400

    text = text[:MAP_SYMPTOMS_MAX_TEXT]
    tokens = _model_symptom_tokens()
    valid_set = set(tokens)

    prompt = _build_symptom_map_prompt(text, tokens)
    raw = _call_biomistral(prompt)
    mapped = _parse_biomistral_tokens(raw, valid_set)

    source = "biomistral"
    if not mapped:
        mapped = _keyword_match_symptoms(text)
        source = "keyword"

    return jsonify({
        "tokens": list(dict.fromkeys(mapped)),
        "matched": len(mapped),
        "source": source,
    }), 200


def _load_local_disease_kb() -> dict:
    """Load local disease knowledge base for predict-disease endpoint."""
    try:
        kb_path = Path(DISEASE_KB_PATH)
        if not kb_path.exists():
            return {}
        with kb_path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        print(f"Could not load disease knowledge base: {exc}")
        return {}

warnings.filterwarnings("ignore")

RESPONSE_STYLE = "doctor-like explanation"
STYLE_FOOTER = "Consult a doctor if symptoms persist."
MAX_RESPONSE_TOKENS = int(os.getenv("MAX_RESPONSE_TOKENS", "120"))
MAX_HISTORY_TURNS = int(os.getenv("MAX_HISTORY_TURNS", "4"))
TEMPERATURE = float(os.getenv("TEMPERATURE", "0.3"))
MAX_HISTORY_TOKENS = int(os.getenv("MAX_HISTORY_TOKENS", "1000"))
sessions: dict[str, deque] = {}
session_token_usage: dict[str, dict[str, int]] = {}
_NATIVE_DISEASE_ALIASES: dict[str, dict[str, str]] = {}
_DISEASE_KB_CACHE: dict[str, dict[str, str]] | None = None
_DISEASE_ALIAS_CACHE: dict[str, str] | None = None

def _is_medical_question(question: str) -> bool:
    """Return True when the user is asking about a health or medical topic."""
    raw = str(question or "")
    q_norm = _normalize_text(question)
    if not raw.strip() and not q_norm:
        return False

    if _detect_disease_in_question(question):
        return True

    if _detect_question_type(question):
        return True

    medical_markers = [
        "fever",
        "pain",
        "cough",
        "cold",
        "vomit",
        "vomiting",
        "diarrhea",
        "rash",
        "headache",
        "stomach pain",
        "stomach ache",
        "abdominal pain",
        "breathing",
        "shortness of breath",
        "chest pain",
        "dizziness",
        "weakness",
        "fatigue",
        "infection",
        "disease",
        "illness",
        "symptom",
        "symptoms",
        "treatment",
        "medicine",
        "doctor",
        "hospital",
        "clinic",
        "health",
        "medical",
        "pregnancy",
        "paralysis",
        "stroke",
        "allergy",
        "asthma",
        "depression",
        "anxiety",
        "kidney",
        "liver",
        "heart",
        "skin",
        "eye",
        "ear",
        "tooth",
        "joint",
        "blood pressure",
        "sugar level",
    ]
    if any(marker in q_norm for marker in medical_markers):
        return True

    native_markers = [
        "ಜ್ವರ",
        "ನೋವು",
        "ಕೆಮ್ಮು",
        "ವಾಂತಿ",
        "ಅತಿಸಾರ",
        "ತಲೆನೋವು",
        "ಉಸಿರಾಟ",
        "ಸೋಂಕು",
        "ರೋಗ",
        "ವೈದ್ಯ",
        "ಆಸ್ಪತ್ರೆ",
        "ಚಿಕಿತ್ಸೆ",
        "ಮದ್ದು",
        "ಡಾಕ್ಟರ್",
        "ಬಗ್ಗೆ",
        "ಹೇಳಿ",
        "ಬರಿಸಿ",
        "ಬಿಡಿ",
        "ಬಗ್ಗೆ ಹೇಳಿ",
        "बुखार",
        "दर्द",
        "खांसी",
        "उल्टी",
        "दस्त",
        "सिरदर्द",
        "सांस",
        "संक्रमण",
        "रोग",
        "डॉक्टर",
        "अस्पताल",
        "इलाज",
        "दवा",
        "बारे",
        "बताओ",
        "बताइए",
        "समझाइए",
    ]
    if any(marker in raw for marker in native_markers):
        return True

    if _is_prevention_or_diet_question(question) or _is_followup_question(question):
        return True

    return False


def _get_openai_client():
    return None


def _build_scope_system_prompt() -> str:
    return ""


def _build_structured_advanced_system_prompt() -> str:
    return ""


def _build_advanced_user_message(question: str, session_id: str | None = None) -> str:
    return ""


def _format_advanced_structured_reply(
    disease: str | None,
    kb_data: dict[str, str] | None,
    summary_text: str,
    target_language: str | None = None,
) -> str:
    return ""


def _build_local_advanced_summary(disease: str | None, kb_data: dict[str, str] | None) -> str:
    return ""


def get_history(session_id: str) -> deque:
    if session_id not in sessions:
        sessions[session_id] = deque()
    return sessions[session_id]


def history_to_messages(session_id: str) -> list:
    messages = []
    for user_text, assistant_text in get_history(session_id):
        messages.append({"role": "user", "content": user_text})
        messages.append({"role": "assistant", "content": assistant_text})
    return messages


def estimate_tokens(text: str) -> int:
    return max(1, len(text or "") // 3)


def trim_history_if_needed(session_id: str):
    hist = get_history(session_id)
    while hist:
        total = sum(estimate_tokens(u) + estimate_tokens(a) for u, a in hist)
        if total <= MAX_HISTORY_TOKENS:
            break
        hist.popleft()


def add_turn(session_id: str, user_text: str, assistant_text: str):
    hist = get_history(session_id)
    hist.append((user_text, assistant_text))
    while len(hist) > MAX_HISTORY_TURNS:
        hist.popleft()
    trim_history_if_needed(session_id)


def _get_usage_bucket(session_id: str) -> dict[str, int]:
    if session_id not in session_token_usage:
        session_token_usage[session_id] = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
        }
    return session_token_usage[session_id]


def call_general_medical_llm(session_id: str, user_query: str, short_answer: bool = False) -> dict | None:
    return None


def _record_token_usage(session_id: str, prompt_tokens: int = 0, completion_tokens: int = 0) -> dict[str, int]:
    bucket = _get_usage_bucket(session_id)
    bucket["prompt_tokens"] += max(0, int(prompt_tokens or 0))
    bucket["completion_tokens"] += max(0, int(completion_tokens or 0))
    return bucket


def _chat_response_payload(session_id: str, reply: str, prompt_tokens: int = 0, completion_tokens: int = 0) -> dict:
    prompt_tokens = max(0, int(prompt_tokens or 0))
    completion_tokens = max(0, int(completion_tokens or 0))
    totals = _record_token_usage(session_id, prompt_tokens, completion_tokens)
    return {
        "reply": reply,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_prompt_tokens": totals.get("prompt_tokens", 0),
        "total_completion_tokens": totals.get("completion_tokens", 0),
    }


def _normalize_punctuation_artifacts(text: str) -> str:
    """Clean common punctuation artifacts from generated/translated text."""
    value = str(text or "").strip()
    if not value:
        return value

    value = re.sub(r"\s+([,.;:!?])", r"\1", value)
    value = re.sub(r"([.!?])\1+", r"\1", value)
    value = re.sub(r"(।)\1+", r"\1", value)
    return value.strip()


def _apply_fixed_style(text: str) -> str:
    """Normalize replies into concise Problem/Action/Doctor format."""
    reply = _normalize_punctuation_artifacts(re.sub(r"\s+", " ", (text or "")).strip())
    if not reply:
        return reply

    chunks = re.split(r"\n+|(?<=[.!?])\s+", reply)
    sentences = []
    for chunk in chunks:
        clean = re.sub(r"^[\-\*•\d\)\.\s]+", "", chunk.strip())
        clean = _normalize_punctuation_artifacts(re.sub(r"\s+", " ", clean).strip())
        if len(clean) < 3:
            continue
        if clean[-1] not in ".!?":
            clean += "."
        sentences.append(clean)

    if not sentences:
        base = _normalize_punctuation_artifacts(reply)
        sentences = [base if base[-1] in ".!?" else f"{base}."]

    problem = _normalize_punctuation_artifacts(sentences[0])
    what_to_do = _normalize_punctuation_artifacts(
        sentences[1] if len(sentences) > 1 else "Take rest, drink clean fluids, and monitor symptoms."
    )

    when_to_see = "Not needed now if symptoms stay mild and improve with basic care."
    risk_markers = [
        "chest pain", "breath", "breathing", "unconscious", "faint", "high fever",
        "dehydration", "severe", "blood", "confusion", "persistent", "worsen",
    ]
    if any(marker in reply.lower() for marker in risk_markers):
        when_to_see = "See a doctor urgently if symptoms worsen or danger signs appear."
    elif len(sentences) > 2:
        when_to_see = _normalize_punctuation_artifacts(sentences[2])

    return "\n".join([
        f"- Problem: {problem}",
        f"- What to do: {what_to_do}",
        f"- When to see a doctor: {when_to_see}",
        f"- {_normalize_punctuation_artifacts(STYLE_FOOTER)}",
    ])


def _unknown_disease_reply(candidate: str | None = None) -> str:
    """Return a safe 1-2 line response for diseases not found in KB."""
    disease_name = str(candidate or "This condition").strip() or "This condition"
    if disease_name.lower() == "this condition":
        line1 = "- This condition needs proper medical evaluation for an accurate diagnosis."
    else:
        line1 = f"- {disease_name} is a medical condition that needs proper clinical evaluation."
    line2 = "- Please consult a doctor for proper guidance."
    line3 = f"- {_normalize_punctuation_artifacts(STYLE_FOOTER)}"
    return "\n".join([line1, line2, line3])


def _rule_based_translate_reply(target_language: str, base_reply: str) -> str:
    """Best-effort local translation fallback when GPT translation is unavailable."""
    text = str(base_reply or "").strip()
    if not text:
        return text

    def _has_latin(value: str) -> bool:
        return bool(re.search(r"[A-Za-z]{2,}", str(value or "")))

    def _extract_disease_name(value: str) -> str:
        m = re.match(r"^\s*([A-Za-z][A-Za-z\s\-]+)\s*:", str(value or "").strip())
        return (m.group(1).strip() if m else "")

    def _localize_disease_name(disease_name: str, lang: str) -> str:
        name = str(disease_name or "").strip().lower()
        if not name:
            return ""

        kannada_map = {
            "dengue": "ಡೆಂಗ್ಯೂ",
            "malaria": "ಮಲೇರಿಯಾ",
            "diabetes": "ಮಧುಮೇಹ",
            "hypertension": "ಉಚ್ಚ ರಕ್ತದೊತ್ತಡ",
            "typhoid": "ಟೈಫಾಯ್ಡ್",
            "tuberculosis": "ಕ್ಷಯರೋಗ",
            "pneumonia": "ನ್ಯೂಮೋನಿಯಾ",
            "influenza": "ಇನ್ಫ್ಲೂಯೆನ್ಸಾ",
            "common cold": "ಸಾಮಾನ್ಯ ಶೀತ",
            "heart disease": "ಹೃದಯ ರೋಗ",
            "asthma": "ಆಸ್ತಮಾ",
            "gerd": "ಜಿಇಆರ್‌ಡಿ",
            "gastritis": "ಗ್ಯಾಸ್ಟ್ರೈಟಿಸ್",
            "food poisoning": "ಆಹಾರ ವಿಷಬಾಧೆ",
            "appendicitis": "ಅಪೆಂಡಿಸೈಟಿಸ್",
            "hepatitis": "ಯಕೃತ್ತಿನ ಉರಿಯೂತ",
            "kidney stones": "ಮೂತ್ರಪಿಂಡದ ಕಲ್ಲು",
            "urinary tract infection": "ಮೂತ್ರಮಾರ್ಗದ ಸೋಂಕು",
            "anemia": "ರಕ್ತಹೀನತೆ",
            "arthritis": "ಸಂಧಿವಾತ",
            "migraine": "ಮೈಗ್ರೇನ್",
            "allergy": "ಅಲರ್ಜಿ",
            "chickenpox": "ಚಿಕನ್‌ಪಾಕ್ಸ್",
            "measles": "ಮೀಸಲ್ಸ್",
            "skin infection": "ಚರ್ಮದ ಸೋಂಕು",
            "eczema": "ಎಕ್ಸಿಮಾ",
            "psoriasis": "ಸೋರಿಯಾಸಿಸ್",
            "bronchitis": "ಬ್ರಾಂಕೈಟಿಸ್",
            "conjunctivitis": "ಕಣ್ಣು ಪೊರೆಯ ಉರಿಯೂತ",
            "ear infection": "ಕಿವಿಯ ಸೋಂಕು",
            "dehydration": "ದೇಹದ ನೀರಿನ ಕೊರತೆ",
            "obesity": "ಅತಿಯಾಗಿ ತೂಕ",
            "depression": "ಮನೋನಿರಾಶೆ",
            "anxiety": "ಆತಂಕ",
            "stroke": "ಸ್ಟ್ರೋಕ್",
            "parkinson disease": "ಪಾರ್ಕಿನ್ಸನ್ ರೋಗ",
            "alzheimer disease": "ಅಲ್ಜೈಮರ್ ರೋಗ",
            "diarrhea": "ಜಲದೋಷ",
        }
        hindi_map = {
            "dengue": "डेंगू",
            "malaria": "मलेरिया",
            "diabetes": "मधुमेह",
            "hypertension": "उच्च रक्तचाप",
            "typhoid": "टाइफाइड",
            "tuberculosis": "क्षय रोग",
            "pneumonia": "निमोनिया",
            "influenza": "इन्फ्लुएंजा",
            "common cold": "साधारण सर्दी",
            "heart disease": "हृदय रोग",
            "asthma": "अस्थमा",
            "gerd": "जीईआरडी",
            "gastritis": "गैस्ट्राइटिस",
            "food poisoning": "खाद्य विषाक्तता",
            "appendicitis": "अपेंडिसाइटिस",
            "hepatitis": "हेपेटाइटिस",
            "kidney stones": "गुर्दे की पथरी",
            "urinary tract infection": "मूत्र मार्ग संक्रमण",
            "anemia": "एनीमिया",
            "arthritis": "गठिया",
            "migraine": "माइग्रेन",
            "allergy": "एलर्जी",
            "chickenpox": "चेचक",
            "measles": "खसरा",
            "skin infection": "त्वचा संक्रमण",
            "eczema": "एक्जिमा",
            "psoriasis": "सोरायसिस",
            "bronchitis": "ब्रोंकाइटिस",
            "conjunctivitis": "कंजंक्टिवाइटिस",
            "ear infection": "कान का संक्रमण",
            "dehydration": "निर्जलीकरण",
            "obesity": "मोटापा",
            "depression": "अवसाद",
            "anxiety": "चिंता",
            "stroke": "स्ट्रोक",
            "parkinson disease": "पार्किंसन रोग",
            "alzheimer disease": "अल्जाइमर रोग",
            "diarrhea": "दस्त",
        }

        if lang == "Kannada":
            return kannada_map.get(name, "")
        if lang == "Hindi":
            return hindi_map.get(name, "")
        return ""

    lines = [line.strip() for line in text.splitlines() if line.strip()]

    def _extract_value(prefix: str) -> str:
        for line in lines:
            clean = re.sub(r"^\s*[-*]\s*", "", line)
            if clean.lower().startswith(prefix.lower()):
                return clean[len(prefix):].strip()
        return ""

    problem_value = _extract_value("Problem:")
    what_to_do_value = _extract_value("What to do:")
    when_to_see_value = _extract_value("When to see a doctor:")

    def _translate_common_phrases(lang: str, value: str) -> str:
        out = str(value or "").strip()
        if not out:
            return out

        if lang == "Hindi":
            replacements = [
                # Common medical phrases
                ("You can reduce risk by", "जोखिम कम करने के लिए"),
                ("Drink fluids and seek medical care", "तरल पदार्थ पिएं और डॉक्टर से सलाह लें"),
                ("See a doctor urgently if symptoms worsen", "यदि लक्षण बढ़ें तो तुरंत डॉक्टर से मिलें"),
                ("Common symptoms include", "सामान्य लक्षण हैं"),
                ("Caused by", "कारण है"),
                # Single words and common terms
                ("symptoms", "लक्षण"),
                ("fever", "बुखार"),
                ("cough", "खांसी"),
                ("headache", "सिरदर्द"),
                ("rest", "आराम"),
                ("drink water", "पानी पिएं"),
                ("see a doctor", "डॉक्टर से मिलें"),
                ("medical", "चिकित्सा"),
                ("treatment", "उपचार"),
                # Disease-specific
                ("dengue", "डेंगू"),
                ("malaria", "मलेरिया"),
                ("transmitted", "संचारित"),
                ("transmit", "संचरण"),
                ("mosquito", "मच्छर"),
                ("mosquito bites", "मच्छर के काटने"),
                ("disease", "रोग"),
                ("viral", "वायरल"),
                ("virus", "वायरस"),
                # Symptoms and conditions
                ("pain", "दर्द"),
                ("rash", "त्वचा पर धब्बे"),
                ("joint pain", "जोड़ों में दर्द"),
                ("high fever", "तेज बुखार"),
                ("fatigue", "कमजोरी"),
                ("chills", "कंपकंपी"),
                ("nausea", "मतली"),
                # Actions
                ("take", "लें"),
                ("drink", "पिएं"),
                ("eat", "खाएं"),
                ("consult", "सलाह लें"),
                ("seek care", "डॉक्टर से सलाह लें"),
                ("apply", "लगाएं"),
                # Time/duration
                ("days", "दिन"),
                ("weeks", "सप्ताह"),
                ("months", "महीने"),
                ("persist", "बने रहें"),
            ]
        elif lang == "Kannada":
            replacements = [
                # Common medical phrases
                ("You can reduce risk by", "ಅಪಾಯವನ್ನು ಕಡಿಮೆ ಮಾಡಲು"),
                ("Drink fluids and seek medical care", "ದ್ರವ ಪದಾರ್ಥಗಳನ್ನು ಕುಡಿಯಿರಿ ಮತ್ತು ವೈದ್ಯರನ್ನು ಸಂಪರ್ಕಿಸಿ"),
                ("See a doctor urgently if symptoms worsen", "ಲಕ್ಷಣಗಳು ಹೆಚ್ಚಾದರೆ ತಕ್ಷಣ ವೈದ್ಯರನ್ನು ಭೇಟಿ ಮಾಡಿ"),
                ("Common symptoms include", "ಸಾಮಾನ್ಯ ಲಕ್ಷಣಗಳು"),
                ("Caused by", "ಕಾರಣ"),
                # Single words and common terms
                ("symptoms", "ಲಕ್ಷಣಗಳು"),
                ("fever", "ಜ್ವರ"),
                ("cough", "ಕಕ್ಕೆ"),
                ("headache", "ತಲೆನೋವು"),
                ("rest", "ವಿಶ್ರಾಂತಿ"),
                ("drink water", "ನೀರು ಕುಡಿಯಿರಿ"),
                ("see a doctor", "ವೈದ್ಯರನ್ನು ಭೇಟಿ ಮಾಡಿ"),
                ("medical", "ವೈದ್ಯಕೀಯ"),
                ("treatment", "ಚಿಕಿತ್ಸೆ"),
                # Disease-specific
                ("dengue", "ಡೆಂಗೆ"),
                ("malaria", "ನೀರುಬಿಕ್ಕೆ"),
                ("transmitted", "ಸಾಗಿಸಲ್ಪಡುವ"),
                ("transmit", "ಸಾಗಿಸು"),
                ("mosquito", "ಸೊಳ್ಳೆ"),
                ("mosquito bites", "ಸೊಳ್ಳೆ ಕೊಚ್ಚುವುದು"),
                ("disease", "ರೋಗ"),
                ("viral", "ವೈರಸ್"),
                ("virus", "ವೈರಸ್"),
                # Symptoms and conditions
                ("pain", "ನೋವು"),
                ("rash", "ರೈತೆ"),
                ("joint pain", "ಕೀಲುಗಳ ನೋವು"),
                ("high fever", "ಹೆಚ್ಚಿನ ಜ್ವರ"),
                ("fatigue", "ದುರ್ಬಲತೆ"),
                ("chills", "ತುರೆದು"),
                ("nausea", "ವಾಂತಿ"),
                # Actions
                ("take", "ತೆಗೆ"),
                ("drink", "ಕುಡಿ"),
                ("eat", "ತಿನ್ನು"),
                ("rest", "ವಿಶ್ರಾಂತಿ"),
                ("consult", "ಸಲಹೆ ಪಡು"),
                ("seek care", "ಸಂಪರ್ಕಿಸು"),
                ("apply", "ಹಾಕು"),
                # Time/duration
                ("days", "ದಿನಗಳು"),
                ("weeks", "ವಾರಗಳು"),
                ("months", "ತಿಂಗಳುಗಳು"),
                ("persist", "ಮುಂದುವರಿದು"),
            ]
        else:
            replacements = []

        for src, dst in replacements:
            out = re.sub(rf"\b{re.escape(src)}\b", dst, out, flags=re.IGNORECASE)
        return out

    if target_language == "Hindi":
        if problem_value or what_to_do_value or when_to_see_value:
            p = _translate_common_phrases("Hindi", problem_value) or "यह स्थिति चिकित्सकीय जांच की जरूरत रखती है।"
            w = _translate_common_phrases("Hindi", what_to_do_value) or "साफ पानी पिएं, आराम करें, और लक्षणों पर नजर रखें।"
            d = _translate_common_phrases("Hindi", when_to_see_value) or "लक्षण बढ़ें या गंभीर संकेत हों तो तुरंत डॉक्टर से मिलें।"
            formatted = "\n".join([
                f"- समस्या: {p}",
                f"- क्या करें: {w}",
                f"- डॉक्टर को कब दिखाएं: {d}",
                "- लक्षण बने रहें तो डॉक्टर से सलाह लें।",
            ])
            if not _has_latin(formatted):
                return formatted

            disease_hint = _localize_disease_name(_extract_disease_name(problem_value), "Hindi")
            disease_line = f"- समस्या: {disease_hint} के लक्षण हैं, सही चिकित्सकीय जांच जरूरी है।" if disease_hint else "- समस्या: यह स्थिति चिकित्सकीय जांच की जरूरत रखती है।"
            return "\n".join([
                disease_line,
                "- क्या करें: आराम करें, साफ पानी पिएं, और लक्षणों पर नजर रखें।",
                "- डॉक्टर को कब दिखाएं: लक्षण बढ़ें, तेज बुखार रहे, या सांस लेने में कठिनाई हो तो तुरंत डॉक्टर से मिलें।",
                "- लक्षण बने रहें तो डॉक्टर से सलाह लें।",
            ])

        # If structured format not found, translate entire text using phrase replacements
        translated_full = _translate_common_phrases("Hindi", text)
        if translated_full and translated_full != text:
            return translated_full

        unknown = re.match(r"^-\s*(.+?)\s+is a medical condition that needs proper clinical evaluation\.?$", lines[0], re.IGNORECASE) if lines else None
        if unknown:
            disease = unknown.group(1).strip()
            return "\n".join([
                f"- {disease} एक चिकित्सा स्थिति है जिसके लिए सही चिकित्सकीय जांच जरूरी है।",
                "- सही मार्गदर्शन के लिए डॉक्टर से सलाह लें।",
                "- लक्षण बने रहें तो डॉक्टर से सलाह लें।",
            ])

        return "\n".join([
            "- समस्या: यह स्थिति चिकित्सकीय जांच की जरूरत रखती है।",
            "- क्या करें: साफ पानी पिएं, आराम करें, और लक्षणों पर नजर रखें।",
            "- डॉक्टर को कब दिखाएं: लक्षण बढ़ें या गंभीर संकेत हों तो तुरंत डॉक्टर से मिलें।",
            "- लक्षण बने रहें तो डॉक्टर से सलाह लें।",
        ])

    if target_language == "Kannada":
        if problem_value or what_to_do_value or when_to_see_value:
            p = _translate_common_phrases("Kannada", problem_value) or "ಈ ಸ್ಥಿತಿಗೆ ಸರಿಯಾದ ವೈದ್ಯಕೀಯ ಪರಿಶೀಲನೆ ಅಗತ್ಯವಿದೆ."
            w = _translate_common_phrases("Kannada", what_to_do_value) or "ಶುದ್ಧ ನೀರು ಕುಡಿಯಿರಿ, ವಿಶ್ರಾಂತಿ ತೆಗೆದುಕೊಳ್ಳಿ, ಮತ್ತು ಲಕ್ಷಣಗಳನ್ನು ಗಮನಿಸಿ."
            d = _translate_common_phrases("Kannada", when_to_see_value) or "ಲಕ್ಷಣಗಳು ಹೆಚ್ಚಾದರೆ ಅಥವಾ ಗಂಭೀರ ಸೂಚನೆಗಳಿದ್ದರೆ ತಕ್ಷಣ ವೈದ್ಯರನ್ನು ಭೇಟಿ ಮಾಡಿ."
            formatted = "\n".join([
                f"- ಸಮಸ್ಯೆ: {p}",
                f"- ಏನು ಮಾಡಬೇಕು: {w}",
                f"- ವೈದ್ಯರನ್ನು ಯಾವಾಗ ಭೇಟಿ ಮಾಡಬೇಕು: {d}",
                "- ಲಕ್ಷಣಗಳು ಮುಂದುವರಿದರೆ ವೈದ್ಯರನ್ನು ಸಂಪರ್ಕಿಸಿ.",
            ])
            if not _has_latin(formatted):
                return formatted

            disease_hint = _localize_disease_name(_extract_disease_name(problem_value), "Kannada")
            disease_line = f"- ಸಮಸ್ಯೆ: {disease_hint} ರೋಗದ ಲಕ್ಷಣಗಳು ಕಂಡುಬಂದಿವೆ; ಸರಿಯಾದ ವೈದ್ಯಕೀಯ ಪರಿಶೀಲನೆ ಅಗತ್ಯವಿದೆ." if disease_hint else "- ಸಮಸ್ಯೆ: ಈ ಸ್ಥಿತಿಗೆ ಸರಿಯಾದ ವೈದ್ಯಕೀಯ ಪರಿಶೀಲನೆ ಅಗತ್ಯವಿದೆ."
            return "\n".join([
                disease_line,
                "- ಏನು ಮಾಡಬೇಕು: ವಿಶ್ರಾಂತಿ ತೆಗೆದುಕೊಳ್ಳಿ, ಶುದ್ಧ ನೀರು ಕುಡಿಯಿರಿ, ಮತ್ತು ಲಕ್ಷಣಗಳನ್ನು ಗಮನಿಸಿ.",
                "- ವೈದ್ಯರನ್ನು ಯಾವಾಗ ಭೇಟಿ ಮಾಡಬೇಕು: ಲಕ್ಷಣಗಳು ಹೆಚ್ಚಾದರೆ, ಹೆಚ್ಚು ಜ್ವರ ಬಂದರೆ, ಅಥವಾ ಉಸಿರಾಟ ಕಷ್ಟವಾದರೆ ತಕ್ಷಣ ವೈದ್ಯರನ್ನು ಭೇಟಿ ಮಾಡಿ.",
                "- ಲಕ್ಷಣಗಳು ಮುಂದುವರಿದರೆ ವೈದ್ಯರನ್ನು ಸಂಪರ್ಕಿಸಿ.",
            ])

        # If structured format not found, translate entire text using phrase replacements
        translated_full = _translate_common_phrases("Kannada", text)
        if translated_full and translated_full != text:
            return translated_full

        unknown = re.match(r"^-\s*(.+?)\s+is a medical condition that needs proper clinical evaluation\.?$", lines[0], re.IGNORECASE) if lines else None
        if unknown:
            disease = unknown.group(1).strip()
            return "\n".join([
                f"- {disease} ಒಂದು ವೈದ್ಯಕೀಯ ಸ್ಥಿತಿ ಆಗಿದ್ದು, ಸರಿಯಾದ ಕ್ಲಿನಿಕಲ್ ಪರೀಕ್ಷೆ ಅಗತ್ಯವಿದೆ.",
                "- ಸರಿಯಾದ ಮಾರ್ಗದರ್ಶನಕ್ಕಾಗಿ ವೈದ್ಯರನ್ನು ಸಂಪರ್ಕಿಸಿ.",
                "- ಲಕ್ಷಣಗಳು ಮುಂದುವರಿದರೆ ವೈದ್ಯರನ್ನು ಸಂಪರ್ಕಿಸಿ.",
            ])

        return "\n".join([
            "- ಸಮಸ್ಯೆ: ಈ ಸ್ಥಿತಿಗೆ ಸರಿಯಾದ ವೈದ್ಯಕೀಯ ಪರಿಶೀಲನೆ ಅಗತ್ಯವಿದೆ.",
            "- ಏನು ಮಾಡಬೇಕು: ಶುದ್ಧ ನೀರು ಕುಡಿಯಿರಿ, ವಿಶ್ರಾಂತಿ ತೆಗೆದುಕೊಳ್ಳಿ, ಮತ್ತು ಲಕ್ಷಣಗಳನ್ನು ಗಮನಿಸಿ.",
            "- ವೈದ್ಯರನ್ನು ಯಾವಾಗ ಭೇಟಿ ಮಾಡಬೇಕು: ಲಕ್ಷಣಗಳು ಹೆಚ್ಚಾದರೆ ಅಥವಾ ಗಂಭೀರ ಸೂಚನೆಗಳಿದ್ದರೆ ತಕ್ಷಣ ವೈದ್ಯರನ್ನು ಭೇಟಿ ಮಾಡಿ.",
            "- ಲಕ್ಷಣಗಳು ಮುಂದುವರಿದರೆ ವೈದ್ಯರನ್ನು ಸಂಪರ್ಕಿಸಿ.",
        ])

    return text


def _rewrite_query_for_kb(session_id: str, question: str) -> dict | None:
    return None


def _extract_target_language(question: str) -> str | None:
    """Detect whether user requested a translated answer language."""
    raw = str(question or "")
    q = _normalize_text(raw)
    raw_lower = raw.lower()

    # Native-script detection first so same-language replies work without explicit 'translate'.
    if re.search(r"[\u0C80-\u0CFF]", raw):
        return "Kannada"
    if re.search(r"[\u0900-\u097F]", raw):
        return "Hindi"

    if "ಕನ್ನಡ" in raw_lower or any(marker in q for marker in ["kannada", "kannada dalli", "kannada nalli", "in kannada"]):
        return "Kannada"

    if "हिंदी" in raw or any(marker in q for marker in ["hindi", "hindi me", "hindime", "in hindi"]):
        return "Hindi"

    return None


def _translate_reply_if_needed(session_id: str, message: str, base_reply: str, short_answer: bool = False) -> dict | None:
    """Translate reply into requested target language when user asks for it."""
    target_language = _extract_target_language(message)
    if not target_language:
        return None

    # Use ONLY rule-based translation for Kannada/Hindi - LLM produces garbled mixed-language output
    # Rule-based is more reliable for complete language switching
    fallback_reply = _rule_based_translate_reply(target_language, base_reply)
    
    if fallback_reply:
        add_turn(session_id, message, fallback_reply)
        return {
            "reply": fallback_reply,
            "prompt_tokens": 0,
            "completion_tokens": 0,
        }

    # If rule-based translation failed completely, return None (user gets English)
    return None


def _get_last_assistant_reply(session_id: str) -> str:
    """Return the most recent assistant response from session history."""
    hist = get_history(session_id)
    if not hist:
        return ""
    _last_user, last_assistant = hist[-1]
    return str(last_assistant or "").strip()


def call_llm(session_id: str, user_query: str, short_answer: bool = False) -> dict | None:
    return None


def call_llm_advanced(
    session_id: str,
    user_query: str,
    target_language: str | None = None,
    short_answer: bool = False,
) -> dict | None:
    return None


def call_llm_transform(session_id: str, user_query: str, source_answer: str, short_answer: bool = False) -> dict | None:
    return None


def _is_transform_request(question: str) -> bool:
    """Detect prompt types that ask to reformat/translate/summarize previous answer."""
    q = _normalize_text(question)
    if not q:
        return False

    markers = [
        "previous answer",
        "previous response",
        "last answer",
        "last response",
        "translate",
        "explain more",
        "explain simply",
        "simplify",
        "beginner",
        "bullet point",
        "table format",
        "checklist",
        "step by step",
        "same advice",
        "script",
        "summarize",
        "simple english",
        "10 year old",
        "follow up triage questions",
    ]

    return any(marker in q for marker in markers)


def _wants_short_answer(question: str) -> bool:
    """Detect when user explicitly asks for concise output."""
    q = _normalize_text(question)
    short_markers = [
        "short answer",
        "small answer",
        "brief",
        "in short",
        "shortly",
        "one line",
        "few words",
    ]
    return any(marker in q for marker in short_markers)


def _is_advanced_question(question: str) -> bool:
    return False


def _shorten_answer(answer: str, max_chars: int = 280) -> str:
    """Trim verbose responses for concise mode."""
    text = re.sub(r"\s+", " ", (answer or "")).strip()
    if len(text) <= max_chars:
        return text
    clipped = text[:max_chars].rsplit(" ", 1)[0].strip()
    return f"{clipped}..."


# Final safety-net cap on the reply sent to the client. This runs on the
# FINAL reply, which for non-English chats is already the TRANSLATED text
# (pipeline_process_query / multilingual_chatbot translates before
# returning) -- upstream chatbot_pipeline.py already caps the English
# reply per-path (140-350 words) before translation, so this used to be a
# blunt second cap of just 150 words with a raw `words[:150] + "..."` cut.
# That silently chopped longer (esp. "advanced" 320-word structured)
# replies mid-sentence once translated -- Hindi/Kannada/etc. commonly need
# MORE space-separated tokens than the English original for the same
# content, so a reply that fit comfortably under the upstream English cap
# could still land well past 150 words after translation and get its tail
# cut off (confirmed against a real stored reply: exactly 150 words,
# ending mid-sentence with "..."). Raised to a real safety-net width and
# always cut at the last sentence boundary (English/Devanagari-aware)
# instead of a raw word-index slice, so a cap that does trigger never
# leaves a reply hanging mid-sentence.
REPLY_WORD_CAP = 500
_SENTENCE_END_RE = re.compile(r"[.!?।॥]")


def _cap_reply_words(text: str, max_words: int = REPLY_WORD_CAP) -> str:
    """Hard safety-net cap on total reply length, trimmed at the last
    complete sentence at/under `max_words` rather than a raw word slice."""
    if not text:
        return text
    words = text.split()
    if len(words) <= max_words:
        return text
    truncated = " ".join(words[:max_words])
    last_end = max((m.end() for m in _SENTENCE_END_RE.finditer(truncated)), default=-1)
    if last_end != -1:
        return truncated[:last_end]
    return truncated + "..."

_DISEASE_KB: dict[str, dict[str, str]] = {}
_DISEASE_ALIASES: dict[str, str] = {}


_llm_pipeline = None
_LLM_MODEL = "distilgpt2"

def _get_llm():
    """Lazy-load the distilgpt2 text-generation pipeline."""
    global _llm_pipeline
    if _llm_pipeline is not None:
        return _llm_pipeline
    try:
        from transformers import pipeline
        print(f"Loading LLM ({_LLM_MODEL}) …")
        _llm_pipeline = pipeline(
            "text-generation",
            model=_LLM_MODEL,
            max_new_tokens=80,
            do_sample=False,        
            truncation=True,
        )
        print(f"SUCCESS LLM loaded ({_LLM_MODEL})")
    except Exception as e:
        print(f"LLM unavailable: {e}")
        _llm_pipeline = None
    return _llm_pipeline


def generate_advice(disease: str, symptoms: list) -> str:
    """Return concise, deterministic advice for a diagnosed disease.

    The previous generative fallback could produce hallucinated or repetitive
    output for some diseases, so the final advice now prefers curated KB text
    and a small rule-based fallback only.
    """
    kb_advice = _kb_advice_for_disease(disease)
    if kb_advice:
        return _shorten_answer(kb_advice, max_chars=180)

    return _shorten_answer(_fallback_advice(disease), max_chars=180)


def _fallback_advice(disease: str) -> str:
    """Rule-based fallback when LLM is unavailable."""
    # Prefer disease-specific guidance from the curated knowledge base.
    kb_advice = _kb_advice_for_disease(disease)
    if kb_advice:
        return kb_advice

    disease_lower = disease.lower()
    if any(k in disease_lower for k in ["diabetes", "sugar"]):
        return ("Monitor blood sugar levels regularly, follow a low-sugar diet, "
                "stay hydrated, and consult a doctor for medication guidance.")
    if any(k in disease_lower for k in ["hypertension", "blood pressure"]):
        return ("Reduce salt intake, avoid stress, exercise gently, "
                "and take prescribed medication consistently.")
    if any(k in disease_lower for k in ["malaria", "fever"]):
        return ("Rest, drink plenty of fluids, take prescribed antimalarials or "
                "antipyretics, and seek clinic care if fever persists beyond 2 days.")
    if any(k in disease_lower for k in ["pneumonia", "respiratory", "cough"]):
        return ("Rest, keep warm, drink warm fluids, and complete the full course "
                "of any prescribed antibiotics. Visit a health facility if breathing worsens.")
    disease_clean = str(disease).strip()
    if disease_clean and disease_clean.lower() not in {"unknown", "unknown condition", "not available"}:
        return (
            f"For {disease_clean}, follow prescribed treatment, rest well, stay hydrated, "
            "eat nutritious food, and consult the nearest health facility if symptoms worsen "
            "or do not improve in 48 hours."
        )

    return ("Rest well, stay hydrated, eat nutritious food, and consult "
            "the nearest health facility if symptoms worsen or do not improve in 48 hours.")


def _kb_advice_for_disease(disease: str) -> str | None:
    kb = _load_disease_kb()
    if not kb:
        return None

    disease_name = _detect_disease_in_question(disease) or str(disease or "").strip()
    if not disease_name:
        return None

    record = kb.get(disease_name)
    if not isinstance(record, dict):
        return None

    preferred_fields = [
        "How to control it?",
        "When to see a doctor?",
        "Prevention",
        "What should a patient do if they have this disease?",
        "What are the symptoms?",
        "What is the disease?",
    ]

    for field in preferred_fields:
        value = str(record.get(field, "")).strip()
        if not value:
            continue
        if field == "What are the symptoms?":
            return f"Common symptoms include {value}."
        return value if value[-1] in ".!?" else f"{value}."

    return None


def _normalize_text(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(text).lower()).strip()


# Deterministic phrase maps for Kannada and Hindi used when we must NOT call the LLM
TRANSLATIONS_KANNADA: dict[str, str] = {
    "- Problem:": "- ಸಮಸ್ಯೆ:",
    "- What to do:": "- ಮಾಡಬೇಕಾದದ್ದು:",
    "- When to see a doctor:": "- ವೈದ್ಯರನ್ನುいつ ನೋಡಬೇಕು:",
    "Consult a doctor if symptoms persist.": "ಲಕ್ಷಣಗಳು ಮುಂದುವರಿದರೆ ವೈದ್ಯರನ್ನು ಸಂಪರ್ಕಿಸಿ.",
    "Information is currently limited.": "ಈಗಾಗಲೇ ಮಾಹಿತಿಯನ್ನು ಹೊಂದಿಲ್ಲ.",
}

TRANSLATIONS_HINDI: dict[str, str] = {
    "- Problem:": "- समस्या:",
    "- What to do:": "- क्या करना चाहिए:",
    "- When to see a doctor:": "- डॉक्टर को कब देखें:",
    "Consult a doctor if symptoms persist.": "यदि लक्षण बने रहें तो डॉक्टर से परामर्श लें।",
    "Information is currently limited.": "वर्तमान में जानकारी सीमित है।",
}


def translate_answer(answer: str, lang: str) -> str:
    """Deterministically translate known phrases in `answer` to `lang` if supported.

    This intentionally does not call any external LLMs and only substitutes fixed phrases.
    """
    if not answer or lang == "english":
        return answer

    mapping = TRANSLATIONS_KANNADA if lang == "kannada" else TRANSLATIONS_HINDI if lang == "hindi" else {}
    out = str(answer)
    for eng, trans in mapping.items():
        out = out.replace(eng, trans)
    return out


def detect_intent(query: str) -> str:
    """Detect intent from the user's query using simple keyword rules.

    Rules (case-insensitive):
    - If query contains "what is" -> "definition"
    - If query contains "symptom" -> "symptoms"
    - If query contains "cause" -> "causes"
    - If query contains "treatment" or "what to do" -> "treatment"
    - If query contains "prevention" -> "prevention"
    - Otherwise -> "general"
    """
    if not isinstance(query, str) or not query.strip():
        return "general"
    q = query.lower()
    if "what is" in q:
        return "definition"
    if "symptom" in q:
        return "symptoms"
    if "cause" in q:
        return "causes"
    if "treatment" in q or "what to do" in q:
        return "treatment"
    if "prevention" in q:
        return "prevention"
    return "general"


def get_context_by_intent(disease_data: dict, intent: str) -> str:
    """Select a single context string from a disease KB record based on intent.

    Mapping:
    - definition -> "What is the disease?"
    - symptoms -> "What are the symptoms?"
    - causes -> "What causes the disease?"
    - treatment -> "What should a patient do if they have this disease?"
    - prevention -> "Prevention"
    - general -> "combined_text"
    """
    if not isinstance(disease_data, dict):
        return ""
    mapping = {
        "definition": "What is the disease?",
        "symptoms": "What are the symptoms?",
        "causes": "What causes the disease?",
        "treatment": "What should a patient do if they have this disease?",
        "prevention": "Prevention",
        "general": "combined_text",
    }
    key = mapping.get(intent, "combined_text")
    value = disease_data.get(key, "") if isinstance(disease_data, dict) else ""
    return str(value or "").strip()


BAD_PHRASES = [
    "i don't know", "i'm not sure",
    "as an ai", "i cannot", "i am not able"
]


def build_mistral_prompt(current_disease: str | None, user_input: str) -> str:
    if current_disease:
        return f"""You are a rural healthcare assistant in India.
Answer ONLY about {current_disease}.
Do NOT mention any other disease.
Be concise, factual, under 3 sentences.
Use simple English suitable for rural users.
If unsure say: I recommend consulting a nearby doctor.
Never make up symptoms, medicines or treatments.
For emergency diseases end with: Please visit a doctor immediately.
User question: {user_input}"""
    return f"""You are a rural healthcare assistant in India.
Answer only health related questions.
Be concise, factual, under 3 sentences.
Use simple English suitable for rural users.
User question: {user_input}"""


def _call_simple_llm(session_id: str | None, user_query: str, filtered_context: str, short_answer: bool = True) -> str:
    """Simple LLM caller that passes only the filtered context to the local LLM pipeline.

    If the local transformer pipeline is unavailable, produce a concise fallback using the
    provided context (or a short KB-style reply).
    Returns a short string reply (2-3 lines recommended by prompt).
    """
    if is_translation_query(user_query):
        return "Translation is handled separately."

    # Get limited conversation history (only last MAX_HISTORY exchanges) to prevent Mistral confusion
    session_key = str(session_id) if session_id else "default"
    recent_history = get_limited_history(session_key)
    current_disease = _get_session_disease(session_key)
    
    # Build prompt with limited context history
    history_text = ""
    if recent_history:
        history_text = "Recent conversation:\n"
        for msg in recent_history:
            role = msg.get("role", "user").capitalize()
            content = msg.get("content", "")[:200]  # Truncate long history entries
            history_text += f"{role}: {content}\n"
        history_text += "\n"
    
    prompt = build_mistral_prompt(current_disease, user_query)
    if filtered_context:
        prompt = f"{prompt}\n\nContext:\n{filtered_context}"
    if history_text:
        prompt = f"{history_text}{prompt}"

    # Try Ollama first (if installed and running)
    try:
        try:
            import ollama
        except Exception:
            ollama = None

        if ollama is not None:
            try:
                response = ollama.generate(
                    model="cniongolo/biomistral:latest",
                    prompt=prompt,
                    stream=False,
                    options={
                        "temperature": float(os.getenv("OLLAMA_TEMPERATURE", "0")),
                        "num_predict": int(os.getenv("OLLAMA_NUM_PREDICT", "150")),
                        "top_p": float(os.getenv("OLLAMA_TOP_P", "0.9")),
                    },
                )
                generated_text = response.get("response", "") or response.get("generated_text", "")
                generated = str(generated_text).strip()
                if any(p in generated.lower() for p in BAD_PHRASES):
                    generated = "I recommend consulting a nearby doctor or health worker."
                # Keep only first 2-3 sentences
                sentences = re.split(r'(?<=[.!?])\s+', generated)
                reply = " ".join(sentences[:3]).strip() if sentences else generated
                if len(reply) > 600:
                    reply = reply[:597].rsplit(" ", 1)[0] + "..."
                return reply
            except Exception as e:
                print(f"Ollama generate error: {e}")

        # Fall back to local transformer pipeline
        llm = _get_llm()
        if llm is None:
            # Fallback: derive a short reply from filtered_context or a rule-based summary
            if filtered_context:
                text = re.sub(r"\s+", " ", filtered_context).strip()
                sentences = re.split(r'(?<=[.!?])\s+', text)
                summary = " ".join(sentences[:2]) if sentences and len(sentences) > 0 else text
                if len(summary) > 280:
                    summary = summary[:277].rsplit(" ", 1)[0] + "..."
                return summary
            # If no context, return a generic safe message
            return "I don't have specifics on that. Please consult a healthcare provider for details."

        try:
            outputs = llm(prompt, max_new_tokens=120, do_sample=False)
            full = outputs[0].get("generated_text", "")
            if full.startswith(prompt):
                generated = full[len(prompt):].strip()
            else:
                generated = full
            sentences = re.split(r'(?<=[.!?])\s+', generated)
            reply = " ".join(sentences[:3]).strip() if sentences else generated.strip()
            if len(reply) > 600:
                reply = reply[:597].rsplit(" ", 1)[0] + "..."
            return reply
        except Exception as e:
            print(f"LLM pipeline error: {e}")
            if filtered_context:
                return (filtered_context.split(". ")[:2] and " ".join(filtered_context.split(". ")[:2])) or filtered_context
            return "I don't have details for that condition; please consult a healthcare professional."
    except Exception as e:
        print(f"_call_simple_llm unexpected error: {e}")
        if filtered_context:
            return filtered_context.split('\n')[0][:300]
        return "I don't have details for that condition; please consult a healthcare professional."


def _load_disease_kb() -> dict[str, dict[str, str]]:
    global _DISEASE_KB_CACHE
    if _DISEASE_KB_CACHE is not None:
        return _DISEASE_KB_CACHE

    try:
        with open(DISEASE_KB_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        _DISEASE_KB_CACHE = data if isinstance(data, dict) else {}
    except Exception as exc:
        print(f"KB load warning: {exc}")
        _DISEASE_KB_CACHE = {}

    return _DISEASE_KB_CACHE


def _build_disease_aliases(kb: dict[str, dict[str, str]]) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for disease in kb.keys():
        disease_name = str(disease or "").strip()
        if not disease_name:
            continue

        aliases[_normalize_text(disease_name)] = disease_name
        aliases[_normalize_text(f"what is {disease_name}")] = disease_name
        aliases[_normalize_text(f"what are {disease_name}")] = disease_name

    aliases.update({
        "dengue fever": "Dengue",
        "high blood pressure": "Hypertension",
        "blood pressure": "Hypertension",
        "diabetes mellitus": "Diabetes",
        "sugar disease": "Diabetes",
    })
    return aliases


def _extract_disease_candidate(question: str) -> str | None:
    """Extract probable disease phrase from user question text."""
    q_norm = _normalize_text(question)
    if not q_norm:
        return None

    patterns = [
        r"(?:what is|what are)\s+([a-z0-9\s]+)",
        r"(?:symptoms|signs|cause|causes|treatment|cure|medicine|management)\s+(?:of|for)\s+([a-z0-9\s]+)",
        r"(?:about)\s+([a-z0-9\s]+)",
    ]

    for pattern in patterns:
        match = re.search(pattern, q_norm)
        if not match:
            continue

        candidate = match.group(1).strip()
        candidate = re.sub(r"\b(the|disease|illness|problem|condition)\b", "", candidate)
        candidate = re.sub(r"\s+", " ", candidate).strip()
        if candidate:
            return candidate

    return None


def _detect_disease_in_question(question: str) -> str | None:
    q_norm = _normalize_text(question)
    if not q_norm:
        return None

    kb = _load_disease_kb()
    global _DISEASE_ALIAS_CACHE
    if _DISEASE_ALIAS_CACHE is None:
        _DISEASE_ALIAS_CACHE = _build_disease_aliases(kb)

    # 1) Try to extract an explicit candidate phrase from the question
    candidate = _extract_disease_candidate(question)
    if candidate:
        candidate_norm = _normalize_text(candidate)
        # exact alias match
        if candidate_norm in _DISEASE_ALIAS_CACHE:
            detected = _DISEASE_ALIAS_CACHE[candidate_norm]
            print(f"[detect] Exact alias match: '{detected}' for candidate '{candidate}'")
            return detected
        # exact disease name match
        for disease in kb.keys():
            if candidate_norm == _normalize_text(disease):
                print(f"[detect] Exact disease name match: '{disease}' for candidate '{candidate}'")
                return disease
        # close match using difflib for minor spelling mistakes
        alias_keys = list(_DISEASE_ALIAS_CACHE.keys())
        close = difflib.get_close_matches(candidate_norm, alias_keys, n=1, cutoff=0.75)
        if close:
            detected = _DISEASE_ALIAS_CACHE.get(close[0])
            print(f"[detect] Close alias match: '{detected}' (matched '{close[0]}') for candidate '{candidate}'")
            return detected

    # 2) Partial matching in the full question (e.g., 'i have dengue fever')
    for disease in kb.keys():
        disease_norm = _normalize_text(disease)
        if disease_norm and (disease_norm in q_norm or q_norm in disease_norm):
            print(f"[detect] Partial match: '{disease}' found in question")
            return disease

    # 3) Alias substring matching
    for alias, disease in _DISEASE_ALIAS_CACHE.items():
        if alias and (alias in q_norm or q_norm in alias):
            print(f"[detect] Alias substring match: alias '{alias}' -> disease '{disease}'")
            return disease

    # 4) Final close-match over disease names for fuzzy matching
    disease_keys_norm = [_normalize_text(d) for d in kb.keys()]
    close2 = difflib.get_close_matches(q_norm, disease_keys_norm, n=1, cutoff=0.75)
    if close2:
        # map normalized key back to original disease name
        match_norm = close2[0]
        for d in kb.keys():
            if _normalize_text(d) == match_norm:
                print(f"[detect] Final close disease match: '{d}' (matched '{match_norm}')")
                return d

    # No disease detected
    return None


def _detect_question_type(question: str) -> str | None:
    """Infer which standard disease Q/A field the user is asking for."""
    q = _normalize_text(question)
    if not q:
        return None

    should_do_markers = [
        "what should", "what to do", "what do i do", "what should i do", "treatment",
        "manage", "management", "care", "medicine", "remedy", "how can i recover",
    ]
    symptoms_markers = ["symptom", "sign", "indication", "how does it feel"]
    early_stage_markers = [
        "early stage", "early symptom", "initial symptom", "first symptom", "starting symptom",
        "beginning symptom", "starting stage", "first stage",
    ]
    effects_markers = ["affect", "effect", "impact", "complication", "complications", "dangerous"]
    prevention_markers = ["prevent", "prevention", "precaution", "avoid", "reduce risk"]
    nutrition_markers = ["food", "foods", "eat", "eating", "diet", "consume", "meal", "meals", "nutrition", "nutritional"]
    exercise_markers = ["exercise", "workout", "physical activity", "yoga", "walk", "walking"]
    causes_markers = ["cause", "causes", "why", "reason", "how do you get"]
    stages_markers = ["stage", "stages", "level", "how bad", "how serious", "grade"]
    emergency_signs_markers = [
        "emergency",
        "very emergency",
        "very serious",
        "critical",
        "life threatening",
        "emergency sign",
        "danger sign",
        "when is it dangerous",
    ]
    what_is_markers = ["what is", "what is the disease", "define", "about", "explain"]

    if any(marker in q for marker in early_stage_markers):
        return "Early Stage Symptoms"
    if any(marker in q for marker in should_do_markers):
        return "What should a patient do if they have this disease?"
    if any(marker in q for marker in symptoms_markers):
        return "What are the symptoms?"
    if any(marker in q for marker in effects_markers):
        return "What are the symptoms?"
    if any(marker in q for marker in prevention_markers):
        return "Prevention"
    if any(marker in q for marker in nutrition_markers):
        return "Food"
    if any(marker in q for marker in exercise_markers):
        return "Exercise"
    if any(marker in q for marker in causes_markers):
        return "What causes the disease?"
    if any(marker in q for marker in stages_markers):
        return "Stages"
    if any(marker in q for marker in emergency_signs_markers):
        return "Emergency signs"
    if any(marker in q for marker in what_is_markers):
        return "What is the disease?"
    return None


def _is_prevention_or_diet_question(question: str) -> bool:
    """Detect if question is specifically about prevention or diet (not general 'what to do')."""
    q = _normalize_text(question)
    prevention_markers = ["prevent", "prevention", "precaution", "avoid", "how to avoid", "reduce risk"]
    diet_markers = ["food", "foods", "eat", "eating", "diet", "consume", "meal", "meals", "nutrition", "nutritional", "what to eat", "what can i eat"]
    return any(marker in q for marker in prevention_markers) or any(marker in q for marker in diet_markers)


VAGUE_FOLLOWUPS = [
    "it",
    "this",
    "that",
    "this disease",
    "that disease",
    "how it",
    "how will it",
    "will it",
    "is it",
    "can it",
    "does it",
    "what about",
    "during this",
    "during it",
    "for this",
    "for it",
    "avoid it",
    "food",
    "foods",
    "eat",
    "diet",
    "consume",
    "stage",
    "stages",
    "level",
    "grade",
    "how bad",
    "how serious",
    "emergency",
    "critical",
    "life threatening",
    "danger sign",
]


def _is_followup_question(question: str) -> bool:
    """Detect brief follow-up prompts that rely on previous disease context."""
    q = _normalize_text(question)
    if not q:
        return False

    if len(q.split()) <= 14 and any(marker in q for marker in VAGUE_FOLLOWUPS):
        return True

    return False


def _infer_recent_disease_from_session(session_id: str) -> str | None:
    """Infer the most recently discussed disease from the current session history."""
    if not session_id:
        return None

    # First, check session-level cached disease with TTL (if present)
    try:
        remembered = _get_session_disease(session_id)
        if remembered:
            print(f"[session] Using remembered disease '{remembered}' for session {session_id}")
            return remembered
    except Exception:
        pass

    # Fallback: scan recent chat history for explicit disease mentions
    hist = list(get_history(session_id))
    if not hist:
        return None

    for user_text, _assistant_text in reversed(hist):
        disease = _detect_disease_in_question(user_text)
        if disease:
            return disease
    return None


def _build_kb_style_answer(disease: str, qa: dict[str, str], question_type: str | None = None) -> str:
    """Compose a high-quality fixed-format answer from KB fields."""
    disease_name = str(disease or "").strip() or "This condition"
    about = str(qa.get("What is the disease?", "")).strip()
    symptoms = str(qa.get("What are the symptoms?", "")).strip()
    causes = str(qa.get("What causes the disease?", "")).strip()
    early_stage = str(qa.get("Early Stage Symptoms", "")).strip()
    prevention = str(qa.get("Prevention", "")).strip()
    food = str(qa.get("Food", "")).strip()
    exercise = str(qa.get("Exercise", "")).strip()
    stages = str(qa.get("Stages", "")).strip()
    emergency_signs = str(qa.get("Emergency signs", "")).strip()
    advice = str(qa.get("What should a patient do if they have this disease?", "")).strip()

    if not advice:
        advice = "Rest, stay hydrated, and follow guidance from a qualified healthcare provider."

    if question_type == "What are the symptoms?":
        problem = f"{disease_name}: {symptoms}" if symptoms else f"{disease_name}: Symptoms information is currently limited."
    elif question_type == "Early Stage Symptoms":
        problem = (
            f"{disease_name}: Early stage symptoms include {early_stage}"
            if early_stage else
            f"{disease_name}: Early stage symptom information is currently limited."
        )
        if not advice:
            advice = "Monitor symptoms closely, rest, and consult a doctor if symptoms get worse."
    elif question_type == "Prevention":
        problem = (
            f"{disease_name}: You can reduce risk by {prevention}"
            if prevention else
            f"{disease_name}: Prevention information is currently limited."
        )
        if not advice:
            advice = "Follow hygiene, avoid known triggers, and consult a doctor for personal prevention advice."
    elif question_type == "Food":
        problem = (
            f"{disease_name}: Recommended food guidance is {food}"
            if food else
            f"{disease_name}: Food guidance is currently limited."
        )
        if not advice:
            advice = "Take light, balanced meals and stay hydrated unless your doctor advised otherwise."
    elif question_type == "Exercise":
        problem = (
            f"{disease_name}: Exercise guidance is {exercise}"
            if exercise else
            f"{disease_name}: Exercise guidance is currently limited."
        )
        if not advice:
            advice = "Do only gentle activity and stop if symptoms worsen."
    elif question_type == "Stages":
        problem = (
            f"Stages of {disease_name}: {stages}"
            if stages else
            f"{disease_name}: Stage information is currently limited."
        )
        if not advice:
            advice = "Get medical staging through proper tests and follow specialist treatment plans."
    elif question_type == "Emergency signs":
        problem = (
            f"Emergency warning signs of {disease_name}: {emergency_signs}"
            if emergency_signs else
            f"{disease_name}: Emergency sign information is currently limited."
        )
        if not advice:
            advice = "If danger signs appear, seek emergency hospital care immediately."
    elif question_type == "What causes the disease?":
        if causes and symptoms:
            problem = f"{disease_name}: Caused by {causes} Common symptoms include {symptoms}"
        elif causes:
            problem = f"{disease_name}: Caused by {causes}"
        else:
            problem = f"{disease_name}: Cause information is currently limited."
    elif question_type == "What is the disease?":
        if about and symptoms:
            problem = f"{disease_name}: {about} Common symptoms include {symptoms}"
        elif about:
            problem = f"{disease_name}: {about}"
        elif symptoms:
            problem = f"{disease_name}: Common symptoms include {symptoms}"
        else:
            problem = f"{disease_name}: Information is currently limited."
    else:
        if symptoms and causes:
            problem = f"{disease_name}: Symptoms include {symptoms} Main cause: {causes}"
        elif symptoms:
            problem = f"{disease_name}: Symptoms include {symptoms}"
        elif about:
            problem = f"{disease_name}: {about}"
        else:
            problem = f"{disease_name}: Information is currently limited."

    when_to_see = (
        "See a doctor urgently if symptoms worsen, breathing becomes difficult, "
        "high fever persists, or there are danger signs like chest pain or dehydration."
    )

    problem = _normalize_punctuation_artifacts(re.sub(r"\s+", " ", problem).strip())
    advice = _normalize_punctuation_artifacts(re.sub(r"\s+", " ", advice).strip())
    when_to_see = _normalize_punctuation_artifacts(re.sub(r"\s+", " ", when_to_see).strip())

    if problem and problem[-1] not in ".!?":
        problem += "."
    if advice and advice[-1] not in ".!?":
        advice += "."
    if when_to_see and when_to_see[-1] not in ".!?":
        when_to_see += "."

    return "\n".join([
        f"- Problem: {problem}",
        f"- What to do: {advice}",
        f"- When to see a doctor: {when_to_see}",
        f"- {_normalize_punctuation_artifacts(STYLE_FOOTER)}",
    ])


def _disease_kb_answer(
    question: str,
    session_id: str | None = None,
    enable_openai_enrichment: bool = False,
) -> str | None:
    del enable_openai_enrichment

    if not isinstance(question, str) or not question.strip():
        return None

    kb = _load_disease_kb()
    if not kb:
        return None

    # Try to detect disease from the question or recent session
    disease = _detect_disease_in_question(question)
    if disease is None and session_id:
        disease = _infer_recent_disease_from_session(session_id)

    # If disease found, use intent-based field selection and ask LLM only that field
    if disease:
        qa = kb.get(disease)
        if not isinstance(qa, dict):
            return None

        intent = detect_intent(question)
        filtered_context = get_context_by_intent(qa, intent)

        # If selected field is empty, fall back to combined_text or the KB-style answer
        if not filtered_context:
            filtered_context = str(qa.get("combined_text", "") or "").strip()

        # If still empty, return the structured KB answer (best effort)
        if not filtered_context:
            answer = _build_kb_style_answer(disease, qa, _detect_question_type(question))
            if _is_incomplete_kb_answer(answer):
                return None
            return answer

        # Call LLM with only the filtered context (do NOT pass the entire disease object)
        try:
            reply = _call_simple_llm(session_id, question, filtered_context, short_answer=True)
            reply = _apply_fixed_style(reply)
            if reply and not _is_incomplete_kb_answer(reply):
                return reply
        except Exception:
            pass

        # Final fallback: return KB-style composed answer
        answer = _build_kb_style_answer(disease, qa, _detect_question_type(question))
        if _is_incomplete_kb_answer(answer):
            return None
        return answer

    # If no disease detected, ask LLM to generate a short, general explanation
    try:
        general_reply = _call_simple_llm(session_id, question, "", short_answer=True)
        general_reply = _apply_fixed_style(general_reply)
        if general_reply:
            return general_reply
    except Exception:
        pass

    # If all else fails, return an unknown-disease safe reply
    return _unknown_disease_reply(None)


def _is_incomplete_kb_answer(answer: str) -> bool:
    text = _normalize_text(answer)
    if not text:
        return True
    return any(marker in text for marker in [
        "information is currently limited",
        "currently limited",
        "unable to identify",
        "not available",
    ])

USERS_FILE = "users.json"
users = {}
PATIENTS_FILE = "patients.json"


def _load_patients_json():
    """Read all records from patients.json; returns a list."""
    if not os.path.exists(PATIENTS_FILE):
        return []
    try:
        with open(PATIENTS_FILE, 'r') as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _append_to_patients_json(record):
    """Append a single record to patients.json (creates file if absent)."""
    existing = _load_patients_json()
    existing.append(record)
    with open(PATIENTS_FILE, 'w') as f:
        json.dump(existing, f, indent=2)


def migrate_patients_json_to_mysql():
    """One-time migration: import any records from patients.json into MySQL
    (only runs when the table is still empty, so it's safe to call every
    startup)."""
    if not mysql_store.is_available():
        return
    if not os.path.exists(PATIENTS_FILE):
        return
    try:
        with open(PATIENTS_FILE, 'r') as f:
            records = json.load(f)
        if not isinstance(records, list) or len(records) == 0:
            return
        if mysql_store.list_patients():
            print("SUCCESS MySQL patients table already has data — skipping JSON migration")
            return
        migrated = 0
        for rec in records:
            rec.pop('_id', None)
            if mysql_store.insert_patient(rec) is not None:
                migrated += 1
        print(f"SUCCESS Migrated {migrated} patient record(s) from patients.json to MySQL")
    except Exception as e:
        print(f"Migration warning: {e}")


def to_float(value, default=0.0):
    try:
        if value is None or value == "":
            return float(default)
        return float(value)
    except (TypeError, ValueError):
        return float(default)


_KNOWN_SYMPTOMS = [
    "Fever", "Cough", "Runny Nose", "Shortness of Breath", "Fatigue", "Headache",
    "Body Aches", "Sore Throat", "Nausea", "Vomiting", "Diarrhea", "Loss of Appetite",
    "Chest Pain", "Chills", "Dizziness", "Joint Pain", "Muscle Pain", "Skin Rash",
    "Frequent Urination", "Increased Thirst", "Blurred Vision", "High Blood Pressure",
]

_SYMPTOM_KEYWORDS = {
    "Fever": ["fever", "pyrexia"],
    "Cough": ["cough"],
    "Runny Nose": ["runny nose", "rhinorrhea"],
    "Shortness of Breath": ["shortness of breath", "breathlessness", "dyspnea"],
    "Fatigue": ["fatigue", "tiredness"],
    "Headache": ["headache"],
    "Body Aches": ["body ache", "body pain"],
    "Sore Throat": ["sore throat"],
    "Nausea": ["nausea"],
    "Vomiting": ["vomiting", "vomit"],
    "Diarrhea": ["diarrhea", "loose motion", "loose stool"],
    "Loss of Appetite": ["loss of appetite", "poor appetite"],
    "Chest Pain": ["chest pain"],
    "Chills": ["chills", "shivering"],
    "Dizziness": ["dizziness", "giddiness"],
    "Joint Pain": ["joint pain", "arthralgia"],
    "Muscle Pain": ["muscle pain", "myalgia"],
    "Skin Rash": ["skin rash", "rash"],
    "Frequent Urination": ["frequent urination", "polyuria"],
    "Increased Thirst": ["increased thirst", "polydipsia"],
    "Blurred Vision": ["blurred vision"],
    "High Blood Pressure": ["high blood pressure", "hypertension"],
}


def _extract_text_from_uploaded_report(uploaded_file) -> str:
    """Extract plain text from uploaded image/pdf report where possible."""
    if uploaded_file is None or not getattr(uploaded_file, "filename", ""):
        return ""

    filename = str(uploaded_file.filename)
    ext = os.path.splitext(filename)[1].lower()
    temp_path = os.path.join(tempfile.gettempdir(), f"rural_report_{os.getpid()}_{filename}")

    try:
        uploaded_file.save(temp_path)
        text = ""

        if ext in {".jpg", ".jpeg", ".png"}:
            try:
                import pytesseract
                from PIL import Image
                tesseract_cmd = os.environ.get("TESSERACT_CMD", "").strip()
                if tesseract_cmd:
                    pytesseract.pytesseract.tesseract_cmd = tesseract_cmd
                text = pytesseract.image_to_string(Image.open(temp_path)) or ""
            except Exception as e:
                print(f"OCR extraction warning: {e}")
                text = ""
        elif ext == ".pdf":
            try:
                from pypdf import PdfReader
                reader = PdfReader(temp_path)
                text = " ".join((page.extract_text() or "") for page in reader.pages)
            except Exception:
                try:
                    import pdfplumber
                    with pdfplumber.open(temp_path) as pdf:
                        for page in pdf.pages:
                            text += page.extract_text() or ""
                except Exception as e:
                    print(f"PDF extraction warning: {e}")
                    text = ""

        return text.strip()
    finally:
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except Exception:
            pass


def _extract_report_fields(text: str) -> dict:
    """Parse common patient/vitals fields from report text."""
    if not text:
        return {}

    t = re.sub(r"\s+", " ", text).strip()
    tl = t.lower()

    fields = {
        "patientName": "",
        "age": "",
        "bloodPressureSystolic": "",
        "bloodPressureDiastolic": "",
        "heartRate": "",
        "temperature": "",
        "sugarLevel": "",
        "labTestResult": "",
        "symptoms": [],
    }

    name_match = re.search(r"(?:patient\s*name|name)\s*[:\-]\s*([A-Za-z][A-Za-z\s]{1,60})", t, re.IGNORECASE)
    if name_match:
        fields["patientName"] = " ".join(name_match.group(1).split())

    age_match = re.search(r"(?:age|yrs|years)\s*[:\-]?\s*(\d{1,3})", t, re.IGNORECASE)
    if age_match:
        fields["age"] = age_match.group(1)

    bp_match = re.search(r"(?:bp|blood\s*pressure)\s*[:\-]?\s*(\d{2,3})\s*/\s*(\d{2,3})", t, re.IGNORECASE)
    if not bp_match:
        bp_match = re.search(r"\b(\d{2,3})\s*/\s*(\d{2,3})\s*mmhg\b", t, re.IGNORECASE)
    if bp_match:
        fields["bloodPressureSystolic"] = bp_match.group(1)
        fields["bloodPressureDiastolic"] = bp_match.group(2)

    hr_match = re.search(r"(?:heart\s*rate|pulse|hr)\s*[:\-]?\s*(\d{2,3})", t, re.IGNORECASE)
    if hr_match:
        fields["heartRate"] = hr_match.group(1)

    temp_match = re.search(r"(?:temperature|temp)\s*[:\-]?\s*(\d{2,3}(?:\.\d+)?)\s*([CF])?", t, re.IGNORECASE)
    if temp_match:
        temp_val = float(temp_match.group(1))
        temp_unit = (temp_match.group(2) or "F").upper()
        if temp_unit == "C":
            temp_val = (temp_val * 9.0 / 5.0) + 32.0
        fields["temperature"] = f"{temp_val:.1f}".rstrip("0").rstrip(".")

    sugar_match = re.search(r"(?:blood\s*sugar|glucose|rbs|fbs)\s*[:\-]?\s*(\d{2,3}(?:\.\d+)?)", t, re.IGNORECASE)
    if sugar_match:
        fields["sugarLevel"] = sugar_match.group(1)

    lab_match = re.search(r"(?:lab\s*test\s*result|lab\s*result|hba1c)\s*[:\-]?\s*(\d+(?:\.\d+)?)", t, re.IGNORECASE)
    if lab_match:
        fields["labTestResult"] = lab_match.group(1)

    detected_symptoms = []
    for symptom in _KNOWN_SYMPTOMS:
        keywords = _SYMPTOM_KEYWORDS.get(symptom, [symptom.lower()])
        if any(kw in tl for kw in keywords):
            detected_symptoms.append(symptom)
    fields["symptoms"] = detected_symptoms

    return fields


def _merge_report_fields_into_data(data: dict, report_fields: dict) -> dict:
    """Fill missing form fields using parsed report values."""
    merged = dict(data or {})
    for key, value in (report_fields or {}).items():
        if key == "symptoms":
            current = merged.get("symptoms")
            if not isinstance(current, list) or len(current) == 0:
                merged["symptoms"] = value if isinstance(value, list) else []
            continue

        current = str(merged.get(key, "")).strip()
        if not current and str(value).strip():
            merged[key] = value
    return merged


def get_request_data():
    if request.is_json:
        return request.get_json(silent=True) or {}, None

    form_data = request.form.to_dict()
    uploaded_file = request.files.get("medicalReport")

    symptoms_value = form_data.get("symptoms")
    if isinstance(symptoms_value, str):
        try:
            parsed_symptoms = json.loads(symptoms_value)
            if isinstance(parsed_symptoms, list):
                form_data["symptoms"] = parsed_symptoms
            else:
                form_data["symptoms"] = []
        except json.JSONDecodeError:
            form_data["symptoms"] = []
    elif symptoms_value is None:
        form_data["symptoms"] = []

    # AI-mapped symptom tokens (natural-language -> model tokens) arrive as a
    # separately-encoded JSON string, mirroring the 'symptoms' field.
    extra_value = form_data.get("extra_symptoms", "[]")
    parsed_extra = []
    if isinstance(extra_value, str):
        try:
            parsed = json.loads(extra_value)
            parsed_extra = parsed if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            parsed_extra = []
    form_data["extra_symptoms"] = [
        str(t).strip() for t in parsed_extra if str(t).strip()
    ]

    return form_data, uploaded_file

def load_users():
    """Load legacy login records from MySQL first, fallback to JSON file."""
    global users

    try:
        db_users = mysql_store.legacy_users_get_all()
        if db_users:
            users = db_users
            return
    except Exception as e:
        print(f"Failed to load users from MySQL: {e}")

    if os.path.exists(USERS_FILE):
        try:
            with open(USERS_FILE, 'r') as f:
                users = json.load(f)
        except Exception:
            users = {}
    else:
        users = {}

    # Best-effort migration from JSON to MySQL when available.
    if users and mysql_store.is_available():
        try:
            for email, user in users.items():
                normalized_email = str(email).strip().lower()
                mysql_store.legacy_user_upsert(
                    normalized_email, str(user.get("fullName", "")).strip(), str(user.get("password", ""))
                )
        except Exception as e:
            print(f"JSON-to-MySQL user migration warning: {e}")


def save_users():
    """Save users to MySQL and JSON (backup)."""
    try:
        for email, user in users.items():
            normalized_email = str(email).strip().lower()
            mysql_store.legacy_user_upsert(
                normalized_email, str(user.get("fullName", "")).strip(), str(user.get("password", ""))
            )
    except Exception as e:
        print(f"Failed to save users to MySQL: {e}")

    try:
        with open(USERS_FILE, 'w') as f:
            json.dump(users, f)
    except Exception as e:
        print(f"Failed to save users.json backup: {e}")


def store_patient_record(data, prediction_result, uploaded_report=None):
    """Persist one patient visit and model result.
    Saves to MySQL, falling back to patients.json when unavailable."""
    symptoms = data.get("symptoms", [])
    if not isinstance(symptoms, list):
        symptoms = []

    record = {
        "createdAt": datetime.now().isoformat(timespec="seconds"),
        "email": str(data.get("email", "")).strip().lower(),
        "patientName": str(data.get("patientName", "")).strip() or "Unknown",
        "age": str(data.get("age", "")).strip(),
        "bloodPressureSystolic": str(data.get("bloodPressureSystolic", "")).strip(),
        "bloodPressureDiastolic": str(data.get("bloodPressureDiastolic", "")).strip(),
        "heartRate": str(data.get("heartRate", "")).strip(),
        "temperature": str(data.get("temperature", "")).strip(),
        "sugarLevel": str(data.get("sugarLevel", "")).strip(),
        "labTestResult": str(data.get("labTestResult", "")).strip(),
        "symptoms": symptoms,
        "medicalReportName": uploaded_report.filename if uploaded_report else "",
        "predictedDisease": prediction_result.get("predicted_disease", "N/A"),
        "confidence": prediction_result.get("confidence", 0),
        "riskCategory": prediction_result.get("risk_category", "Unknown"),
        "riskLevel": prediction_result.get("risk_level", "Unknown"),
        "riskScore": prediction_result.get("risk_score", 0),
        "recommendation": prediction_result.get("recommendation", ""),
    }

    if mysql_store.insert_patient(record) is not None:
        return True

    # MySQL unavailable -- fall back to the local JSON file, same degrade
    # path /patients GET/DELETE already use.
    try:
        existing = _load_patients_json()
        next_id = max((int(r.get("id", 0) or 0) for r in existing), default=0) + 1
        record["id"] = next_id
        _append_to_patients_json(record)
        return True
    except Exception as e:
        print(f"Patient record was not saved: MySQL unavailable and JSON fallback failed: {e}")
        return False


def load_guarded_pipeline():
    """Load the new guarded 40-symptom ensemble lazily. Returns (ok, error)."""
    return _new_load_models()

def analyze_vitals(data):
    """Analyze vital signs independently — returns risk_category and vitals_analysis"""
    bp_sys = to_float(data.get('bloodPressureSystolic', 0))
    bp_dia = to_float(data.get('bloodPressureDiastolic', 0))
    hr = to_float(data.get('heartRate', 0))
    temp = to_float(data.get('temperature', 0))
    sugar = to_float(data.get('sugarLevel', 0))

    # Individual vital status
    if bp_sys >= 140 or bp_dia >= 90:
        bp_status = "High"
    elif bp_sys >= 120 or bp_dia >= 80:
        bp_status = "Elevated"
    else:
        bp_status = "Normal"

    if hr > 110 or hr < 50:
        hr_status = "High Risk"
    elif hr > 100:
        hr_status = "Elevated"
    else:
        hr_status = "Normal"

    if temp >= 102:
        temp_status = "High Fever"
    elif temp >= 100.4:
        temp_status = "Fever"
    else:
        temp_status = "Normal"

    if sugar >= 200:
        sugar_status = "High"
    elif sugar >= 126:
        sugar_status = "Elevated"
    else:
        sugar_status = "Normal"

    # Overall risk score from vitals
    score = 0
    age = to_float(data.get('age', 0))
    if age >= 65: score += 18
    elif age >= 50: score += 10
    elif age < 12: score += 6

    if bp_sys >= 160 or bp_dia >= 100: score += 22
    elif bp_sys >= 140 or bp_dia >= 90: score += 14

    if hr > 110 or hr < 50: score += 14
    elif hr > 95: score += 8

    if temp >= 102: score += 16
    elif temp >= 100.4: score += 10

    if sugar >= 250: score += 18
    elif sugar >= 180: score += 12

    lab = to_float(data.get('labTestResult', 0))
    if lab >= 8: score += 14
    elif lab >= 5: score += 8

    symptoms = data.get('symptoms', [])
    score += min(len(symptoms) * 4, 20)
    if any(s in symptoms for s in ["Shortness of Breath", "Chest Pain"]):
        score += 10

    risk_score = max(5, min(95, round(score)))

    if risk_score >= 70:
        risk_category = "High"
    elif risk_score >= 40:
        risk_category = "Moderate"
    else:
        risk_category = "Low"

    return {
        "risk_category": risk_category,
        "risk_score": risk_score,
        "vitals_analysis": {
            "bp": bp_status,
            "heart_rate": hr_status,
            "temperature": temp_status,
            "sugar": sugar_status
        }
    }


def evaluate_assessment(data):
    """Fallback assessment when ML model is not loaded — uses LLM for advice."""
    vitals = analyze_vitals(data)
    risk_category = vitals["risk_category"]
    symptoms = data.get("symptoms", [])
    disease_label = "Unknown condition"
    risk_prefix = _recommendation_for_risk(risk_category)
    llm_advice = generate_advice(disease_label, symptoms)
    recommendation = _shorten_answer(f"{risk_prefix} {llm_advice}", max_chars=220)
    return {
        "success": True,
        "predicted_disease": "Not available (model not loaded)",
        "confidence": 0,
        "risk_category": risk_category,
        "risk_score": vitals["risk_score"],
        "risk_level": _compute_risk_level("Unknown", 0, False),
        "vitals_analysis": vitals["vitals_analysis"],
        "recommendation": recommendation,
    }

def _recommendation_for_risk(risk_category: str) -> str:
    """Vitals-based risk recommendation used as a prefix to the LLM advice."""
    if risk_category == "High":
        return "Urgent physician review is advised."
    elif risk_category == "Moderate":
        return "Close follow-up within 24-48 hours is recommended."
    else:
        return "Continue supportive care and monitor for changes."


@app.route('/predict', methods=['POST'])
def predict():
    """
    Predict health assessment based on patient data
    Expected JSON from React form:
    {
        "patientName": string,
        "age": string,
        "bloodPressureSystolic": string,
        "bloodPressureDiastolic": string,
        "heartRate": string,
        "temperature": string,
        "sugarLevel": string,
        "labTestResult": string,
        "symptoms": [list of symptom strings]
    }
    """
    try:
        data, uploaded_report = get_request_data()

        if uploaded_report is not None:
            report_text = _extract_text_from_uploaded_report(uploaded_report)
            parsed_fields = _extract_report_fields(report_text)
            data = _merge_report_fields_into_data(data, parsed_fields)
        
        if not data:
            return jsonify({
                "disease": "Error",
                "risk": 0,
                "error": "No data provided"
            }), 400

        # Merge checkbox selections with AI-mapped extra symptoms, deduplicate
        # (preserving checkbox order). The 17-symptom cap below only mattered
        # for the legacy 17-column model; the new guarded pipeline has none.
        checkbox_symptoms = data.get("symptoms", [])
        if not isinstance(checkbox_symptoms, list):
            checkbox_symptoms = [checkbox_symptoms]
        extra_symptoms = data.get("extra_symptoms", [])
        if not isinstance(extra_symptoms, list):
            extra_symptoms = []

        combined: list[str] = []
        _seen: set[str] = set()
        for _s in list(checkbox_symptoms) + list(extra_symptoms):
            _key = str(_s or "").strip()
            if _key and _key not in _seen:
                _seen.add(_key)
                combined.append(_key)
        # NOTE: the 17-symptom cap below applied only to the legacy 17-column
        # positional model; the new guarded pipeline has NO cap.
        combined = combined[:17]

        data["symptoms"] = combined
        data["extra_symptoms"] = [str(t or "").strip() for t in extra_symptoms if str(t or "").strip()]

        try:
            _new_pipeline_ok, _new_pipeline_err = _new_load_models()
        except Exception as exc:
            _new_pipeline_ok, _new_pipeline_err = False, str(exc)

        if _new_pipeline_ok:
            canonical_boxes, dropped_boxes = canonicalize_checkboxes(combined)
            guard = _new_predict_guarded(canonical_boxes)

            vitals = analyze_vitals(data)
            top1 = guard["predictions"][0]
            predicted_disease = top1["disease"]
            confidence = round(top1["confidence"] * 100, 1)

            symptoms_selected = data.get("symptoms", [])
            if not isinstance(symptoms_selected, list):
                symptoms_selected = [symptoms_selected]
            llm_advice = generate_advice(predicted_disease, symptoms_selected)
            risk_prefix = _recommendation_for_risk(vitals["risk_category"])
            recommendation = _shorten_answer(
                f"{risk_prefix} AI advice for {predicted_disease}: {llm_advice}",
                max_chars=220,
            )

            response_payload = {
                "predicted_disease": predicted_disease,
                "confidence": confidence,
                "risk_category": vitals["risk_category"],
                "risk_score": vitals["risk_score"],
                "risk_level": guard["risk_level"],
                "vitals_analysis": vitals["vitals_analysis"],
                "recommendation": recommendation,
                "model_votes": {
                    "svm": guard["model_agreement"]["svm"],
                    "nb": guard["model_agreement"]["nb"],
                    "rf": guard["model_agreement"]["rf"],
                },
                # new guarded-pipeline schema
                "predictions": guard["predictions"],
                "confidence_band": guard["confidence_band"],
                "top1_vs_top2_margin": guard["top1_vs_top2_margin"],
                "model_agreement": guard["model_agreement"],
                "flags": guard["flags"],
                "confusable_with_note": guard["confusable_with_note"],
                "emergency_alert": guard["emergency_alert"],
                "guarded_recommendation": guard["recommendation"],
                "matched_symptoms": guard["matched_symptoms"],
                "ignored_checkboxes": guard["ignored_checkboxes"] + dropped_boxes,
                "disclaimer": guard["disclaimer"],
                "patientName": str(data.get("patientName", "")).strip(),
                "age": str(data.get("age", "")).strip(),
                "bloodPressureSystolic": str(data.get("bloodPressureSystolic", "")).strip(),
                "bloodPressureDiastolic": str(data.get("bloodPressureDiastolic", "")).strip(),
                "heartRate": str(data.get("heartRate", "")).strip(),
                "temperature": str(data.get("temperature", "")).strip(),
                "sugarLevel": str(data.get("sugarLevel", "")).strip(),
                "labTestResult": str(data.get("labTestResult", "")).strip(),
                "symptoms": symptoms_selected,
                "combined_symptom_count": len(symptoms_selected),
                "ai_detected_symptoms": [str(t or "").strip() for t in extra_symptoms if str(t or "").strip()],
            }

            saved_ok = store_patient_record(data, response_payload, uploaded_report)
            response_payload["saved_to_db"] = bool(saved_ok)
            return jsonify(response_payload), 200

        if _new_pipeline_err:
            print(f"[predict] new guarded pipeline unavailable, using rule-based fallback: {_new_pipeline_err}")

  
        result = evaluate_assessment(data)
        result.update({
            "patientName": str(data.get("patientName", "")).strip(),
            "age": str(data.get("age", "")).strip(),
            "bloodPressureSystolic": str(data.get("bloodPressureSystolic", "")).strip(),
            "bloodPressureDiastolic": str(data.get("bloodPressureDiastolic", "")).strip(),
            "heartRate": str(data.get("heartRate", "")).strip(),
            "temperature": str(data.get("temperature", "")).strip(),
            "sugarLevel": str(data.get("sugarLevel", "")).strip(),
            "labTestResult": str(data.get("labTestResult", "")).strip(),
            "symptoms": data.get("symptoms", []) if isinstance(data.get("symptoms", []), list) else [],
            "combined_symptom_count": len(data.get("symptoms", [])) if isinstance(data.get("symptoms", []), list) else 0,
            "model_loaded": False,
        })
        saved_ok = store_patient_record(data, result, uploaded_report)
        result["saved_to_db"] = bool(saved_ok)
        return jsonify(result), 200
            
    except Exception as e:
        print(f"Error in prediction: {e}")
        return jsonify({
            "predicted_disease": "Error",
            "confidence": 0,
            "risk_category": "Unknown",
            "risk_score": 0,
            "risk_level": "Unknown",
            "vitals_analysis": {},
            "error": f"Prediction failed: {str(e)}"
        }), 500



_GENERAL_AI_KNOWLEDGE: list[tuple[list[str], str]] = [
    (["fever", "temperature", "high temp"],
     "A fever (temperature above 100.4 °F / 38 °C) usually signals infection. Rest, stay hydrated with ORS or clean water, and take paracetamol for temperature above 102 °F. If the fever lasts more than 3 days or exceeds 104 °F, visit the nearest health facility immediately."),
    (["malaria", "mosquito", "chills", "shivering"],
     "Malaria is spread by mosquito bites and causes fever, chills, and body aches. Take antimalarial medication as prescribed by a health worker. Prevent it by sleeping under insecticide-treated nets and removing stagnant water near your home."),
    (["diabetes", "blood sugar", "sugar level", "glucose"],
     "Diabetes means your blood sugar is too high. Eat less sugar and white rice; prefer whole grains and vegetables. Exercise 30 minutes daily, take your medicine regularly, and check your blood sugar every week. Fasting sugar above 126 mg/dL needs medical attention."),
    (["blood pressure", "hypertension", "bp", "high bp"],
     "High blood pressure (above 140/90 mmHg) damages the heart and kidneys silently. Reduce salt, avoid alcohol and tobacco, exercise gently, and take prescribed medications daily. Check your BP at least once a month."),
    (["cough", "cold", "flu", "respiratory", "breathing"],
     "For a cough or cold, rest and drink warm fluids. Steam inhalation can relieve congestion. If you have difficulty breathing, cough with blood, or fever above 102 °F, see a health worker — it could be pneumonia or TB."),
    (["diarrhea", "loose stools", "vomiting", "dehydration"],
     "Diarrhea causes dangerous dehydration rapidly. Drink ORS (Oral Rehydration Solution) or clean water with a pinch of salt and sugar. Avoid raw food. If there is blood in stools or vomiting persists more than 12 hours, go to a clinic immediately."),
    (["chest pain", "heart", "heart attack", "cardiac"],
     "Chest pain or tightness is a serious warning sign. Stop all activity, sit or lie down, and call for emergency help immediately. Do not ignore chest pain — it could be a heart attack. Chew an aspirin (325 mg) if available and there is no allergy."),
    (["anemia", "pale", "weakness", "iron", "hemoglobin"],
     "Anemia means low iron in the blood, causing fatigue, pale skin, and breathlessness. Eat iron-rich foods: leafy greens (spinach), beans, eggs, and meat. Take iron supplements as prescribed. Pregnant women and children are especially vulnerable."),
    (["tuberculosis", "tb", "night sweats", "blood cough"],
     "TB is a curable bacterial infection of the lungs. Symptoms: cough for more than 2 weeks, night sweats, weight loss, and blood in sputum. Get a free TB test at your government health center. Complete the full 6-month medication course — stopping early causes drug-resistant TB."),
    (["typhoid", "enteric fever", "stomach pain", "abdominal"],
     "Typhoid causes prolonged fever, stomach pain, and weakness. It spreads through contaminated water and food. Antibiotics prescribed by a doctor cure it. Always drink boiled or filtered water and wash hands before eating."),
    (["pregnancy", "prenatal", "antenatal", "maternal"],
     "During pregnancy, attend all antenatal checkups (at least 4 visits). Take iron and folic acid supplements daily. Eat nutritious food, avoid alcohol and tobacco. Institutional delivery is strongly recommended — go to a health facility when labor begins."),
    (["child", "infant", "baby", "vaccination", "immunization"],
     "All children must be vaccinated on schedule (BCG, Polio, DPT, Measles etc.). Breastfeed exclusively for 6 months. Monitor the child's weight monthly. If the child has high fever, convulsions, or refuses to feed, seek medical care immediately."),
    (["skin", "rash", "allergy", "itch"],
     "Skin rashes can be caused by allergies, infections, or insect bites. Keep the area clean and dry. Calamine lotion or antihistamines help with itching. If the rash spreads rapidly, is painful, or is accompanied by fever, see a health worker."),
    (["injury", "wound", "bleeding", "cut"],
     "Clean the wound immediately with clean water and apply gentle pressure to stop bleeding. Cover with a clean cloth or bandage. Watch for signs of infection: redness, swelling, pus, or fever. Deep or large wounds need stitching at a clinic."),
    (["mental health", "depression", "stress", "anxiety", "mental"],
     "Mental health is as important as physical health. Talk to a trusted person about your feelings. Maintain a daily routine, get enough sleep, and limit alcohol. If you feel persistently sad, hopeless, or have thoughts of self-harm, contact a mental health worker."),
    (["nutrition", "diet", "malnutrition", "weight loss", "food"],
     "A balanced diet includes grains, pulses/beans, vegetables, fruits, and some protein (eggs/meat/fish). MUAC measurement below 11.5 cm in children under 5 indicates severe malnutrition — visit a nutrition rehabilitation center. Avoid processed food high in salt and sugar."),
    (["water", "sanitation", "hygiene", "handwash"],
     "Always drink boiled or filtered water. Wash hands with soap before eating and after using the toilet. Build and use a proper toilet — open defecation spreads disease. Dispose of garbage properly to prevent mosquito and fly breeding."),
    (["snake bite", "snakebite", "scorpion", "bite"],
     "Stay calm — panic speeds venom spread. Immobilize the bitten limb, keep it below heart level, remove jewellery, and go to the nearest hospital immediately with anti-venom. Do NOT cut, suck the wound, or apply a tourniquet."),
    (["first aid", "emergency", "help"],
     "For any emergency: call local ambulance services. Basic first aid: control bleeding with pressure, keep the patient calm and warm, do not give food or water if surgery may be needed. CPR (chest compressions) for unconscious patients who are not breathing."),
]

def _keyword_answer(question: str) -> str | None:
    q_lower = question.lower()
    for keywords, answer in _GENERAL_AI_KNOWLEDGE:
        if any(kw in q_lower for kw in keywords):
            return _apply_fixed_style(answer)
    return None


def _llm_chat_answer(question: str) -> str | None:
    """Try to get a useful answer from distilgpt2."""
    llm = _get_llm()
    if llm is None:
        return None
    prompt = (
        "You are a rural healthcare assistant providing simple, accurate medical advice. "
        f"Patient question: {question} "
        "Answer:"
    )
    try:
        outputs = llm(prompt, max_new_tokens=100, do_sample=False, truncation=True)
        full_text: str = outputs[0]["generated_text"]
        answer = full_text[len(prompt):].strip()
        if len(answer) < 20:
            return None
        sentences = re.split(r'(?<=[.!?])\s+', answer)
        result = " ".join(sentences[:3]).strip()
        if result and result[-1] not in ".!?":
            result += "."
        return _apply_fixed_style(result) if len(result) > 20 else None
    except Exception as e:
        print(f"LLM chat error: {e}")
        return None


@app.route('/clear-session', methods=['POST'])
def clear_session():
    data = request.get_json(silent=True) or {}
    session_id = str(data.get("session_id", "default")).strip() or "default"
    sessions.pop(session_id, None)
    session_token_usage.pop(session_id, None)
    clear_chat_session(session_id)
    _evict_file_chunks(session_id)
    return jsonify({"cleared": True}), 200


@app.route('/api/conversations', methods=['GET'])
def list_conversations():
    """List the logged-in user's saved conversations (newest first).

    Conversations are stored per logged-in user (keyed by their Supabase
    user id), so each user only ever sees their own chats.
    """
    try:
        user_id = str(request.args.get("user_id", "")).strip()
        if not user_id:
            return jsonify({"success": True, "conversations": []}), 200

        conversations = []
        try:
            conversations = mysql_store.chat_conversations_list(user_id)
        except Exception as exc:
            print(f"[CHAT-STORE] mysql list failed: {exc}")
        if not conversations:
            conversations = _chat_list_conversations(user_id)

        user_email = str(request.args.get("user_email", "") or "").strip().lower()
        response_count = _user_response_count(user_email) if user_email else 0

        return jsonify({
            "success": True,
            "conversations": conversations,
            "response_count": response_count,
        }), 200
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500


@app.route('/api/conversations/<conversation_id>/messages', methods=['GET'])
def get_conversation_messages(conversation_id):
    """Return the full message history of one conversation (oldest first).

    Also re-seeds the in-memory session context so a resumed conversation
    keeps working, and reports the user's total response count so the
    frontend badge stays accurate.
    """
    try:
        messages = []
        try:
            messages = mysql_store.chat_messages_list(conversation_id)
        except Exception as exc:
            print(f"[CHAT-STORE] mysql messages read failed: {exc}")
        if not messages:
            messages = _chat_get_messages(conversation_id)

        try:
            history = [
                {
                    "role": m.get("sender") == "user" and "user" or "assistant",
                    "content": m.get("message_text", ""),
                }
                for m in messages
                if m.get("message_text")
            ]
            _SESSION_HISTORY[conversation_id] = history[-MAX_HISTORY:]
            last = next(
                (m for m in reversed(messages) if m.get("sender") == "assistant"),
                None,
            )
            if last is not None:
                _SESSION_LAST_RESPONSE[conversation_id] = last.get("message_text", "")
        except Exception as exc:
            print(f"[CHAT-STORE] failed to re-seed session context: {exc}")

        user_email = str(request.args.get("user_email", "") or "").strip().lower()
        response_count = _user_response_count(user_email) if user_email else 0

        return jsonify({
            "success": True,
            "conversation_id": conversation_id,
            "count": len(messages),
            "messages": messages,
            "response_count": response_count,
        }), 200
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500


@app.route('/api/conversations/<conversation_id>', methods=['DELETE'])
def delete_conversation(conversation_id):
    """Permanently delete a conversation and all of its messages.

    This is the ONLY way a conversation is ever removed — there is no
    automatic expiry, TTL, or cleanup job for chat data.
    """
    try:
        user_id = str(request.args.get("user_id", "") or request.args.get("user", "")).strip()
        ok = _chat_delete_conversation(conversation_id, user_id)
        if not ok:
            return jsonify({
                "success": False,
                "error": "Conversation not found or you do not have permission to delete it.",
            }), 404
        try:
            _SESSION_HISTORY.pop(conversation_id, None)
            _SESSION_LAST_RESPONSE.pop(conversation_id, None)
        except Exception:
            pass
        return jsonify({"success": True, "deleted": conversation_id}), 200
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500


@app.route('/api/conversations/<conversation_id>', methods=['PATCH'])
def rename_conversation(conversation_id):
    """Set a custom title for one of the logged-in user's conversations.

    Only the user who owns the conversation may rename it (ownership is
    validated the same way as deletion). The title is trimmed and capped
    at 50 characters; a blank title is rejected so the auto-generated
    title is never replaced by an empty one.
    """
    try:
        user_id = str(
            request.args.get("user_id", "")
            or request.args.get("user", "")
            or ""
        ).strip()
        data = request.get_json(silent=True) or {}
        title = str(data.get("title", "")).strip()[:50]
        if not title:
            return jsonify({
                "success": False,
                "error": "title is required and must not be empty.",
            }), 400
        conv = _chat_rename_conversation(conversation_id, user_id, title)
        if conv is None:
            return jsonify({
                "success": False,
                "error": "Conversation not found or you do not have permission to rename it.",
            }), 404
        return jsonify({"success": True, "conversation": conv}), 200
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500


def get_disease_info_from_kb(disease_name: str, question_type: str = "full summary") -> str:
    """Return KB answer text for a disease and question type."""
    kb = _load_local_disease_kb()
    qa = kb.get(str(disease_name or "").strip(), {}) if isinstance(kb, dict) else {}
    if not isinstance(qa, dict) or not qa:
        return "Information not available in the knowledge base."

    normalized_q = str(question_type or "").strip().lower()
    query_lower = normalized_q

    if normalized_q in {"full summary", "full_summary", "summary", "full"}:
        return limit_response_words(_build_kb_style_answer(str(disease_name), qa, None), 350)

    if any(w in query_lower for w in ["stage", "stages", "level", "grade", "how bad", "how serious"]):
        field = str(qa.get("Stages", "")).strip()
        if field:
            return limit_response_words(f"Stages of {disease_name}: {field}", 200)
        return "Stage information is currently limited for this disease."

    if any(w in query_lower for w in ["emergency sign", "very emergency", "critical sign", "life threatening", "danger sign", "when is it dangerous"]):
        field = str(qa.get("Emergency signs", "")).strip()
        if field:
            return limit_response_words(f"Emergency warning signs of {disease_name}: {field}", 200)
        return "Emergency warning signs are currently limited for this disease."

    return limit_response_words(_build_kb_style_answer(str(disease_name), qa, str(question_type)), 200)


@app.route('/predict-disease', methods=['POST'])
def predict_disease():
    """Predict disease from symptoms using the NEW guarded 3-model ensemble.

    Request:  { "symptoms": [checkbox labels or model tokens], "session_id": ... }
    Response: predicted_disease, svm/nb/rf agreement, precautions, qa_summary.
    """
    data = request.get_json(silent=True) or {}
    symptoms = data.get("symptoms", [])
    session_id = str(data.get("session_id", "default")).strip() or "default"

    if not isinstance(symptoms, list):
        symptoms = [symptoms]

    new_ok, new_err = _new_load_models()
    if not new_ok:
        return jsonify({"error": f"ML model not loaded: {new_err}"}), 503

    try:
        canonical_boxes, dropped_boxes = canonicalize_checkboxes(symptoms)
        if not canonical_boxes:
            return jsonify({
                "error": "No recognized symptoms",
                "valid_symptoms": list(_NEW_CHECKBOX_TO_SYMPTOMS.keys()),
            }), 400

        guard = _new_predict_guarded(canonical_boxes)
        final_prediction = guard["predictions"][0]["disease"]
        qa_summary = get_disease_info_from_kb(str(final_prediction), "full summary")

        try:
            _set_session_disease(session_id, str(final_prediction))
        except Exception as exc:
            print(f"[predict-disease] failed to set session disease: {exc}")

        return jsonify({
            "predicted_disease": str(final_prediction),
            "svm": guard["model_agreement"]["svm"],
            "nb": guard["model_agreement"]["nb"],
            "rf": guard["model_agreement"]["rf"],
            "predictions": guard["predictions"],
            "confidence_band": guard["confidence_band"],
            "flags": guard["flags"],
            "emergency_alert": guard["emergency_alert"],
            "risk_level": guard["risk_level"],
            "confusable_with_note": guard["confusable_with_note"],
            "recommendation": guard["recommendation"],
            "disclaimer": guard["disclaimer"],
            "ignored_checkboxes": guard["ignored_checkboxes"] + dropped_boxes,
            "qa_summary": qa_summary,
        }), 200
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint - fast response without Ollama check"""
    try:
        from chatbot_response import _SESSION_LAST_DISEASE
        process = psutil.Process(os.getpid())
        memory_mb = process.memory_info().rss / 1024 / 1024
        active_sessions = len(_SESSION_LAST_DISEASE)
        
        from chatbot_pipeline import ollama_ready
        llm_available = bool(ollama_ready())

        from speech_service import asr_status
        _asr = asr_status()

        return jsonify({
            "status": "ok",
            "memory_mb": round(memory_mb, 2),
            "active_sessions": active_sessions,
            "max_sessions": MAX_SESSIONS,
            "llm_available": llm_available,
            "llm_host": "127.0.0.1:11434",
            "llm_model": "cniongolo/biomistral:latest",
            "asr": {
                "available": _asr["loaded"],
                "model": _asr["model"],
                "vad": _asr["vad"],
                "error": _asr.get("error"),
            },
            "message": "Backend API Running"
        }), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/debug-symptoms', methods=['GET'])
def debug_symptoms():
    """Debug: show the guarded pipeline's checkbox vocabulary and load status."""
    ok, err = _new_load_models()
    report = []
    for label, tokens in _NEW_CHECKBOX_TO_SYMPTOMS.items():
        report.append({
            "frontend_label": label,
            "model_tokens": tokens,
        })
    return jsonify({
        "model_type": "predict_disease_guarded (expanded_form_*.joblib ensemble)",
        "model_loaded": bool(ok),
        "load_error": err,
        "checkbox_count": len(_NEW_CHECKBOX_TO_SYMPTOMS),
        "checkbox_mapping": report,
    }), 200


@app.route('/model-info', methods=['GET'])
def model_info():
    """Debug: show the guarded pipeline's loaded state and model topology."""
    ok, err = _new_load_models()
    if not ok:
        return jsonify({"error": f"Model not loaded: {err}"}), 500
    return jsonify({
        "type": "predict_disease_guarded (expanded_form_*.joblib ensemble)",
        "pipeline": "MultiLabelBinarizer (40 symptoms) + RF/NB/SVM soft-vote",
        "loaded": True,
        "checkbox_count": len(_NEW_CHECKBOX_TO_SYMPTOMS),
        "valid_checkboxes": list(_NEW_CHECKBOX_TO_SYMPTOMS.keys()),
        "precautions_sources": "backend/Disease_precaution.csv",
    }), 200



ADMIN_EMAIL    = os.environ.get("ADMIN_EMAIL",    "admin@ruralhealthcare.com")
# No hardcoded password default -- both /admin-login and /doctor-login
# below already reject an empty submitted password before comparing
# ("Email and password are required"), so an unset env var means this
# account simply can't log in (fails closed) instead of silently
# accepting a fixed, source-visible password. Previously defaulted to a
# real, source-committed password (see git history) -- treat that old
# value as compromised and rotate it if it's still in use anywhere.
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
if not ADMIN_PASSWORD:
    print("[app] WARNING: ADMIN_PASSWORD not set in .env -- admin login is disabled")

# Doctor role: same patient-dashboard feature as Admin (see /patients below,
# shared by both), gated behind its own separate credentials rather than
# reusing the admin account -- a distinct login for a distinct role.
DOCTOR_EMAIL    = os.environ.get("DOCTOR_EMAIL",    "doctor@ruralhealthcare.com")
DOCTOR_PASSWORD = os.environ.get("DOCTOR_PASSWORD", "")
if not DOCTOR_PASSWORD:
    print("[app] WARNING: DOCTOR_PASSWORD not set in .env -- the shared doctor "
          "login is disabled (self-registered /doctor-register accounts still work)")

# Self-service doctor accounts -- on top of the single .env-configured
# DOCTOR_EMAIL/DOCTOR_PASSWORD pair above (kept working for backward
# compat), doctors can now create their own login via /doctor-register.
# Each account's email must end in @ruralhealthcare.com (mirrors
# is_valid_gmail_email's pattern below, just a different required domain --
# this isn't a real mailbox that gets verified, it's just the org's
# internal-account naming convention, same as DOCTOR_EMAIL's default).
DOCTOR_ACCOUNTS_FILE = "doctor_accounts.json"
DOCTOR_EMAIL_DOMAIN_RE = re.compile(r"^[A-Za-z0-9._%+-]+@ruralhealthcare\.com$", re.IGNORECASE)


def _is_valid_doctor_email(email: str) -> bool:
    return bool(DOCTOR_EMAIL_DOMAIN_RE.fullmatch(str(email or "").strip()))


def _load_doctor_accounts() -> list:
    """Read all self-registered doctor accounts; returns a list of
    {"email", "password_hash", "created_at"} dicts."""
    if not os.path.exists(DOCTOR_ACCOUNTS_FILE):
        return []
    try:
        with open(DOCTOR_ACCOUNTS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_doctor_accounts(accounts: list) -> None:
    """Persist doctor accounts atomically, with a short retry on the final
    rename -- same OneDrive-sync-lock issue (WinError 5) documented on
    _chat_store_save, and login credentials are exactly the kind of write
    that shouldn't be allowed to silently drop."""
    tmp_path = DOCTOR_ACCOUNTS_FILE + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(accounts, f, ensure_ascii=False, indent=2)
    last_exc = None
    for attempt in range(4):
        try:
            os.replace(tmp_path, DOCTOR_ACCOUNTS_FILE)
            return
        except OSError as exc:
            last_exc = exc
            if attempt < 3:
                time.sleep(0.05 * (attempt + 1))
    print(f"[doctor-accounts] failed to persist after retries: {last_exc}")


@app.route('/doctor-register', methods=['POST'])
def doctor_register():
    """Self-service doctor account creation. Email must end in
    @ruralhealthcare.com; password is hashed (never stored in plain text)
    before being saved to DOCTOR_ACCOUNTS_FILE."""
    try:
        data = request.get_json(silent=True) or {}
        email = str(data.get("email", "")).strip().lower()
        password = str(data.get("password", "")).strip()

        if not email or not password:
            return jsonify({"success": False, "error": "Email and password are required"}), 400
        if not _is_valid_doctor_email(email):
            return jsonify({"success": False, "error": "Email must end with @ruralhealthcare.com"}), 400
        if len(password) < 8:
            return jsonify({"success": False, "error": "Password must be at least 8 characters"}), 400
        if email == DOCTOR_EMAIL.strip().lower():
            return jsonify({"success": False, "error": "An account with this email already exists"}), 409

        accounts = _load_doctor_accounts()
        if any(a.get("email") == email for a in accounts):
            return jsonify({"success": False, "error": "An account with this email already exists"}), 409

        accounts.append({
            "email": email,
            "password_hash": hash_password(password),
            "created_at": datetime.now().isoformat(timespec="seconds"),
        })
        _save_doctor_accounts(accounts)
        return jsonify({"success": True, "message": "Doctor account created"}), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/api/users', methods=['GET'])
def list_users_endpoint():
    """List real registered users straight from Supabase Auth (the actual
    source of truth) -- no local sync step to forget, so this covers
    email/password AND every OAuth provider (Google/GitHub/LinkedIn)
    equally. See backend/supabase_admin.py for why this replaced the old
    MySQL `users` table mirror."""
    try:
        return jsonify({"success": True, "users": supabase_admin.list_users()}), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/api/feedback', methods=['GET'])
def list_feedback_endpoint():
    """List every website feedback-form submission (name, email, message,
    star rating) for the Admin Dashboard's Feedback tab, newest first."""
    try:
        return jsonify({"success": True, "feedback": mysql_store.feedback_get_all()}), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/admin-login', methods=['POST'])
def admin_login():
    """Admin login endpoint for dashboard authentication"""
    try:
        data = request.get_json(silent=True) or {}
        email = str(data.get("email", "")).strip()
        password = str(data.get("password", "")).strip()

        if not email or not password:
            return jsonify({"success": False, "error": "Email and password are required"}), 400

        if email == ADMIN_EMAIL and password == ADMIN_PASSWORD:
            return jsonify({"success": True, "message": "Admin authenticated"}), 200
        else:
            return jsonify({"success": False, "error": "Invalid email or password"}), 401
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/doctor-login', methods=['POST'])
def doctor_login():
    """Doctor login: the single .env-configured DOCTOR_EMAIL/DOCTOR_PASSWORD
    pair still works (backward compat), OR any account self-registered via
    /doctor-register."""
    try:
        data = request.get_json(silent=True) or {}
        email = str(data.get("email", "")).strip()
        password = str(data.get("password", "")).strip()

        if not email or not password:
            return jsonify({"success": False, "error": "Email and password are required"}), 400

        if email == DOCTOR_EMAIL and password == DOCTOR_PASSWORD:
            return jsonify({"success": True, "message": "Doctor authenticated"}), 200

        email_lower = email.lower()
        accounts = _load_doctor_accounts()
        match = next((a for a in accounts if a.get("email") == email_lower), None)
        if match and match.get("password_hash") == hash_password(password):
            return jsonify({"success": True, "message": "Doctor authenticated"}), 200

        return jsonify({"success": False, "error": "Invalid email or password"}), 401
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/patients', methods=['GET'])
def get_patients():
    """Return patient records for the admin dashboard."""
    try:
        if mysql_store.is_available():
            return jsonify({"success": True, "patients": mysql_store.list_patients()}), 200

        records = _load_patients_json()
        return jsonify({"success": True, "patients": records}), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/patients/<int:patient_id>', methods=['DELETE'])
def delete_patient(patient_id: int):
    """Delete a patient record by id for the admin dashboard."""
    try:
        if mysql_store.is_available():
            if mysql_store.delete_patient(patient_id):
                return jsonify({"success": True, "message": "Patient record deleted"}), 200
            return jsonify({"success": False, "error": "Patient record not found"}), 404

        records = _load_patients_json()
        filtered = [record for record in records if int(record.get("id", -1)) != patient_id]
        if len(filtered) == len(records):
            return jsonify({"success": False, "error": "Patient record not found"}), 404

        with open(PATIENTS_FILE, 'w') as handle:
            json.dump(filtered, handle, indent=2)
        return jsonify({"success": True, "message": "Patient record deleted"}), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/patients/delete/all', methods=['DELETE'])
def delete_all_patients():
    """Delete ALL patient records - requires admin authentication."""
    try:
        if mysql_store.is_available():
            deleted = mysql_store.delete_all_patients()
            return jsonify({"success": True, "message": f"Deleted {deleted} patient records"}), 200

        # Delete from JSON file
        with open(PATIENTS_FILE, 'w') as handle:
            json.dump([], handle, indent=2)
        return jsonify({"success": True, "message": "All patient records deleted"}), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ===== FILE ATTACHMENTS (temporary uploads, 24h cleanup) =====

def _sanitize_filename(filename: str) -> str:
    """Keep only the basename, strip path separators, cap length."""
    name = os.path.basename(str(filename or "").replace("\\", "/"))
    name = name.strip().replace("\x00", "")
    return name[:180]


def _safe_session_dir(session_id: str) -> str:
    """Sanitize a session id into a safe directory name."""
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", str(session_id or "default"))
    return safe[:120] or "default"


def _cleanup_old_uploads() -> int:
    """Delete uploaded files (and empty session dirs) older than 24 hours.
    Runs on every new upload. Returns the number of files removed."""
    removed = 0
    cutoff = time.time() - UPLOAD_MAX_AGE_SECONDS
    try:
        if not os.path.isdir(UPLOAD_DIR):
            return 0
        for root, dirs, files in os.walk(UPLOAD_DIR, topdown=False):
            for name in files:
                path = os.path.join(root, name)
                try:
                    if os.path.getmtime(path) < cutoff:
                        os.remove(path)
                        removed += 1
                except OSError:
                    pass
            for name in dirs:
                path = os.path.join(root, name)
                try:
                    if not os.listdir(path):
                        os.rmdir(path)
                except OSError:
                    pass
    except OSError as exc:
        print(f"[UPLOAD] cleanup failed: {exc}")
    return removed


def _resolve_upload_path(session_id: str, file_id: str) -> str | None:
    """Map a session_id + file_id to the stored file path, or None when the
    file does not exist. Guards against path traversal."""
    if not file_id:
        return None
    if os.path.basename(str(file_id)) != str(file_id):
        return None
    path = os.path.join(UPLOAD_DIR, _safe_session_dir(session_id), str(file_id))
    if not os.path.isfile(path):
        return None
    return path


# Chunk cache for attached files: (session_id, file_id) -> list[str] chunks.
# Chunking happens ONCE per upload; every later question about the same file
# reuses these chunks (see _get_file_chunks) instead of re-extracting or
# re-chunking. Bounded so a long-lived server never grows without limit.
_FILE_CHUNKS_CACHE: dict = {}
_FILE_CHUNKS_CACHE_MAX = 200


def _evict_file_chunks(session_id: str) -> int:
    """Drop cached chunks for one session (called on session clear)."""
    keys = [k for k in _FILE_CHUNKS_CACHE if k[0] == session_id]
    for key in keys:
        _FILE_CHUNKS_CACHE.pop(key, None)
    return len(keys)


def _get_file_chunks(session_id: str, file_id: str, file_name: str, upload_path: str) -> list:
    """Extract + chunk an attached file ONCE per upload.

    Returns the cached chunk list. Raises FileExtractionError with a
    user-facing message when the file cannot be read (empty/corrupt/
    unreadable) so /ai-chat can reply honestly instead of hallucinating.
    """
    key = (session_id, file_id)
    cached = _FILE_CHUNKS_CACHE.get(key)
    if cached is not None:
        print(f"[CHAT] Reusing {len(cached)} chunk(s) for '{file_name}' "
              f"(session {session_id}) -- chunks not recomputed")
        return cached

    file_type_guess = file_name.rsplit(".", 1)[-1].lower() if "." in file_name else ""
    text = _extract_file_text(upload_path, file_type_guess)
    if not text or not text.strip():
        raise _FileExtractionError(
            "I couldn't read this file — it may be empty, corrupted, or in an unreadable format."
        )
    from doc_chunker import chunk_text as _chunk_text
    chunks = _chunk_text(text)
    if not chunks:
        raise _FileExtractionError(
            "I couldn't read this file — it may be empty, corrupted, or in an unreadable format."
        )
    _FILE_CHUNKS_CACHE[key] = chunks
    if len(_FILE_CHUNKS_CACHE) > _FILE_CHUNKS_CACHE_MAX:
        _FILE_CHUNKS_CACHE.pop(next(iter(_FILE_CHUNKS_CACHE)), None)
    print(f"[CHAT] Chunked file '{file_name}' into {len(chunks)} chunk(s) "
          f"({len(text)} extracted chars, once per upload)")
    return chunks


@app.route('/chat/upload', methods=['POST'])
def chat_upload():
    """Upload a chat attachment (pdf/doc/docx/csv/txt, max 10 MB).

    Multipart fields: `file` (the upload), `session_id` (optional).
    The file is stored temporarily under backend/uploads/{session_id}/ and
    deleted automatically after 24 hours. Returns:
        { "file_id": "...", "filename": "...", "file_type": "pdf" }
    """
    try:
        _cleanup_old_uploads()

        upload = request.files.get("file")
        if upload is None or not upload.filename:
            return jsonify({"error": "No file provided."}), 400

        session_id = _safe_session_dir(str(request.form.get("session_id", "default")).strip() or "default")

        filename = _sanitize_filename(upload.filename)
        if not filename:
            return jsonify({"error": "Invalid file name."}), 400

        # Server-side size check (never trust client-side validation alone).
        upload.stream.seek(0, os.SEEK_END)
        size = upload.stream.tell()
        upload.stream.seek(0)
        if size > UPLOAD_MAX_BYTES:
            return jsonify({"error": "File too large. Maximum size is 10 MB."}), 400
        if size == 0:
            return jsonify({"error": "The file is empty."}), 400

        # Magic-byte + extension validation BEFORE saving.
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(filename)[1]) as tmp:
                upload.save(tmp)
                temp_path = tmp.name
            file_type = _resolve_file_type(filename, temp_path)
        except _FileExtractionError as exc:
            if temp_path and os.path.exists(temp_path):
                os.remove(temp_path)
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            if temp_path and os.path.exists(temp_path):
                os.remove(temp_path)
            print(f"[UPLOAD] save failed: {exc}")
            return jsonify({"error": "File upload failed. Please try again."}), 500

        session_dir = os.path.join(UPLOAD_DIR, session_id)
        os.makedirs(session_dir, exist_ok=True)
        file_id = f"{uuid.uuid4().hex[:12]}_{filename}"
        final_path = os.path.join(session_dir, file_id)
        try:
            os.replace(temp_path, final_path)
        except OSError:
            try:
                os.remove(temp_path)
            except OSError:
                pass
            return jsonify({"error": "File upload failed. Please try again."}), 500

        return jsonify({
            "file_id": file_id,
            "filename": filename,
            "file_type": file_type,
        }), 200
    except Exception as exc:
        print(f"[UPLOAD] unexpected error: {exc}")
        return jsonify({"error": "File upload failed. Please try again."}), 500


@app.route('/ai-chat', methods=['POST'])
def ai_chat():
    """
    TRANSLATION-FIRST AI ASSISTANT ENDPOINT
    
    STEP 1: Check if user is requesting translation → NLLB-200 ONLY (never Mistral)
    STEP 2: If not translation, use pipeline to get response
    STEP 3: Store response in session for future translation requests
    STEP 4: Return response (pure script enforced)
    
    Request:  { "message": "user question", "session_id": "optional" }
    Response: { "reply": "answer", "status": "ok" }
    """
    try:
        data = request.get_json(silent=True) or {}
        message = str(data.get("message", "")).strip()
        session_id = str(data.get("session_id", "default")).strip() or "default"
        # UI language dropdown (ISO-639-1 code: en/kn/hi/ta/te) — overrides
        # auto-detection so replies come back in the selected language.
        ui_lang_code = str(data.get("language", "") or "").strip().lower()
        ui_target_lang = {
            "en": "eng_Latn",
            "kn": "kan_Knda",
            "hi": "hin_Deva",
            "ta": "tam_Taml",
            "te": "tel_Telu",
        }.get(ui_lang_code)
        # Supabase user id (logged-in users only); used to store conversations
        # per user so each user only sees their own chats.
        user_id = str(data.get("user_id", "") or "").strip()
        user_email = str(data.get("user_email", "") or "").strip().lower()

        # Optional file attachment (see POST /chat/upload).
        file_id = str(data.get("file_id", "") or "").strip()
        file_name = _sanitize_filename(str(data.get("filename", "") or "").strip())
        has_file = bool(file_id)

        if not message and not has_file:
            return jsonify({"error": "message is required"}), 400

        # ===== PER-USER CHAT-COUNT CAP (step 0a) — max 10 saved chats =====
        # Blocks starting an 11th conversation outright (no reply generated,
        # nothing saved) rather than silently evicting an old one, per spec.
        # A no-op for continuing an existing conversation or for guests.
        if _would_exceed_conversation_cap(session_id, user_id):
            return jsonify({
                "reply": CONVERSATION_CAP_MESSAGE,
                "status": "ok",
                "conversation_cap_reached": True,
                "session_id": session_id,
            }), 200

        # ===== PER-USER RESPONSE QUOTA + COOLDOWN (step 0b) =====
        # 30 responses per user email, then a 5-hour cooling-off window
        # during which every request is blocked with the exact time the
        # cooldown ends (retry_after_iso) so the frontend can show it.
        # Checked BEFORE the reply is generated -- a blocked request never
        # reaches the LLM/KB pipeline and never counts against the quota.
        if user_email:
            limit_state = _response_limit_state(user_email)
            if limit_state["limit_reached"]:
                return jsonify({
                    "reply": (
                        "You have reached your limit of "
                        f"{RESPONSE_LIMIT_PER_WINDOW} messages. Please visit "
                        "again after your cooling period ends."
                    ),
                    "status": "ok",
                    "limit_reached": True,
                    "retry_after_iso": limit_state["retry_after_iso"],
                    "response_count": limit_state["count"],
                    "session_id": session_id,
                }), 200

        # ===== AI GATEWAY: RATE LIMIT (step 1) =====
        # Disabled: ai_gateway.RATE_LIMITING_ENABLED is False, so this check
        # always returns allowed=True. Rate limiting can be turned back on by
        # setting that flag to True.
        client_ip = request.remote_addr or "unknown"
        _gw_t0 = time.perf_counter()
        rate_check = ai_gateway.rate_limit_check(client_ip)
        if not rate_check["allowed"]:
            ai_gateway.log_request(
                ip=client_ip,
                message_length=len(message),
                guardrail_triggered=False,
                guardrail_type=None,
                response_time_ms=(time.perf_counter() - _gw_t0) * 1000.0,
                was_blocked=True,
            )
            _chat_save_exchange(
                session_id, user_id, user_email, message, rate_check["response"], "ratelimited"
            )
            return jsonify({
                "reply": rate_check["response"],
                "status": "ok",
                "rate_limited": True,
                "session_id": session_id,
            }), 200

        # ===== AI GATEWAY: INPUT SANITIZATION (step 2) =====
        sanitized = ai_gateway.sanitize_input(message) if message else {"text": "", "trimmed": False}
        message = sanitized["text"]
        trim_note = ai_gateway.TRIM_NOTE if sanitized["trimmed"] else ""
        if not message and not has_file:
            return jsonify({"error": "message is required"}), 400

        # ===== GUARDRAILS (step 3) — blocked messages never reach the LLM =====
        guard = guardrails.check_guardrails(message)
        if guard["blocked"]:
            ai_gateway.log_request(
                ip=client_ip,
                message_length=len(message),
                guardrail_triggered=True,
                guardrail_type=guard["reason"],
                response_time_ms=(time.perf_counter() - _gw_t0) * 1000.0,
                was_blocked=True,
            )
            _chat_save_exchange(
                session_id,
                user_id,
                user_email,
                message,
                guard["response"],
                "emergency" if guard["emergency"] else "guardrail",
            )
            return jsonify({
                "reply": guard["response"],
                "status": "ok",
                "guardrail_type": guard["reason"],
                "emergency": guard["emergency"],
                "session_id": session_id,
            }), 200

        # ===== FILE ATTACHMENT: extract + chunk ONCE per upload, scan the
        # file content for prompt injection (same INJECTION_PATTERNS as
        # typed messages). Per-question retrieval of the top relevant
        # chunks happens inside the pipeline (chatbot_pipeline /
        # llm_router.handle_file_question) so the prompt stays small.
        file_content_text = None
        file_chunks = None
        display_message = message
        user_question = message
        if has_file:
            upload_path = _resolve_upload_path(session_id, file_id)
            if upload_path is None:
                return jsonify({
                    "reply": "Sorry, the uploaded file was not found or has expired. Please attach it again.",
                    "status": "ok",
                    "file_error": True,
                    "session_id": session_id,
                }), 200
            try:
                file_chunks = _get_file_chunks(session_id, file_id, file_name, upload_path)
            except _FileExtractionError as exc:
                return jsonify({
                    "reply": str(exc),
                    "status": "ok",
                    "file_error": True,
                    "session_id": session_id,
                }), 200
            except Exception as exc:
                print(f"[CHAT] file extraction failed: {exc}")
                return jsonify({
                    "reply": "Sorry, I couldn't read this file. It may be corrupted or in an unsupported format.",
                    "status": "ok",
                    "file_error": True,
                    "session_id": session_id,
                }), 200

            file_content_text = "\n\n---\n\n".join(file_chunks)

            # Guardrail the file CONTENT too (injection patterns only).
            file_guard = guardrails.check_guardrails(message or "summarize this file", extra_text=file_content_text)
            if file_guard["blocked"]:
                ai_gateway.log_request(
                    ip=client_ip,
                    message_length=len(message),
                    guardrail_triggered=True,
                    guardrail_type=file_guard["reason"],
                    response_time_ms=(time.perf_counter() - _gw_t0) * 1000.0,
                    was_blocked=True,
                )
                _chat_save_exchange(
                    session_id,
                    user_id,
                    user_email,
                    f"[Attached file: {file_name}]\n\n{message}",
                    file_guard["response"],
                    "emergency" if file_guard["emergency"] else "guardrail",
                    file_name,
                    file_content_text,
                )
                return jsonify({
                    "reply": file_guard["response"],
                    "status": "ok",
                    "guardrail_type": file_guard["reason"],
                    "emergency": file_guard["emergency"],
                    "session_id": session_id,
                }), 200

            user_question = message if message else (
                "Please summarize this document and highlight anything health-related."
            )
            display_message = f"[Attached file: {file_name}]\n\n{message}"

        # ===== AI GATEWAY: SESSION MANAGEMENT (idle expiry + redis-free context) =====
        try:
            ai_gateway.expire_idle_sessions()
        except Exception:
            pass
        session_id = ai_gateway.resolve_session(session_id)

        # Hard safety cap for API callers; UI already enforces same limit.
        if len(message) > MAX_INPUT_CHARS:
            message = _trim_to_word_boundary(message, MAX_INPUT_CHARS).strip()

        # ===== PER-USER RESPONSE COUNT (tracking only, unlimited) =====
        # The AI Assistant chat is unlimited; the count is still tracked for
        # display purposes. The per-IP rate guardrail still applies.

        # ===== RESPONSE COUNT (display only — no blocking) =====
        from chatbot_response import _SESSION_LAST_DISEASE
        session_data = _SESSION_LAST_DISEASE.get(session_id, {})
        response_count = session_data.get("response_count", 0)
        warning = ""

        # ===== STEP 1: DETECT TRANSLATION QUERY (NLLB-ONLY PATH) =====
        if is_translation_query(message):
            # Get last response from session
            last_response = _SESSION_LAST_RESPONSE.get(session_id, "")
            
            if not last_response.strip():
                reply = "Please ask a health question first."
            else:
                # Translate using NLLB-200 only (NEVER Mistral)
                target_lang = detect_language(message)
                translated = translate_text(last_response, "eng_Latn", target_lang)

                # Remove any mixed language characters
                clean_output = enforce_language(translated, target_lang)

                if not clean_output.strip():
                    reply = "Translation failed. Please try again."
                else:
                    reply = clean_output.strip()

                # NOTE: deliberately NOT calling limit_response_words() here.
                # That 300-word cap is meant for normal chatbot replies (see
                # its other call sites in this file); applying it AGAIN to an
                # already-complete translation just truncates good output
                # with no benefit -- translate_text() has already produced
                # the full text via chunking, so it should be returned whole.

                _SESSION_LAST_RESPONSE[session_id] = reply
            
            post_limit_state = {"limit_reached": False, "retry_after_iso": None}
            if user_email:
                new_count = _increment_user_response_count(user_email)
                post_limit_state = _response_limit_state(user_email)
            else:
                if session_id in _SESSION_LAST_DISEASE:
                    _SESSION_LAST_DISEASE[session_id]["response_count"] = response_count + 1
                new_count = response_count + 1

            final_reply = reply + warning

            _chat_save_exchange(session_id, user_id, user_email, message, final_reply, "normal")

            return jsonify({
                "reply": final_reply,
                "status": "ok",
                "response_count": new_count,
                "limit_reached": post_limit_state["limit_reached"],
                "retry_after_iso": post_limit_state["retry_after_iso"],
            }), 200

        # ===== STEP 1B: HOSPITAL SEARCH INTENT (Trigger 1, no LLM/KB/cache) =====
        # Short-circuits like the translation-query step above: this never
        # touches the LLM, KB matcher, or cache. Location isn't known yet
        # (that's a browser API) -- this just answers with the "let me find
        # hospitals" text and a flag; the frontend follows up with its own
        # geolocation request and a separate call to /hospitals/nearby.
        if is_hospital_search_query(message):
            urgent = is_medical_urgency(message)
            reply = "Let me find hospitals near you. I'll need your location for this."
            final_reply = reply + warning

            _chat_save_exchange(session_id, user_id, user_email, message, final_reply, "normal")

            return jsonify({
                "reply": final_reply,
                "status": "ok",
                "session_id": session_id,
                "hospital_search_requested": True,
                "hospital_search_urgent": urgent,
                "response_count": response_count,
                "limit_reached": False,
            }), 200

        old_disease = _get_session_disease(session_id)

        if session_id not in _SESSION_LAST_DISEASE:
            _SESSION_LAST_DISEASE[session_id] = {
                "disease": "",
                "last_question_type": "",
                "timestamp": datetime.now(),
                "response_count": response_count,
                "language": "english"
            }

        # ===== CACHE LOOKUP (step 4) — before any LLM call =====
        # File-attached messages are NEVER cached: the file content makes
        # each one unique, so caching would waste memory.
        cached_reply = None if has_file else chat_cache.get(message, ui_target_lang)
        if cached_reply is not None:
            print(f"[chat] tier=cache time={(time.perf_counter() - _gw_t0) * 1000.0:.0f}ms "
                  f"message_preview={message[:40]!r}")
            ai_gateway.log_request(
                ip=client_ip,
                message_length=len(message),
                guardrail_triggered=False,
                guardrail_type=None,
                response_time_ms=(time.perf_counter() - _gw_t0) * 1000.0,
                was_blocked=False,
                cache_hit=True,
            )
            post_limit_state = {"limit_reached": False, "retry_after_iso": None}
            if user_email:
                new_count = _increment_user_response_count(user_email)
                post_limit_state = _response_limit_state(user_email)
            else:
                new_count = response_count + 1
            cached_reply_text = cached_reply + (trim_note if trim_note else "")
            _chat_save_exchange(session_id, user_id, user_email, message, cached_reply_text, "normal")
            # Mirror the non-cached path below: without these, a cache HIT
            # silently breaks "translate my last answer" (session_last_response
            # never gets set) and loses conversation-history/gateway context
            # for this turn, even though the request otherwise succeeded.
            if cached_reply_text and not is_translation_query(message):
                _SESSION_LAST_RESPONSE[session_id] = cached_reply_text
            update_history(session_id, display_message, cached_reply_text)
            try:
                ai_gateway.push_session_message(session_id, message, cached_reply_text)
            except Exception:
                pass
            return jsonify({
                "reply": cached_reply_text,
                "status": "ok",
                "cached": True,
                "session_id": session_id,
                "response_count": new_count,
                "limit_reached": post_limit_state["limit_reached"],
                "retry_after_iso": post_limit_state["retry_after_iso"],
                "model_tier": "primary",
            }), 200

        diag = _new_pipeline_diag()
        total_start = time.perf_counter()
        recent_history = get_limited_history(session_id)
        reply = pipeline_process_query(
            query=user_question,
            session_id=session_id,
            predicted_disease=None,
            diag=diag,
            history=recent_history,
            target_language=ui_target_lang,
            file_attached=has_file,
            file_chunks=file_chunks if has_file else None,
        )
        total_time_ms = (time.perf_counter() - total_start) * 1000.0
        # Step 5b (pipeline audit 2026-08-24): logged for EVERY response, not
        # just some tiers, so a FAQ/KB question silently taking LLM-length
        # time (a red flag it's bypassing the fast path) is visible in the
        # console immediately, before ever needing to check Portkey's
        # dashboard.
        print(f"[chat] tier={diag.get('model_tier', 'unknown')} time={total_time_ms:.0f}ms "
              f"message_preview={message[:40]!r}")

        new_disease = _get_session_disease(session_id)
        if old_disease and new_disease and old_disease.lower() != new_disease.lower():
            _SESSION_HISTORY[session_id] = []
        
        # ===== AI GATEWAY: OUTPUT SANITIZATION (step 7) =====
        if reply:
            reply = ai_gateway.sanitize_output(reply)

        if reply:
            reply = _cap_reply_words(reply, max_words=REPLY_WORD_CAP)

        # ===== CACHE WRITE (step 6) — only stable, common questions;
        # file-attached messages are never cached =====
        try:
            if reply and not has_file and chat_cache.should_cache(message):
                chat_cache.set(message, reply, ui_target_lang)
        except Exception as _cache_err:
            print(f"[CACHE] failed to store response: {_cache_err}")
        
        if reply:
            update_history(session_id, display_message, reply)
        
        if reply and not is_translation_query(message):
            _SESSION_LAST_RESPONSE[session_id] = reply
        
        post_limit_state = {"limit_reached": False, "retry_after_iso": None}
        if user_email:
            new_count = _increment_user_response_count(user_email)
            post_limit_state = _response_limit_state(user_email)
        else:
            if session_id in _SESSION_LAST_DISEASE:
                _SESSION_LAST_DISEASE[session_id]["response_count"] = response_count + 1
            new_count = response_count + 1

        # ===== AI GATEWAY: SESSION CONTEXT (last 10 turns) =====
        try:
            ai_gateway.push_session_message(session_id, message, reply or "")
        except Exception:
            pass
        
        # ===== RAG INTERACTION LOGGING =====
        try:
            _lang_code = detect_language(message)
            _lang_label = {
                "eng_Latn": "english",
                "hin_Deva": "hindi",
                "kan_Knda": "kannada",
                "tam_Taml": "tamil",
                "tel_Telu": "telugu",
            }.get(_lang_code, _lang_code)
            retrieved_pairs = diag.get("retrieved") or []
            retrieved_chunks = [content for _src, content in retrieved_pairs if content]
            retrieved_sources = sorted({src for src, _content in retrieved_pairs if src})
            _save_rag_chat_log({
                "session_id": session_id,
                "timestamp": datetime.now(),
                "question": message,
                "detected_language": _lang_label,
                "retrieved_chunks": retrieved_chunks,
                "retrieved_sources": retrieved_sources,
                "retrieval_time_ms": float(diag.get("retrieval_time_ms", 0.0)),
                "llm_response": reply or "",
                "llm_time_ms": float(diag.get("llm_time_ms", 0.0)),
                "total_time_ms": total_time_ms,
                "model_used": diag.get("model_used", ""),
            })
        except Exception as _rag_log_err:
            print(f"[RAG-LOG] Failed to build/save chat log: {_rag_log_err}")

        # ===== AI GATEWAY: REQUEST LOGGING (step 8 — privacy-safe) =====
        # Single logging call for every non-cached chat response, regardless
        # of which tier answered. Metadata only — never the message/reply.
        try:
            ai_gateway.log_request(
                ip=client_ip,
                message_length=len(message),
                guardrail_triggered=False,
                guardrail_type=None,
                response_time_ms=total_time_ms,
                was_blocked=False,
                cache_hit=False,
                model_tier=diag.get("model_tier"),
                model_used=diag.get("model_used"),
            )
        except Exception as _gw_log_err:
            print(f"[AI-GATEWAY] failed to log chat request: {_gw_log_err}")

        final_reply = reply + warning + (trim_note if trim_note else "")

        # ===== HOSPITAL SEARCH (Trigger 2): high risk % in an attached file =====
        # Runs on the same file_content_text already extracted for the normal
        # answer above -- no re-extraction, no touching file_extractor.py or
        # the chunking/retrieval pipeline. Offered at most once per session
        # (spec step 5d): once shown, _SESSION_HOSPITAL_OFFER_SHOWN marks the
        # session so neither a "yes" nor a "no" nor silence triggers it again.
        high_risk_offer = None
        if has_file and file_content_text and session_id not in _SESSION_HOSPITAL_OFFER_SHOWN:
            risk_percent = hospital_search.extract_risk_percentage(file_content_text)
            if risk_percent is not None and risk_percent >= hospital_search.HIGH_RISK_THRESHOLD:
                _SESSION_HOSPITAL_OFFER_SHOWN.add(session_id)
                high_risk_offer = {
                    "risk_percent": risk_percent,
                    "message": (
                        f"⚠ I noticed this report shows a risk level of {risk_percent}%, "
                        "which is high. I'd strongly recommend visiting a hospital soon. "
                        "Would you like me to find the nearest hospitals to you?"
                    ),
                }

        # ===== PERSISTENT CHAT HISTORY (never auto-deleted) =====
        # For file chats, the extracted text (not the raw file) is stored with
        # the message so the conversation still makes sense after the
        # temporary upload is cleaned up.
        _chat_save_exchange(
            session_id,
            user_id,
            user_email,
            display_message,
            final_reply,
            "normal",
            file_name if has_file else None,
            file_content_text if has_file else None,
        )

        response_payload = {
            "reply": final_reply,
            "status": "ok",
            "cached": False,
            "session_id": session_id,
            "response_count": new_count,
            "limit_reached": post_limit_state["limit_reached"],
            "retry_after_iso": post_limit_state["retry_after_iso"],
            "model_tier": str(diag.get("model_tier", "primary")),
            "model_used": str(diag.get("model_used", "")),
        }
        if high_risk_offer:
            response_payload["high_risk_offer"] = high_risk_offer
        return jsonify(response_payload), 200

    except Exception as e:
        print(f"AI chat error: {e}")
        import traceback; traceback.print_exc()
        return jsonify({
            "reply": "Sorry, I encountered an error. Please try again.",
            "status": "error",
            "error_details": str(e)
        }), 500


# Compatibility alias: /chat is the same AI chat endpoint as /ai-chat
# (older clients and the curl test suite use /chat).
@app.route('/chat', methods=['POST'])
def chat():
    return ai_chat()


@app.route('/hospitals/nearby', methods=['POST'])
def nearby_hospitals():
    """Find hospitals near a user-supplied lat/lng (Overpass API search for
    amenity=hospital). Called only after the frontend's own geolocation
    prompt -- this endpoint never requests or infers location itself.

    Request:  { "latitude": float, "longitude": float, "accuracy_meters": float? }
    Response: { "hospitals": [...] } or { "error": "..." }

    Privacy (spec step 6): coordinates are used only for this one Overpass
    API call and are never written to disk/logs/DB -- only a boolean +
    timestamp is logged below, matching the existing print()-style logging
    used throughout this file (no raw coordinates, ever).
    """
    data = request.get_json(silent=True) or {}
    lat = data.get('latitude')
    lng = data.get('longitude')
    if lat is None or lng is None:
        return jsonify({'error': 'Location not provided'}), 400

    # Always the exact GPS coordinate the browser reported (never a vague
    # address/area name -- that's what /hospitals/by-place is for) searched
    # within a small, fixed radius (hospital_search.DEFAULT_RADIUS_METERS)
    # so results stay genuinely nearby. `accuracy_meters` is logged only,
    # for diagnosing reports like "no hospitals found" -- it does NOT widen
    # the search radius: a poor-accuracy point gets a client-side warning
    # (see LOW_ACCURACY_THRESHOLD_METERS in AIAssistant.tsx) asking the user
    # to enable GPS, rather than silently searching a wider, less relevant
    # area around a coordinate that may not even be close to correct.
    accuracy_meters = data.get('accuracy_meters')
    results, error = hospital_search.find_nearby_hospitals(lat, lng)
    print(f"[hospital_search] search performed at {datetime.now().isoformat()} "
          f"(accuracy={accuracy_meters}, radius={hospital_search.DEFAULT_RADIUS_METERS}m, "
          f"found={len(results) if results is not None else 0})")

    if error:
        return jsonify({'error': error}), 502
    return jsonify({'hospitals': results, 'radius_meters': hospital_search.DEFAULT_RADIUS_METERS}), 200


@app.route('/hospitals/by-place', methods=['POST'])
def hospitals_by_place():
    """Find hospitals near a free-text place name (city/area) via
    Nominatim geocoding -- the fallback path for when the device's
    location is unavailable or too inaccurate to trust for a GPS search.

    Request:  { "place": "Mysuru" }
    Response: { "hospitals": [...] } or { "error": "..." }
    """
    data = request.get_json(silent=True) or {}
    place = str(data.get('place', '') or '').strip()
    if not place:
        return jsonify({'error': 'Please enter a city or area name.'}), 400

    results, error = hospital_search.find_hospitals_by_place(place)
    print(f"[hospital_search] place search performed at {datetime.now().isoformat()} "
          f"(found={len(results) if results is not None else 0})")

    if error:
        return jsonify({'error': error}), 502
    return jsonify({'hospitals': results}), 200


@app.route('/hospitals/geocode', methods=['POST'])
def hospitals_geocode():
    """Resolve a free-text place name to {lat, lng} ONLY -- no hospital
    search. Backs the location-confirmation map's "type your city/area
    name" box: the frontend re-centers the draggable pin here first, then
    the user still has to press "Search from here" to actually run
    /hospitals/nearby. Same Nominatim geocoder as /hospitals/by-place
    (hospital_search.geocode_place), just without the search bundled in.

    Request:  { "place": "Mysuru" }
    Response: { "lat": float, "lng": float } or { "error": "..." }
    """
    data = request.get_json(silent=True) or {}
    place = str(data.get('place', '') or '').strip()
    if not place:
        return jsonify({'error': 'Please enter a city or area name.'}), 400

    lat, lng, error = hospital_search.geocode_place(place)
    if error:
        return jsonify({'error': error}), 502
    return jsonify({'lat': lat, 'lng': lng}), 200


@app.route('/cache/stats', methods=['GET'])
def cache_stats():
    """Cache statistics for the in-memory chat response cache."""
    return jsonify(chat_cache.stats()), 200


def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()

def is_valid_gmail_email(email: str) -> bool:
    return re.fullmatch(r"[A-Za-z0-9._%+-]+@gmail\.com", email or "") is not None


def _store_feedback_to_mysql(
    name: str,
    email: str,
    subject: str,
    message: str,
    delivery: str = "unknown",
    delivery_status: str = "pending",
    rating: int | None = None,
) -> bool:
    """Persist feedback in MySQL."""
    try:
        return mysql_store.feedback_insert(
            name, email, subject, message, delivery, delivery_status, rating=rating
        )
    except Exception as e:
        print(f"Failed to store feedback in MySQL: {e}")
        return False


@app.route('/send-feedback', methods=['POST'])
def send_feedback():
    """Send feedback email from the website feedback form."""
    try:
        data = request.get_json(silent=True) or {}
        print(f"[send-feedback] incoming data: {data}")
        name = str(data.get("name", "")).strip()
        email = str(data.get("email", "")).strip()
        subject = str(data.get("subject", "General Feedback")).strip() or "General Feedback"
        message = str(data.get("message", "")).strip()

        # Star rating (1-5) is optional -- users can leave a message without
        # rating, or rate without writing anything beyond the required message.
        rating = data.get("rating")
        if rating is not None:
            try:
                rating = int(rating)
            except (TypeError, ValueError):
                rating = None
            else:
                if not 1 <= rating <= 5:
                    rating = None

        if not name or not email or not message:
            return jsonify({"success": False, "error": "Name, email, and message are required."}), 400

        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            return jsonify({"success": False, "error": "Please provide a valid email address."}), 400

        delivery = "mysql"
        delivery_status = "saved"

        smtp_configured = (
            SMTP_USER
            and SMTP_PASSWORD
            and SMTP_USER != "your_email@gmail.com"
            and SMTP_PASSWORD != "your_app_password"
        )

        if smtp_configured:
            try:
                msg = MIMEMultipart()
                msg["From"] = SMTP_USER
                msg["To"] = FEEDBACK_TO_EMAIL
                msg["Reply-To"] = email
                msg["Subject"] = subject

                body = (
                    "New feedback received from Rural Healthcare website\n\n"
                    f"Name: {name}\n"
                    f"Email: {email}\n"
                    f"Subject: {subject}\n\n"
                    "Message:\n"
                    f"{message}\n"
                )
                msg.attach(MIMEText(body, "plain"))
                # Prefer SendGrid when configured
                if SENDGRID_API_KEY and SendGridAPIClient is not None and Mail is not None:
                    try:
                        sg_body = body
                        sendgrid_msg = Mail(
                            from_email=email or SMTP_USER,
                            to_emails=FEEDBACK_TO_EMAIL,
                            subject=subject,
                            plain_text_content=sg_body,
                        )
                        sg = SendGridAPIClient(SENDGRID_API_KEY)
                        resp = sg.send(sendgrid_msg)
                        if resp.status_code in (200, 202):
                            delivery = "sendgrid"
                            delivery_status = "sent"
                        else:
                            print(f"SendGrid returned status {resp.status_code}")
                    except Exception as sg_err:
                        print(f"SendGrid error: {sg_err}")

                # Fallback to SMTP if SendGrid not available or failed
                if delivery != "sendgrid":
                    with smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=15) as server:
                        server.starttls()
                        server.login(SMTP_USER, SMTP_PASSWORD)
                        server.sendmail(SMTP_USER, FEEDBACK_TO_EMAIL, msg.as_string())
                    delivery = "email"
                    delivery_status = "sent"

                stored = _store_feedback_to_mysql(name, email, subject, message, delivery=delivery, delivery_status=delivery_status, rating=rating)
                if delivery == "sendgrid":
                    response_message = "Feedback sent and saved successfully."
                elif delivery == "email":
                    response_message = "Feedback emailed and saved successfully."
                else:
                    response_message = "Feedback saved successfully. Our team will review it soon."

                return jsonify({
                    "success": True,
                    "message": response_message,
                    "delivery": delivery,
                    "stored": stored,
                }), 200
            except Exception as e:
                print(f"Send feedback email error: {e}")

        if _store_feedback_to_mysql(name, email, subject, message, delivery=delivery, delivery_status=delivery_status, rating=rating):
            return jsonify({
                "success": True,
                "message": "Feedback saved successfully. Our team will review it soon.",
                "delivery": "mysql",
            }), 200

        return jsonify({
            "success": False,
            "error": "Feedback could not be delivered or stored. Please try again later.",
        }), 503
    except Exception as e:
        print(f"Send feedback error: {e}")
        return jsonify({"success": False, "error": "Failed to send feedback."}), 500

@app.route('/register', methods=['POST'])
def register():
    """Register a new user"""
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({"success": False, "error": "No data provided"}), 400
        
        fullname = data.get('fullName', '').strip()
        email = data.get('email', '').strip().lower()
        password = data.get('password', '').strip()
        
        if not fullname or not email or not password:
            return jsonify({"success": False, "error": "All fields are required"}), 400

        if not is_valid_gmail_email(email):
            return jsonify({"success": False, "error": "Please enter a valid Gmail address ending with @gmail.com"}), 400

        if len(password) < 6:
            return jsonify({"success": False, "error": "Password must be at least 6 characters"}), 400
        
        if mysql_store.is_available():
            if mysql_store.legacy_user_get(email) is not None:
                return jsonify({"success": False, "error": "Email already registered"}), 409
        elif email in users:
            return jsonify({"success": False, "error": "Email already registered"}), 409
        
        users[email] = {
            "fullName": fullname,
            "email": email,
            "password": hash_password(password)
        }
        save_users()
        
        return jsonify({
            "success": True,
            "message": "Registration successful",
            "user": {"email": email, "fullName": fullname}
        }), 201
        
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/login', methods=['POST'])
def login():
    """Authenticate user"""
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({"success": False, "error": "No data provided"}), 400
        
        email = data.get('email', '').strip().lower()
        password = data.get('password', '').strip()
        
        if not email or not password:
            return jsonify({"success": False, "error": "Email and password are required"}), 400

        if not is_valid_gmail_email(email):
            return jsonify({"success": False, "error": "Please enter a valid Gmail address ending with @gmail.com"}), 400
        
        user = None
        if mysql_store.is_available():
            user = mysql_store.legacy_user_get(email)
            if user:
                users[email] = {
                    "fullName": str(user.get("fullName", "")).strip(),
                    "email": email,
                    "password": str(user.get("password", "")),
                }
                user = users[email]

        if user is None:
            user = users.get(email)

        if not user:
            return jsonify({"success": False, "error": "Invalid email or password"}), 401

        stored = user['password']

        # Support both hashed passwords and legacy plain-text passwords
        password_ok = (stored == hash_password(password)) or (stored == password)
        if not password_ok:
            return jsonify({"success": False, "error": "Invalid email or password"}), 401

        if stored == password:
            users[email]['password'] = hash_password(password)
            save_users()
        
        return jsonify({
            "success": True,
            "message": "Login successful",
            "user": {"email": email, "fullName": user['fullName']}
        }), 200
        
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/', methods=['GET'])
def home():
    """Home endpoint - simple response"""
    return jsonify({
        "status": "Backend API Running",
        "frontend": "http://localhost:5173",
        "api_endpoints": "/health, /ai-chat, /predict-disease"
    }), 200



@app.route('/predict-from-report', methods=['POST'])
def predict_from_report():
    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400
    file = request.files['file']
    filename = file.filename
    ext = os.path.splitext(filename)[1].lower()
    temp_path = os.path.join(tempfile.gettempdir(), filename)
    file.save(temp_path)

    text = ""
    if ext in ['.jpg', '.jpeg', '.png']:
        try:
            import pytesseract
            from PIL import Image
            text = pytesseract.image_to_string(Image.open(temp_path))
        except Exception as e:
            return jsonify({'error': f'OCR failed: {str(e)}'}), 500
    elif ext == '.pdf':
        try:
            import pdfplumber
            with pdfplumber.open(temp_path) as pdf:
                for page in pdf.pages:
                    text += page.extract_text() or ""
        except Exception as e:
            return jsonify({'error': f'PDF parsing failed: {str(e)}'}), 500
    else:
        return jsonify({'error': 'Unsupported file type'}), 400
    
        vitals = extract_vitals_from_text(text)
        return jsonify({**vitals, 'extracted_text': text})
    


@app.route('/api/tts/voices', methods=['GET'])
def tts_voices():
    from tts_service import supported_languages
    return jsonify({
        "languages": supported_languages(),
        "default": "en",
    }), 200


@app.route('/api/tts', methods=['POST'])
def tts_speak():
    """Synthesize speech from text using the local MMS-TTS models.

    Request:  { "text": "string", "language": "kn" }
    Response: audio/wav binary body, or JSON error.
    """
    data = request.get_json(silent=True) or {}
    text = str(data.get("text", "")).strip()
    language = str(data.get("language", "")).strip() or None

    if not text:
        return jsonify({"error": "text is required"}), 400
    if len(text) > 1000:
        text = text[:1000]

    try:
        from tts_service import synthesize

        wav_bytes, sample_rate = synthesize(text, language)
        return Response(wav_bytes, mimetype="audio/wav")
    except Exception as exc:
        print(f"[tts] MMS-TTS error: {exc}")
        import traceback; traceback.print_exc()
        return jsonify({"error": f"TTS failed: {exc}"}), 500


@app.route('/api/asr', methods=['POST'])
def asr():
    """Transcribe uploaded audio to text using Faster-Whisper (local ASR).

    Request:  multipart/form-data
              "audio"    (file, required): wav/mp3/m4a/webm/ogg/flac/...
              "language" (str, optional):  ISO-639-1 code to force detection
    Response: { "text": str, "language": str, "confidence": float }
    """
    file = request.files.get("audio")
    if file is None or not getattr(file, "filename", ""):
        return jsonify({"error": "audio file is required"}), 400

    audio_bytes = file.read()
    language = request.form.get("language") or None

    try:
        from speech_service import transcribe_audio
        result = transcribe_audio(audio_bytes, file.filename, language)
        print(
            f"[ASR] OK '{file.filename}' lang={result.get('language')} "
            f"conf={result.get('confidence')} chars={len(result.get('text', ''))}"
        )
        return jsonify(result), 200
    except ValueError as exc:
        print(f"[ASR] REJECTED '{file.filename}' ({language or 'auto'}): {exc}")
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        print(f"[ASR] ERROR '{file.filename}' ({language or 'auto'}): {exc}")
        import traceback; traceback.print_exc()
        return jsonify({"error": f"ASR failed: {exc}"}), 500


if __name__ == "__main__":
    print("\n" + "="*50)
    print("Rural Healthcare ML Backend".center(50))
    print("="*50 + "\n")

    # Non-blocking checks
    try:
        import socket
        sock = socket.create_connection(("127.0.0.1", 11434), timeout=1)
        sock.close()
        print(" Ollama running")
        try:
            import ollama
            available = [m.get("model") or m.get("name") for m in ollama.list().get("models", [])]
            if "cniongolo/biomistral:latest" in available:
                print(" Biomistral model loaded (cniongolo/biomistral:latest)")
            else:
                print(" WARNING cniongolo/biomistral:latest not installed - run: ollama pull cniongolo/biomistral:latest")
        except Exception as e:
            print(f" WARNING could not list Ollama models: {e}")
    except Exception:
        print(" ERROR Ollama not reachable on 127.0.0.1:11434")
        print("       Chat will reply with a clear 'AI engine unavailable' message.")
        print("       Start Ollama with: ollama serve")

    try:
        from speech_service import asr_status
        _asr = asr_status()
        print(f" ASR ready ({_asr['model']}, lazy load)" if _asr["loaded"] else f" ASR configured ({_asr['model']}, loads on first request)")
    except Exception:
        print(" ASR unavailable")

    # Pre-warm every heavy model in ONE background thread, sequentially, so
    # startup stays non-blocking without racing multiple transformers
    # `from_pretrained()` calls against each other. transformers' low-CPU-
    # memory load path materializes weights via a temporary meta-device
    # context; that context is not safe to enter from multiple threads at
    # once, and doing so was observed to corrupt EVERY subsequent model call
    # in the process (IT2 *and* its NLLB fallback both failing forever with
    # "Tensor on device meta is not on the expected device cpu!", silently
    # leaving every non-English reply untranslated). Loading one model at a
    # time avoids that race entirely; each step still logs its own timing so
    # this isn't slower to observe, just serialized.
    def _prewarm_all_models():
        try:
            from speech_service import _get_model
            _get_model()
            print("[ASR] model pre-warmed in background")
        except Exception as exc:
            print(f"[ASR] pre-warm failed: {exc}")

        try:
            from translation_service import preload as _it2_preload
            _it2_preload()
        except Exception as exc:
            print(f"[Translate] pre-warm failed: {exc}")
        try:
            from chatbot_pipeline import preload_translation
            preload_translation()
        except Exception as exc:
            print(f"[Translate] NLLB pre-warm failed: {exc}")

        try:
            from tts_service import preload as _tts_preload
            _tts_preload()
        except Exception as exc:
            print(f"[tts] pre-warm failed: {exc}")

        try:
            from faq_matcher import prewarm_semantic
            prewarm_semantic()
        except Exception as exc:
            print(f"[faq_matcher] pre-warm failed: {exc}")

    threading.Thread(target=_prewarm_all_models, daemon=True).start()

    # Fast (no model loading), so done synchronously rather than in the
    # background prewarm thread above.
    try:
        mysql_store.init_schema()
        # One-time backfills from the old JSON fallback files into MySQL --
        # all three are no-ops once the corresponding table already has data.
        load_users()
        migrate_patients_json_to_mysql()
        migrate_chat_store_json_to_mysql()
    except Exception as exc:
        print(f"[mysql_store] startup init failed: {exc}")

    kb = _load_local_disease_kb()
    if kb and len(kb) > 0:
        print(f" Disease KB loaded ({len(kb)} diseases)")

 
    try:
        ok, err = load_guarded_pipeline()
        if ok:
            print(f" SUCCESS predict_disease_guarded.py loaded ({len(_NEW_CHECKBOX_TO_SYMPTOMS)}-symptom RF/NB/SVM ensemble)")
        else:
            print(f" WARNING predict_disease_guarded.py load FAILED: {err}")
    except Exception as e:
        print(f" WARNING Error loading guarded prediction pipeline: {e}")

    # ===== STARTUP SELF-TEST (Step 5a, pipeline audit 2026-08-24) =====
    # Runs automatically every startup -- catches FAQ/KB fast-path
    # regressions the moment they're introduced instead of only surfacing
    # as a silent production incident that burns Portkey quota. Loud
    # CRITICAL banner on failure; never blocks startup (a broken selftest
    # should be impossible to miss, not a crash).
    try:
        _run_pipeline_selftest()
    except Exception as exc:
        print(f"CRITICAL: startup self-test itself raised {type(exc).__name__}: {exc}")

    # Render (and most PaaS hosts) assign their own port at runtime via the
    # PORT env var and route external traffic to it -- a hardcoded port
    # would simply not be listening on the right one. 0.0.0.0 (not
    # 127.0.0.1) is required too: 127.0.0.1 only accepts connections from
    # inside the same machine, but the platform's load balancer connects
    # from outside the container. Falls back to 5001/127.0.0.1 behavior
    # locally when PORT isn't set (unset HOST_BIND keeps local runs exactly
    # as before, in case 0.0.0.0 ever needs to be avoided in a given dev
    # environment).
    PORT = int(os.environ.get("PORT", 5001))
    HOST = os.environ.get("HOST_BIND", "0.0.0.0")

    print(f"\n Starting server on port {PORT}...\n")
    print(f" Running on http://{HOST}:{PORT}\n")

    try:
        from waitress import serve
        # threads=4 meant only 4 requests could even be ACCEPTED at once --
        # with TTS calls routinely taking 30-160s+ (this hardware has no
        # GPU), 4 slow TTS/audio-mode requests could occupy every worker
        # thread, leaving an unrelated, otherwise-fast translation request
        # stuck waiting for a free thread rather than for the (separate)
        # translation lock it actually needs. The CPU-bound work itself is
        # still fully serialized by TRANSLATE_LOCK / tts_service._lock
        # either way, so this doesn't add real parallelism -- it just stops
        # short requests from queueing behind unrelated slow ones purely for
        # lack of a worker thread to run on.
        serve(app, host=HOST, port=PORT, threads=16)
    except ImportError:
        app.run(host=HOST, port=PORT, debug=False, threaded=True, use_reloader=False)

