"""Risk-level classification for disease predictions.

Risk level is computed from BOTH the disease's clinical severity AND the
model's confidence -- never confidence alone, never severity alone.

Rules:
  * Emergency diseases are ALWAYS High Risk regardless of confidence.
  * High-severity disease + confidence >= 70% -> High Risk.
  * High-severity disease + lower confidence -> Medium Risk.
  * Medium-severity disease + confidence >= 70% -> Medium Risk.
  * Medium-severity disease + lower confidence -> Low Risk.
  * Low-severity disease -> always Low Risk.
  * Unknown disease -> Medium Risk (safer default than Low).

Disease names must match the model's label vocabulary exactly
(see DiseaseAndSymptoms.csv / expanded_form_label_encoder.joblib).
"""

EMERGENCY_DISEASES = {
    "Heart attack",
    "Paralysis (brain hemorrhage)",
}

HIGH_SEVERITY_DISEASES = {
    "Heart attack",
    "Paralysis (brain hemorrhage)",
    "Tuberculosis",
    "Pneumonia",
    "Dengue",
    "Typhoid",
    "AIDS",
    "Hepatitis B",
    "Hepatitis C",
    "Hepatitis D",
    "Hepatitis E",
    "hepatitis A",
    "Chronic cholestasis",
    "Alcoholic hepatitis",
    "Jaundice",
    "Diabetes",
    "Hypertension",
}

MEDIUM_SEVERITY_DISEASES = {
    "Malaria",
    "Bronchial Asthma",
    "Gastroenteritis",
    "GERD",
    "Peptic ulcer diseae",
    "Hyperthyroidism",
    "Hypothyroidism",
    "Hypoglycemia",
    "Migraine",
    "Arthritis",
    "Osteoarthristis",
    "Cervical spondylosis",
    "Urinary tract infection",
    "(vertigo) Paroymsal  Positional Vertigo",
}

LOW_SEVERITY_DISEASES = {
    "Common Cold",
    "Allergy",
    "Acne",
    "Fungal infection",
    "Impetigo",
    "Psoriasis",
    "Drug Reaction",
    "Chicken pox",
    "Dimorphic hemmorhoids(piles)",
    "Varicose veins",
}

HIGH_CONFIDENCE_THRESHOLD = 0.70


def _normalize_confidence(confidence):
    """Accept either a fraction (0.0-1.0) or a percentage (0-100)."""
    try:
        c = float(confidence)
    except (TypeError, ValueError):
        c = 0.0
    if c > 1.0:
        c = c / 100.0
    return max(0.0, min(1.0, c))


def compute_risk_level(disease, confidence, emergency_alert=False):
    """Classify an assessment into 'Low Risk' | 'Medium Risk' | 'High Risk'.

    Args:
        disease: str -- predicted disease (must match model label vocabulary).
        confidence: float -- model confidence (fraction 0-1 or percentage 0-100).
        emergency_alert: bool -- True when an emergency disease appears among
            the top model possibilities.

    IMPORTANT -- this function is pure/deterministic, but its OUTPUT is
    persisted (MongoDB `patients` records, browser localStorage assessment
    history). If you change severity-set membership or HIGH_CONFIDENCE_THRESHOLD,
    every previously stored record keeps its OLD output forever unless you
    re-run the migrations for BOTH storage layers:
      * backend/migrate_risk_levels.py            (MongoDB `patients`)
      * client/utils/risk.ts backfillRiskLevels()  (localStorage, runs on
        ProfilePage load) -- must unconditionally recompute and compare,
        not just fill in missing values, or stale-but-valid values never
        get corrected. See risk.test.ts for the regression case.
    Consider a `risk_logic_version` field on stored records if this keeps
    happening, so future migrations can target only records from an older
    version instead of reprocessing everything.
    """
    disease = (disease or "").strip()
    conf = _normalize_confidence(confidence)

    # Rule 1: Emergency diseases are ALWAYS High Risk, regardless of confidence.
    if disease in EMERGENCY_DISEASES or emergency_alert:
        return "High Risk"

    # Rule 2: High-severity disease + high confidence = High Risk.
    if disease in HIGH_SEVERITY_DISEASES and conf >= HIGH_CONFIDENCE_THRESHOLD:
        return "High Risk"

    # Rule 3: High-severity disease + medium/low confidence = Medium Risk
    #         (still a serious disease, just less certain).
    if disease in HIGH_SEVERITY_DISEASES:
        return "Medium Risk"

    # Rule 4: Medium-severity disease + high confidence = Medium Risk.
    if disease in MEDIUM_SEVERITY_DISEASES and conf >= HIGH_CONFIDENCE_THRESHOLD:
        return "Medium Risk"

    # Rule 5: Medium-severity disease + low confidence = Low Risk
    #         (uncertain AND not dangerous even if correct).
    if disease in MEDIUM_SEVERITY_DISEASES:
        return "Low Risk"

    # Rule 6: Low-severity disease = always Low Risk regardless of confidence.
    if disease in LOW_SEVERITY_DISEASES:
        return "Low Risk"

    # Fallback for any disease not categorized above: default to Medium Risk
    # (safer default than Low when severity is unknown).
    return "Medium Risk"


def severity_tier(disease):
    """Return the severity tier name for a disease ('emergency' | 'high' |
    'medium' | 'low' | 'unknown')."""
    disease = (disease or "").strip()
    if disease in EMERGENCY_DISEASES:
        return "emergency"
    if disease in HIGH_SEVERITY_DISEASES:
        return "high"
    if disease in MEDIUM_SEVERITY_DISEASES:
        return "medium"
    if disease in LOW_SEVERITY_DISEASES:
        return "low"
    return "unknown"


if __name__ == "__main__":
    cases = [
        ("Heart attack", 0.966, False, "High Risk"),
        ("Chicken pox", 0.635, False, "Low Risk"),
        ("Diabetes", 0.75, False, "High Risk"),
        ("Common Cold", 0.99, False, "Low Risk"),
        ("Heart attack", 0.40, False, "High Risk"),
        ("Tuberculosis", 0.55, False, "Medium Risk"),
        ("Malaria", 0.80, False, "Medium Risk"),
        ("Malaria", 0.45, False, "Low Risk"),
        ("Some unknown disease", 0.9, False, "Medium Risk"),
    ]
    for disease, conf, alert, expected in cases:
        got = compute_risk_level(disease, conf, alert)
        status = "PASS" if got == expected else "FAIL"
        print(f"[{status}] {disease!r} @ {conf*100:.1f}% -> {got} (expected {expected})")
