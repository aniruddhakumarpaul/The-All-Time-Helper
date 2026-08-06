import unittest
from pathlib import Path


class FrontendEntryCacheBustTests(unittest.TestCase):
    def test_bootstrap_and_animations_are_versioned_in_template(self):
        root = Path(__file__).resolve().parents[2]
        template = (root / "templates" / "index.html").read_text(encoding="utf-8")
        animations = (root / "static" / "css" / "animations.css").read_text(encoding="utf-8")
        self.assertIn('/static/css/animations.css?v=212', template)
        self.assertIn('/static/js/bootstrap.js?v=215', template)
        self.assertIn('/static/js/composer_context_tray.js?v=10', template)
        self.assertIn('/static/js/email_draft.js?v=7', template)
        self.assertNotIn('href="/static/css/animations.css"', template)
        self.assertNotIn('src="/static/js/bootstrap.js"', template)

        self.assertIn("product_controls.css?v=5", animations)
        self.assertNotIn("product_controls.css?v=4", animations)
        self.assertNotIn("animations.css?v=211", template)

if __name__ == "__main__":
    unittest.main()
