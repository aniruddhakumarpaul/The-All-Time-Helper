import sqlite3
import time
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    apply: Callable[[sqlite3.Connection], None]


def _create_core_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            email TEXT PRIMARY KEY,
            hashed_password TEXT,
            verified INTEGER DEFAULT 0,
            otp TEXT,
            otp_expiry REAL,
            name TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS chats (
            id TEXT PRIMARY KEY,
            user_email TEXT,
            title TEXT,
            messages_json TEXT,
            updated_at REAL,
            FOREIGN KEY(user_email) REFERENCES users(email)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS email_send_log (
            job_id TEXT PRIMARY KEY,
            user_email TEXT,
            recipients TEXT,
            status TEXT,
            timestamp REAL
        )
        """
    )


def _deprecate_persistent_admin_authorization(conn: sqlite3.Connection) -> None:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
    if "admin_authorized" in columns:
        conn.execute("UPDATE users SET admin_authorized = 0")


def _normalize_chat_timestamps_to_milliseconds(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        UPDATE chats
        SET updated_at = updated_at * 1000
        WHERE updated_at >= 1000000000 AND updated_at < 100000000000
        """
    )


MIGRATIONS = (
    Migration(1, "create core schema", _create_core_schema),
    Migration(2, "deprecate persistent admin authorization", _deprecate_persistent_admin_authorization),
    Migration(3, "normalize chat timestamps to milliseconds", _normalize_chat_timestamps_to_milliseconds),
)


def run_migrations(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at REAL NOT NULL
        )
        """
    )
    conn.commit()
    applied = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}

    for migration in MIGRATIONS:
        if migration.version in applied:
            continue
        try:
            conn.execute("BEGIN IMMEDIATE")
            migration.apply(conn)
            conn.execute(
                "INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, ?, ?)",
                (migration.version, migration.name, time.time()),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
