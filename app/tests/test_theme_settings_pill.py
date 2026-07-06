import unittest
from pathlib import Path


class ThemeSettingsPillTests(unittest.TestCase):
    def test_settings_theme_pill_stylesheet_is_loaded(self):
        root = Path(__file__).resolve().parents[2]
        animations = (root / "static" / "css" / "animations.css").read_text(encoding="utf-8")
        self.assertIn("theme_settings_pill.css?v=1", animations)

    def test_settings_theme_pill_is_cylindrical(self):
        root = Path(__file__).resolve().parents[2]
        css = (root / "static" / "css" / "theme_settings_pill.css").read_text(encoding="utf-8")
        self.assertIn("#theme-btn-settings", css)
        self.assertIn("border-radius: 999px", css)
        self.assertIn("height: 34px", css)
        self.assertIn("min-width: 112px", css)
        self.assertIn("box-shadow: none", css)
        self.assertIn("#current-theme-icon-settings", css)
        self.assertIn("white-space: nowrap", css)

    def test_settings_theme_control_remains_in_template(self):
        root = Path(__file__).resolve().parents[2]
        template = (root / "templates" / "index.html").read_text(encoding="utf-8")
        self.assertIn("theme-btn-settings", template)
        self.assertIn("current-theme-icon-settings", template)


if __name__ == "__main__":
    unittest.main()
