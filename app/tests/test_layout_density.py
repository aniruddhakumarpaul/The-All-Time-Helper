import unittest
from pathlib import Path


class LayoutDensityTests(unittest.TestCase):
    def test_compact_density_stylesheet_is_loaded(self):
        root = Path(__file__).resolve().parents[2]
        animations = (root / "static" / "css" / "animations.css").read_text(encoding="utf-8")
        self.assertIn("compact_density.css?v=2", animations)

    def test_compact_density_targets_desktop_layout_scale(self):
        root = Path(__file__).resolve().parents[2]
        compact = (root / "static" / "css" / "compact_density.css").read_text(encoding="utf-8")
        self.assertIn("@media (min-width: 851px)", compact)
        self.assertIn("width: 268px", compact)
        self.assertIn("max-width: 960px", compact)
        self.assertIn("padding: 22px 34px", compact)
        self.assertIn("font-size: 0.93rem", compact)
        self.assertIn("max-height: 380px", compact)
        self.assertIn("max-width: 710px", compact)

    def test_compact_density_targets_bulky_icons(self):
        root = Path(__file__).resolve().parents[2]
        compact = (root / "static" / "css" / "compact_density.css").read_text(encoding="utf-8")
        self.assertIn(".new-chat svg", compact)
        self.assertIn(".action-btn svg", compact)
        self.assertIn("width: 16px", compact)
        self.assertIn("width: 32px !important", compact)
        self.assertIn("#main-send-btn", compact)
        self.assertIn("#model-toggle", compact)


if __name__ == "__main__":
    unittest.main()
