import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class InteractionIntegrityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
        cls.palette = (ROOT / "static" / "js" / "palette.js").read_text(encoding="utf-8")
        cls.ui = (ROOT / "static" / "js" / "ui.js").read_text(encoding="utf-8")
        cls.tray = (ROOT / "static" / "js" / "composer_context_tray.js").read_text(encoding="utf-8")
        cls.style = (ROOT / "static" / "css" / "style_v3.css").read_text(encoding="utf-8")

    def test_message_text_is_selectable_and_context_drag_has_a_handle(self):
        self.assertIn('class="txt" draggable="false"', self.ui)
        self.assertIn("data-context-drag-handle", self.ui)
        self.assertIn("Boolean(window.isGDown)", self.tray)
        self.assertIn("if (textBubble && !explicitHandle && !window.isGDown)", self.tray)
        self.assertIn("user-select: text !important", self.style)
        self.assertIn(".context-drag-handle", self.style)

    def test_command_palette_uses_complete_listbox_semantics(self):
        self.assertIn('role="combobox"', self.template)
        self.assertIn('aria-controls="pal-results"', self.template)
        self.assertIn('id="pal-results" role="listbox"', self.template)
        self.assertIn('id="pal-close"', self.template)
        self.assertIn("row.setAttribute('role', 'option')", self.palette)
        self.assertIn("aria-activedescendant", self.palette)

    def test_command_palette_selection_matches_rendered_results(self):
        self.assertIn("const PALETTE_RESULT_LIMIT = 30", self.palette)
        self.assertIn("palResults.forEach((result, index)", self.palette)
        self.assertIn("setPaletteIndex(palResults.length - 1)", self.palette)
        self.assertIn("active.scrollIntoView({ block: 'nearest' })", self.palette)
        self.assertNotIn("palResults.slice(0, 10)", self.palette)

    def test_ctrl_shift_shortcuts_do_not_toggle_palette(self):
        self.assertIn("!event.altKey && !event.shiftKey", self.palette)
        self.assertIn("event.key.toLowerCase() === 'k'", self.palette)

    def test_palette_and_inspector_controls_have_stable_geometry(self):
        self.assertIn("Interaction integrity and command palette v2", self.style)
        self.assertIn("grid-template-rows: auto minmax(0, 1fr) auto", self.style)
        self.assertIn("overscroll-behavior: contain", self.style)
        self.assertIn("body.motion-ready .msg:hover .txt", self.style)
        self.assertIn(".history-actions", self.style)
        self.assertIn(".code-wrapper:focus-within .code-actions", self.style)

    def test_mobile_composer_preserves_touch_targets(self):
        self.assertIn("Mobile composer target integrity", self.style)
        self.assertIn(".pill-bar > .action-btn", self.style)
        self.assertIn("min-width: 36px", self.style)
        self.assertIn("flex-basis: 44px", self.style)

    def test_interaction_assets_are_cache_busted(self):
        self.assertIn("/static/css/style_v3.css?v=146", self.template)
        self.assertIn("/static/js/composer_context_tray.js?v=7", self.template)
        self.assertIn("/static/js/palette.js?v=212", self.template)
        self.assertIn("/static/js/app.js?v=222", self.template)


if __name__ == "__main__":
    unittest.main()