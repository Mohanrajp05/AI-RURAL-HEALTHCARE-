"""Guarded disease prediction pipeline (new 40-symptom form models).

This module ports the logic from Disease dataset/Dataset/model.ipynb into the
backend so the Flask server can serve the new ensemble:

  MultiLabelBinarizer (40 form symptoms) + 3-model ensemble
  (RandomForest | GaussianNB | RBF-SVM) with mean soft-voting,
  guarded by confidence bands, ambiguity margins, model-disagreement and
  confusable-group flags, plus per-disease precaution lookup.

It REPLACES the old 199 one-hot get_dummies pipeline (order-dependent
features, train/serving vocab-skew -> "only 5-6 diseases" bug).

Conventions:
    * checkboxes_to_symptom_vector() is position-independent — the 40-dim
      vector only encodes membership in the symptom set, so checkbox order
      never influences predictions.
    * All .joblib / .csv files must sit in the SAME directory as this file
      (paths are resolved relative to __file__).
"""

import csv
import os
from difflib import get_close_matches

import joblib
import numpy as np

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

_ENC_PATH = os.path.join(BASE_DIR, "expanded_form_encoder.joblib")
_LABEL_PATH = os.path.join(BASE_DIR, "expanded_form_label_encoder.joblib")
_RF_PATH = os.path.join(BASE_DIR, "expanded_form_rf.joblib")
_NB_PATH = os.path.join(BASE_DIR, "expanded_form_nb.joblib")
_SVM_PATH = os.path.join(BASE_DIR, "expanded_form_svm.joblib")
_PRECAUTIONS_PATH = os.path.join(BASE_DIR, "Disease_precaution.csv")

CONFIDENCE_BANDS = [
    (0.70, "HIGH"),
    (0.50, "MEDIUM"),
    (0.00, "LOW"),
]
AMBIGUOUS_MARGIN_THRESHOLD = 0.25

# Minimum mean soft-vote probability an emergency disease must hold before the
# URGENT banner may fire. Without this floor, ANY emergency disease that
# mathematically occupies a top-3 rank slot triggers the banner even at sub-1%
# probability (41 classes must sum to 100%, so something always fills ranks
# 2-3). Measured: the false-positive Paralysis case scored only 0.0087 while
# genuine heart-attack signals scored 0.50-0.97. Calibration reference: the
# 0.35-0.50 confidence band measured ~41% real accuracy, so anything far below
# it is rank-slot noise, not a real emergency signal.
EMERGENCY_ALERT_MIN_PROBABILITY = 0.15

# frontend checkbox label -> model symptom tokens (mirrors notebook)
CHECKBOX_TO_SYMPTOMS = {
    "Mild Fever": ["mild_fever"],
    "High Fever": ["high_fever"],
    "Cough": ["cough"],
    "Runny Nose": ["runny_nose"],
    "Breathlessness": ["breathlessness"],
    "Fatigue": ["fatigue"],
    "Headache": ["headache"],
    "Muscle Pain": ["muscle_pain"],
    "Throat Irritation": ["throat_irritation"],
    "Nausea": ["nausea"],
    "Vomiting": ["vomiting"],
    "Diarrhoea": ["diarrhoea"],
    "Loss of Appetite": ["loss_of_appetite"],
    "Chest Pain": ["chest_pain"],
    "Chills": ["chills"],
    "Dizziness": ["dizziness"],
    "Joint Pain": ["joint_pain"],
    "Skin Rash": ["skin_rash"],
    "Polyuria": ["polyuria"],
    "Blurred and Distorted Vision": ["blurred_and_distorted_vision"],
    "Abdominal Pain": ["abdominal_pain"],
    "Yellowish Skin": ["yellowish_skin"],
    "Yellowing of Eyes": ["yellowing_of_eyes"],
    "Malaise": ["malaise"],
    "Itching": ["itching"],
    "Sweating": ["sweating"],
    "Dark Urine": ["dark_urine"],
    "Irritability": ["irritability"],
    "Excessive Hunger": ["excessive_hunger"],
    "Weight Loss": ["weight_loss"],
    "Lethargy": ["lethargy"],
    "Phlegm": ["phlegm"],
    "Swelled Lymph Nodes": ["swelled_lymph_nodes"],
    "Loss of Balance": ["loss_of_balance"],
    "Abnormal Menstruation": ["abnormal_menstruation"],
    "Muscle Weakness": ["muscle_weakness"],
    "Depression": ["depression"],
    "Fast Heart Rate": ["fast_heart_rate"],
    "Red Spots Over Body": ["red_spots_over_body"],
    "Back Pain": ["back_pain"],

    # --- 4 new symptoms added after diagnostic review (see retrain_expanded_form.py) ---
    # Token audit against DiseaseAndSymptoms.csv (131-token vocabulary):
    "Sensitivity to Light": ["visual_disturbances"],
    # ^ no "photophobia"/"sensitivity"/"light" token exists in the CSV; the
    #   Migraine rows use "visual_disturbances" (114 rows, Migraine-only, zero
    #   Vertigo rows), so that token is the model equivalent.
    "Wheezing": [],
    # ^ no "wheezing" token exists in the CSV at all -> no model equivalent yet.
    "Severe Joint Pain": ["joint_pain"],
    # ^ the CSV has no dedicated "severe" joint token; Dengue rows use
    #   "joint_pain", so this checkbox reuses that existing bit.
    "Pale / Clay-coloured Stools": ["dischromic _patches"],
    # ^ token exists in the CSV (note the dataset typo "dischromic _patches"),
    #   but ONLY on Fungal-infection rows - flagging this in case we need a
    #   dedicated jaundice token later.

    # --- 10 new symptoms added to unlock 14 more diseases (batch 2) ---
    "Neck Pain": ["neck_pain"],
    "Swollen / Stiff Joints": ["swelling_joints"],
    "Stomach Pain / Acidity": ["stomach_pain"],
    "Spinning Sensation": ["spinning_movements"],
    "Burning Urination": ["burning_micturition"],
    "Skin Peeling": ["skin_peeling"],
    "Weakness in Limbs": ["weakness_in_limbs"],
    "Swollen Stomach": ["swelling_of_stomach"],
    "Watery / Itchy Eyes": ["watering_from_eyes"],
    "Constipation": ["constipation"],

    # --- 8 new symptoms added to unlock the final 8 diseases (batch 3) ---
    "Pus-filled Pimples / Blackheads": ["pus_filled_pimples"],
    "Continuous Sneezing": ["continuous_sneezing"],
    "Joint Stiffness / Painful Walking": ["painful_walking"],
    "Muscle Wasting": ["muscle_wasting"],
    "Leg Swelling / Varicose Veins": ["swollen_legs"],
    "Bleeding / Bloody Stool": ["bloody_stool"],
    "Bladder Discomfort": ["bladder_discomfort"],
    "Obesity / Excess Weight": ["obesity"],
}

