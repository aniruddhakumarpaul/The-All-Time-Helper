import unittest
from pathlib import Path


class FrontendEntryCacheBustTests(unittest.TestCase):
    def test_bootstrap_and_animations_are_versioned_in_template(self):
        root = Path(__file__).resolve().parents[2]
        template = (root / "templates" / "index.html").read_text(encoding="utf-8")
        self.assertIn('/static/css/animations.css?v=210', template)
        self.assertIn('/static/js/bootstrap.js?v=214', template)
        self.assertIn('/static/js/composer_context_tray.js?v=9', template)
        self.assertIn('/static/js/email_draft.js?v=6', template)
        self.assertNotIn('href="/static/css/animations.css"', template)
        self.assertNotIn('src="/static/js/bootstrap.js"', template)


if __name__ == "__main__":
    unittest.main()
