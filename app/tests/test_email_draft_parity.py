import base64
import json
import shutil
import subprocess
import unittest
from pathlib import Path

from app.contracts.email_draft import (
    InvalidEmailDraftVersion,
    UnsupportedEmailDraftVersion,
    normalize_email_draft,
    serialize_delivery,
    serialize_full_transient,
    serialize_persistable,
    serialize_prompt_context,
)


ROOT = Path(__file__).resolve().parents[2]


class EmailDraftParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = json.loads((ROOT / "app" / "contracts" / "email_draft_parity_fixtures.json").read_text(encoding="utf-8"))

    @staticmethod
    def python_outputs(raw):
        draft = normalize_email_draft(raw)
        return {
            "canonical": serialize_full_transient(draft),
            "full": serialize_full_transient(draft),
            "prompt": serialize_prompt_context(draft),
            "persisted": serialize_persistable(draft),
            "delivery": serialize_delivery(draft),
        }

    def test_python_rejects_malformed_and_future_versions(self):
        for name, raw in self.fixture["invalid"].items():
            error_type = UnsupportedEmailDraftVersion if name.startswith("future_") else InvalidEmailDraftVersion
            with self.subTest(name=name), self.assertRaises(error_type):
                normalize_email_draft(raw)

    def test_python_and_javascript_serializers_are_exactly_equal(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js is not installed")
        source_b64 = base64.b64encode((ROOT / "static" / "js" / "email_draft_contract.js").read_bytes()).decode("ascii")
        fixture_b64 = base64.b64encode(json.dumps(self.fixture).encode("utf-8")).decode("ascii")
        script = r"""
            globalThis.window = {};
            const source = Buffer.from(process.argv[1], 'base64').toString('utf8');
            const fixture = JSON.parse(Buffer.from(process.argv[2], 'base64').toString('utf8'));
            eval(source);
            const contract = window.helperEmailDraftContract;
            const outputs = {};
            for (const [name, raw] of Object.entries(fixture.valid)) {
                const draft = contract.normalize(raw);
                outputs[name] = {
                    canonical: contract.serializeFullTransient(draft),
                    full: contract.serializeFullTransient(draft),
                    prompt: contract.serializePromptContext(draft),
                    persisted: contract.serializePersistable(draft),
                    delivery: contract.serializeDelivery(draft),
                };
            }
            const errors = {};
            for (const [name, raw] of Object.entries(fixture.invalid)) {
                try { contract.normalize(raw); }
                catch (error) { errors[name] = { name: error.name, code: error.code }; }
            }
            process.stdout.write(JSON.stringify({ outputs, errors }));
        """
        result = subprocess.run(
            [node, "--input-type=module", "--eval", script, source_b64, fixture_b64],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        browser = json.loads(result.stdout)
        for name, raw in self.fixture["valid"].items():
            with self.subTest(name=name):
                self.assertEqual(browser["outputs"][name], self.python_outputs(raw))
        for name in self.fixture["invalid"]:
            with self.subTest(name=name):
                expected = "unsupported_email_draft_version" if name.startswith("future_") else "invalid_email_draft_version"
                self.assertEqual(browser["errors"][name]["code"], expected)


if __name__ == "__main__":
    unittest.main()
