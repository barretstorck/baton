import pathlib
import tempfile
import unittest

from baton import identity

VALID = """
[baton]
app_id = 1
installation_id = 10
key_path = "/k/baton.pem"

[reviewer]
app_id = 2
installation_id = 20
key_path = "/k/rev.pem"
login = "custom[bot]"
"""


class LoadTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = pathlib.Path(self.tmp.name) / "o-r"
        self.dir.mkdir()

    def load(self, text=None):
        if text is not None:
            (self.dir / "identity.toml").write_text(text)
        return identity.load("o", "r", home=self.tmp.name)

    def test_absent(self):
        self.assertIsNone(self.load())

    def test_valid(self):
        ids = self.load(VALID)
        self.assertEqual(ids.baton, identity.App(1, 10, "/k/baton.pem", "baton[bot]"))
        self.assertEqual(ids.reviewer.login, "custom[bot]")
        self.assertEqual(ids.logins(), ["baton[bot]", "custom[bot]"])

    def test_only_one_entry(self):
        ids = self.load('[reviewer]\napp_id = 2\ninstallation_id = 3\nkey_path = "k"\n')
        self.assertIsNone(ids.baton)
        self.assertEqual(ids.logins(), ["baton-reviewer[bot]"])

    def test_missing_key(self):
        with self.assertRaisesRegex(identity.IdentityError, "missing key_path"):
            self.load("[baton]\napp_id = 1\ninstallation_id = 2\n")

    def test_bad_toml(self):
        with self.assertRaisesRegex(identity.IdentityError, "not valid TOML"):
            self.load("[baton\n")

    def test_wrong_types(self):
        for text in ('[baton]\napp_id = "1"\ninstallation_id = 2\nkey_path = "k"\n',
                     '[baton]\napp_id = 1\ninstallation_id = 2\nkey_path = 5\n',
                     '[baton]\napp_id = 1\ninstallation_id = 2\nkey_path = "k"\nlogin = 3\n',
                     'baton = 3\n', '[other]\nx = 1\n'):
            with self.assertRaises(identity.IdentityError, msg=text):
                self.load(text)


if __name__ == "__main__":
    unittest.main()
