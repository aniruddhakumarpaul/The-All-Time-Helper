import base64
import json
import shutil
import subprocess
import unittest
from pathlib import Path


class FrontendChatSyncTests(unittest.TestCase):
    def test_newest_chat_merge_and_order(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js is not installed")

        root = Path(__file__).resolve().parents[2]
        source = (root / "static" / "js" / "chat_sync.js").read_bytes()
        module_url = "data:text/javascript;base64," + base64.b64encode(source).decode("ascii")
        script = """
            const { mergeChatsByRecency } = await import(process.argv[1]);
            const local = [
                { id: 'same', title: 'local-new', ms: [{ c: 'new' }], updated_at: 20, pinned: true },
                { id: 'local-only', title: 'local only', ms: [], updated_at: 1700000100 }
            ];
            const remote = [
                { id: 'same', title: 'remote-old', ms: [], updated_at: 10 },
                { id: 'remote-only', title: 'remote only', ms: [], updated_at: 1700000000000 }
            ];
            process.stdout.write(JSON.stringify(mergeChatsByRecency(local, remote)));
        """
        result = subprocess.run(
            [node, "--input-type=module", "--eval", script, module_url],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        )
        chats = json.loads(result.stdout)

        self.assertEqual([chat["id"] for chat in chats], ["same", "local-only", "remote-only"])
        self.assertEqual(chats[0]["title"], "local-new")

    def test_app_persists_active_chat_before_cloud_sync(self):
        root = Path(__file__).resolve().parents[2]
        app_js = (root / "static" / "js" / "app.js").read_text(encoding="utf-8")
        template = (root / "templates" / "index.html").read_text(encoding="utf-8")

        self.assertIn("if (state.activeId) localStorage.setItem('helper_active_chat_v2', state.activeId)", app_js)
        self.assertIn("chat.updated_at = Date.now();\n    requestChatPersist();", app_js)
        self.assertIn("mergeChatsByRecency(state.chats, data.chats)", app_js)
        self.assertIn('/static/js/app.js?v=203', template)


if __name__ == "__main__":
    unittest.main()
