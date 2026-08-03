import sqlite3
import os
from app.logger import logger
from app.schema_migrations import run_migrations

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_db_env = os.getenv("DB_FILE", "users.db")
DB_FILE = _db_env if os.path.isabs(_db_env) else os.path.join(BASE_DIR, _db_env)

SQLITE_FAILURE_CATEGORIES = {
    "database_corrupt",
    "database_not_database",
    "database_locked",
    "database_read_only",
    "database_disk_full",
    "database_schema_error",
    "database_constraint_error",
    "database_io_error",
    "database_unknown",
}


def classify_sqlite_error(exc: BaseException) -> str:
    """Map SQLite failures to safe low-cardinality operational categories."""
    message = str(exc or "").lower()
    if "database disk image is malformed" in message or "malformed" in message:
        return "database_corrupt"
    if "file is not a database" in message:
        return "database_not_database"
    if "database is locked" in message or "database table is locked" in message or "busy" in message:
        return "database_locked"
    if "readonly" in message or "read-only" in message:
        return "database_read_only"
    if "disk is full" in message or "database or disk is full" in message or "no space left" in message:
        return "database_disk_full"
    if isinstance(exc, sqlite3.IntegrityError) or "constraint" in message or "foreign key" in message:
        return "database_constraint_error"
    if "no such table" in message or "no such column" in message or "schema" in message:
        return "database_schema_error"
    if isinstance(exc, sqlite3.OperationalError) and any(term in message for term in ("i/o", "io error", "unable to open", "input/output")):
        return "database_io_error"
    if isinstance(exc, sqlite3.DatabaseError):
        return "database_unknown"
    return "database_unknown"


def is_transient_sqlite_error(exc: BaseException) -> bool:
    return classify_sqlite_error(exc) == "database_locked"

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
