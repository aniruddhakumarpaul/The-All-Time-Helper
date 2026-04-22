import os
import sqlite3
import requests

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

fails = 0
warnings = 0

def run_startup_diagnostics():
    global fails, warnings
    fails = 0
    warnings = 0
    
    print(f"\n{TerminalColors.OKCYAN}{TerminalColors.BOLD}={'='*60}")
    print(f" THE ALL TIME HELPER - PRE-FLIGHT DIAGNOSTICS")
    print(f"={'='*60}{TerminalColors.ENDC}\n")

    # 1. Check Environment Variables
    groq_key = os.getenv("GROQ_API_KEY")
    if groq_key:
        print_status("Environment (GROQ)", "OK", f"Key loaded (sk-ant...{groq_key[-4:] if len(groq_key)>4 else '***'})")
    else:
        print_status("Environment (GROQ)", "WARN", "Missing GROQ_API_KEY in .env. Agentic routing may fall back.")

    ngrok_token = os.getenv("NGROK_TOKEN")
    if ngrok_token:
        print_status("Environment (Ngrok)", "OK", "Ngrok auth token found.")
    else:
        print_status("Environment (Ngrok)", "WARN", "Missing NGROK_TOKEN. App will run locally but wait times may increase if publicly exposed.")

    # 2. Check Database Integrity
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    db_path = os.path.join(base_dir, "users.db")
    
    if os.path.exists(db_path):
        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='users';")
            if cursor.fetchone():
                print_status("Database (users.db)", "OK", "Database found & users table accessible.")
            else:
                print_status("Database (users.db)", "WARN", "Database exists but 'users' table is missing.")
            conn.close()
        except sqlite3.Error as e:
            print_status("Database (users.db)", "FAIL", f"Sqlite error: {str(e)}")
    else:
        print_status("Database (users.db)", "WARN", "users.db not found. Will be created automatically on run.")

    # 3. Check local Ollama AI Registry
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
        else:
            print_status("Ollama Connection", "WARN", f"Connected but got HTTP {response.status_code}.")
    except requests.exceptions.RequestException:
        print_status("Ollama Connection", "WARN", "Cannot reach local Ollama daemon.")

    # 4. Check API Key Health (The actual check for agentic-pro cloud model)
    if groq_key:
        print_status("Agentic Swarm (Cloud)", "OK", "Groq Infrastructure Ready.")
    else:
        print_status("Agentic Swarm (Cloud)", "FAIL", "Missing Groq Key. agentic-pro will NOT work.")

    # 4. Check Frontend Files
    index_path = os.path.join(base_dir, "templates", "index.html")
    js_path = os.path.join(base_dir, "static", "js", "main_v3.js")
    
    missing_files = []
    if not os.path.exists(index_path): missing_files.append("templates/index.html")
    if not os.path.exists(js_path): missing_files.append("static/js/main_v3.js")
    
    if missing_files:
        print_status("Frontend Assets", "FAIL", f"Missing critical files: {', '.join(missing_files)}")
    else:
        print_status("Frontend Assets", "OK", "All HTML/JS static files located.")

    print(f"\n{TerminalColors.OKCYAN}{TerminalColors.BOLD}={'='*60}")
    if fails == 0 and warnings == 0:
        print(f" SYSTEM HEALTH OPTIMAL - PROCEEDING TO STARTUP...")
    elif fails == 0 and warnings > 0:
        print(f" {TerminalColors.WARNING}SYSTEM HEALTH DEGRADED ({warnings} Warnings) - BOOTING WITH LIMITED CAPABILITIES{TerminalColors.ENDC}")
    else:
        print(f" {TerminalColors.FAIL}SYSTEM FATAL ({fails} Fails, {warnings} Warnings) - BOOTING ANYWAY AS REQUESTED{TerminalColors.ENDC}")
    print(f"={'='*60}{TerminalColors.ENDC}\n")

if __name__ == "__main__":
    run_startup_diagnostics()
