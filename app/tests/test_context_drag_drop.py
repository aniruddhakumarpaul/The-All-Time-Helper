import unittest
from pathlib import Path


class ContextDragDropTests(unittest.TestCase):
    def test_context_drag_drop_layer_is_loaded(self):
        root = Path(__file__).resolve().parents[2]
        bootstrap = (root / "static" / "js" / "bootstrap.js").read_text(encoding="utf-8")
        self.assertIn("context_drag_drop", bootstrap)
        self.assertIn("context-drag-drop", bootstrap)

    def test_context_drag_drop_calls_retrieve_context(self):
        root = Path(__file__).resolve().parents[2]
        script = (root / "static" / "js" / "context_drag_drop.js").read_text(encoding="utf-8")
        self.assertIn("/retrieve_context", script)
        self.assertIn("retrieveDroppedContext", script)
        self.assertIn("dataTransfer", script)
        self.assertIn("chat-area", script)
        self.assertIn("input-wrap", script)
        self.assertIn("prompt", script)

    def test_context_drag_drop_attaches_retrieved_snippets(self):
        root = Path(__file__).resolve().parents[2]
        script = (root / "static" / "js" / "context_drag_drop.js").read_text(encoding="utf-8")
        self.assertIn("__helperState", script)
        self.assertIn("attachedContexts", script)
        self.assertIn("retrieved-drag-drop", script)
        self.assertIn("MAX_ATTACHED_CONTEXTS", script)
        self.assertIn("MAX_TOTAL_CONTEXT_CHARS", script)

    def test_context_drag_drop_makes_chat_messages_draggable(self):
        root = Path(__file__).resolve().parents[2]
        script = (root / "static" / "js" / "context_drag_drop.js").read_text(encoding="utf-8")
        self.assertIn("tempDragUnlock", script)
        self.assertIn("context-draggable", script)
        self.assertIn("draggable", script)
        self.assertIn("dragstart", script)
        self.assertIn("drop", script)


if __name__ == "__main__":
    unittest.main()
