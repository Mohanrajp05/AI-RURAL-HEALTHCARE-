"""Response caching for the Rural Healthcare AI chatbot.

A simple in-memory TTL cache (no Redis):

  * Key:   SHA256 of the normalized message (lowercased, stripped, punctuation removed)
  * Value: LLM response string
  * TTL:   1 hour
  * Size:  max 200 entries, FIFO eviction

Only "stable, common" questions are cached — anything personal/emergency-like
or shorter than 10 characters is always passed to the LLM.
"""

import csv
import hashlib
import os
import re
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DISEASE_CSV = os.path.join(BASE_DIR, "Disease_precaution.csv")

CACHE_TTL_SECONDS = 60 * 60
CACHE_MAX_ENTRIES = 200

CACHE_KEYWORDS = [
    "symptoms of",
    "what is",
    "precautions for",
    "how to prevent",
    "causes of",
]

PERSONAL_PRONOUNS = (" i ", " my ", " me ", " our ", " i'", " i'm", " my ")

_cache: dict = {}  # key -> {"response": str, "timestamp": float}
_hits = 0
_misses = 0

_NON_ALNUM_RE = re.compile(r"[^a-z0-9\s]+")
_WS_RE = re.compile(r"\s+")

_disease_names: list = []
_disease_loaded = False


def _load_disease_names() -> list:
    """Load the 41 disease names covered by this system."""
    global _disease_names, _disease_loaded
    if _disease_loaded:
        return _disease_names
    _disease_loaded = True
    if os.path.exists(DISEASE_CSV):
        try:
            with open(DISEASE_CSV, encoding="utf-8") as handle:
                _disease_names = [str(r.get("Disease") or "").strip() for r in csv.DictReader(handle)]
            _disease_names = [d for d in (_disease_names or []) if d]
        except (OSError, csv.Error):
            _disease_names = []
    return _disease_names


def normalize_message(message: str) -> str:
    """Lowercase, strip, remove punctuation, collapse whitespace."""
    text = _NON_ALNUM_RE.sub(" ", str(message or "").lower())
    return _WS_RE.sub(" ", text).strip()


def _cache_key(message: str, lang: str | None = None) -> str:
    """Hash of the normalized message plus the reply language (if any), so
    English/translated replies never collide."""
    raw = f"{normalize_message(message)}|{lang or 'eng_Latn'}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def should_cache(message: str, disease_names: list | None = None) -> bool:
    """Decide whether a message is safe to cache (stable, non-personal)."""
    text = str(message or "").strip()
    if len(text) < 10:
        return False

    normalized = normalize_message(text)
    padded = f" {normalized} "

    # Personal queries need fresh responses.
    if any(pronoun in padded for pronoun in PERSONAL_PRONOUNS):
        return False

    names = _load_disease_names() if disease_names is None else disease_names
    lowered_names = [str(n).lower().strip() for n in names]

    if any(name and name in normalized for name in lowered_names):
        return True
    if any(keyword in normalized for keyword in CACHE_KEYWORDS):
        return True

    return False


def _evict_if_full() -> None:
    if len(_cache) <= CACHE_MAX_ENTRIES:
        return
    # FIFO eviction: drop the oldest entries until we fit.
    order = sorted(_cache.items(), key=lambda kv: kv[1]["timestamp"])
    for key, _entry in order:
        _cache.pop(key, None)
        if len(_cache) <= CACHE_MAX_ENTRIES:
            break


def get(message: str, lang: str | None = None):
    """Return the cached response for a message (and language), or None on a miss/expiry."""
    global _hits, _misses
    key = _cache_key(message, lang)
    entry = _cache.get(key)
    if entry is None:
        _misses += 1
        return None
    if time.time() - entry["timestamp"] > CACHE_TTL_SECONDS:
        _cache.pop(key, None)
        _misses += 1
        return None
    _hits += 1
    return entry["response"]


def set(message: str, response: str, lang: str | None = None) -> None:
    """Store an LLM response in the cache (FIFO eviction at capacity)."""
    key = _cache_key(message, lang)
    _cache[key] = {"response": str(response or ""), "timestamp": time.time()}
    _evict_if_full()


def stats() -> dict:
    """Stats for the GET /cache/stats endpoint."""
    now = time.time()
    total = _hits + _misses
    hit_rate = (_hits / total * 100.0) if total else 0.0
    oldest = min((e["timestamp"] for e in _cache.values()), default=now)
    return {
        "total_entries": len(_cache),
        "hit_rate_percent": round(hit_rate, 1),
        "oldest_entry_age_minutes": round(max(0.0, (now - oldest) / 60.0), 1),
        "max_capacity": CACHE_MAX_ENTRIES,
    }


if __name__ == "__main__":
    print("cache standalone sanity test")
    print("-" * 50)
    print("disease names loaded:", len(_load_disease_names()))
    print("cacheable (malaria):", should_cache("what are the symptoms of malaria"))
    print("cacheable (personal):", should_cache("I have fever and headache what could it be"))
    print("cacheable (short):", should_cache("hi"))
    set("what are the symptoms of malaria", "Malaria answer...")
    print("get hit:", get("What are the Symptoms of Malaria!"))
    print("get miss:", get("what is dengue"))
    print("stats:", stats())