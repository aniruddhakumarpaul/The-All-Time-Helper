# The All Time Helper

A versatile assistant application.

## Prerequisites

Before running the application, you need to set up your environment variables.

### Environment Setup

1. Create a file named `.env` in the root directory.
2. Copy and paste the following variables into the `.env` file and replace the placeholders with your actual credentials:

```bash
# Email Configuration (for sending emails)
SMTP_SERVER=smtp.gmail.com
SMTP_PORT=587
SENDER_EMAIL=your-email@gmail.com
SENDER_PWD=your-app-password

# Ngrok Configuration (for local tunneling)
NGROK_TOKEN=your-ngrok-auth-token

# Security
SECRET_KEY=your-random-secret-key
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=1440

# Database
DB_FILE=users.db

# AI API Key (Groq)
GROQ_API_KEY=your-groq-api-key
GROQ_API_KEY_BACKUP=optional-backup-groq-api-key

# Optional Gemini cloud models
GEMINI_API_KEY=your-gemini-api-key

# Optional OpenRouter cloud models
OPENROUTER_API_KEY=your-openrouter-api-key

# Local Ollama
OLLAMA_URL=http://localhost:11434

# Email safety
EMAIL_MODE=SIMULATE
ADMIN_KEY=your-local-admin-key-for-email-send-approval
```

### Installation

```bash
pip install -r requirements.txt
python app/main.py
```

The app expects Python 3.12 and starts on `http://localhost:9000` by default. The entry point automatically restarts itself with Python bytecode writes disabled because the local ChromaDB store is sensitive to locked `__pycache__` files on Windows.

## Project Docs

Use the markdown docs under `docs/` as the first source of truth for repo context:

- `docs/architecture.md`
- `docs/routing.md`
- `docs/image_pipeline.md`
- `docs/memory.md`
- `docs/decisions.md`

### Supported Model IDs

- `agentic-pro`: Groq-backed supervisor swarm.
- `gemma4-cloud`: Groq-backed lightweight cloud fallback.
- `gemma4-openrouter`: OpenRouter-backed Gemma 4 cloud endpoint, separate from local `gemma4:e2b`. It tries Gemma 4 26B first, then fallback Gemma endpoints when OpenRouter's free upstream pool is rate limited.
- `gemini-1.5-flash-latest`: Gemini Flash via `GEMINI_API_KEY` or `GOOGLE_API_KEY`.
- `gemini-1.5-pro-latest`: Gemini Pro via `GEMINI_API_KEY` or `GOOGLE_API_KEY`.
- Local Ollama models such as `gemma4:e2b`, `gemma2:2b`, `helper`, `phi3`, and `moondream`.

---
*Note: This repository does not contain sensitive credentials or user databases.*
