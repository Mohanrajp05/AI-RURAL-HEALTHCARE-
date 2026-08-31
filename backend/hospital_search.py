
import math
import os
import re
import socket
import threading
import time

import requests
import urllib3.util.connection as _urllib3_conn

from env_loader import load_env_file

load_env_file()

# overpass-api.de (and, less often, nominatim.openstreetmap.org) publish both
# AAAA and A records. Many cloud hosts (Render's free tier included) have no
# outbound IPv6 route at all, so a getaddrinfo() that hands back the IPv6
# address first makes the very first connect() attempt fail immediately with
# "[Errno 101] Network is unreachable" -- urllib3 does try the remaining
# addresses from getaddrinfo, but on some of these hosts every address
# resolves to an unreachable family, so the request never gets a chance to
# reach the working IPv4 address at all. Forcing IPv4-only resolution here
# sidesteps the whole class of failure; both services are reachable over
# IPv4, so this costs nothing. Process-wide (not just this module's session)
# since urllib3.util.connection is a shared import, but that's fine -- every
# other outbound call in this app (Portkey, Groq, Supabase, Aiven) is IPv4-
# reachable too.
_urllib3_conn.allowed_gai_family = lambda: socket.AF_INET

# ---------------------------------------------------------------------------
# OpenStreetMap stack: Overpass API for the hospital search itself, Nominatim
# for free-text place -> lat/lng geocoding. Both are free, need no API key,
# and are self-hosted-friendly (either can be pointed at a private instance
# by changing the URL below). This replaces the previous LocationIQ-based
# implementation, which was the source of the ~40km-off distance bug traced
# earlier -- LOCATIONIQ_API_KEY in .env is now unused (see .env, kept
# commented out for rollback).
# ---------------------------------------------------------------------------

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"

# overpass-api.de (the primary, most up-to-date public instance) actively
# refuses connections from cloud/datacenter IP ranges (Render's included) as
# an anti-abuse measure -- confirmed here by "[Errno 111] Connection
# refused" on the very same host that resolves and connects fine from a
# residential IP. These community-run mirrors run the same Overpass QL
# interpreter against their own copy of OSM data and are tried in order
# after the primary, so one being blocked/down doesn't take hospital search
# down with it.
OVERPASS_FALLBACK_URLS = [
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]

# Both services' fair-use policies ask that a client identify itself and not
# hammer the endpoint -- no API key to misuse instead, so this is the only
# guardrail. Nominatim's policy is explicit about 1 request/second; Overpass
# publishes no fixed number but asks for the same restraint on its public
# instance, so the one throttle is reused for both.
_USER_AGENT = "AI-Rural-Healthcare-Prediction/1.0 (+https://github.com/Mohanrajp05/AI-Rural-Healthcare-Disease-Prediction)"
_MIN_REQUEST_INTERVAL_SECONDS = 1.0

_throttle_lock = threading.Lock()
_last_request_at = {"nominatim": 0.0, "overpass": 0.0}


def _throttle(provider: str) -> None:
    """Block just long enough to keep requests to `provider` at most
    1/second, mirroring Nominatim's usage policy (and applied to Overpass
    in the same spirit). A simple last-call timestamp is enough here --
    this backend only ever issues one hospital search or geocode at a time
    per incoming request.
    """
    with _throttle_lock:
        now = time.monotonic()
        elapsed = now - _last_request_at[provider]
        wait = _MIN_REQUEST_INTERVAL_SECONDS - elapsed
        if wait > 0:
            time.sleep(wait)
        _last_request_at[provider] = time.monotonic()


NETWORK_ERROR_MESSAGE = "Hospital search failed due to a network error. Please try again."

DEFAULT_RADIUS_METERS = 5000

_EARTH_RADIUS_METERS = 6371000


def haversine_distance_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two lat/lon points, in meters."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(d_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    )
    return 2 * _EARTH_RADIUS_METERS * math.asin(math.sqrt(a))


