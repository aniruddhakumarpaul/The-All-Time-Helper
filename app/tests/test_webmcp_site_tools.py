import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class WebMcpSiteToolsSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = (ROOT / "static" / "js" / "app.js").read_text(encoding="utf-8")
        cls.bootstrap = (ROOT / "static" / "js" / "bootstrap.js").read_text(encoding="utf-8")
        cls.site_tools = (ROOT / "static" / "js" / "webmcp_tools.js").read_text(encoding="utf-8")

    def test_bootstrap_loads_page_scoped_site_tools(self):
        self.assertIn("injectScript('webmcp_tools', '1', 'webmcp-site-tools')", self.bootstrap)
        self.assertIn("typeof document.modelContext?.registerTool === 'function'", self.site_tools)
        self.assertIn("modelContext.registerTool(tool, { signal: controller.signal })", self.site_tools)

    def test_registered_tools_are_bounded_and_human_in_the_loop(self):
        expected = {
            "helper_get_workspace_state",
            "helper_search_conversations",
            "helper_open_conversation",
            "helper_start_new_conversation",
            "helper_prepare_prompt",
            "helper_set_theme",
            "helper_set_assistant_route",
        }
        for name in expected:
            self.assertIn(f"name: '{name}'", self.site_tools)
        self.assertIn("additionalProperties: false", self.site_tools)
        self.assertIn("This tool never sends the prompt or starts model work.", self.site_tools)
        self.assertNotIn("window.send", self.site_tools)
        self.assertNotIn("send_email", self.site_tools)
        self.assertNotIn("Admin Key", self.site_tools)

    def test_app_bridge_reuses_existing_ui_actions_and_requires_authentication(self):
        self.assertIn("function requireSiteToolAuthentication()", self.app)
        self.assertIn("window.HelperSiteTools = createSiteToolBridge();", self.app)
        self.assertIn("loadChat(chat.id);", self.app)
        self.assertIn("startNewChat();", self.app)
        self.assertIn("prompt.dispatchEvent(new Event('input', { bubbles: true }));", self.app)
        self.assertIn("ui.selModel(id, option.dataset.modelName", self.app)
        self.assertIn("window.applyThemeChoice(theme);", self.app)


if __name__ == "__main__":
    unittest.main()
