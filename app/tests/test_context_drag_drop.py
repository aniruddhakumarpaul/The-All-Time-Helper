import unittest
from pathlib import Path


class ContextDragDropTests(unittest.TestCase):
    def test_composer_context_layer_is_loaded(self):
        root = Path(__file__).resolve().parents[2]
        bootstrap = (root / "static" / "js" / "bootstrap.js").read_text(encoding="utf-8")
        template = (root / "templates" / "index.html").read_text(encoding="utf-8")
        self.assertIn("composer_context_tray", bootstrap)
        self.assertIn("composer-context-tray", bootstrap)
        self.assertIn("composer_context_tray.js?v=6", template)
        self.assertNotIn("context_drag_drop", bootstrap)

    def test_composer_context_uses_attached_contexts_not_legacy_retrieve(self):
        root = Path(__file__).resolve().parents[2]
        script = (root / "static" / "js" / "composer_context_tray.js").read_text(encoding="utf-8")
        self.assertIn("application/x-helper-composer-context", script)
        self.assertIn("dataTransfer", script)
        self.assertIn("chat-area", script)
        self.assertIn("input-wrap", script)
        self.assertIn("prompt", script)
        self.assertIn("st.attachedContexts", script)
        self.assertNotIn("/retrieve_context", script)

    def test_composer_context_attaches_targeted_context(self):
        root = Path(__file__).resolve().parents[2]
        script = (root / "static" / "js" / "composer_context_tray.js").read_text(encoding="utf-8")
        self.assertIn("addContext", script)
        self.assertIn("attachedContexts", script)
        self.assertIn("MAX_ITEMS", script)
        self.assertIn("MAX_TOTAL_CHARS", script)
        self.assertIn("EMAIL_DRAFT_CONTEXT", script)

    def test_composer_context_makes_sources_draggable_without_click_blocking(self):
        root = Path(__file__).resolve().parents[2]
        script = (root / "static" / "js" / "composer_context_tray.js").read_text(encoding="utf-8")
        self.assertIn("composer-draggable-context", script)
        self.assertIn("draggable", script)
        self.assertIn("dragstart", script)
        self.assertIn("drop", script)
        self.assertIn("isInteractiveDraftControl", script)


if __name__ == "__main__":
    unittest.main()
