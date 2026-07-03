import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class MigrationTests(unittest.TestCase):
    def test_new_database_records_versions_without_legacy_admin_column(self):
        from app import database

        with tempfile.TemporaryDirectory() as temp_dir:
            db_file = str(Path(temp_dir) / "users.db")
            with patch.object(database, "DB_FILE", db_file):
                database.init_db()

            conn = sqlite3.connect(db_file)
            try:
                columns = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
                versions = [row[0] for row in conn.execute("SELECT version FROM schema_migrations ORDER BY version")]
            finally:
                conn.close()

        self.assertNotIn("admin_authorized", columns)
        self.assertEqual(versions, [1, 2])

    def test_existing_persistent_admin_grants_are_cleared(self):
        from app import database

        with tempfile.TemporaryDirectory() as temp_dir:
            db_file = str(Path(temp_dir) / "users.db")
            conn = sqlite3.connect(db_file)
            try:
                conn.execute(
                    """
                    CREATE TABLE users (
                        email TEXT PRIMARY KEY,
                        hashed_password TEXT,
                        verified INTEGER DEFAULT 0,
                        otp TEXT,
                        otp_expiry REAL,
                        name TEXT,
                        admin_authorized INTEGER DEFAULT 0
                    )
                    """
                )
                conn.execute("INSERT INTO users (email, admin_authorized) VALUES ('user@example.com', 1)")
                conn.commit()
            finally:
                conn.close()

            with patch.object(database, "DB_FILE", db_file):
                database.init_db()

            conn = sqlite3.connect(db_file)
            try:
                grant = conn.execute(
                    "SELECT admin_authorized FROM users WHERE email = 'user@example.com'"
                ).fetchone()[0]
            finally:
                conn.close()

        self.assertEqual(grant, 0)


if __name__ == "__main__":
    unittest.main()
