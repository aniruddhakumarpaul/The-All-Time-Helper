import unittest
from pathlib import Path


class PromptBarCleanupTests(unittest.TestCase):
    def test_bootstrap_removes_legacy_prompt_button(self):
        root = Path(__file__).resolve().parents[2]
        bootstrap = (root / "static" / "js" / "bootstrap.js").read_text(encoding="utf-8")
        self.assertIn("removeLegacyPromptThemeButton", bootstrap)
        self.assertIn("document.getElementById('theme-btn')?.remove()", bootstrap)

    def test_settings_theme_control_remains(self):
        root = Path(__file__).resolve().parents[2]
        template = (root / "templates" / "index.html").read_text(encoding="utf-8")
        self.assertIn("theme-btn-settings", template)
        self.assertIn("current-theme-icon-settings", template)


if __name__ == "__main__":
    unittest.main()
