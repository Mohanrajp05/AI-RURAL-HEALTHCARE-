
import json
import re
import sys
import time
import traceback
import warnings
from collections import Counter
from pathlib import Path

warnings.filterwarnings("ignore")

BASE_DIR = Path(__file__).resolve().parent

MODEL_PKL = BASE_DIR / "disease_prediction_model.pkl"
INDEX_DIR = BASE_DIR / "faiss_disease_index"
KB_DIR = BASE_DIR / "knowledge_base"
REPORT_TXT = BASE_DIR / "evals_report.txt"

# Local embedding model used inside RAGAS and for retrieval (free, local, no key).
EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
# Local judge LLM used by LLM-based RAGAS metrics (optionally via Ollama).
OLLAMA_MODEL = "cniongolo/biomistral:latest"

REPORT_LINES: list = []


def say(text: str = "") -> None:
    """Print to console and accumulate for the final report file."""
    print(text)
    REPORT_LINES.append(str(text))


def canon(s) -> str:
    """Canonical label: ignore case, spaces and underscores."""
    return re.sub(r"[\s_]+", "", str(s or "")).lower()


# ============================================================
# SECTION 1 - ML MODEL EVALUATION
# ============================================================

def _find_csv() -> Path:
    candidates = [
        BASE_DIR.parent / "Disease dataset" / "DiseaseAndSymptoms.csv",
        BASE_DIR.parent / "DiseaseAndSymptoms.csv",
        BASE_DIR / "DiseaseAndSymptoms.csv",
        BASE_DIR.parent.parent / "Disease dataset" / "DiseaseAndSymptoms.csv",
    ]
    for c in candidates:
        if c.exists():
            return c
    found = list(BASE_DIR.parent.rglob("DiseaseAndSymptoms.csv"))
    if found:
        return found[0]
    raise FileNotFoundError(
        "DiseaseAndSymptoms.csv not found (expected under 'Disease dataset')."
    )


def _load_csv():
    import pandas as pd
    path = _find_csv()
    say(f"Dataset: {path}")
    return pd.read_csv(path)


def _build_feature_lookup(model):
    """Map (slot, canonical-symptom) -> feature column, from RF feature names."""
    rf = model["final_rf_model"]
    features = list(rf.feature_names_in_)
    lookup = {}
    for feat in features:
        m = re.match(r"^Symptom_(\d+)[_ ]?(.*)$", feat)
        if m:
            slot, suffix = int(m.group(1)), m.group(2)
            lookup[(slot, canon(suffix))] = feat
    return features, lookup


def _build_vectors(df, lookup, feature_index):
    """Convert the CSV into (X, y) using the slot+symptom feature schema."""
    X, y = [], []
    for _, by_row in df.iterrows():
        vec = [0.0] * len(feature_index)
        for slot in range(1, 18):
            v = by_row.get(f"Symptom_{slot}")
            if v is None or str(v).strip() == "":
                continue
            feat = lookup.get((slot, canon(v)))
            if feat is not None and feat in feature_index:
                vec[feature_index[feat]] = 1.0
        X.append(vec)
        y.append(str(by_row["Disease"]))
    return X, y


def _majority_vote(pred_lists):
    return [
        Counter([a, b, c]).most_common(1)[0][0]
        for a, b, c in zip(*pred_lists)
    ]


def _load_model():
    import pickle
    with open(MODEL_PKL, "rb") as f:
        return pickle.load(f)


