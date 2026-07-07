import unittest
from pathlib import Path


class LoggerDedupTests(unittest.TestCase):
    def test_logger_disables_propagation_and_guards_handlers(self):
        root = Path(__file__).resolve().parents[2]
        source = (root / "app" / "logger.py").read_text(encoding="utf-8")
        self.assertIn("logger.propagate = False", source)
        self.assertIn("def _has_handler", source)
        self.assertIn("if not _has_handler(logging.StreamHandler)", source)
        self.assertIn("if not _has_handler(RotatingFileHandler", source)


if __name__ == "__main__":
    unittest.main()
