import os
import sqlite3
import requests
from dotenv import load_dotenv
from app.logger import logger

load_dotenv()


class TerminalColors:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKCYAN = '\033[96m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'


fails = 0
warnings = 0


def print_status(component: str, status: str, message: str = ""):
    global fails, warnings
    if status == "OK":
        status_text = f"[{TerminalColors.OKGREEN}  OK  {TerminalColors.ENDC}]"
    elif status == "WARN":
        status_text = f"[{TerminalColors.WARNING} WARN {TerminalColors.ENDC}]"
        warnings += 1
    else:
        status_text = f"[{TerminalColors.FAIL} FAIL {TerminalColors.ENDC}]"
        fails += 1

    print(f"{status_text} {TerminalColors.BOLD}{component.ljust(25)}{TerminalColors.ENDC} {message}")
    log_msg = f"{component}: {status} - {message}"
    if status == "FAIL":
        logger.error(log_msg)
    elif status == "WARN":
        logger.warning(log_msg)
    else:
        logger.info(log_msg)


def run_startup_diagnostics():
    global fails, warnings
    fails = 0
    warnings = 0

    print(f"\n{TerminalColors.OKCYAN}{TerminalColors.BOLD}={'='*60}")
    print(" THE ALL TIME HELPER - PRE-FLIGHT DIAGNOSTICS")
    print(f"={'='*60}{TerminalColors.ENDC}\n")

    groq_key = os.getenv("GROQ_API_KEY")
    if groq_key:
        print_status("Environment (GROQ)", "OK", f"Key loaded (...{groq_key[-4:] if len(groq_key)>4 else '***'})")
    else:
        print_status("Environment (GROQ)", "WARN", "Missing GROQ_API_KEY in .env. Agentic routing may fall back.")

    ngrok_token = os.getenv("NGROK_TOKEN")
    if ngrok_token:
        print_status("Environment (Ngrok)", "OK", "Ngrok auth token found.")
    else:
        print_status("Environment (Ngrok)", "WARN", "Missing NGROK_TOKEN. App will run locally.")

    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    db_file = os.getenv("DB_FILE", "users.db")
    db_path = db_file if os.path.isabs(db_file) else os.path.join(base_dir, db_file)

    if os.path.exists(db_path):
        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='users';")
            if cursor.fetchone():
                print_status("Database", "OK", f"Database found & users table accessible ({db_path}).")
            else:
                print_status("Database", "WARN", "Database exists but 'users' table is missing.")
            conn.close()
        except sqlite3.Error as e:
            print_status("Database", "FAIL", f"Sqlite error: {str(e)}")
    else:
        print_status("Database", "WARN", "Database not found. It will be created automatically on run.")

    ollama_url = os.getenv("OLLAMA_URL", "http://localhost:11434")
    try:
        response = requests.get(f"{ollama_url}/api/tags", timeout=1.0)
        if response.status_code == 200:
            models = [m['name'] for m in response.json().get('models', [])]
            if "agentic-pro:latest" in models or "agentic-pro" in models:
                print_status("Ollama (agentic-pro)", "WARN", "Found 'agentic-pro' in Ollama. Local tag might conflict with Cloud model name.")
            if "helper:latest" in models or "helper" in models:
                print_status("Ollama (helper)", "OK", "Found fine-tuned personal persona model.")
            else:
                print_status("Ollama (helper)", "WARN", "Missing 'helper'. Local persona training incomplete.")
            if "gemma4:e2b" in models or "gemma4:latest" in models:
                print_status("Ollama (Gemma 4)", "OK", "Native VLM orchestrator detected.")
            else:
                print_status("Ollama (Gemma 4)", "WARN", "Gemma 4 not found. Native vision analysis may be unavailable.")
        else:
            print_status("Ollama Connection", "WARN", f"Connected but got HTTP {response.status_code}.")
    except requests.exceptions.RequestException:
        print_status("Ollama Connection", "WARN", "Cannot reach local Ollama daemon.")

    if groq_key:
        print_status("Agentic Swarm (Cloud)", "OK", "Groq infrastructure ready.")
    else:
        print_status("Agentic Swarm (Cloud)", "WARN", "Missing Groq key. agentic-pro will not work.")

    admin_key = os.getenv("ADMIN_KEY")
    sender_email = os.getenv("SENDER_EMAIL")
    if admin_key:
        print_status("Security (Admin Key)", "OK", "Admin Key configured for sensitive tools.")
    else:
        print_status("Security (Admin Key)", "WARN", "Missing ADMIN_KEY. Email tool will be locked.")

    if sender_email and os.getenv("SENDER_PWD"):
        print_status("Email (SMTP)", "OK", f"Sender configured ({sender_email}).")
    else:
        print_status("Email (SMTP)", "WARN", "SMTP credentials missing. Email tool will return errors.")

    frontend_files = [
        os.path.join(base_dir, "templates", "index.html"),
        os.path.join(base_dir, "static", "js", "app.js"),
        os.path.join(base_dir, "static", "js", "utils.js"),
        os.path.join(base_dir, "static", "js", "ui.js"),
    ]
    missing_files = [os.path.relpath(path, base_dir) for path in frontend_files if not os.path.exists(path)]
    if missing_files:
        print_status("Frontend Assets", "FAIL", f"Missing critical files: {', '.join(missing_files)}")
    else:
        print_status("Frontend Assets", "OK", "Current HTML/JS entry points located.")

    print(f"\n{TerminalColors.OKCYAN}{TerminalColors.BOLD}={'='*60}")
    if fails == 0 and warnings == 0:
        print(" SYSTEM HEALTH OPTIMAL - PROCEEDING TO STARTUP...")
    elif fails == 0 and warnings > 0:
        print(f" {TerminalColors.WARNING}SYSTEM HEALTH DEGRADED ({warnings} Warnings) - BOOTING WITH LIMITED CAPABILITIES{TerminalColors.ENDC}")
    else:
        print(f" {TerminalColors.FAIL}SYSTEM FATAL ({fails} Fails, {warnings} Warnings) - BOOTING ANYWAY AS REQUESTED{TerminalColors.ENDC}")
    print(f"={'='*60}{TerminalColors.ENDC}\n")


if __name__ == "__main__":
    run_startup_diagnostics()
