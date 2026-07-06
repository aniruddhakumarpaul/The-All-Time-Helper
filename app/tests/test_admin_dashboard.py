import os
import unittest
from pathlib import Path

os.environ.setdefault("SECRET_KEY", "test-secret-key-for-admin-dashboard-tests")


class AdminDashboardTests(unittest.TestCase):
    def test_admin_route_registered_in_factory(self):
        root = Path(__file__).resolve().parents[2]
        factory = (root / "app" / "factory.py").read_text(encoding="utf-8")
        self.assertIn("from app.routes import admin", factory)
        self.assertIn("app.include_router(admin.router)", factory)

    def test_admin_frontend_loader_registered(self):
        root = Path(__file__).resolve().parents[2]
        bootstrap = (root / "static" / "js" / "bootstrap.js").read_text(encoding="utf-8")
        dashboard = (root / "static" / "js" / "admin_dashboard.js").read_text(encoding="utf-8")
        self.assertIn("admin_dashboard", bootstrap)
        self.assertIn("/admin/status", dashboard)
        self.assertIn("openAdminOpsDashboard", dashboard)

    def test_admin_status_shape_helpers(self):
        from app.routes.admin import _component

        item = _component("Database", "ok", "Ready", {"count": 1})
        self.assertEqual(item["name"], "Database")
        self.assertEqual(item["status"], "ok")
        self.assertEqual(item["summary"], "Ready")
        self.assertEqual(item["details"], {"count": 1})


if __name__ == "__main__":
    unittest.main()
