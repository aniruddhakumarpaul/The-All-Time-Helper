import os
import unittest
from pathlib import Path

os.environ.setdefault("SECRET_KEY", "test-secret-key-for-job-center-tests")


class JobCenterTests(unittest.TestCase):
    def test_jobs_route_registered_in_factory(self):
        root = Path(__file__).resolve().parents[2]
        factory = (root / "app" / "factory.py").read_text(encoding="utf-8")
        self.assertIn("from app.routes import admin, auth, chat, email_delivery, health, jobs, proxy", factory)
        self.assertIn("app.include_router(jobs.router)", factory)

    def test_job_center_frontend_loader_registered(self):
        root = Path(__file__).resolve().parents[2]
        bootstrap = (root / "static" / "js" / "bootstrap.js").read_text(encoding="utf-8")
        job_center = (root / "static" / "js" / "job_center.js").read_text(encoding="utf-8")
        self.assertIn("job_center", bootstrap)
        self.assertIn("/jobs/status", job_center)
        self.assertIn("openJobCenter", job_center)
        self.assertIn("data-cancel-job-id", job_center)

    def test_visible_jobs_filter_shape(self):
        from app.routes.jobs import _visible_jobs_for_owner

        self.assertEqual(_visible_jobs_for_owner("nobody@example.com"), [])


if __name__ == "__main__":
    unittest.main()
