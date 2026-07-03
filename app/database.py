import sqlite3
import os
from app.logger import logger

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_db_env = os.getenv("DB_FILE", "users.db")
DB_FILE = _db_env if os.path.isabs(_db_env) else os.path.join(BASE_DIR, _db_env)


def _connect():
    conn = sqlite3.connect(DB_FILE, timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def get_db():
    conn = _connect()
    try:
        yield conn
    finally:
        conn.close()


def init_db():
    os.makedirs(os.path.dirname(DB_FILE), exist_ok=True)
    conn = _connect()
    c = conn.cursor()

    c.execute("PRAGMA journal_mode = WAL")

    c.execute('''CREATE TABLE IF NOT EXISTS users (
                    email TEXT PRIMARY KEY,
                    hashed_password TEXT,
                    verified INTEGER DEFAULT 0,
                    otp TEXT,
                    otp_expiry REAL,
                    name TEXT
                 )''')

    try:
        c.execute("ALTER TABLE users ADD COLUMN admin_authorized INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass

    c.execute('''CREATE TABLE IF NOT EXISTS chats (
                    id TEXT PRIMARY KEY,
                    user_email TEXT,
                    title TEXT,
                    messages_json TEXT,
                    updated_at REAL,
                    FOREIGN KEY(user_email) REFERENCES users(email)
                 )''')

    c.execute('''CREATE TABLE IF NOT EXISTS email_send_log (
                    job_id TEXT PRIMARY KEY,
                    user_email TEXT,
                    recipients TEXT,
                    status TEXT,
                    timestamp REAL
                 )''')

    conn.commit()
    conn.close()
    logger.info("[Database] Initialization and schema check complete.")
