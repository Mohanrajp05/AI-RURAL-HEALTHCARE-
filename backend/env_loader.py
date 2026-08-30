"""Minimal backend/.env loader (no external dependency).

Shared by portkey_config.py and portkey_llm.py so both Portkey modules read
the same .env without importing from each other (avoids a circular import
now that portkey_llm delegates to portkey_config for its client config).
Never overwrites a variable already present in the real environment.
"""

import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def load_env_file():
    env_path = os.path.join(BASE_DIR, ".env")
    if not os.path.exists(env_path):
        return
    try:
        with open(env_path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
    except OSError:
        pass
