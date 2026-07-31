import base64
import json
import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class StateMutationTests(unittest.TestCase):
    def test_high_risk_mutations_notify_once_and_preserve_data(self):
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
            const calls = { chats: 0, contexts: 0, images: 0, uploads: 0 };
            state.subscribe('chats', () => { calls.chats += 1; });
            state.subscribe('attachedContexts', () => { calls.contexts += 1; });
            state.subscribe('currentImages', () => { calls.images += 1; });
            state.subscribe('pendingImageUploads', () => { calls.uploads += 1; });
            state.replaceChats([{ id: 'chat-1', ms: [] }]);
            state.appendMessage('chat-1', { r: 'u', c: 'hello' });
            state.updateChat('chat-1', { title: 'Updated' });
            state.truncateMessages('chat-1', 0);
            state.addAttachedContext({ kind: 'text', text: 'context' });
            state.removeAttachedContext(item => item.kind === 'text');
            state.replaceCurrentImages([{ id: 'file-1' }]);
            state.setPendingImageUploads(Promise.resolve());
            process.stdout.write(JSON.stringify({ calls, chat: state.chats[0] }));
        """
        result = subprocess.run(
            [node, "--input-type=module", "--eval", script, module_url],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        payload = json.loads(result.stdout)
        self.assertEqual(payload["calls"], {"chats": 4, "contexts": 2, "images": 1, "uploads": 1})
        self.assertEqual(payload["chat"], {"id": "chat-1", "ms": [], "title": "Updated"})


if __name__ == "__main__":
    unittest.main()
