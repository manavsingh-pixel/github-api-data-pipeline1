import unittest
from src.pipeline import normalize_repo

class NormalizeRepoTests(unittest.TestCase):
    def test_normalizes_missing_optional_fields(self):
        row = normalize_repo({"id": 7, "full_name": "octo/demo"}, "2026-10-19", "now")
        self.assertEqual(row["repo_id"], 7)
        self.assertEqual(row["language"], "Unknown")
        self.assertEqual(row["stars"], 0)
        self.assertEqual(row["is_archived"], 0)

    def test_invalid_identity_is_skipped(self):
        self.assertIsNone(normalize_repo({"full_name": "octo/demo"}, "2026-10-19", "now"))

    def test_negative_counts_do_not_enter_clean_table(self):
        row = normalize_repo({"id": 8, "full_name": "octo/demo", "stargazers_count": -4}, "2026-10-19", "now")
        self.assertEqual(row["stars"], 0)

if __name__ == "__main__":
    unittest.main()
