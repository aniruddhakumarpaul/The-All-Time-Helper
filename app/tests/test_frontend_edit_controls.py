import unittest
from pathlib import Path


class FrontendEditControlTests(unittest.TestCase):
    def test_save_and_submit_is_wired_after_render_with_current_dom_values(self):
        root = Path(__file__).resolve().parents[2]
        ui = (root / "static" / "js" / "ui.js").read_text(encoding="utf-8")
        app = (root / "static" / "js" / "app.js").read_text(encoding="utf-8")

        self.assertIn("submit.className = 'auth-btn edit-btn';", ui)
        self.assertIn("submit.textContent = 'Save & Submit';", ui)
        self.assertIn("submit.addEventListener('click', () => window.submitEdit?.(Number(idx), txtDiv));", ui)
        self.assertIn("window.submitEdit = submitEdit;", app)
        self.assertIn("const textarea = container.querySelector('textarea');", app)
        self.assertIn("const newText = textarea?.value.trim();", app)
        self.assertIn("chat.ms = chat.ms.slice(0, idx);", app)
        self.assertIn("await send();", app)
        self.assertNotIn("container.querySelector('#", app)
        self.assertNotIn("onclick=\"submitEdit", ui)

    def test_save_and_submit_flow_still_renders_from_stable_message_tools(self):
        root = Path(__file__).resolve().parents[2]
        ui = (root / "static" / "js" / "ui.js").read_text(encoding="utf-8")
        self.assertIn("div.querySelector('[data-edit-index]')?.addEventListener('click'", ui)
        self.assertIn("startEditPrompt(Number(idx), event.currentTarget)", ui)
        self.assertIn("cancelEdit(Number(idx))", ui)


if __name__ == "__main__":
    unittest.main()
