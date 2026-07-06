import unittest
from pathlib import Path


class LayoutDensityTests(unittest.TestCase):
    def test_compact_density_stylesheet_is_loaded(self):
        root = Path(__file__).resolve().parents[2]
        animations = (root / "static" / "css" / "animations.css").read_text(encoding="utf-8")
        self.assertIn("compact_density.css?v=3", animations)

    def test_compact_density_targets_desktop_layout_scale(self):
        root = Path(__file__).resolve().parents[2]
        compact = (root / "static" / "css" / "compact_density.css").read_text(encoding="utf-8")
        self.assertIn("@media (min-width: 851px)", compact)
        self.assertIn("width: 272px", compact)
        self.assertIn("max-width: 980px", compact)
        self.assertIn("padding: 24px 36px", compact)
        self.assertIn("font-size: 0.94rem", compact)
        self.assertIn("max-height: 390px", compact)
        self.assertIn("max-width: 720px", compact)

    def test_compact_density_keeps_icons_proportional(self):
        root = Path(__file__).resolve().parents[2]
        compact = (root / "static" / "css" / "compact_density.css").read_text(encoding="utf-8")
        self.assertIn(".new-chat svg", compact)
        self.assertIn(".action-btn svg", compact)
        self.assertIn("width: 20px", compact)
        self.assertIn("width: 38px !important", compact)
        self.assertIn("#main-send-btn", compact)
        self.assertIn("#model-toggle", compact)

    def test_image_preview_is_outside_prompt_row(self):
        root = Path(__file__).resolve().parents[2]
        compact = (root / "static" / "css" / "compact_density.css").read_text(encoding="utf-8")
        self.assertIn("#img-preview-area", compact)
        self.assertIn("bottom: calc(100% + 8px)", compact)
        self.assertIn(".img-thumb-wrap", compact)
        self.assertIn(".img-remove-btn", compact)


if __name__ == "__main__":
    unittest.main()
