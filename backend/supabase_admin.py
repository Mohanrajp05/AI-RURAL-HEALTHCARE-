"""Server-side Supabase Admin API client.

Lists real registered users directly from Supabase Auth -- the actual
source of truth for authentication (email/password, Google, GitHub,
LinkedIn OAuth all land there). This REPLACES the old local MySQL
`users` table mirror/sync (upsert_user/list_users in mysql_store.py,
/api/users/sync in app.py) -- that table required every auth path to
remember to call a sync endpoint, and OAuth logins never did, so it was
permanently out of date. Querying Supabase directly has no sync step to
forget.

Uses the service_role secret key, which grants full admin access to your
Supabase project -- it must NEVER be sent to the frontend or logged.
Keep it in backend/.env (SUPABASE_SERVICE_ROLE_KEY) only.
"""
import os

import requests

from env_loader import load_env_file

load_env_file()

# Reuses the frontend's project URL var as the single source of truth for
# which Supabase project this is -- no need to duplicate it under a
# backend-only name. VITE_SUPABASE_URL is the correctly-spelled name used
# everywhere else (.env.example, client/.env.local, VITE_SUPABASE_ANON_KEY);
# VITE_SUPERBASE_URL ("SUPER" typo) is kept as a fallback only because
# backend/.env and this file both originally used that misspelling, and
# whichever Render's dashboard actually has configured should still be
# picked up without needing this code changed again.
SUPABASE_URL = (
    os.environ.get("VITE_SUPABASE_URL")
    or os.environ.get("VITE_SUPERBASE_URL")
    or os.environ.get("SUPABASE_URL")
    or ""
).rstrip("/")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")

REQUEST_TIMEOUT_SECONDS = 10


def is_configured() -> bool:
    """True when both the project URL and service_role key are present."""
    return bool(SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY)


def list_users(per_page: int = 200) -> list:
    """All registered Supabase Auth users, newest first.

    Returns a list of dicts: {id, email, full_name, provider, created_at,
    last_sign_in_at}. Deliberately excludes raw provider identity blobs,
    tokens, and anything else sensitive -- only what the Admin Dashboard
    needs to display. Empty list (not an exception) when unconfigured or
    the call fails; never raises.
    """
    if not is_configured():
        print("[supabase_admin] SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY not configured.")
        return []

    headers = {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
    }

    users = []
    page = 1
    try:
        while True:
            resp = requests.get(
                f"{SUPABASE_URL}/auth/v1/admin/users",
                headers=headers,
                params={"page": page, "per_page": per_page},
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
            resp.raise_for_status()
            payload = resp.json()
            batch = payload.get("users", payload if isinstance(payload, list) else [])
            if not batch:
                break

            for u in batch:
                meta = u.get("user_metadata") or {}
                identities = u.get("identities") or []
                provider = (
                    identities[0].get("provider")
                    if identities
                    else (u.get("app_metadata") or {}).get("provider")
                ) or "email"
                users.append({
                    "id": u.get("id"),
                    "email": u.get("email") or "",
                    "full_name": meta.get("full_name") or meta.get("name") or "",
                    "provider": provider,
                    "created_at": u.get("created_at"),
                    "last_sign_in_at": u.get("last_sign_in_at"),
                })

            if len(batch) < per_page:
                break
            page += 1
    except Exception as exc:
        print(f"[supabase_admin] list_users failed: {exc!r}")
        return []

    users.sort(key=lambda u: u.get("created_at") or "", reverse=True)
    return users