# token -> frontend checkbox label, used to feed/verified BioMistral tokens
# (and old-checkbox aliases) back through predict_guarded()
TOKEN_TO_CHECKBOX = {
    token: label
    for label, tokens in CHECKBOX_TO_SYMPTOMS.items()
    for token in tokens
}

CONFUSABLE_GROUPS = [
    {"Arthritis", "Osteoarthristis"},
    {"Hepatitis B", "Hepatitis C", "Hepatitis D", "Hepatitis E", "hepatitis A",
     "Chronic cholestasis", "Peptic ulcer diseae"},
    {"Hyperthyroidism", "Hypothyroidism", "Varicose veins"},
]

EMERGENCY_DISEASES = {"Heart attack", "Paralysis (brain hemorrhage)"}

# Risk-level classification (disease severity x model confidence) lives in
# risk_classification.py; predict_guarded() exposes it as "risk_level".
from risk_classification import compute_risk_level as _compute_risk_level

# ---------------------------------------------------------------------------
# lazy model loading (kept out of import time so the Flask app can fall back
# to the old pipeline if these files are missing)
# ---------------------------------------------------------------------------
_mlb = None
_label_encoder = None
_rf = None
_nb = None
_svm = None
_precautions_map = {}
_load_error: str | None = None


def _normalize_disease(name: str) -> str:
    return "".join(str(name or "").lower().split())