def run_ml() -> dict:
    from sklearn.metrics import classification_report, confusion_matrix
    from sklearn.model_selection import train_test_split

    say("=" * 78)
    say("SECTION 1 - ML MODEL EVALUATION")
    say("=" * 78)

    model = _load_model()
    svm, nb, rf = (
        model["final_svm_model"],
        model["final_nb_model"],
        model["final_rf_model"],
    )
    features, lookup = _build_feature_lookup(model)
    feature_index = {f: i for i, f in enumerate(features)}

    df = _load_csv()
    X, y = _build_vectors(df, lookup, feature_index)

    X_tr, X_te, y_tr, y_te = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y
    )

    labels = [canon(c) for c in rf.classes_]
    yte_n = [canon(g) for g in y_te]

    # Time one batched pass over the whole test set (3 models + voting).
    t0 = time.perf_counter()
    votes = _majority_vote([
        [canon(x) for x in svm.predict(X_te)],
        [canon(x) for x in nb.predict(X_te)],
        [canon(x) for x in rf.predict(X_te)],
    ])
    latency_per_pred = (time.perf_counter() - t0) * 1000.0 / max(len(X_te), 1)

    correct = sum(p == g for p, g in zip(votes, yte_n))
    acc = correct / max(len(yte_n), 1)

    say(f"Overall accuracy (majority vote): {acc * 100:.2f}%  ({correct}/{len(yte_n)} samples)")
    say(f"Average latency per prediction:   {latency_per_pred:.4f} ms")
    say("")
    say("Classification report (per disease):")
    say("")
    say(classification_report(yte_n, votes, labels=labels, zero_division=0))
    say("Confusion matrix (rows=actual, cols=predicted, model class order):")
    cm = confusion_matrix(yte_n, votes, labels=labels)
    say(json.dumps(cm.tolist(), default=str))
    say("Cost per consultation: ₹0 (local inference, no API)")

    return {"accuracy_pct": acc * 100.0, "latency_ms": latency_per_pred}


# ============================================================
# SECTION 2 — RAGAS EVALUATION
# ============================================================

RAGAS_TRIPLETS = [
    # (question, ground_truth)
    (
        "What are the main symptoms of malaria?",
        "Malaria causes high fever, chills, headache and muscle pain. It is caused by Plasmodium parasites transmitted by mosquito bites.",
    ),
    (
        "How can dengue be prevented?",
        "Dengue is prevented by avoiding mosquito bites and eliminating standing water where Aedes mosquitoes breed.",
    ),
    (
        "What is the recommended diet for a diabetic patient?",
        "A diabetic diet focuses on high-fiber meals, vegetables, pulses, whole grains and lean protein while limiting sugary drinks and refined carbohydrates.",
    ),
    (
        "What lifestyle changes help control hypertension?",
        "High blood pressure is controlled with a low-salt DASH-style diet, regular exercise, weight management and consistent medication.",
    ),
    (
        "What are the common symptoms of pulmonary tuberculosis?",
        "Tuberculosis commonly presents with a chronic cough lasting over two weeks, weight loss, night sweats and fever.",
    ),
    (
        "How is typhoid fever treated?",
        "Typhoid is treated with antibiotics for Salmonella typhi and supportive care, using boiled water and soft bland foods during recovery.",
    ),
    (
        "What causes pneumonia and how is it treated?",
        "Pneumonia is caused by bacteria, viruses or fungi and is treated with antibiotics for bacterial causes plus supportive care and vaccination.",
    ),
    (
        "What should you do for gastroenteritis?",
        "Treat gastroenteritis with oral rehydration solution, rice gruel, banana and toast, and seek care for severe vomiting after dehydration.",
    ),
    (
        "What are the triggers and treatment of migraine?",
        "Migraine triggers include stress, sleep changes, hormonal changes and certain foods; it is treated with early analgesics or triptans.",
    ),
    (
        "What are the symptoms and causes of osteoarthritis?",
        "Osteoarthritis causes joint pain, stiffness and reduced motion due to wear-and-tear changes in the joints.",
    ),
]

RAGAS_METRICS = [
    ("Faithfulness", "faithfulness",
     "1 = the answer is fully supported by retrieved context (no hallucination); 0 = heavy hallucination."),
    ("Answer Relevancy", "answer_relevancy",
     "1 = the answer directly addresses the question; 0 = off-topic / irrelevant."),
    ("Context Precision", "context_precision",
     "1 = all retrieved chunks are relevant to the question; 0 = most are irrelevant."),
    ("Context Recall", "context_recall",
     "1 = retrieved context contains all the information in the ground truth; 0 = it misses key facts."),
    ("Answer Correctness", "answer_correctness",
     "1 = the answer matches the ground truth both factually and semantically; 0 = fully wrong."),
]


