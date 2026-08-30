# Rural Healthcare ML Backend

This Flask backend provides an ML-powered health assessment API that loads your trained scikit-learn model and returns predictions based on patient data.

## Setup Instructions

### 1. Add Your Model

Copy your trained `model.pkl` file into this backend folder:

```
backend/
  ├── app.py
  ├── model.pkl  ← Place your model here
  ├── requirements.txt
  └── README.md
```

### 2. Create Virtual Environment

Navigate to backend folder and create a virtual environment:

```bash
# Windows
python -m venv venv
venv\Scripts\activate

# Mac/Linux
python -m venv venv
source venv/bin/activate
```

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

### 4. Run the Flask Server

```bash
python app.py
```

The server will start on `http://localhost:5000`

## API Endpoints

### `POST /predict`

Send patient data to get health assessment predictions.

**Request:**
```json
{
  "age": 45,
  "bloodPressureSystolic": 130,
  "bloodPressureDiastolic": 85,
  "heartRate": 72,
  "temperature": 98.6,
  "sugarLevel": 120,
  "labTestResult": 5.5,
  "symptoms": ["Fever", "Cough"]
}
```

**Response:**
```json
{
  "success": true,
  "disease": "Predicted Disease Name",
  "riskPercentage": 65,
  "recommendation": "Clinical recommendation based on prediction"
}
```

### `GET /health`

Check if the backend service is running and model is loaded.

**Response:**
```json
{
  "status": "ok",
  "model_loaded": true
}
```

### `GET /`

Get service information.

**Response:**
```json
{
  "message": "Rural Healthcare ML Backend",
  "status": "Model loaded and ready",
  "endpoints": {
    "POST /predict": "Get health assessment prediction",
    "GET /health": "Check service health"
  }
}
```

## Model Requirements

Your `model.pkl` must:
- Be a trained scikit-learn classifier (e.g., RandomForestClassifier, SVC)
- Accept 8 features: age, systolic BP, diastolic BP, heart rate, temperature, sugar level, lab result, symptom count
- Support `predict()` and ideally `predict_proba()` methods
- Be compatible with Python 3.9+

## Troubleshooting

- **Model not loading?** Ensure `model.pkl` is in the backend folder with exact file name
- **Import errors?** Verify all packages in requirements.txt are installed: `pip install -r requirements.txt`
- **CORS errors from React?** flask-cors is already configured in app.py
- **Port 5000 in use?** Edit app.py and change `port=5000` to another port (e.g., 5001)

## Optional: Use Fallback Assessment

If `model.pkl` is missing, the API will use a built-in rule-based assessment logic that still returns consistent predictions without a trained model.
