import unittest
from pathlib import Path


class ChatContextReuseTests(unittest.TestCase):
    def test_reuse_script_preserves_raw_context_payload(self):
        root = Path(__file__).resolve().parents[2]
        script = (root / "static" / "js" / "chat_context_reuse.js").read_text(encoding="utf-8")
        self.assertIn("application/x-helper-composer-context", script)
        self.assertIn("window.addEventListener('dragstart'", script)
        self.assertIn(".chat-context-card", script)
        self.assertIn("chat.ms[messageIndex]?.contexts?.[contextIndex]", script)
        self.assertIn("event.dataTransfer.setData(CONTEXT_MIME", script)

    def test_bootstrap_loads_reuse_script_before_composer_tray(self):
        root = Path(__file__).resolve().parents[2]
        bootstrap = (root / "static" / "js" / "bootstrap.js").read_text(encoding="utf-8")
        reuse_index = bootstrap.index("injectScript('chat_context_reuse', '1', 'chat-context-reuse')")
        tray_index = bootstrap.index("injectScript('composer_context_tray', '6', 'composer-context-tray')")
        self.assertLess(reuse_index, tray_index)


if __name__ == "__main__":
    unittest.main()