def _short_address(tags: dict) -> str:
    """Build a short "road, area, city" address from an OSM element's tags
    -- OSM has no single display_name string like the old provider did, so
    this is built up from the addr:* tags instead. Falls back to a generic
    placeholder when a hospital has no address tags at all (common for
    smaller/rural facilities in OSM).
    """
    parts = [
        tags.get("addr:street"),
        tags.get("addr:suburb") or tags.get("addr:neighbourhood"),
        tags.get("addr:city") or tags.get("addr:county"),
    ]
    short = ", ".join(p for p in parts if p)
    return short or "Address not available"


def find_nearby_hospitals(latitude, longitude, radius_meters=DEFAULT_RADIUS_METERS, max_results=5):
    """Overpass search (amenity=hospital) for hospitals around
    (latitude, longitude), within a small fixed radius (see
    DEFAULT_RADIUS_METERS) so only genuinely nearby hospitals come back.

    Returns (results, error): exactly one of the two is populated.
    `results` is `[]` (not None/error) when the API succeeds with zero
    hospitals in range -- that's a valid "nothing nearby" answer, not a
    failure the caller needs to distinguish from a real error.
    """
    try:
        lat = float(latitude)
        lng = float(longitude)
    except (TypeError, ValueError):
        return None, "Invalid location."
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        return None, "Invalid location."

    # Nodes cover point-mapped hospitals; ways/relations cover hospitals
    # mapped as a building outline -- "out center" gives those a single
    # lat/lon (their centroid) so both shapes produce a usable point below.
    #
    # No count limit on `out center` here -- Overpass does NOT sort matches
    # by distance from the query point, it returns them in its own internal
    # (roughly OSM-id) order. A densely-mapped area can easily have 100s of
    # amenity=hospital elements within a few km (296 were found within 5km
    # in Bengaluru's Banashankari during testing), so capping the Overpass
    # side to e.g. 20 silently drops genuinely-nearer hospitals whenever
    # they happen to sit past that cutoff in Overpass's ordering -- exactly
    # what happened to a real hospital ~150m away that never showed up.
    # Pulling the full set in-radius and doing the distance sort/slice
    # ourselves (below) is what actually finds the nearest `max_results`.
    query = f"""
    [out:json][timeout:25];
    (
      node["amenity"="hospital"](around:{radius_meters},{lat},{lng});
      way["amenity"="hospital"](around:{radius_meters},{lat},{lng});
      relation["amenity"="hospital"](around:{radius_meters},{lat},{lng});
    );
    out center;
    """

    resp = None
    for url in (OVERPASS_URL, *OVERPASS_FALLBACK_URLS):
        _throttle("overpass")
        try:
            resp = requests.post(
                url,
                data={"data": query},
                headers={"User-Agent": _USER_AGENT},
                timeout=30,  # > the query's own [timeout:25] server-side budget
            )
        except Exception as exc:
            print(f"[hospital_search] Overpass request to {url} failed: {exc!r}")
            resp = None
            continue
        if resp.status_code == 200:
            break
        print(f"[hospital_search] Overpass status from {url}: {resp.status_code}")
        resp = None

    if resp is None:
        return None, NETWORK_ERROR_MESSAGE

    try:
        data = resp.json()
    except Exception as exc:
        print(f"[hospital_search] bad Overpass response body: {exc!r}")
        return None, NETWORK_ERROR_MESSAGE

    results = []
    for el in data.get("elements", []):
        tags = el.get("tags", {})
        name = tags.get("name")
        if not name:
            continue  # unnamed nodes aren't useful to show on a hospital card

        place_lat = el.get("lat")
        place_lng = el.get("lon")
        if place_lat is None or place_lng is None:
            center = el.get("center") or {}
            place_lat = center.get("lat")
            place_lng = center.get("lon")
        if place_lat is None or place_lng is None:
            continue

        try:
            place_lat = float(place_lat)
            place_lng = float(place_lng)
        except (TypeError, ValueError):
            continue

        distance = haversine_distance_meters(lat, lng, place_lat, place_lng)
        results.append({
            "name": name,
            "address": _short_address(tags),
            "rating": None,       # not available from OSM data
            "open_now": None,     # not available from OSM data
            "distance_meters": int(round(distance)),
            "lat": place_lat,
            "lng": place_lng,
            "maps_url": f"https://www.google.com/maps/dir/?api=1&destination={place_lat},{place_lng}",
        })

    # Guarantee nearest-first ordering (1 km -> 10 km, ...) regardless of
    # whatever order the API itself returned elements in.
    results.sort(key=lambda r: r["distance_meters"])
    return results[:max_results], None


