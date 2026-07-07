import unittest
from pathlib import Path


class PremiumMotionTests(unittest.TestCase):
    def test_premium_motion_css_is_loaded(self):
        root = Path(__file__).resolve().parents[2]
        animations = (root / "static" / "css" / "animations.css").read_text(encoding="utf-8")
        self.assertIn("premium_motion.css?v=1", animations)

    def test_premium_motion_css_has_safe_reduced_motion_guard(self):
        root = Path(__file__).resolve().parents[2]
        css = (root / "static" / "css" / "premium_motion.css").read_text(encoding="utf-8")
        self.assertIn("prefers-reduced-motion", css)
        self.assertIn("premium-message-enter", css)
        self.assertIn("premium-thinking-shimmer", css)
        self.assertIn("premium-send-ready", css)
        self.assertIn("premium-context-scan", css)
        self.assertIn("#input-wrap:focus-within .pill-bar", css)
        self.assertIn("#history-list > *:hover", css)
        self.assertIn("#pal-results > *", css)
        self.assertIn("#context-results > *", css)

    def test_motion_enhancer_is_loaded_after_reuse_and_before_tray(self):
        root = Path(__file__).resolve().parents[2]
        bootstrap = (root / "static" / "js" / "bootstrap.js").read_text(encoding="utf-8")
        self.assertIn("injectScript('motion_enhancements', '1', 'premium-motion')", bootstrap)
        reuse_index = bootstrap.index("injectScript('chat_context_reuse', '1', 'chat-context-reuse')")
        motion_index = bootstrap.index("injectScript('motion_enhancements', '1', 'premium-motion')")
        tray_index = bootstrap.index("injectScript('composer_context_tray', '5', 'composer-context-tray')")
        self.assertLess(reuse_index, motion_index)
        self.assertLess(motion_index, tray_index)

    def test_motion_enhancer_is_idempotent_and_non_networking(self):
        root = Path(__file__).resolve().parents[2]
        js = (root / "static" / "js" / "motion_enhancements.js").read_text(encoding="utf-8")
        self.assertIn("__premiumMotionInstalled", js)
        self.assertIn("MutationObserver", js)
        self.assertIn("hydratePrompt", js)
        self.assertIn("hydratePremiumMotion", js)
        self.assertNotIn("fetch(", js)
        self.assertNotIn("XMLHttpRequest", js)
        self.assertNotIn("innerHTML", js)
        self.assertNotIn("document.write", js)


if __name__ == "__main__":
    unittest.main()
