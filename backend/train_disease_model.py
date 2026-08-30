

import os
import re
import pickle
from collections import Counter

import numpy as np
import pandas as pd
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import LabelEncoder
from sklearn.svm import SVC
from sklearn.naive_bayes import GaussianNB
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, classification_report

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CSV_PATH = os.path.join(BASE_DIR, "..", "Disease dataset", "DiseaseAndSymptoms.csv")
OUT_PATH = os.path.join(BASE_DIR, "disease_prediction_model.pkl")

N_SLOTS = 17


def clean(symptom: str) -> str:
    """Normalize a symptom cell to a stable lowercased token with underscores."""
    if symptom is None:
        return ""
    return re.sub(r"[^a-z0-9]+", "_", str(symptom).strip().lower()).strip("_")


def load_dataframe() -> pd.DataFrame:
    if not os.path.exists(CSV_PATH):
        raise FileNotFoundError(f"CSV not found: {CSV_PATH}")
    return pd.read_csv(CSV_PATH)


def build_feature_matrix(df: pd.DataFrame):
    """
    One-hot binary features per (slot, symptom) exactly like the shipped model,
    e.g. 'Symptom_3_high_fever'. Returns X (DataFrame), y (labels), symptom_set.
    """
    rows = []
    for _, row in df.iterrows():
        present = []
        for slot in range(1, N_SLOTS + 1):
            val = clean(row.get(f"Symptom_{slot}", ""))
            if val:
                present.append((slot, val))
        rows.append(present)

    all_features = sorted({f"Symptom_{slot}_{sym}" for slots in rows for slot, sym in slots})
    symptom_set = sorted({sym for _, (slot, sym) in enumerate([(s, y) for s, y in _flatten(rows)])})
    return rows, all_features, symptom_set


def _flatten(rows):
    for group in rows:
        for slot, sym in group:
            yield slot, sym


def build_X(rows, feature_cols) -> pd.DataFrame:
    X = pd.DataFrame(0, index=np.arange(len(rows)), columns=feature_cols)
    for i, group in enumerate(rows):
        for slot, sym in group:
            col = f"Symptom_{slot}_{sym}"
            if col in feature_cols:
                X.at[i, col] = 1
    return X


def main():
    df = load_dataframe()
    print(f"Loaded {len(df)} rows from {CSV_PATH}")

    rows, feature_cols, symptom_set = build_feature_matrix(df)
    X = build_X(rows, feature_cols)
    y = df["Disease"].astype(str).values

    le = LabelEncoder()
    y_enc = le.fit_transform(y)
    labels = list(le.classes_)

    X_train, X_test, y_train, y_test = train_test_split(
        X, y_enc, test_size=0.2, random_state=42, stratify=y_enc
    )

    svm = SVC(kernel="linear", probability=True, C=0.5, random_state=42)
    nb = GaussianNB()
    rf = RandomForestClassifier(n_estimators=200, max_depth=12, random_state=42, class_weight="balanced")

    for name, clf in [("SVM", svm), ("NB", nb), ("RF", rf)]:
        clf.fit(X_train, y_train)
        pred = clf.predict(X_test)
        print(f"\n===== {name} ===== accuracy={accuracy_score(y_test, pred):.4f}")

    # Ensemble majority vote over decoded labels
    preds_svm = le.inverse_transform(svm.predict(X_test))
    preds_nb = le.inverse_transform(nb.predict(X_test))
    preds_rf = le.inverse_transform(rf.predict(X_test))
    votes = [Counter([a, b, c]).most_common(1)[0][0] for a, b, c in zip(preds_svm, preds_nb, preds_rf)]
    y_true = le.inverse_transform(y_test)
    acc = accuracy_score(y_true, votes)
    print(f"\n===== ENSEMBLE (majority vote) ===== accuracy={acc:.4f}")
    print(classification_report(y_true, votes, zero_division=0))

    model = {
        "final_svm_model": svm,
        "final_nb_model": nb,
        "final_rf_model": rf,
        "symptom_index": {f"Symptom_{i}": i - 1 for i in range(1, N_SLOTS + 1)},
    }
    with open(OUT_PATH, "wb") as f:
        pickle.dump(model, f)
    print(f"\nSaved ensemble to {OUT_PATH}")


if __name__ == "__main__":
    main()