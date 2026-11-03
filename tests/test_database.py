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

    def test_import_preview_parses_without_writing(self) -> None:
        preview = app.build_import_preview(
            [
                "# comment",
                "Artist One @[artistone](https://soundcloud.com/artistone) - Preview Track",
                "Artist Without A Title",
            ]
        )

        self.assertEqual(preview["non_comment_lines"], 2)
        self.assertEqual(preview["parsed_line_count"], 2)
        self.assertEqual(len(preview["parsed_rows"]), 2)
        self.assertEqual(len(preview["warnings"]), 1)
        self.assertFalse(self.database_path.exists())

    def test_import_preview_captures_plus_separated_artists(self) -> None:
        rows = app.parse_soundcloud_line(
            "30. Kali Malone + Drew McDowall "
            "@kali-malone @drewmcdowall – A Sound That Is Alive"
        )

        self.assertEqual([row["artist_name"] for row in rows], ["Kali Malone", "Drew McDowall"])
        self.assertEqual(
            [row["handle"] for row in rows],
            ["kali-malone", "drewmcdowall"],
        )
        self.assertEqual(
            [row["track_title"] for row in rows],
            ["A Sound That Is Alive", "A Sound That Is Alive"],
        )

    def test_completed_list_bulk_updates_missing_artist_profile_fields(self) -> None:
        app.append_to_artist_database(
            [{"artist_name": "Research Artist", "track_title": "Research Track"}],
            dj_name="Research DJ",
            set_number="1",
            set_date="2026-01-09",
        )

        entries = [
            {
                "artist_name": "Research Artist",
                "handle": "@researchartist",
                "url": "https://soundcloud.com/researchartist",
            }
        ]
        self.assertEqual(
            app.preview_artist_profile_updates(entries),
            {"handles": 1, "urls": 1, "conflicts": 0},
        )
        self.assertEqual(
            app.update_existing_artist_profiles(entries),
            {"handles": 1, "urls": 1},
        )
        self.assertEqual(
            app.update_existing_artist_profiles(entries),
            {"handles": 0, "urls": 0},
        )

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

    def test_undo_latest_import_preserves_shared_tracks_and_artists(self) -> None:
        entry = [
            {
                "artist_name": "Shared Artist",
                "track_title": "Shared Track",
                "source_text": "Shared Artist - Shared Track",
            }
        ]
        app.append_to_artist_database(
            entry,
            dj_name="First DJ",
            set_number="1",
            set_date="2026-01-03",
        )
        app.append_to_artist_database(
            entry,
            dj_name="Second DJ",
            set_number="2",
            set_date="2026-01-04",
        )

        undone = app.undo_latest_import()
        self.assertEqual(undone["dj_name"], "Second DJ")
        health = app.get_database_health()
        self.assertEqual(health["sets"], 1)
        self.assertEqual(health["tracks"], 1)
        self.assertEqual(health["appearances"], 1)
        self.assertEqual(health["artists"], 1)

        undone = app.undo_latest_import()
        self.assertEqual(undone["dj_name"], "First DJ")
        health = app.get_database_health()
        self.assertEqual(health["sets"], 0)
        self.assertEqual(health["tracks"], 0)
        self.assertEqual(health["appearances"], 0)
        self.assertEqual(health["artists"], 1)
        self.assertIsNone(app.get_latest_import())

    def test_set_browser_returns_summary_and_tracklist(self) -> None:
        app.append_to_artist_database(
            [
                {
                    "artist_name": "Set Artist",
                    "track_title": "Set Track",
                    "source_text": "Set Artist - Set Track",
                }
            ],
            dj_name="Set DJ",
            set_number="12",
            set_date="2026-01-05",
            radio_show="ABYSS",
        )

        summaries = app.load_set_summaries()
        self.assertEqual(len(summaries), 1)
        self.assertEqual(summaries[0]["dj_name"], "Set DJ")
        self.assertEqual(summaries[0]["track_count"], 1)

        tracks = app.load_set_tracks(summaries[0]["id"])
        self.assertEqual(tracks[0]["track_title"], "Set Track")
        self.assertEqual(tracks[0]["credited_artists"], "Set Artist")

    def test_set_metadata_can_be_updated_without_losing_tracks(self) -> None:
        app.append_to_artist_database(
            [
                {
                    "artist_name": "Editable Artist",
                    "track_title": "Editable Track",
                }
            ],
            dj_name="Original DJ",
            set_number="1",
            set_date="2026-01-06",
        )
        set_row = app.load_set_summaries()[0]

        app.update_set_metadata(
            set_row["id"],
            "Renamed DJ",
            "2",
            "2026-01-07",
            "ABYSS",
        )

        updated = app.load_set_summaries()[0]
        self.assertEqual(updated["dj_name"], "Renamed DJ")
        self.assertEqual(updated["set_number"], "2")
        self.assertEqual(updated["set_date"], "2026-01-07")
        self.assertEqual(updated["radio_show"], "ABYSS")
        self.assertEqual(updated["track_count"], 1)

    def test_artist_handle_conflict_can_be_resolved(self) -> None:
        app.init_artist_database()
        with app.get_db_connection() as connection:
            artist_id = connection.execute(
                "INSERT INTO artists (artist_name, handle, url) VALUES (?, ?, ?)",
                ("Conflict Artist", "original", "https://soundcloud.com/original"),
            ).lastrowid
            conflict_id = connection.execute(
                """
                INSERT INTO artist_handle_conflicts
                    (artist_id, artist_name, handle, url, source_text, created_at)
                VALUES (?, ?, ?, ?, ?, datetime('now'))
                """,
                (
                    artist_id,
                    "Conflict Artist",
                    "edited",
                    "https://soundcloud.com/edited",
                    "Conflict Artist @[edited](https://soundcloud.com/edited)",
                ),
            ).lastrowid
            connection.commit()

        app.resolve_artist_handle_conflict(
            conflict_id,
            artist_id,
            "@edited",
            "HTTPS://SoundCloud.com/Edited",
        )

        with app.get_db_connection() as connection:
            artist = connection.execute(
                "SELECT handle, url FROM artists WHERE id = ?", (artist_id,)
            ).fetchone()
            remaining_conflicts = connection.execute(
                "SELECT COUNT(*) FROM artist_handle_conflicts"
            ).fetchone()[0]

        self.assertEqual(tuple(artist), ("edited", "https://soundcloud.com/edited"))
        self.assertEqual(remaining_conflicts, 0)

    def test_artist_lookup_returns_ranked_profile_and_history(self) -> None:
        app.append_to_artist_database(
            [
                {
                    "artist_name": "Lookup Artist",
                    "handle": "@lookupartist",
                    "url": "https://soundcloud.com/lookupartist",
                    "track_title": "Lookup Track",
                }
            ],
            dj_name="Lookup DJ",
            set_number="3",
            set_date="2026-01-08",
        )

        matches = app.search_artist_directory("lookup artst")
        self.assertEqual(matches[0]["artist_name"], "Lookup Artist")
        self.assertEqual(matches[0]["handle"], "lookupartist")
        self.assertGreaterEqual(matches[0]["score"], 80)

        history = app.load_artist_history(matches[0]["id"])
        self.assertEqual(history[0]["track_title"], "Lookup Track")
        self.assertEqual(history[0]["dj_name"], "Lookup DJ")
        history_csv = app.export_artist_history_csv(history)
        self.assertIn("track_title,dj_name,set_number,set_date,radio_show", history_csv)
        self.assertIn("Lookup Track,Lookup DJ,3,2026-01-08,NO SIGNAL", history_csv)


if __name__ == "__main__":
    unittest.main()
