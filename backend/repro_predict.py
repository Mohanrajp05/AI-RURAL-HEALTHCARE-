import time

t0 = time.time()
print("import app pieces...", flush=True)
from predict_disease_guarded import load_models, predict_guarded

print("load_models...", flush=True)
ok, err = load_models()
print(f"load_models: ok={ok} err={err} [{time.time()-t0:.1f}s]", flush=True)

from app import canonicalize_checkboxes, generate_advice, analyze_vitals, store_patient_record

boxes = ["Cough", "Fatigue"]
canon, dropped = canonicalize_checkboxes(boxes)
print(f"canonical: {canon} [{time.time()-t0:.1f}s]", flush=True)

guard = predict_guarded(canon)
print(f"guard top1: {guard['predictions'][0]['disease']} [{time.time()-t0:.1f}s]", flush=True)

data = {
    "patientName": "Dev Test",
    "age": "30",
    "gender": "Male",
    "bloodPressureSystolic": "120",
    "bloodPressureDiastolic": "80",
    "heartRate": "72",
    "temperature": "98.6",
    "sugarLevel": "95",
    "symptoms": boxes,
    "extra_symptoms": [],
}
adv = generate_advice(guard["predictions"][0]["disease"], canon)
print(f"advice ok [{time.time()-t0:.1f}s]", flush=True)
vitals = analyze_vitals(data)
print(f"vitals ok [{time.time()-t0:.1f}s]", flush=True)
saved = store_patient_record(data, {"disease": guard["predictions"][0]["disease"]}, None)
print(f"store ok: {saved} [{time.time()-t0:.1f}s]", flush=True)
print(f"TOTAL {time.time()-t0:.1f}s", flush=True)