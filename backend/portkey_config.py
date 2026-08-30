
import os

from env_loader import load_env_file

load_env_file()


def _discover_slug_targets():
    """Build fallback targets from every Portkey slug found in .env.

    Returns a list of target dicts whose model string is "@{slug}/{model}",
    the Model Catalog format. Reads both the list format
    (PORTKEY_SLUGS + PORTKEY_MODEL) and the per-provider format
    (PORTKEY_<NAME>_SLUG + PORTKEY_<NAME>_MODEL).
    """
    targets = []

    # Format 1: PORTKEY_SLUGS = comma-separated slugs, one shared model.
    model = os.environ.get("PORTKEY_MODEL", "").strip()
    slugs_csv = os.environ.get("PORTKEY_SLUGS", "").strip()
    if model and slugs_csv:
        for slug in (s.strip() for s in slugs_csv.split(",")):
            if slug:
                targets.append({"override_params": {"model": f"@{slug}/{model}"}})


    for key, value in os.environ.items():
        if key.startswith("PORTKEY_") and key.endswith("_SLUG"):
            prefix = key[: -len("_SLUG")]  # e.g. "PORTKEY_GEMINI"
            model_key = f"{prefix}_MODEL"
            model_name = os.environ.get(model_key, "").strip()
            if not model_name:
                print(f"[portkey_config] WARNING: {key} is set but "
                      f"{model_key} is missing -- skipping this provider")
                continue
            for slug in (s.strip() for s in value.split(",")):
                if slug:
                    targets.append({"override_params": {"model": f"@{slug}/{model_name}"}})

    return targets


PORTKEY_TARGETS = _discover_slug_targets()

PORTKEY_API_KEY = os.environ.get(
    "PORTKEY_API_KEY",
    os.environ.get("PORTKEY_GATEWAY_KEY", ""),
).strip()


PORTKEY_MODELS = [t["override_params"]["model"] for t in PORTKEY_TARGETS]

PORTKEY_CONFIG = {
    "strategy": {"mode": "fallback"},
    "targets": PORTKEY_TARGETS,
}

if not PORTKEY_TARGETS:
    print("[portkey_config] WARNING: No Portkey provider slugs found "
          "in .env -- Portkey tier will be unavailable")
elif not PORTKEY_API_KEY:
    print("[portkey_config] WARNING: Portkey slugs found but no gateway "
          "API key (PORTKEY_GATEWAY_KEY/PORTKEY_API_KEY) -- Portkey tier "
          "will be unavailable")
else:
    print(f"[portkey_config] Loaded {len(PORTKEY_TARGETS)} provider "
          f"target(s): {[t['override_params']['model'] for t in PORTKEY_TARGETS]}")


def portkey_cloud_enabled() -> bool:
    """True when the cloud tier is fully configured (key + at least one slug).

    Kept for backward compatibility; llm_router now checks the same
    conditions inline.
    """
    return bool(PORTKEY_API_KEY and PORTKEY_TARGETS)
