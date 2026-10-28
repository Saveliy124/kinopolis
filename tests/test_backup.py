import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from scripts.backup import backup_database


class BackupTests(unittest.TestCase):
    def test_backup_includes_wal_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "source.db"
            destination = Path(folder) / "backup.db"
            with closing(sqlite3.connect(source)) as connection:
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute("CREATE TABLE example (id INTEGER)")
                connection.execute("INSERT INTO example VALUES (7)")
                connection.commit()
                backup_database(source, destination)
                with closing(sqlite3.connect(destination)) as restored:
                    self.assertEqual(restored.execute("SELECT id FROM example").fetchone()[0], 7)
                with self.assertRaises(FileExistsError):
                    backup_database(source, destination)

    def test_missing_database_is_not_created(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "missing.db"
            with self.assertRaises(ValueError):
                backup_database(source, Path(folder) / "backup.db")
            self.assertFalse(source.exists())