def _nominatim_search(query: str):
    """One raw Nominatim lookup for `query`. Returns (lat, lng, hard_error):
    `hard_error` is set only for a genuine network/HTTP/bad-body failure --
    a clean "zero matches" response comes back as (None, None, None), so
    geocode_place()'s fallback chain below can tell "Nominatim is down"
    apart from "this specific phrasing just isn't in OSM" while it tries
    increasingly simplified rephrasings.
    """
    _throttle("nominatim")
    try:
        resp = requests.get(
            NOMINATIM_URL,
            params={
                "q": query,
                "format": "json",
                "limit": 1,
                "countrycodes": "in",
            },
            headers={"User-Agent": _USER_AGENT},
            timeout=10,
        )
    except Exception as exc:
        print(f"[hospital_search] geocode request failed: {exc!r}")
        return None, None, NETWORK_ERROR_MESSAGE

    if resp.status_code != 200:
        print(f"[hospital_search] Nominatim geocode status: {resp.status_code}")
        return None, None, f"Could not look up '{query}' (status: {resp.status_code})."

    try:
        matches = resp.json()
    except Exception as exc:
        print(f"[hospital_search] bad geocode response body: {exc!r}")
        return None, None, NETWORK_ERROR_MESSAGE

    if not isinstance(matches, list) or not matches:
        return None, None, None  # clean "not found" -- not a hard error

    try:
        lat = float(matches[0]["lat"])
        lng = float(matches[0]["lon"])
    except (KeyError, TypeError, ValueError):
        return None, None, None
    return lat, lng, None


# Strips unit/floor/apartment/PG/room numbering -- the kind of internal
# building detail that is almost never in OSM's map data -- so attempt 2
# below retries with just the area-sounding portion of the query.
_UNIT_SUFFIX_RE = re.compile(
    r"\b(unit|floor|flat|apartment|apt|pg|room|block|no)\s*\.?\s*\d*\b",
    re.IGNORECASE,
)

# QWERTY-adjacent keys, used only to guess a *single* likely mistyped
# character (e.g. "spave" -> "space", c/v being neighbours on the keyboard).
# Deliberately not a real spell-checker: no dictionary, no multi-character
# edits, just "what's one key over from what was typed".
_QWERTY_ADJACENT = {
    "q": "wa", "w": "qeas", "e": "wrsd", "r": "etdf", "t": "ryfg",
    "y": "tugh", "u": "yihj", "i": "uojk", "o": "ipkl", "p": "ol",
    "a": "qwsz", "s": "awedxz", "d": "serfcx", "f": "drtgvc", "g": "ftyhbv",
    "h": "gyujnb", "j": "huikmn", "k": "jiolm", "l": "kop",
    "z": "asx", "x": "zsdc", "c": "xdfv", "v": "cfgb", "b": "vghn",
    "n": "bhjm", "m": "njk",
}


