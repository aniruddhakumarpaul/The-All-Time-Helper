import base64
import json
import shutil
import subprocess
import unittest
from pathlib import Path

from app.contracts.email_draft import (
    EMAIL_DRAFT_SCHEMA_VERSION,
    EmailDraft,
    serialize_delivery,
    serialize_persistable,
    serialize_prompt_context,
)


ROOT = Path(__file__).resolve().parents[2]


class EmailDraftContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.examples = json.loads((ROOT / "app" / "contracts" / "email_draft_examples.json").read_text(encoding="utf-8"))

    def test_legacy_payload_normalizes_to_versioned_metadata(self):
        draft = EmailDraft.model_validate(self.examples["legacy"])

        self.assertEqual(draft.schema_version, EMAIL_DRAFT_SCHEMA_VERSION)
        self.assertEqual(draft.recipient, "person@example.com")
        self.assertEqual(draft.attachments[0].filename, "notes.txt")
        self.assertEqual(draft.attachments[0].mime_type, "text/plain")

    def test_prompt_and_persistence_serializers_forbid_attachment_content(self):
        draft = EmailDraft.model_validate({
            **self.examples["versioned"],
            "attachments": [{
                **self.examples["versioned"]["attachments"][0],
                "content": "transient-only-bytes",
                "path": "C:/private/file.png",
            }],
            "attachment_content": "legacy-transient-content",
        })

        prompt_context = serialize_prompt_context(draft)
        persisted = serialize_persistable(draft)
        delivery = serialize_delivery(draft)

        self.assertEqual(prompt_context["schema_version"], 1)
        self.assertNotIn('"content":', json.dumps(prompt_context))
        self.assertNotIn("path", json.dumps(prompt_context))
        self.assertNotIn("attachment_content", persisted)
        self.assertNotIn('"content":', json.dumps(persisted))
        self.assertEqual(len(persisted["attachments"]), 1)
        self.assertEqual(delivery["attachments"][0]["content"], "transient-only-bytes")

    def test_multiple_attachment_metadata_and_legacy_aliases_survive(self):
        draft = EmailDraft.model_validate(self.examples["versioned"])
        payload = serialize_prompt_context(draft)

        self.assertEqual(len(payload["attachments"]), 2)
        self.assertEqual(payload["attachments"][0]["name"], "photo.png")
        self.assertEqual(payload["attachments"][0]["type"], "image/png")
        self.assertEqual(payload["attachments"][1]["source"], "generated")

    def test_browser_contract_matches_persistence_boundary(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js is not installed")
        source = (ROOT / "static" / "js" / "email_draft_contract.js").read_bytes()
        module_url = "data:text/javascript;base64," + base64.b64encode(source).decode("ascii")
        script = """
            import fs from 'node:fs';
            globalThis.window = {};
            const code = Buffer.from(process.argv[1].split(',')[1], 'base64').toString('utf8');
            eval(code);
            const draft = window.helperEmailDraftContract.normalize({
                to: 'person@example.com',
                subject: 'Test',
                attachment_content: 'secret',
                attachments: [{ id: 'upload-1', name: 'photo.png', type: 'image/png', content: 'secret' }]
            });
            const persisted = window.helperEmailDraftContract.serializePersistable(draft);
            process.stdout.write(JSON.stringify({
                version: draft.schema_version,
                attachmentCount: persisted.attachments.length,
                hasContent: JSON.stringify(persisted).includes('secret')
            }));
        """
        result = subprocess.run(
            [node, "--input-type=module", "--eval", script, module_url],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(json.loads(result.stdout), {"version": 1, "attachmentCount": 1, "hasContent": False})


if __name__ == "__main__":
    unittest.main()
