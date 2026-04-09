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
```

### Installation

```bash
# Example installation steps (if applicable)
pip install -r requirements.txt
python main.py
```

---
*Note: This repository does not contain sensitive credentials or user databases.*