def _typo_variants(text: str, limit: int = 5):
    """Yield up to `limit` single-adjacent-key corrections of `text`, one
    word and one character at a time -- bounded so a typo check never turns
    into dozens of extra Nominatim calls (each candidate costs one throttled
    request, and this only runs after every real attempt has already
    failed).

    Known limitation from that same bound: candidates are generated
    left-to-right, so a typo late in a long word (position 11 of a 12-char
    word, say) can be past the `limit`-candidate cutoff before it's ever
    tried, and this deliberately doesn't spend extra Nominatim calls
    chasing full coverage. It catches the common case (a mistyped letter
    near the start of a shortish word/phrase) without turning into a real
    spell-checker.
    """
    words = text.split()
    seen = set()
    for w_idx, word in enumerate(words):
        if len(word) < 4:
            continue  # too short for a confident single-letter guess
        for c_idx, ch in enumerate(word.lower()):
            for neighbour in _QWERTY_ADJACENT.get(ch, ""):
                candidate_word = word[:c_idx] + neighbour + word[c_idx + 1:]
                if candidate_word == word:
                    continue
                candidate_words = words[:w_idx] + [candidate_word] + words[w_idx + 1:]
                candidate = " ".join(candidate_words)
                if candidate.lower() in seen:
                    continue
                seen.add(candidate.lower())
                yield candidate
                if len(seen) >= limit:
                    return


def _split_variants(text: str, min_word_len: int = 8, max_attempts: int = 8):
    """Yield candidate phrasings where one run-together word (>= min_word_len
    chars, hinting two words got typed with no space between them -- e.g.
    "seethacircle") is split into two at each internal position. Bounded at
    `max_attempts` candidates total, for the same fair-use reason as
    _typo_variants. Unlike a letter-substitution guess, splitting doesn't
    change any character the user typed -- just re-tokenizes it -- so it's
    confident enough to apply silently rather than as a "did you mean".
    """
    words = text.split()
    yielded = 0
    for w_idx, word in enumerate(words):
        if len(word) < min_word_len:
            continue
        for split_at in range(2, len(word) - 1):
            candidate_words = words[:w_idx] + [word[:split_at], word[split_at:]] + words[w_idx + 1:]
            yield " ".join(candidate_words)
            yielded += 1
            if yielded >= max_attempts:
                return


def geocode_place(place_text: str):
    """Resolve a free-text place name ("Mysuru", "Koramangala, Bangalore")
    to (lat, lng) via Nominatim. Returns (lat, lng, error): exactly one of
    (lat, lng) / error is populated.

    Nominatim is an address/place geocoder, not a business-name search --
    "PES Living Space Unit 1" genuinely isn't in OSM's data, and no amount
    of query-massaging turns it into a business directory. What this CAN
    fix is the query being *more specific than OSM's data*, so before
    giving up it retries with increasingly simplified rephrasings:
      1. the exact text as typed
      2. the same text with unit/floor/apartment/PG/room numbering stripped
      3. that result with trailing words progressively dropped (a business/
         property name is often "<name> <real area name>"; dropping words
         from the end peels toward the area name Nominatim actually has)
      4. the original text with any run-together word split back into two
         at each internal position (e.g. "seethacircle" -> "seetha circle")
    and, only if all of those fail, tries a few bounded single-letter typo
    corrections purely to name a specific "did you mean" suggestion in the
    final error message -- it does not silently search the corrected
    spelling on the user's behalf.
    """
    place_text = str(place_text or "").strip()
    if not place_text:
        return None, None, "Please enter a city or area name."

    # Attempt 1: exact text as typed.
    lat, lng, hard_error = _nominatim_search(place_text)
    if hard_error:
        return None, None, hard_error
    if lat is not None:
        return lat, lng, None

    # Attempt 2: strip unit/floor/apartment/PG/room numbering and retry.
    simplified = _UNIT_SUFFIX_RE.sub("", place_text).strip(" ,")
    if simplified and simplified.lower() != place_text.lower():
        lat, lng, hard_error = _nominatim_search(simplified)
        if hard_error:
            return None, None, hard_error
        if lat is not None:
            return lat, lng, None

    # Attempt 3: progressively drop trailing words from whichever of the
    # two phrasings above is left to try with. Stops at 2 words, never 1 --
    # a lone leftover word ("Pes", "unit", "block") is common enough as a
    # place name *somewhere* in the country that it will confidently match
    # something hundreds of km away, which is worse than admitting defeat
    # (this is the same class of silent-wrong-location bug the LocationIQ
    # migration was meant to fix, just self-inflicted instead of upstream).
    words = (simplified or place_text).split()
    while len(words) > 2:
        words = words[:-1]
        candidate = " ".join(words)
        lat, lng, hard_error = _nominatim_search(candidate)
        if hard_error:
            return None, None, hard_error
        if lat is not None:
            return lat, lng, None

    # Attempt 3.5: a run-together word with no space typed between two real
    # words (e.g. "seethacircle" -> "seetha circle") -- tried on the
    # original text, since attempt 2/3's simplification could have already
    # dropped the run-together word entirely.
    for candidate in _split_variants(place_text):
        lat, lng, hard_error = _nominatim_search(candidate)
        if hard_error:
            return None, None, hard_error
        if lat is not None:
            return lat, lng, None

    # Attempt 4 (message-only): a few bounded single-adjacent-key
    # corrections, checked purely to see whether a "did you mean" is worth
    # naming in the failure message below -- never applied silently, since
    # auto-navigating a hospital search to an unconfirmed guessed location
    # isn't safe to do without the user seeing what changed.
    did_you_mean = None
    for variant in _typo_variants(place_text):
        v_lat, v_lng, v_hard_error = _nominatim_search(variant)
        if v_hard_error:
            break  # Nominatim itself is failing -- stop guessing, report that
        if v_lat is not None:
            did_you_mean = variant
            break

    if did_you_mean:
        return None, None, (
            f"I couldn't find '{place_text}' by that name. Did you mean "
            f"'{did_you_mean}'? OpenStreetMap works best with street "
            f"addresses, area names, or well-known landmarks rather than "
            f"specific business/building names -- try searching for "
            f"'{did_you_mean}', or a nearby street/locality instead."
        )

    return None, None, (
        f"I couldn't find '{place_text}' by name -- OpenStreetMap works "
        f"best with street addresses, area names, or well-known landmarks "
        f"rather than specific business/building names. Try searching for "
        f"the nearby street or locality instead (e.g. 'Srinivasnagar, "
        f"Banashankari' or '16th Main Road, Bengaluru')."
    )


