# AI Rural Healthcare Prediction and AI Chatbot

Rural Healthcare AI Assistant is a full-stack healthcare platform built for symptom-based disease prediction, multilingual medical guidance, patient record management, and feedback capture. The project combines a React + TypeScript frontend with a Flask backend, MongoDB persistence, an ensemble ML prediction model, Ollama-powered chatbot responses, and NLLB-based translation for English, Hindi, and Kannada.

## Overview

The application is designed for rural healthcare support workflows:

- Users can submit symptoms and vital signs to receive disease predictions.
- Users can chat with the AI assistant in English, Hindi, or Kannada.
- The backend stores patient records, user accounts, and feedback in MongoDB.
- Feedback is delivered through SendGrid or SMTP, with MongoDB used for persistence and tracking.
- A dedicated admin dashboard allows review of records and patients.

## Key Features

- Disease prediction from symptoms and vitals using an ensemble ML pipeline.
- Multilingual chatbot support with NLLB-200 translation.
- Chat-based AI assistant endpoint backed by Flask.
- MongoDB-backed patient records and feedback storage.
- Email feedback delivery with SendGrid primary support and SMTP fallback.
- Authentication flow for register/login and admin login.
- Feedback modal in the frontend with secure backend submission.

## Current Project Architecture

- Frontend: React, TypeScript, Vite, Tailwind CSS.
- Backend: Flask, Python, scikit-learn, pandas, PyMongo, Ollama integration.
- Model stack: SVM, Gaussian Naive Bayes, and Random Forest with majority voting.
- Translation: NLLB-200 for English, Hindi, and Kannada.
- Data: MongoDB Atlas, local disease knowledge base JSON, session memory.

## Main Frontend Routes

- `/` - Landing page
- `/assess` - Assessment flow
- `/ai-assistant` - Chat assistant
- `/login` - Login page
- `/register` - Registration page
- `/admin` - Admin dashboard
- `/profile` - User profile page
- `/privacy-policy` - Privacy policy
- `/terms-of-service` - Terms of service
- `/disclaimer` - Medical disclaimer

## Main Backend Endpoints

- `POST /predict` - Predict disease from symptoms and vitals.
- `POST /predict-disease` - Predict disease from symptom input.
- `POST /predict-from-report` - Predict from uploaded report text.
- `POST /ai-chat` - Multilingual chatbot endpoint.
- `POST /send-feedback` - Store and email feedback.
- `POST /register` - Create user account.
- `POST /login` - Authenticate user.
- `POST /admin-login` - Authenticate admin.
- `GET /health` - Backend health check.
- `GET /model-info` - Model metadata and class details.
- `GET /patients` - Fetch stored patient records.
- `POST /clear-session` - Reset chatbot session memory.

## Verified Current Model Data

- Disease knowledge base: 51 diseases.
- ML prediction model: 41 disease classes.
- Supported languages: 3.
- Default backend port: `5000`.
- Default frontend port: `5173`.

## Feedback System

The feedback form in the UI sends data to the Flask backend, which uses a 3-tier delivery and storage flow:

1. SendGrid API when configured.
2. SMTP/Gmail via `smtplib` when SendGrid is unavailable.
3. MongoDB storage for persistence and delivery tracking.

## Technology Stack

- Frontend: React, TypeScript, Vite, Tailwind CSS, React Router.
- Backend: Flask, Python, Flask-CORS, PyMongo, pandas, scikit-learn, transformers, torch.
- AI services: Ollama Mistral, NLLB-200 translation.
- Database: MongoDB Atlas.
- Email: SendGrid and SMTP.

## Repository Structure

```text
AI-Rural-Healthcare-Prediction--main/
├── client/                  # React frontend
├── backend/                 # Flask API, ML pipeline, chatbot, DB logic
├── Disease dataset/         # CSV dataset used for disease modeling
├── public/                  # Static assets
├── shared/                  # Shared API helpers
├── research_paper.txt       # Project paper draft/content
└── README.md                # This file
```

## Setup

### 1. Install Frontend Dependencies

```bash
cd client
pnpm install
```

### 2. Install Backend Dependencies

```bash
cd backend
pip install -r requirements.txt
```

### 3. Start the Backend

```bash
cd backend
python app.py
```

The API runs on `http://127.0.0.1:5000`.

### 4. Start the Frontend

```bash
cd client
pnpm run dev
```

The frontend runs on `http://localhost:5173` by default.

## Required Environment / Services

- MongoDB connection string in `MONGO_URI` or `MONGO_FALLBACK_URI`.
- Ollama running locally with the `mistral` model available.
- Optional `SENDGRID_API_KEY` for primary email delivery.
- Optional SMTP settings: `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_SERVER`, `SMTP_PORT`.
- Optional `ADMIN_EMAIL` and `ADMIN_PASSWORD` for dashboard login.

## Notes

- The chatbot pipeline is translation-aware and keeps responses short and medically cautious.
- User registration and login require valid Gmail addresses in the current UI flow.
- Feedback name and email fields are required in the UI.
- Patient data, feedback, and users are stored through MongoDB when available, with JSON fallback behavior in some paths.

## Troubleshooting

- If port `5173` is busy, Vite will start on the next free port.
- If the backend cannot connect to MongoDB, check your connection string and network access.
- If Ollama is not running, start it before using the chatbot.
- If email delivery fails, verify SendGrid or SMTP credentials.

## License

No license file is currently included in the repository.
