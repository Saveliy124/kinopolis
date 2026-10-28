from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from cinema.config import Config
from cinema.web import create_application


ROOT = Path(__file__).resolve().parent.parent


class DatabaseTests(unittest.TestCase):
    def test_integrity_indexes_are_created(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Config(
                root_dir=ROOT,
                database_path=Path(directory) / "schema.db",
                admin_password="AdminPass1",
            )
            app = create_application(config)
            with app.service.database.connection() as connection:
                foreign_keys = connection.execute("PRAGMA foreign_keys").fetchone()[0]
                indexes = {
                    row["name"]: row["partial"]
                    for row in connection.execute("PRAGMA index_list('tickets')")
                }
            self.assertEqual(foreign_keys, 1)
            self.assertIn("uq_active_ticket", indexes)
            self.assertEqual(indexes["uq_active_ticket"], 1)


if __name__ == "__main__":
    unittest.main()
