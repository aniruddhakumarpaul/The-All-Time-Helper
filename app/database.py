import sqlite3
import os
from app.logger import logger
from app.schema_migrations import run_migrations

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
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        run_migrations(conn)
    finally:
        conn.close()
    logger.info("[Database] Explicit schema migrations complete.")
