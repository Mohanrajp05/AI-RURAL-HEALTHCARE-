"""Circuit breaker for the Portkey Gateway tier.

Why this exists: backend/.env can list a LOT of Model Catalog slugs (one per
provider key rotation, to work around free-tier rate limits). Both
llm_router.call_portkey and portkey_llm.call_portkey try their target list
one at a time, client-side. With 100+ targets configured, a single request
made while the gateway is unreachable (network down, every key rate-limited)
would have to exhaust the ENTIRE list -- each target waiting up to its own
timeout -- before falling back to Ollama. That can take many minutes and is
what actually looks like "the gateway disconnected" from the frontend: the
chat just hangs.

This module fixes that with two independent measures, both shared by every
Portkey caller so they only need to be tuned in one place:

1. Bounded, shuffled target selection (`pick_targets`) -- try only a capped
   sample per request instead of the whole list, so one call can never take
   longer than MAX_TARGETS_PER_CALL * per-target-timeout. Shuffling spreads
   load across the slug pool instead of always hammering the same
   (possibly still-cooling-down) rate-limited slug first.

2. A simple open/closed breaker (`circuit_is_open`, `record_all_failed`,
   `record_success`) -- once a request has tried its sample and every target
   in it failed, skip Portkey entirely for COOLDOWN_SECONDS on subsequent
   requests (instant fall-through to the local Ollama tier) instead of
   re-attempting a doomed gateway call each time. After the cooldown, the
   next request is a normal (bounded) probe: success closes the breaker,
   failure reopens it for another cooldown window.
"""

import random
import time

# How long to skip Portkey entirely after a request exhausts its whole
# target sample without success. Short enough that a real recovery (gateway
# comes back, rate limit resets) is noticed quickly; long enough that a
# still-down gateway isn't retried on every single chat message.
COOLDOWN_SECONDS = 45

# Max number of targets tried per request, regardless of how many are
# configured in .env. Keeps worst-case added latency bounded (this many *
# the per-target timeout) even with a large slug pool.
MAX_TARGETS_PER_CALL = 8

_state = {"open_until": 0.0}


def circuit_is_open() -> bool:
    """True when Portkey should be skipped entirely this request."""
    return time.time() < _state["open_until"]


def record_all_failed() -> None:
    """Call after every tried target failed -- opens the breaker."""
    _state["open_until"] = time.time() + COOLDOWN_SECONDS
    print(f"[portkey_circuit] all targets failed -- pausing Portkey for "
          f"{COOLDOWN_SECONDS}s, falling back to local models")


def record_success() -> None:
    """Call as soon as any target succeeds -- closes the breaker."""
    if _state["open_until"]:
        print("[portkey_circuit] Portkey reachable again -- breaker closed")
    _state["open_until"] = 0.0


def pick_targets(all_targets: list) -> list:
    """Return a bounded, shuffled subset of targets to try this call."""
    if len(all_targets) <= MAX_TARGETS_PER_CALL:
        return list(all_targets)
    return random.sample(all_targets, MAX_TARGETS_PER_CALL)
