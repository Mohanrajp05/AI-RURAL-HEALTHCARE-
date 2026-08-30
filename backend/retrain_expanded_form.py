

import os

import joblib
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.naive_bayes import GaussianNB
from sklearn.svm import SVC
from sklearn.preprocessing import MultiLabelBinarizer, LabelEncoder
from sklearn.model_selection import train_test_split, cross_val_score
from sklearn.metrics import accuracy_score

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

DATA_CANDIDATES = [
    os.path.join(BASE_DIR, "DiseaseAndSymptoms.csv"),
    os.path.join(BASE_DIR, "..", "Disease dataset", "Dataset", "DiseaseAndSymptoms.csv"),
    os.path.join(os.getcwd(), "DiseaseAndSymptoms.csv"),
]

OUT_PATHS = {
    "rf": os.path.join(BASE_DIR, "expanded_form_rf.joblib"),
    "nb": os.path.join(BASE_DIR, "expanded_form_nb.joblib"),
    "svm": os.path.join(BASE_DIR, "expanded_form_svm.joblib"),
    "encoder": os.path.join(BASE_DIR, "expanded_form_encoder.joblib"),
    "label_encoder": os.path.join(BASE_DIR, "expanded_form_label_encoder.joblib"),
}

MAX_ACCURACY_DROP = 0.02

# The original 40 form tokens, listed exactly as in model.ipynb cell 13.
BASE_FORM_SYMPTOMS = [
    "mild_fever", "high_fever", "cough", "runny_nose", "breathlessness",
    "fatigue", "headache", "muscle_pain", "throat_irritation", "nausea",
    "vomiting", "diarrhoea", "loss_of_appetite", "chest_pain", "chills",
    "dizziness", "joint_pain", "skin_rash", "polyuria",
    "blurred_and_distorted_vision",
    "abdominal_pain", "yellowish_skin", "yellowing_of_eyes", "malaise",
    "itching", "sweating", "dark_urine", "irritability",
    "excessive_hunger", "weight_loss", "lethargy", "phlegm",
    "swelled_lymph_nodes", "loss_of_balance", "abnormal_menstruation",
    "muscle_weakness", "depression", "fast_heart_rate",
    "red_spots_over_body", "back_pain",
]

# New CSV tokens added by the 4 new checkboxes (see predict_disease_guarded.py):
#   "Sensitivity to Light"        -> "visual_disturbances" (no photophobia token in CSV)
#   "Wheezing"                    -> <no CSV token exists - checkbox maps to []>
#   "Severe Joint Pain"           -> reuses "joint_pain" (already in the 40)
#   "Pale / Clay-coloured Stools" -> "dischromic _patches" (exact CSV spelling)
NEW_TOKENS = ["visual_disturbances", "dischromic _patches"]

# 10 new CSV tokens added by the 10 new checkboxes (see predict_disease_guarded.py):
#   "Neck Pain"            -> "neck_pain"
#   "Swollen / Stiff Joints" -> "swelling_joints"
#   "Stomach Pain / Acidity" -> "stomach_pain"
#   "Spinning Sensation"   -> "spinning_movements"
#   "Burning Urination"    -> "burning_micturition"
#   "Skin Peeling"         -> "skin_peeling"
#   "Weakness in Limbs"    -> "weakness_in_limbs"
#   "Swollen Stomach"      -> "swelling_of_stomach"
#   "Watery / Itchy Eyes"  -> "watering_from_eyes"
#   "Constipation"         -> "constipation"
NEW_TOKENS = [
    "visual_disturbances", "dischromic _patches",
    "neck_pain", "swelling_joints", "stomach_pain", "spinning_movements",
    "burning_micturition", "skin_peeling", "weakness_in_limbs",
    "swelling_of_stomach", "watering_from_eyes", "constipation",
    "pus_filled_pimples", "continuous_sneezing", "painful_walking",
    "muscle_wasting", "swollen_legs", "bloody_stool",
    "bladder_discomfort", "obesity",
]


def pick_data_path():
    for cand in DATA_CANDIDATES:
        if os.path.exists(cand):
            return cand
    raise FileNotFoundError(
        "DiseaseAndSymptoms.csv not found. Tried: " + ", ".join(DATA_CANDIDATES)
    )


