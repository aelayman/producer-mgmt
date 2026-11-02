import sqlite3
import tempfile
import unittest
from pathlib import Path

import app


class DatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_directory.name) / "test_artist_database.db"
        self.original_database_path = app.ARTIST_DATABASE_PATH
        app.ARTIST_DATABASE_PATH = self.database_path

    def tearDown(self) -> None:
        app.ARTIST_DATABASE_PATH = self.original_database_path
        self.temp_directory.cleanup()

    def test_import_creates_normalized_records_and_skips_duplicate_set(self) -> None:
        entries = [
            {
                "artist_name": "Artist One",
                "handle": "@artistone",
                "url": "https://soundcloud.com/artistone",
                "track_title": "Shared Track",
                "source_text": "Artist One - Shared Track",
            },
            {
                "artist_name": "Artist Two",
                "handle": "@artisttwo",
                "url": "https://soundcloud.com/artisttwo",
                "track_title": "Shared Track",
                "source_text": "Artist Two - Shared Track",
            },
        ]

        result = app.append_to_artist_database(
            entries,
            dj_name="Test DJ",
            set_number="1",
            set_date="2026-01-01",
            radio_show="NO SIGNAL",
        )

        self.assertEqual(result, {"created": 1, "skipped_duplicates": 0})
        health = app.get_database_health()
        self.assertEqual(health["djs"], 1)
        self.assertEqual(health["sets"], 1)
        self.assertEqual(health["artists"], 2)
        self.assertEqual(health["tracks"], 1)
        self.assertEqual(health["appearances"], 1)
        self.assertEqual(health["sets_without_tracks"], 0)
        self.assertEqual(health["orphaned_appearances"], 0)
        self.assertEqual(health["latest_dj"], "Test DJ")

        duplicate_result = app.append_to_artist_database(
            entries,
            dj_name="Test DJ",
            set_number="1",
            set_date="2026-01-01",
            radio_show="NO SIGNAL",
        )
        self.assertEqual(duplicate_result, {"created": 0, "skipped_duplicates": 2})

    def test_backup_is_a_readable_sqlite_database(self) -> None:
        app.append_to_artist_database(
            [
                {
                    "artist_name": "Backup Artist",
                    "track_title": "Backup Track",
                    "source_text": "Backup Artist - Backup Track",
                }
            ],
            dj_name="Backup DJ",
            set_number="1",
            set_date="2026-01-02",
        )

        filename, backup_bytes = app.create_database_backup()
        backup_path = Path(self.temp_directory.name) / filename
        backup_path.write_bytes(backup_bytes)

        with sqlite3.connect(backup_path) as connection:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }

        self.assertEqual(integrity, "ok")
        self.assertIn("track_catalog", tables)
        self.assertIn("track_appearances", tables)
        self.assertNotIn("tracks", tables)


if __name__ == "__main__":
    unittest.main()
