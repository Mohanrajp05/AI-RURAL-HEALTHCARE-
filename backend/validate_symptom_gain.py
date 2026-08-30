"""Compare low-confidence predictions before/after the expanded-form retrain.

Run BEFORE retraining:   py -m validate_symptom_gain --mode before
Run AFTER  retraining:   py -m validate_symptom_gain --mode after

"before" strips the 4 new checkboxes from the label->token map at runtime so
predictions reflect the old 40-symptom model+pipeline. "after" uses the full
44-symptom mapping with the retrained artifacts.
"""

import argparse

from predict_disease_guarded import predict_guarded, CHECKBOX_TO_SYMPTOMS

NEW_BOXES = [
    "Sensitivity to Light",
    "Wheezing",
    "Severe Joint Pain",
    "Pale / Grey-coloured Stools",
]

CASES = {
    "Migraine": ["Headache", "Nausea", "Vomiting", "Sensitivity to Light"],
    "Bronchial Asthma": ["Cough", "Breathlessness", "Fatigue", "Wheezing"],
    "Dengue": ["High Fever", "Muscle Pain", "Headache", "Severe Joint Pain"],
    "Jaundice": ["Yellowish Skin", "Loss of Appetite", "Nausea", "Pale / Grey-coloured Stools"],
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["before", "after"], required=True)
    args = ap.parse_args()

    mode = args.mode
    if mode == "before":
        for box in NEW_BOXES:
            CHECKBOX_TO_SYMPTOMS.pop(box, None)

    print(f"=== CASE SCORING: {mode} (models = current backend joblibs) ===")
    for label, boxes in CASES.items():
        result = predict_guarded(boxes, top_n=3)
        preds = result["predictions"]
        top = preds[0] if preds else None
        if top:
            print(f"{label:18s} -> {top['disease']:20s} conf={top['confidence'] * 100:5.1f}%  band={result['confidence_band']}  flags={result['flags']}")
        else:
            print(f"{label:18s} -> NO PREDICTION (guarded)")
        for p in preds[1:]:
            print(f"{'':18s}    alt: {p['disease']:20s} conf={p['confidence'] * 100:5.1f}%")
        print(f"          matched_tokens={result['matched_symptoms']}  ignored={result['ignored_checkboxes']}")
    print()


if __name__ == "__main__":
    main()