def load_split(form_symptoms):
    """Load + strip the CSV and return the train/test split for a vocabulary."""
    df = pd.read_csv(pick_data_path())
    symptom_cols = [c for c in df.columns if c.startswith("Symptom")]
    for c in symptom_cols:
        df[c] = df[c].apply(lambda x: x.strip() if isinstance(x, str) else x)
    df["Disease"] = df["Disease"].str.strip()

    symptom_lists = df[symptom_cols].apply(
        lambda row: sorted({v for v in row if pd.notna(v) and v in form_symptoms}),
        axis=1,
    )

    mlb = MultiLabelBinarizer(classes=form_symptoms)
    X = mlb.fit_transform(symptom_lists)
    le = LabelEncoder()
    y = le.fit_transform(df["Disease"])

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y
    )
    return X_train, X_test, y_train, y_test, mlb, le


def instantiate_models():
    """Exact notebook constructors - do not tune anything here."""
    return {
        "rf": RandomForestClassifier(n_estimators=100, random_state=42),
        "nb": GaussianNB(),
        "svm": SVC(kernel="rbf", probability=True, random_state=42),
    }


def evaluate_vocabulary(form_symptoms):
    """Train the 3 models on the given vocabulary; returns per-model metrics."""
    X_train, X_test, y_train, y_test, mlb, le = load_split(form_symptoms)
    models = instantiate_models()
    metrics = {}
    for name, model in models.items():
        model.fit(X_train, y_train)
        preds = model.predict(X_test)
        metrics[name] = {"test_acc": float(accuracy_score(y_test, preds))}
    return metrics


def train_and_save(form_symptoms):
    """Fit the final models on the same 80/20 split and write the artifacts."""
    X_train, X_test, y_train, y_test, mlb, le = load_split(form_symptoms)
    models = instantiate_models()
    for model in models.values():
        model.fit(X_train, y_train)

    joblib.dump(mlb, OUT_PATHS["encoder"])
    joblib.dump(le, OUT_PATHS["label_encoder"])
    joblib.dump(models["rf"], OUT_PATHS["rf"])
    joblib.dump(models["nb"], OUT_PATHS["nb"])
    joblib.dump(models["svm"], OUT_PATHS["svm"])


def main():
    expanded_form_symptoms = list(dict.fromkeys(BASE_FORM_SYMPTOMS + NEW_TOKENS))
    print(f"[retrain] vocab: {len(BASE_FORM_SYMPTOMS)} -> {len(expanded_form_symptoms)} tokens")
    print(f"[retrain] data : {os.path.relpath(pick_data_path(), BASE_DIR)}")

    print("\n=== BASELINE (old 40-token vocabulary) ===")
    base = evaluate_vocabulary(BASE_FORM_SYMPTOMS)
    for name, m in base.items():
        print(f"  {name:3s}: test_acc={m['test_acc']:.3f}")

    print("\n=== EXPANDED (with the 4 new form symptoms) ===")
    expanded = evaluate_vocabulary(expanded_form_symptoms)
    regressions = []
    for name in ("rf", "nb", "svm"):
        diff = expanded[name]["test_acc"] - base[name]["test_acc"]
        print(f"  {name:3s}: test_acc={expanded[name]['test_acc']:.3f}  "
              f"(baseline {base[name]['test_acc']:.3f}, delta {diff:+.3f})")
        if diff < -MAX_ACCURACY_DROP:
            regressions.append((name, diff))

    if regressions:
        print("\n[retrain] ACCURACY GATE FAILED - NOT saving the new models.")
        for name, diff in regressions:
            print(f"  {name} regressed {-diff * 100:.1f} points (limit 2.0).")
        print(f"[retrain] Keeping the existing artifacts in {BASE_DIR}")
        raise SystemExit(2)

    train_and_save(expanded_form_symptoms)
    print("\n[retrain] Saving the 5 model artifacts to", BASE_DIR)
    for key, path in OUT_PATHS.items():
        print(f"  {key:14s} -> {os.path.basename(path)}")
    print("[retrain] done.")


if __name__ == "__main__":
    main()