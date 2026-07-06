import unittest
from pathlib import Path

from app.logic.profile_links import resolve_public_profile_link_request


class PublicProfileLinkTests(unittest.TestCase):
    def test_resolves_instagram_username_profile_link(self):
        result = resolve_public_profile_link_request("search the web for instagram profile link username a_regular_mf")
        self.assertIn("https://www.instagram.com/a_regular_mf/", result)
        self.assertIn("@a_regular_mf", result)

    def test_resolves_at_handle(self):
        result = resolve_public_profile_link_request("find instagram profile url @a_regular_mf")
        self.assertIn("https://www.instagram.com/a_regular_mf/", result)

    def test_ignores_non_profile_requests(self):
        self.assertIsNone(resolve_public_profile_link_request("what is instagram"))
        self.assertIsNone(resolve_public_profile_link_request("search web for news"))

    def test_cloud_and_local_paths_use_profile_resolver(self):
        root = Path(__file__).resolve().parents[2]
        cloud = (root / "app" / "logic" / "agent_cloud.py").read_text(encoding="utf-8")
        local = (root / "app" / "logic" / "agent_local.py").read_text(encoding="utf-8")
        self.assertIn("resolve_public_profile_link_request", cloud)
        self.assertIn("resolve_public_profile_link_request", local)
        self.assertIn("profile_link = resolve_public_profile_link_request", cloud)
        self.assertIn("profile_link = resolve_public_profile_link_request", local)


if __name__ == "__main__":
    unittest.main()
