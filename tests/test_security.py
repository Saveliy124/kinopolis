import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from cinema.__main__ import has_default_admin_password
from cinema.config import Config
from cinema.security import hash_password, validate_email, validate_password, verify_password
from cinema.web import create_application


class SecurityTests(unittest.TestCase):
    def test_password_hash_uses_salt_and_verifies(self):
        first = hash_password("StrongPass1")
        second = hash_password("StrongPass1")
        self.assertNotEqual(first, second)
        self.assertTrue(verify_password("StrongPass1", first))
        self.assertFalse(verify_password("WrongPass1", first))

    def test_password_policy(self):
        self.assertEqual(validate_password("StrongPass1"), [])
        self.assertGreaterEqual(len(validate_password("short")), 2)

    def test_email_validation(self):
        self.assertTrue(validate_email("user@example.org"))
        self.assertFalse(validate_email("not-an-email"))

    def test_existing_default_admin_password_is_detected_after_config_change(self):
        with TemporaryDirectory() as directory:
            root = Path(__file__).resolve().parent.parent
            database_path = Path(directory) / "cinema.db"
            create_application(Config(root, database_path, admin_password="Admin123!"))
            app = create_application(
                Config(root, database_path, admin_password="PrivateAdmin123!")
            )
            self.assertTrue(has_default_admin_password(app))
            fresh = create_application(
                Config(root, Path(directory) / "fresh.db", admin_password="PrivateAdmin123!")
            )
            self.assertFalse(has_default_admin_password(fresh))


if __name__ == "__main__":
    unittest.main()
