import unittest
from pathlib import Path


class ComposerContextTrayTests(unittest.TestCase):
    def test_composer_context_script_is_loaded(self):
        root = Path(__file__).resolve().parents[2]
        bootstrap = (root / "static" / "js" / "bootstrap.js").read_text(encoding="utf-8")
        self.assertIn("composer_context_tray", bootstrap)
        self.assertIn("composer-context-tray", bootstrap)
        self.assertIn("'3', 'composer-context-tray'", bootstrap)
        self.assertNotIn("context_drag_drop", bootstrap)

    def test_composer_context_stylesheet_is_loaded(self):
        root = Path(__file__).resolve().parents[2]
        animations = (root / "static" / "css" / "animations.css").read_text(encoding="utf-8")
        self.assertIn("composer_context_tray.css?v=2", animations)

    def test_drag_sources_include_text_images_and_widgets(self):
        root = Path(__file__).resolve().parents[2]
        script = (root / "static" / "js" / "composer_context_tray.js").read_text(encoding="utf-8")
        self.assertIn(".msg .txt", script)
        self.assertIn("img.chat-rendered-img", script)
        self.assertIn("img.chat-img-preview", script)
        self.assertIn(".email-draft-card", script)
        self.assertIn("EMAIL_DRAFT_CONTEXT", script)

    def test_context_tray_renders_above_prompt_bar_in_flow(self):
        root = Path(__file__).resolve().parents[2]
        script = (root / "static" / "js" / "composer_context_tray.js").read_text(encoding="utf-8")
        css = (root / "static" / "css" / "composer_context_tray.css").read_text(encoding="utf-8")
        self.assertIn("composer-context-tray", script)
        self.assertIn("container.insertBefore(tray, container.firstChild)", script)
        self.assertIn("position: static", css)
        self.assertIn("flex-direction: column", css)
        self.assertIn("order: -2", css)
        self.assertNotIn("bottom: calc(100% + 10px)", css)
        self.assertIn("composer-context-chip", css)
        self.assertIn("Drop to target next prompt", css)

    def test_context_items_feed_existing_send_path(self):
        root = Path(__file__).resolve().parents[2]
        script = (root / "static" / "js" / "composer_context_tray.js").read_text(encoding="utf-8")
        app = (root / "static" / "js" / "app.js").read_text(encoding="utf-8")
        self.assertIn("st.attachedContexts", script)
        self.assertIn("state.attachedContexts.map(serializeAttachedContext)", app)
        self.assertIn("[Attached Context", app)

    def test_context_tray_clears_after_send_starts(self):
        root = Path(__file__).resolve().parents[2]
        script = (root / "static" / "js" / "composer_context_tray.js").read_text(encoding="utf-8")
        self.assertIn("clearContexts", script)
        self.assertIn("scheduleClearAfterSend", script)
        self.assertIn("promptCleared && requestStarted", script)
        self.assertIn("main-send-btn", script)
        self.assertIn("clearComposerContextTray", script)

    def test_context_tray_does_not_observe_its_own_rendering(self):
        root = Path(__file__).resolve().parents[2]
        script = (root / "static" / "js" / "composer_context_tray.js").read_text(encoding="utf-8")
        self.assertIn("renderingTray", script)
        self.assertIn("scheduleRender", script)
        self.assertIn("installSourceObserver", script)
        self.assertNotIn("observer.observe(document.body", script)
        self.assertIn("#composer-context-tray", script)
        self.assertIn("if (renderingTray) return", script)


if __name__ == "__main__":
    unittest.main()
