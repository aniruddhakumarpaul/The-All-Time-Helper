# The All Time Helper

A FastAPI-based assistant application with local/cloud model routing, chat history sync, image handling, and gated email tooling.

## Prerequisites

Create a `.env` file in the repository root before starting the app.

```bash
# Email Configuration (required only for OTP and email sending)
SMTP_SERVER=smtp.gmail.com
SMTP_PORT=587
SENDER_EMAIL=your-email@gmail.com
SENDER_PWD=your-app-password

# Ngrok Configuration (optional local tunnel)
NGROK_TOKEN=your-ngrok-auth-token

# Security
SECRET_KEY=your-random-secret-key
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=1440
ADMIN_KEY=your-sensitive-action-admin-key

# Database
DB_FILE=users.db

# AI API Keys
GROQ_API_KEY=your-groq-api-key
GROQ_API_KEY_BACKUP=optional-backup-groq-key
OPENROUTER_API_KEY=optional-openrouter-key
GEMINI_API_KEY=optional-gemini-key

# Local inference
OLLAMA_URL=http://localhost:11434
ALLOWED_ORIGINS=http://localhost:9000
```

## Installation

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python -m app.main
```

Alternative:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 9000 --reload
```

## Security notes

Sensitive actions such as live email sending require a valid `ADMIN_KEY` for the current request. The app should not persist blanket admin authorization in the user table.

The repository should not contain `.env`, database files, model files, generated uploads, logs, or local scratch state. The `.gitignore` is configured for those categories.
