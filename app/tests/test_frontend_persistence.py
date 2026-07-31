import base64
import json
import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class FrontendPersistenceTests(unittest.TestCase):
    def test_state_touch_notifies_after_in_place_mutation(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js is not installed")
        source = (ROOT / "static" / "js" / "state.js").read_bytes()
        module_url = "data:text/javascript;base64," + base64.b64encode(source).decode("ascii")
        script = """
            globalThis.window = {};
            globalThis.document = {
                readyState: 'complete',
                querySelector: () => ({})
            };
            const { state } = await import(process.argv[1]);
            let calls = 0;
            state.subscribe('attachedContexts', () => { calls += 1; });
            state.attachedContexts.push({ kind: 'text' });
            state.touch('attachedContexts');
            process.stdout.write(JSON.stringify({ calls, count: state.attachedContexts.length }));
        """
        result = subprocess.run(
            [node, "--input-type=module", "--eval", script, module_url],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(json.loads(result.stdout), {"calls": 1, "count": 1})

    def test_email_persistence_is_event_driven_and_lifecycle_flushed(self):
        context = (ROOT / "static" / "js" / "email_context_prompt.js").read_text(encoding="utf-8")
        repair = (ROOT / "static" / "js" / "email_draft_repair.js").read_text(encoding="utf-8")
        self.assertIn("state.subscribe", context)
        self.assertIn("scheduleLocalChatCacheSave", context)
        self.assertIn("flushLocalChatCacheSave", context)
        self.assertIn("appState.subscribe", repair)
        self.assertIn("scheduleSave", repair)
        self.assertNotIn("setInterval", context)
        self.assertNotIn("setInterval", repair)


if __name__ == "__main__":
    unittest.main()