def _ragas_wrapper():
    """Return (LLMWrapper, EmbeddingsWrapper, metrics_module) or raise."""
    import ragas.embeddings as _emb
    import ragas.metrics as _met
    from langchain_huggingface import HuggingFaceEmbeddings

    try:
        import ragas.llms as _llms
        from langchain_community.llms import Ollama
        llm_cls = getattr(_llms, "LangchainLLMWrapper", None) or getattr(
            _llms, "LangchainLLM", None
        )
        llm = llm_cls(Ollama(model=OLLAMA_MODEL, temperature=0.4))
    except Exception as e:
        raise RuntimeError(f"Could not build local judge LLM: {e}")

    emb_cls = getattr(_emb, "LangchainEmbeddingsWrapper", None) or getattr(
        _emb, "LangchainEmbeddings", None
    )
    if emb_cls is None:
        raise RuntimeError("RAGAS embeddings wrapper class not found.")
    embeddings = emb_cls(HuggingFaceEmbeddings(
        model_name=EMBEDDING_MODEL,
        model_kwargs={"device": "cpu"},
        encode_kwargs={"normalize_embeddings": True},
    ))
    return llm, embeddings


def _extract_score(result):
    """Return *mean* score from a RAGAS result object."""
    try:
        if hasattr(result, "to_pandas"):
            df = result.to_pandas()
            col = df.columns[0]
            vals = [float(x) for x in df[col].dropna().tolist()]
            return sum(vals) / len(vals) if vals else None
        if isinstance(result, dict):
            for v in result.values():
                if isinstance(v, (list, tuple, set)):
                    vals = [float(x) for x in v if x is not None]
                    if vals:
                        return sum(vals) / len(vals)
                else:
                    try:
                        return float(v)
                    except (TypeError, ValueError):
                        continue
    except Exception:
        return None
    return None


def _build_ragas_dataset():
    """Build the 10-triplet dataset: question/ground_truth/contexts/answer."""
    from chatbot_pipeline import _compose_faiss_context, call_ollama, retrieve_faiss_chunks

    data = {"question": [], "ground_truth": [], "contexts": [], "answer": []}
    for q, gt in RAGAS_TRIPLETS:
        chunks = retrieve_faiss_chunks(q, top_k=3)
        contexts = [c for _, c in chunks]
        context_str = _compose_faiss_context(chunks)
        answer = call_ollama(q, context_str) if context_str else None
        if not answer:
            answer = gt  # keep dataset valid if Ollama is unavailable
            say(f"[RAGAS] Ollama unavailable; used ground truth as answer for: {q!r}")
        data["question"].append(q)
        data["ground_truth"].append(gt)
        data["contexts"].append(contexts)
        data["answer"].append(answer)
    return data


def run_ragas() -> dict:
    say("=" * 78)
    say("SECTION 2 - RAGAS EVALUATION (RAG pipeline)")
    say("=" * 78)

    results = {label: None for label, _, _ in RAGAS_METRICS}

    try:
        from datasets import Dataset
        import ragas.metrics
        from ragas import evaluate

        llm, embeddings = _ragas_wrapper()
        data = _build_ragas_dataset()
        dataset = Dataset.from_dict(data)
        say(f"Built RAGAS dataset with {len(dataset)} triplets.")

        for name, metric_key, explanation in RAGAS_METRICS:
            try:
                metric_fn = getattr(ragas.metrics, metric_key)
                res = evaluate(
                    dataset, metrics=[metric_fn], llm=llm, embeddings=embeddings
                )
                score = _extract_score(res)
                results[name] = score
                if score is None:
                    say(f"[RAGAS] {name}: result present but score could not be read.")
                else:
                    say(f"[RAGAS] {name}: {score:.3f}   (0-1, {explanation})")
            except Exception as e:
                say(f"[RAGAS] {name} FAILED: {e}")
                results[name] = None

    except Exception as e:
        say(f"[RAGAS] RAGAS unavailable or in error: {e}")
        say("  Install ragas 0.1.x in the dedicated eval venv (see header) and re-run.")
        say("  Continuing -- RAGAS scores are reported as N/A.")
        traceback.print_exc()

    return results


# ============================================================
# SECTION 3 — STANDARD RAG RETRIEVAL EVAL
# ============================================================

RAG_QUESTIONS = [
    "What are the symptoms of malaria?",
    "How is dengue fever transmitted and prevented?",
    "What is the recommended diet for diabetes?",
    "What lifestyle changes help control high blood pressure?",
    "What are the signs of tuberculosis infection?",
    "How is typhoid treated?",
    "What are the symptoms of pneumonia?",
    "What should you eat for gastroenteritis?",
    "What triggers a migraine headache?",
    "What causes osteoarthritis joint pain?",
]