def find_hospitals_by_place(place_text: str, radius_meters=15000, max_results=5):
    """Geocode `place_text` (city/area name) via Nominatim, then run the
    same Overpass hospital search around it -- the GPS-free fallback path
    for when the device's own location is unavailable or too inaccurate to
    trust. A wider default radius than find_nearby_hospitals's since a
    named place is a whole area, not a point.
    """
    lat, lng, error = geocode_place(place_text)
    if error:
        return None, error
    return find_nearby_hospitals(lat, lng, radius_meters=radius_meters, max_results=max_results)



HIGH_RISK_THRESHOLD = 70

_RISK_PATTERNS = [
    re.compile(r"risk\s*(?:level|score)[:\s]+(\d{1,3})\s*%", re.IGNORECASE),
    re.compile(r"risk[:\s]+(\d{1,3})\s*%", re.IGNORECASE),
    re.compile(r"confidence[:\s]+(\d{1,3})\s*%", re.IGNORECASE),
    re.compile(r"(\d{1,3})\s*%\s*risk", re.IGNORECASE),
]


def extract_risk_percentage(text: str):
    """Return the first 0-100 risk/confidence percentage found in `text`,
    or None if none of the known phrasings appear.
    """
    if not text:
        return None
    for pattern in _RISK_PATTERNS:
        match = pattern.search(text)
        if match:
            value = int(match.group(1))
            if 0 <= value <= 100:
                return value
    return None


if __name__ == "__main__":
    print("hospital_search standalone sanity test")
    print("-" * 50)
    print("provider: OpenStreetMap (Overpass + Nominatim), no API key required")
    print("risk 'Risk: 85%' ->", extract_risk_percentage("Overall Risk: 85%"))
    print("risk 'Risk Level: 92%' ->", extract_risk_percentage("Risk Level: 92%"))
    print("risk 'ML Confidence: 88%' ->", extract_risk_percentage("ML Confidence: 88%"))
    print("risk '20% risk' ->", extract_risk_percentage("Overall this is a 20% risk case"))
    print("risk 'no mention' ->", extract_risk_percentage("Patient has a mild cough."))
