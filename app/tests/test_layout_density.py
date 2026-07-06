import unittest
from pathlib import Path


class LayoutDensityTests(unittest.TestCase):
    def test_compact_density_stylesheet_is_loaded(self):
        root = Path(__file__).resolve().parents[2]
        animations = (root / "static" / "css" / "animations.css").read_text(encoding="utf-8")
        self.assertIn("compact_density.css?v=1", animations)

    def test_compact_density_targets_desktop_layout_scale(self):
        root = Path(__file__).resolve().parents[2]
        compact = (root / "static" / "css" / "compact_density.css").read_text(encoding="utf-8")
        self.assertIn("@media (min-width: 851px)", compact)
        self.assertIn("width: 288px", compact)
        self.assertIn("max-width: 1020px", compact)
        self.assertIn("padding: 28px 42px", compact)
        self.assertIn("font-size: 0.98rem", compact)
        self.assertIn("max-height: 440px", compact)
        self.assertIn("max-width: 760px", compact)


if __name__ == "__main__":
    unittest.main()
