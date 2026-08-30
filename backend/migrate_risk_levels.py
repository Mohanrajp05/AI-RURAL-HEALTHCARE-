"""One-time migration: recompute risk_level for existing stored assessment
records using the canonical backend compute_risk_level() function.

Sources of stored records:
  1. MySQL `patients` table (admin dashboard / patient log) -- this script
     updates those in place.
  2. Browser localStorage `assessments_<email>` (Assessment History page) --
     cannot be reached from Python; the ProfilePage performs the equivalent
     in-place backfill on load (client/utils/risk.ts), logging the count.

Run:  venv\\Scripts\\python backend\\migrate_risk_levels.py
"""

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND_DIR))

import mysql_store  # noqa: E402
from risk_classification import compute_risk_level  # noqa: E402


def migrate():
    print("=" * 60)
    print("risk_level migration for stored assessment records")
    print("=" * 60)

    if not mysql_store.is_available():
        print("MySQL unavailable. Skipping database records.")
        print("(localStorage records are backfilled by ProfilePage on load.)")
        return

    records = mysql_store.list_patients()
    total = len(records)

    updated = 0
    unchanged = 0
    skipped = 0
    breakdown = {}

    for rec in records:
        disease = str(rec.get("predictedDisease") or "").strip()
        if not disease or disease in ("N/A", "Error"):
            continue
        confidence = rec.get("confidence", 0)
        try:
            confidence = float(confidence)
        except (TypeError, ValueError):
            confidence = 0.0
        # Old records did not store emergency_alert; the disease-name rule
        # still catches emergency diseases (Heart attack, Paralysis).
        new_level = compute_risk_level(disease, confidence, False)

        old_level = rec.get("riskLevel")
        if old_level == new_level:
            unchanged += 1
            continue

        if mysql_store.update_patient_risk_level(rec["id"], new_level):
            updated += 1
            breakdown[new_level] = breakdown.get(new_level, 0) + 1
            print(
                f"  id={rec.get('id')} {disease!r} conf={confidence:.1%} "
                f"riskLevel {old_level or '(missing)'!r} -> {new_level!r}"
            )
        else:
            skipped += 1

    print("-" * 60)
    print(f"Total patient records in table       : {total}")
    print(f"Records updated in place             : {updated}")
    print(f"Records already correct              : {unchanged}")
    print(f"Records skipped (update failed)      : {skipped}")
    print(f"Updated by tier: {breakdown or 'none'}")
    print("-" * 60)
    print("Done. localStorage Assessment History records are backfilled by the")
    print("Profile page on next load (see client/utils/risk.ts).")


if __name__ == "__main__":
    migrate()
