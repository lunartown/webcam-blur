import unittest

from updater import UpdateCheckError, is_newer_version, update_from_release


class UpdaterTest(unittest.TestCase):
    def test_version_compare(self):
        self.assertTrue(is_newer_version("v0.2.0", "0.1.9"))
        self.assertFalse(is_newer_version("v0.1.0", "0.1.0"))
        self.assertFalse(is_newer_version("v0.0.9", "0.1.0"))

    def test_update_from_release_prefers_dmg_asset(self):
        release = {
            "tag_name": "v0.2.0",
            "html_url": "https://example.com/release",
            "assets": [
                {
                    "name": "webcam-blur-0.2.0.dmg",
                    "browser_download_url": "https://example.com/app.dmg",
                }
            ],
            "body": "changes",
        }

        update = update_from_release(release, current="0.1.0")

        self.assertEqual(update.version, "0.2.0")
        self.assertEqual(update.url, "https://example.com/app.dmg")

    def test_update_from_release_ignores_current_version(self):
        self.assertIsNone(update_from_release({"tag_name": "v0.1.0"}, current="0.1.0"))

    def test_update_from_release_requires_download_url(self):
        with self.assertRaises(UpdateCheckError):
            update_from_release({"tag_name": "v0.2.0"}, current="0.1.0")


if __name__ == "__main__":
    unittest.main()