def _load_precaution_map():
    global _precautions_map
    if _precautions_map:
        return _precautions_map
    if not os.path.exists(_PRECAUTIONS_PATH):
        return {}
    with open(_PRECAUTIONS_PATH, encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            disease = str(row.get("Disease") or "").strip()
            if not disease:
                continue
            precautions = [
                str(row.get(f"Precaution_{i}") or "").strip()
                for i in range(1, 5)
                if str(row.get(f"Precaution_{i}") or "").strip()
            ]
            _precautions_map[_normalize_disease(disease)] = precautions
    return _precautions_map


def _precautions_for(disease: str) -> list:
    """Return the precautions row for a predicted disease, matched loosely."""
    pmap = _load_precaution_map()
    if not pmap:
        return []
    key = _normalize_disease(disease)
    if key in pmap:
        return pmap[key]
    matches = get_close_matches(key, pmap.keys(), n=1, cutoff=0.8)
    return pmap[matches[0]] if matches else []


def load_models():
    """Load encoders + ensemble lazily. Returns (ok, error) tuple."""
    global _mlb, _label_encoder, _rf, _nb, _svm, _load_error
    if _rf is not None:
        return True, None
    try:
        _mlb = joblib.load(_ENC_PATH)
        _label_encoder = joblib.load(_LABEL_PATH)
        _rf = joblib.load(_RF_PATH)
        _nb = joblib.load(_NB_PATH)
        _svm = joblib.load(_SVM_PATH)
        _load_error = None
        return True, None
    except Exception as exc:  # noqa: BLE001
        _load_error = str(exc)
        _mlb = _label_encoder = _rf = _nb = _svm = None
        return False, _load_error


def checkboxes_to_symptom_vector(checked_boxes):
    tokens = set()
    dropped = []
    for box in checked_boxes:
        mapped = CHECKBOX_TO_SYMPTOMS.get(box)
        if not mapped:
            dropped.append(box)
            continue
        tokens.update(mapped)
    vec = _mlb.transform([sorted(tokens)])
    return vec, sorted(tokens), dropped


def confidence_band(p):
    for threshold, label in CONFIDENCE_BANDS:
        if p >= threshold:
            return label
    return "LOW"


def predict_guarded(checked_boxes, top_n=3, min_symptoms=2):
    ok, err = load_models()
    if not ok:
        raise RuntimeError(f"model pipeline unavailable: {err}")

    vec, matched_tokens, dropped = checkboxes_to_symptom_vector(checked_boxes)

    proba_rf = _rf.predict_proba(vec)[0]
    proba_nb = _nb.predict_proba(vec)[0]
    proba_svm = _svm.predict_proba(vec)[0]
    avg_proba = (proba_rf + proba_nb + proba_svm) / 3

    order = np.argsort(avg_proba)[::-1]
    top_idx = order[:top_n]
    ranked = [
        {
            "disease": _label_encoder.inverse_transform([i])[0],
            "confidence": round(float(avg_proba[i]), 4),
            "precautions": _precautions_for(_label_encoder.inverse_transform([i])[0]),
        }
        for i in top_idx
    ]
    top1_disease = ranked[0]["disease"]
    top1_conf = ranked[0]["confidence"]
    margin = float(avg_proba[order[0]] - avg_proba[order[1]])

    # per-model agreement
    top1_rf = _label_encoder.inverse_transform([proba_rf.argmax()])[0]
    top1_nb = _label_encoder.inverse_transform([proba_nb.argmax()])[0]
    top1_svm = _label_encoder.inverse_transform([proba_svm.argmax()])[0]
    models_agree = len({top1_rf, top1_nb, top1_svm}) == 1

    flags = []
    band = confidence_band(top1_conf)
    too_few = len(matched_tokens) < min_symptoms
    if too_few:
        band = "LOW"
        flags.append("TOO_FEW_SYMPTOMS")
    if band != "HIGH":
        flags.append(f"{band}_CONFIDENCE")
    if margin < AMBIGUOUS_MARGIN_THRESHOLD:
        flags.append("AMBIGUOUS_TOP_CANDIDATES")
    if not models_agree:
        flags.append("MODEL_DISAGREEMENT")

    confusable_note = None
    for group in CONFUSABLE_GROUPS:
        if top1_disease in group:
            confusable_note = (
                f"'{top1_disease}' cannot be reliably separated from "
                f"{sorted(group - {top1_disease})} using only checkbox symptoms."
            )
            break

    emergency_hits = [
        p["disease"]
        for p in ranked
        if p["disease"] in EMERGENCY_DISEASES
        and p["confidence"] >= EMERGENCY_ALERT_MIN_PROBABILITY
    ]

    risk_level = _compute_risk_level(top1_disease, top1_conf, bool(emergency_hits))

    # human-readable recommendation
    if emergency_hits:
        recommendation = (
            f"URGENT: {', '.join(emergency_hits)} appears among the top possibilities. "
            f"Recommend immediate in-person clinical evaluation regardless of model confidence."
        )
    elif "TOO_FEW_SYMPTOMS" in flags:
        recommendation = "Not enough symptoms selected for a meaningful prediction. Ask the patient for more."
    elif band == "HIGH" and models_agree and margin >= AMBIGUOUS_MARGIN_THRESHOLD:
        recommendation = "High-confidence prediction. Still confirm with a clinician before treatment."
    else:
        recommendation = (
            "Low-confidence / ambiguous result. Treat this as a shortlist, not a diagnosis -- "
            "refer to a clinician for confirmation."
        )

    return {
        "predictions": ranked,
        "confidence_band": band,
        "top1_vs_top2_margin": round(margin, 4),
        "model_agreement": {"rf": top1_rf, "nb": top1_nb, "svm": top1_svm, "all_agree": models_agree},
        "flags": flags,
        "confusable_with_note": confusable_note,
        "emergency_alert": bool(emergency_hits),
        "risk_level": risk_level,
        "recommendation": recommendation,
        "matched_symptoms": matched_tokens,
        "ignored_checkboxes": dropped,
        "disclaimer": "This is a screening aid, not a medical diagnosis.",
    }


def available_checkboxes() -> list:
    """Expose the 40 checkbox labels the model understands (UI helper)."""
    return list(CHECKBOX_TO_SYMPTOMS.keys())


if __name__ == "__main__":
    ok, err = load_models()
    print("load_ok:", ok, err)
    print("--- High-confidence, unambiguous case ---")
    for k, v in predict_guarded(["Dizziness", "Nausea", "Vomiting", "Headache"]).items():
        print(f"  {k}: {v}")

    print("\n--- Known-confusable case (Arthritis vs Osteoarthritis) ---")
    for k, v in predict_guarded(["Joint Pain", "Muscle Pain"]).items():
        print(f"  {k}: {v}")

    print("\n--- Too few symptoms ---")
    for k, v in predict_guarded(["Headache"]).items():
        print(f"  {k}: {v}")