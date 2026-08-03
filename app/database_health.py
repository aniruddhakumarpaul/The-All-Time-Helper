"""Non-destructive SQLite health checks for explicit diagnostics."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any

from app.database import DB_FILE, classify_sqlite_error


def run_database_health(db_file: str | os.PathLike[str] | None = None, *, full_check: bool = False) -> dict[str, Any]:
    """Run bounded SQLite checks without mutating or repairing the database."""
    path = Path(db_file or DB_FILE)
    result: dict[str, Any] = {
        "database_file": path.name,
        "exists": path.exists(),
        "status": "unavailable" if not path.exists() else "unknown",
        "failure_category": None,
        "quick_check": None,
        "integrity_check": None,
        "journal_mode": None,
        "foreign_key_violations": 0,
        "chats_table": False,
        "chat_columns": [],
        "chat_indexes": [],
        "schema_versions": [],
    }
    if not path.exists():
        return result

    conn = None
    try:
        uri = f"file:{path.resolve().as_posix()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=3)
        quick = str(conn.execute("PRAGMA quick_check;").fetchone()[0]).lower()
        result["quick_check"] = quick
        if full_check:
            result["integrity_check"] = str(conn.execute("PRAGMA integrity_check;").fetchone()[0]).lower()
        result["journal_mode"] = str(conn.execute("PRAGMA journal_mode;").fetchone()[0]).lower()
        foreign_keys = conn.execute("PRAGMA foreign_key_check;").fetchall()
        result["foreign_key_violations"] = len(foreign_keys)
        columns = conn.execute("PRAGMA table_info(chats);").fetchall()
        result["chat_columns"] = [str(row[1]) for row in columns]
        result["chats_table"] = bool(columns)
        result["chat_indexes"] = [str(row[1]) for row in conn.execute("PRAGMA index_list(chats);").fetchall()]
        try:
            result["schema_versions"] = [
                {"version": int(row[0]), "name": str(row[1])}
                for row in conn.execute("SELECT version, name FROM schema_migrations ORDER BY version;").fetchall()
            ]
        except sqlite3.DatabaseError:
            result["schema_versions"] = []
        healthy = quick == "ok" and (not full_check or result["integrity_check"] == "ok")
        healthy = healthy and result["foreign_key_violations"] == 0 and result["chats_table"]
        result["status"] = "healthy" if healthy else "degraded"
        return result
    except sqlite3.DatabaseError as exc:
        result["status"] = "unhealthy"
        result["failure_category"] = classify_sqlite_error(exc)
        return result
    finally:
        if conn is not None:
            conn.close()