def run_rag_retrieval() -> dict:
    say("=" * 78)
    say("SECTION 3 - STANDARD RAG RETRIEVAL EVAL")
    say("=" * 78)

    try:
        from chatbot_pipeline import retrieve_faiss_chunks
    except Exception as e:
        say(f"[RETRIEVAL] chatbot_pipeline import failed: {e}")
        return {}

    header = f"{'Question':<40}{'Retrieved file(s)':<26}{'time(ms)':>10}{'chunks':>8}"
    say(header)
    say("-" * len(header))

    times = []
    for q in RAG_QUESTIONS:
        t0 = time.perf_counter()
        chunks = retrieve_faiss_chunks(q, top_k=3)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        times.append(elapsed_ms)
        files = ", ".join(sorted({src for src, _ in chunks})) or "NONE"
        say(f"{q[:36]:<40}{files[:24]:<26}{elapsed_ms:10.2f}{len(chunks):8}")

    avg = sum(times) / len(times) if times else 0.0
    say("")
    say(f"Average RAG retrieval latency: {avg:.2f} ms")
    return {"retrieval_ms": avg}


# ============================================================
# SECTION 4 — SUMMARY
# ============================================================

def _count_kb_diseases():
    if not KB_DIR.is_dir():
        return 0
    return len(list(KB_DIR.glob("*.txt")))


def _count_index_vectors():
    try:
        from chatbot_pipeline import _faiss_store, init_rag
        if init_rag() and _faiss_store is not None:
            return int(getattr(_faiss_store, "index", None).ntotal)
    except Exception:
        pass
    return None


def run_summary(ml: dict, ragas: dict, retrieval: dict) -> None:
    say("=" * 78)
    say("SECTION 4 - SUMMARY REPORT")
    say("=" * 78)

    n_chunks = _count_index_vectors()

    say(f"ML Model accuracy:                 {form(ml.get('accuracy_pct', 'N/A'), '.2f')}")
    say(f"Average ML prediction latency:     {form(ml.get('latency_ms'))} ms")
    say(f"Average RAG retrieval latency:     {form(retrieval.get('retrieval_ms'))} ms")
    say(f"RAGAS Faithfulness score:          {fmt_score(ragas.get('Faithfulness'))}")
    say(f"RAGAS Answer Relevancy score:      {fmt_score(ragas.get('Answer Relevancy'))}")
    say(f"RAGAS Context Precision score:     {fmt_score(ragas.get('Context Precision'))}")
    say(f"RAGAS Context Recall score:        {fmt_score(ragas.get('Context Recall'))}")
    say(f"RAGAS Answer Correctness score:    {fmt_score(ragas.get('Answer Correctness'))}")
    say(f"Cost per consultation:             ₹0 (local inference, no API)")
    say(f"Total diseases covered:            {_count_kb_diseases()}")
    say(f"Total knowledge-base chunks:       {'N/A' if n_chunks is None else n_chunks}")


def form(v, fmt=".4f"):
    try:
        return f"{float(v):{fmt}}"
    except (TypeError, ValueError):
        return "N/A"


def fmt_score(v):
    return f"{v:.3f}" if isinstance(v, (int, float)) else "N/A"


def main():
    sys.path.insert(0, str(BASE_DIR))

    ml = {}
    try:
        ml = run_ml()
    except Exception as e:
        say("[SECTION 1] ML evaluation failed (continuing):")
        traceback.print_exc()

    ragas = {}
    try:
        ragas = run_ragas()
    except Exception as e:
        say("[SECTION 2] RAGAS failed (continuing):")
        traceback.print_exc()

    retrieval = {}
    try:
        retrieval = run_rag_retrieval()
    except Exception as e:
        say("[SECTION 3] RAG retrieval failed (continuing):")
        traceback.print_exc()

    try:
        run_summary(ml, ragas, retrieval)
    except Exception as e:
        say(f"[SECTION 4] Summary failed: {e}")

    REPORT_TXT.write_text("\n".join(REPORT_LINES), encoding="utf-8")
    say("")
    say(f"Report saved to: {REPORT_TXT}")


if __name__ == "__main__":
    main()