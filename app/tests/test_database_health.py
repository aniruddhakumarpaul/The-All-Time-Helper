import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from app import database
from app.database_health import run_database_health
from app.repository import ChatRepository
from app.routes import chat


class DatabaseHealthTests(unittest.TestCase):
    def test_classifier_uses_safe_low_cardinality_categories(self):
        cases = {
            sqlite3.DatabaseError("database disk image is malformed"): "database_corrupt",
            sqlite3.DatabaseError("file is not a database"): "database_not_database",
            sqlite3.OperationalError("database is locked"): "database_locked",
            sqlite3.OperationalError("attempt to write a readonly database"): "database_read_only",
            sqlite3.OperationalError("database or disk is full"): "database_disk_full",
            sqlite3.OperationalError("no such table: chats"): "database_schema_error",
            sqlite3.IntegrityError("FOREIGN KEY constraint failed"): "database_constraint_error",
        }
        for error, expected in cases.items():
            with self.subTest(expected=expected):
                self.assertEqual(database.classify_sqlite_error(error), expected)

    def test_health_check_is_read_only_and_reports_schema(self):
        with tempfile.TemporaryDirectory(dir="C:\\tmp") as temp_dir:
            db_file = str(Path(temp_dir) / "users.db")
            with patch.object(database, "DB_FILE", db_file):
                database.init_db()
            result = run_database_health(db_file, full_check=True)
        self.assertEqual(result["status"], "healthy")
        self.assertEqual(result["quick_check"], "ok")
        self.assertEqual(result["integrity_check"], "ok")
        self.assertTrue(result["chats_table"])
        self.assertEqual([item["version"] for item in result["schema_versions"]], [1, 2, 3])
        self.assertNotIn("messages_json", json_safe(result))

    def test_health_check_categorizes_corrupt_or_non_database_file(self):
        with tempfile.TemporaryDirectory(dir="C:\\tmp") as temp_dir:
            db_file = Path(temp_dir) / "broken.db"
            db_file.write_bytes(b"not sqlite")
            result = run_database_health(db_file)
        self.assertEqual(result["status"], "unhealthy")
        self.assertIn(result["failure_category"], {"database_corrupt", "database_not_database"})

    def test_repository_rolls_back_invalid_sync_and_remains_usable(self):
        conn = sqlite3.connect(":memory:")
        try:
            conn.execute("CREATE TABLE chats (id TEXT PRIMARY KEY, user_email TEXT, title TEXT, messages_json TEXT, updated_at REAL)")
            with self.assertRaises(ValueError):
                ChatRepository.sync_user_chats(conn, "owner@example.com", {"chats": "invalid"})
            ChatRepository.sync_user_chats(conn, "owner@example.com", {"chats": [{"id": "c1", "title": "Kept", "ms": []}]})
            self.assertEqual(conn.execute("SELECT title FROM chats WHERE id='c1'").fetchone()[0], "Kept")
        finally:
            conn.close()

    def test_sync_route_retries_only_locked_and_rolls_back_terminal_database_failure(self):
        class FakeDb:
            def __init__(self):
                self.rollbacks = 0
            def rollback(self):
                self.rollbacks += 1

        db = FakeDb()
        with patch.object(chat.ChatRepository, "sync_user_chats", side_effect=[sqlite3.OperationalError("database is locked"), None]) as sync, patch.object(chat.time, "sleep"):
            self.assertEqual(chat.sync_chats([], "owner@example.com", db), {"success": True})
        self.assertEqual(sync.call_count, 2)
        self.assertEqual(db.rollbacks, 1)

        db = FakeDb()
        with patch.object(chat.ChatRepository, "sync_user_chats", side_effect=sqlite3.DatabaseError("database disk image is malformed")):
            with self.assertRaises(HTTPException) as raised:
                chat.sync_chats([], "owner@example.com", db)
        self.assertEqual(raised.exception.status_code, 500)
        self.assertEqual(raised.exception.detail, "Conversations could not be synced.")
        self.assertEqual(db.rollbacks, 1)


    def test_get_chats_maps_corrupt_database_to_safe_service_error(self):
        class FakeDb:
            pass

        with patch.object(chat.ChatRepository, "get_chats_for_user", side_effect=sqlite3.DatabaseError("database disk image is malformed")):
            with self.assertRaises(HTTPException) as raised:
                chat.get_chats("owner@example.com", FakeDb())
        self.assertEqual(raised.exception.status_code, 503)
        self.assertEqual(raised.exception.detail, "Conversations are temporarily unavailable.")

def json_safe(result):
    return {key: value for key, value in result.items() if key in {"database_file", "exists", "status", "failure_category", "quick_check", "integrity_check", "journal_mode", "foreign_key_violations", "chats_table", "chat_columns", "chat_indexes", "schema_versions"}}


if __name__ == "__main__":
    unittest.main()