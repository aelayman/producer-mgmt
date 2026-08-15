"""
Identify Existing Tags
-----------------------
A tool that matches names from your list against a MASTER REFERENCE LIST
containing @ handles, then returns your list with tags inserted
plus a deduplicated list of all matched tags.

MASTER LIST:
- Stored in 'master_reference_list.txt' in the same folder as this script
- Loaded automatically every time the app runs — no need to paste it each time
- You can grow it over time by:
    (a) Editing the file directly in any text editor
    (b) Using the "Manage Master List" tab in the web UI
- Format: one entry per line, e.g. "Fred Jones @fredejones – Dynamics presentation"

OVERVIEW OF HOW THIS WORKS:
1. The app loads your master list from 'master_reference_list.txt' automatically.

2. You provide a "new list" — your own list of names WITHOUT @ handles. Example:
       1. Fred Jones – Supply Chain Demo

3. The tool compares each name in your new list against all names in the
   master list using fuzzy matching (so minor typos/variations still work).
   When it finds a match, it grabs the @ handle from the master list.

4. Output:
   - Your list re-numbered with the @ handle inserted after each matched name
   - A separate deduplicated list of just the unique @ handles found

ADJUSTING THE MATCHING:
- The "threshold" (default 82 out of 100) controls how strict matching is.
- Higher threshold = names must be more similar to count as a match.
- Lower threshold = more lenient, but risks false positives.
- The slider in the web UI lets you tune this in real time.
"""

import csv
import re
import sqlite3
from datetime import date, datetime
from io import StringIO
from pathlib import Path
import streamlit as st
from thefuzz import fuzz  # fuzzy string matching library (uses Levenshtein distance)


# =============================================================================
# MASTER LIST FILE PATH
# This is the persistent file where all known names + @ handles are stored.
# The app reads from this file automatically. You can also edit it by hand.
# It lives in the same folder as this script.
# =============================================================================
MASTER_LIST_PATH = Path(__file__).parent / "master_reference_list.txt"
ARTIST_DATABASE_PATH = Path(__file__).parent / "artist_database.db"


def load_master_list() -> str:
    """
    Load the master reference list from disk.
    Returns the file contents as a string, or empty string if file doesn't exist.
    """
    if MASTER_LIST_PATH.exists():
        return MASTER_LIST_PATH.read_text(encoding="utf-8")
    return ""


def append_to_master_list(new_entries: str) -> int:
    """
    Append new entries to the master reference list file.
    Adds a blank line separator before the new content if the file isn't empty.

    Args:
        new_entries: Raw text with new entries (one per line)

    Returns:
        Number of valid entries (lines with @ handles) that were added.
    """
    # Count how many valid lines are being added (must contain an @ handle)
    valid_count = sum(
        1 for line in new_entries.strip().splitlines()
        if line.strip() and "@" in line and not line.strip().startswith("#")
    )

    # Append to the file
    with open(MASTER_LIST_PATH, "a", encoding="utf-8") as f:
        # Add a newline separator if the file already has content
        existing = MASTER_LIST_PATH.read_text(encoding="utf-8") if MASTER_LIST_PATH.exists() else ""
        if existing and not existing.endswith("\n"):
            f.write("\n")
        f.write(new_entries.strip() + "\n")

    return valid_count


def get_db_connection() -> sqlite3.Connection:
    """Create a SQLite database connection for the artist database."""
    conn = sqlite3.connect(ARTIST_DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def clean_text(value: object) -> str:
    """Convert database values to strings and treat None as empty text."""
    if value is None:
        return ""
    return str(value)


def normalize_lookup_text(value: object) -> str:
    """Normalize names so duplicate records can be detected reliably."""
    value = clean_text(value).strip().lower()
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def format_artist_handle(handle: object) -> str:
    """Format a stored artist handle with exactly one leading @ symbol."""
    handle = clean_text(handle).strip()
    return f"@{handle.lstrip('@')}" if handle else ""


def init_artist_database() -> None:
    """Create the hierarchical artist database tables if they do not already exist."""
    with get_db_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS djs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dj_name TEXT NOT NULL UNIQUE
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dj_id INTEGER NOT NULL,
                set_number TEXT NOT NULL,
                set_date TEXT,
                radio_show TEXT NOT NULL DEFAULT 'NO SIGNAL',
                UNIQUE(dj_id, set_number, set_date, radio_show)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS artists (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                artist_name TEXT NOT NULL UNIQUE,
                handle TEXT,
                url TEXT,
                location TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tracks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                set_id INTEGER NOT NULL,
                artist_id INTEGER NOT NULL,
                track_title TEXT NOT NULL,
                source_text TEXT,
                notes TEXT,
                first_seen TEXT,
                last_seen TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS artist_handle_conflicts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                artist_id INTEGER NOT NULL,
                artist_name TEXT NOT NULL,
                handle TEXT,
                url TEXT,
                source_text TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(artist_id, handle, url)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS track_catalog (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                track_title TEXT NOT NULL,
                track_key TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS track_artists (
                track_id INTEGER NOT NULL,
                artist_id INTEGER NOT NULL,
                credit_order INTEGER NOT NULL DEFAULT 1,
                PRIMARY KEY(track_id, artist_id),
                FOREIGN KEY(track_id) REFERENCES track_catalog(id),
                FOREIGN KEY(artist_id) REFERENCES artists(id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS track_appearances (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                set_id INTEGER NOT NULL,
                track_id INTEGER NOT NULL,
                source_text TEXT,
                notes TEXT,
                first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL,
                UNIQUE(set_id, track_id),
                FOREIGN KEY(set_id) REFERENCES sets(id),
                FOREIGN KEY(track_id) REFERENCES track_catalog(id)
            )
            """
        )
        conn.commit()
        migrate_legacy_tracks()
        dedupe_artist_database()


def get_or_create_dj(conn: sqlite3.Connection, dj_name: str) -> int:
    dj_name = clean_text(dj_name).strip() or "Unknown DJ"
    existing = conn.execute("SELECT id FROM djs WHERE dj_name = ?", (dj_name,)).fetchone()
    if existing:
        return existing["id"]
    cursor = conn.execute("INSERT INTO djs (dj_name) VALUES (?)", (dj_name,))
    return int(cursor.lastrowid)


def record_artist_handle_conflict(
    conn: sqlite3.Connection,
    artist_id: int,
    artist_name: str,
    handle: str,
    url: str,
    source_text: str = "",
) -> None:
    """Preserve a non-matching handle instead of silently discarding it."""
    if not handle and not url:
        return
    conn.execute(
        """
        INSERT OR IGNORE INTO artist_handle_conflicts
            (artist_id, artist_name, handle, url, source_text, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (artist_id, artist_name, handle, url, source_text, datetime.utcnow().isoformat()),
    )


def dedupe_artist_database() -> dict[str, int]:
    """Merge obvious duplicate DJs, artists, sets, and tracks in the SQLite database."""
    with get_db_connection() as conn:
        # Merge duplicate DJs by normalized name.
        dj_rows = conn.execute("SELECT id, dj_name FROM djs ORDER BY id").fetchall()
        dj_map: dict[str, int] = {}
        for row in dj_rows:
            key = normalize_lookup_text(row["dj_name"])
            if not key:
                continue
            if key in dj_map:
                conn.execute("UPDATE sets SET dj_id = ? WHERE dj_id = ?", (dj_map[key], row["id"]))
                conn.execute("DELETE FROM djs WHERE id = ?", (row["id"],))
            else:
                dj_map[key] = row["id"]

        # Merge duplicate artists by normalized name and preserve the first non-empty handle/url/location.
        artist_rows = conn.execute("SELECT id, artist_name, handle, url, location FROM artists ORDER BY id").fetchall()
        artist_map: dict[str, int] = {}
        for row in artist_rows:
            key = normalize_lookup_text(row["artist_name"])
            if not key:
                continue
            if key in artist_map:
                target_id = artist_map[key]
                conn.execute("UPDATE tracks SET artist_id = ? WHERE artist_id = ?", (target_id, row["id"]))
                conn.execute(
                    """
                    INSERT OR IGNORE INTO track_artists (track_id, artist_id, credit_order)
                    SELECT track_id, ?, credit_order
                    FROM track_artists
                    WHERE artist_id = ?
                    """,
                    (target_id, row["id"]),
                )
                conn.execute("DELETE FROM track_artists WHERE artist_id = ?", (row["id"],))
                target = conn.execute("SELECT handle, url, location FROM artists WHERE id = ?", (target_id,)).fetchone()
                handle = clean_text(row["handle"]).strip()
                url = clean_text(row["url"]).strip()
                location = clean_text(row["location"]).strip()
                target_handle = clean_text(target["handle"]).strip()
                target_url = clean_text(target["url"]).strip()
                if (handle and target_handle and handle != target_handle) or (url and target_url and url != target_url):
                    record_artist_handle_conflict(
                        conn,
                        target_id,
                        clean_text(row["artist_name"]).strip(),
                        handle,
                        url,
                    )
                if not clean_text(target["handle"]).strip() and handle:
                    conn.execute("UPDATE artists SET handle = ? WHERE id = ?", (handle, target_id))
                if not clean_text(target["url"]).strip() and url:
                    conn.execute("UPDATE artists SET url = ? WHERE id = ?", (url, target_id))
                if not clean_text(target["location"]).strip() and location:
                    conn.execute("UPDATE artists SET location = ? WHERE id = ?", (location, target_id))
                conn.execute("DELETE FROM artists WHERE id = ?", (row["id"],))
            else:
                artist_map[key] = row["id"]

        # Merge duplicate sets by normalized DJ/set/date/show signature.
        set_rows = conn.execute("SELECT id, dj_id, set_number, set_date, radio_show FROM sets ORDER BY id").fetchall()
        set_map: dict[tuple, int] = {}
        for row in set_rows:
            key = (
                row["dj_id"],
                normalize_lookup_text(row["set_number"]),
                normalize_lookup_text(row["set_date"]),
                normalize_lookup_text(row["radio_show"]),
            )
            if key in set_map:
                target_id = set_map[key]
                conn.execute("UPDATE tracks SET set_id = ? WHERE set_id = ?", (target_id, row["id"]))
                conn.execute(
                    """
                    INSERT OR IGNORE INTO track_appearances
                        (set_id, track_id, source_text, notes, first_seen, last_seen)
                    SELECT ?, track_id, source_text, notes, first_seen, last_seen
                    FROM track_appearances
                    WHERE set_id = ?
                    """,
                    (target_id, row["id"]),
                )
                conn.execute("DELETE FROM track_appearances WHERE set_id = ?", (row["id"],))
                conn.execute("DELETE FROM sets WHERE id = ?", (row["id"],))
            else:
                set_map[key] = row["id"]

        # Remove duplicate tracks for the same set/artist/title/source.
        track_rows = conn.execute("SELECT id, set_id, artist_id, track_title, source_text FROM tracks ORDER BY id").fetchall()
        seen_tracks: set[tuple] = set()
        for row in track_rows:
            key = (
                row["set_id"],
                row["artist_id"],
                normalize_lookup_text(row["track_title"]),
                normalize_lookup_text(row["source_text"]),
            )
            if key in seen_tracks:
                conn.execute("DELETE FROM tracks WHERE id = ?", (row["id"],))
            else:
                seen_tracks.add(key)

        conn.commit()

        return {
            "djs": len(dj_rows),
            "artists": len(artist_rows),
            "sets": len(set_rows),
            "tracks": len(track_rows),
        }


def get_or_create_set(conn: sqlite3.Connection, dj_name: str, set_number: str, set_date: str, radio_show: str) -> int:
    dj_id = get_or_create_dj(conn, dj_name)
    set_number = clean_text(set_number).strip() or "1"
    set_date = clean_text(set_date).strip()
    radio_show = clean_text(radio_show).strip() or "NO SIGNAL"
    existing = conn.execute(
        "SELECT id FROM sets WHERE dj_id = ? AND set_number = ? AND set_date = ? AND radio_show = ?",
        (dj_id, set_number, set_date, radio_show),
    ).fetchone()
    if existing:
        return existing["id"]
    cursor = conn.execute(
        "INSERT INTO sets (dj_id, set_number, set_date, radio_show) VALUES (?, ?, ?, ?)",
        (dj_id, set_number, set_date, radio_show),
    )
    return int(cursor.lastrowid)


def find_existing_set(conn: sqlite3.Connection, dj_name: str, set_number: str, set_date: str, radio_show: str) -> sqlite3.Row | None:
    dj_name = clean_text(dj_name).strip() or "Unknown DJ"
    set_number = clean_text(set_number).strip() or "1"
    set_date = clean_text(set_date).strip()
    radio_show = clean_text(radio_show).strip() or "NO SIGNAL"
    dj_row = conn.execute("SELECT id FROM djs WHERE dj_name = ?", (dj_name,)).fetchone()
    if not dj_row:
        return None

    normalized_set_number = normalize_lookup_text(set_number)
    normalized_set_date = normalize_lookup_text(set_date)
    normalized_show = normalize_lookup_text(radio_show)

    for row in conn.execute(
        "SELECT id, set_number, set_date, radio_show FROM sets WHERE dj_id = ?",
        (dj_row["id"],),
    ).fetchall():
        if (
            normalize_lookup_text(row["set_number"]) == normalized_set_number
            and normalize_lookup_text(row["set_date"]) == normalized_set_date
            and normalize_lookup_text(row["radio_show"]) == normalized_show
        ):
            return row
    return None


def get_or_create_artist(
    conn: sqlite3.Connection,
    artist_name: str,
    handle: str = "",
    url: str = "",
    location: str = "",
    source_text: str = "",
) -> int:
    artist_name = clean_text(artist_name).strip() or "Unknown artist"
    handle = clean_text(handle).strip()
    url = clean_text(url).strip()
    location = clean_text(location).strip()
    existing = conn.execute("SELECT id, handle, url, location FROM artists WHERE artist_name = ?", (artist_name,)).fetchone()
    if existing:
        if (existing["handle"] and handle and existing["handle"] != handle) or (existing["url"] and url and existing["url"] != url):
            record_artist_handle_conflict(conn, existing["id"], artist_name, handle, url, source_text)
        if not existing["handle"] and handle:
            conn.execute("UPDATE artists SET handle = ? WHERE id = ?", (handle, existing["id"]))
        if not existing["url"] and url:
            conn.execute("UPDATE artists SET url = ? WHERE id = ?", (url, existing["id"]))
        if not existing["location"] and location:
            conn.execute("UPDATE artists SET location = ? WHERE id = ?", (location, existing["id"]))
        return existing["id"]
    cursor = conn.execute(
        "INSERT INTO artists (artist_name, handle, url, location) VALUES (?, ?, ?, ?)",
        (artist_name, handle, url, location),
    )
    return int(cursor.lastrowid)


def get_or_create_track(conn: sqlite3.Connection, track_title: str, now: str) -> int:
    track_title = clean_text(track_title).strip() or "Untitled track"
    track_key = normalize_lookup_text(track_title)
    existing = conn.execute(
        "SELECT id FROM track_catalog WHERE track_key = ?",
        (track_key,),
    ).fetchone()
    if existing:
        conn.execute(
            "UPDATE track_catalog SET updated_at = ? WHERE id = ?",
            (now, existing["id"]),
        )
        return int(existing["id"])
    cursor = conn.execute(
        """
        INSERT INTO track_catalog (track_title, track_key, created_at, updated_at)
        VALUES (?, ?, ?, ?)
        """,
        (track_title, track_key, now, now),
    )
    return int(cursor.lastrowid)


def migrate_legacy_tracks() -> None:
    """Copy rows from the original appearance-shaped tracks table into the normalized tables."""
    now = datetime.utcnow().isoformat()
    with get_db_connection() as conn:
        legacy_rows = conn.execute(
            """
            SELECT
                t.set_id,
                t.artist_id,
                t.track_title,
                t.source_text,
                t.notes,
                t.first_seen,
                t.last_seen
            FROM tracks t
            """
        ).fetchall()
        for row in legacy_rows:
            track_id = get_or_create_track(conn, row["track_title"], now)
            conn.execute(
                """
                INSERT OR IGNORE INTO track_artists (track_id, artist_id, credit_order)
                VALUES (?, ?, 1)
                """,
                (track_id, row["artist_id"]),
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO track_appearances
                    (set_id, track_id, source_text, notes, first_seen, last_seen)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    row["set_id"],
                    track_id,
                    row["source_text"] or "",
                    row["notes"] or "",
                    row["first_seen"] or now,
                    row["last_seen"] or now,
                ),
            )
        conn.commit()


def load_artist_database() -> list[dict]:
    """Load the normalized database as one row per credited artist appearance."""
    init_artist_database()
    with get_db_connection() as conn:
        rows = conn.execute(
            """
            SELECT
                a.artist_name,
                a.handle,
                a.url,
                a.location,
                tc.track_title,
                ta.source_text,
                ta.notes,
                d.dj_name,
                s.set_number,
                s.set_date,
                s.radio_show,
                (SELECT COUNT(*)
                 FROM track_appearances ta2
                 JOIN track_artists tr2 ON tr2.track_id = ta2.track_id
                 WHERE tr2.artist_id = a.id) AS appearance_count,
                ta.first_seen,
                ta.last_seen
            FROM track_appearances ta
            JOIN track_catalog tc ON tc.id = ta.track_id
            JOIN track_artists tr ON tr.track_id = tc.id
            JOIN artists a ON a.id = tr.artist_id
            JOIN sets s ON s.id = ta.set_id
            JOIN djs d ON d.id = s.dj_id
            ORDER BY a.artist_name, s.set_date, s.set_number, tc.track_title
            """
        ).fetchall()
        return [dict(row) for row in rows]


def load_artist_handle_conflicts() -> list[dict]:
    """Load preserved handles that conflicted with an artist's primary record."""
    init_artist_database()
    with get_db_connection() as conn:
        rows = conn.execute(
            """
            SELECT artist_name, handle, url, source_text, created_at
            FROM artist_handle_conflicts
            ORDER BY artist_name, created_at
            """
        ).fetchall()
        return [dict(row) for row in rows]


def load_normalized_database_tables() -> dict[str, list[dict]]:
    """Load separate views of artists, tracks, credits, and set appearances."""
    init_artist_database()
    with get_db_connection() as conn:
        artists = conn.execute(
            "SELECT artist_name, handle, url, location FROM artists ORDER BY artist_name"
        ).fetchall()
        tracks = conn.execute(
            """
            SELECT
                tc.track_title,
                (SELECT GROUP_CONCAT(a2.artist_name, ', ')
                 FROM track_artists tr2
                 JOIN artists a2 ON a2.id = tr2.artist_id
                 WHERE tr2.track_id = tc.id) AS credited_artists,
                (SELECT COUNT(*) FROM track_appearances ta2 WHERE ta2.track_id = tc.id)
                    AS appearance_count
            FROM track_catalog tc
            ORDER BY tc.track_title
            """
        ).fetchall()
        credits = conn.execute(
            """
            SELECT
                tc.track_title,
                a.artist_name,
                a.handle,
                tr.credit_order
            FROM track_artists tr
            JOIN track_catalog tc ON tc.id = tr.track_id
            JOIN artists a ON a.id = tr.artist_id
            ORDER BY tc.track_title, tr.credit_order, a.artist_name
            """
        ).fetchall()
        appearances = conn.execute(
            """
            SELECT
                tc.track_title,
                GROUP_CONCAT(a.artist_name, ', ') AS credited_artists,
                d.dj_name,
                s.set_number,
                s.set_date,
                s.radio_show,
                ta.source_text,
                ta.first_seen,
                ta.last_seen
            FROM track_appearances ta
            JOIN track_catalog tc ON tc.id = ta.track_id
            JOIN sets s ON s.id = ta.set_id
            JOIN djs d ON d.id = s.dj_id
            LEFT JOIN track_artists tr ON tr.track_id = tc.id
            LEFT JOIN artists a ON a.id = tr.artist_id
            GROUP BY ta.id
            ORDER BY s.set_date, s.set_number, tc.track_title
            """
        ).fetchall()
        return {
            "artists": [dict(row) for row in artists],
            "tracks": [dict(row) for row in tracks],
            "credits": [dict(row) for row in credits],
            "appearances": [dict(row) for row in appearances],
        }


def append_to_artist_database(
    entries: list[dict],
    dj_name: str = "",
    set_number: str = "",
    set_date: str = "",
    radio_show: str = "NO SIGNAL",
) -> dict[str, int]:
    """Create or update DJ/set/artist/track records for one or more entries."""
    if not entries:
        return {"created": 0, "skipped_duplicates": 0}

    init_artist_database()
    now = datetime.utcnow().isoformat()

    with get_db_connection() as conn:
        existing_set = find_existing_set(conn, dj_name, set_number, set_date, radio_show)
        if existing_set:
            conn.commit()
            return {"created": 0, "skipped_duplicates": len(entries)}

        set_id = get_or_create_set(conn, dj_name, set_number, set_date, radio_show)
        created_count = 0
        skipped_duplicates = 0
        grouped_entries: dict[str, list[dict]] = {}
        for entry in entries:
            track_title = (entry.get("track_title") or entry.get("artist_name") or "").strip()
            grouped_entries.setdefault(normalize_lookup_text(track_title), []).append(entry)

        for track_entries in grouped_entries.values():
            first_entry = track_entries[0]
            track_title = (first_entry.get("track_title") or first_entry.get("artist_name") or "").strip()
            source_text = (first_entry.get("source_text") or "").strip()
            notes = (first_entry.get("notes") or "").strip()
            track_id = get_or_create_track(conn, track_title, now)
            existing_appearance = conn.execute(
                "SELECT id FROM track_appearances WHERE set_id = ? AND track_id = ?",
                (set_id, track_id),
            ).fetchone()
            if existing_appearance:
                skipped_duplicates += len(track_entries)
                continue

            for credit_order, entry in enumerate(track_entries, 1):
                artist_name = (entry.get("artist_name") or "Unknown artist").strip()
                handle = (entry.get("handle") or "").strip()
                url = (entry.get("url") or "").strip()
                artist_id = get_or_create_artist(
                    conn,
                    artist_name,
                    handle=handle,
                    url=url,
                    location="",
                    source_text=(entry.get("source_text") or "").strip(),
                )
                conn.execute(
                    """
                    INSERT OR IGNORE INTO track_artists (track_id, artist_id, credit_order)
                    VALUES (?, ?, ?)
                    """,
                    (track_id, artist_id, credit_order),
                )

            conn.execute(
                """
                INSERT INTO track_appearances
                    (set_id, track_id, source_text, notes, first_seen, last_seen)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (set_id, track_id, source_text, notes, now, now),
            )
            created_count += 1

        conn.commit()
    return {"created": created_count, "skipped_duplicates": skipped_duplicates}


def export_artist_database_csv() -> str:
    """Export the artist database as a CSV string."""
    rows = load_artist_database()
    output = StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=[
            "artist_name",
            "handle",
            "url",
            "location",
            "track_title",
            "appearance_count",
            "dj_name",
            "set_number",
            "set_date",
            "radio_show",
            "source_text",
            "notes",
            "first_seen",
            "last_seen",
        ],
    )
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return output.getvalue()


def parse_soundcloud_line(line: str) -> list[dict]:
    """
    Parse a SoundCloud-style line into one or more artist database rows.

    Handles collaborative entries like:
    - Danny Goliger & Nikki Nair @[dannygoliger](https://soundcloud.com/dannygoliger) @[nikki-nair](https://soundcloud.com/nikki-nair) - I Like Millions
    - Mor Elian @[morelian](https://soundcloud.com/morelian) - Swerving Mantis
    """
    line = line.strip()
    if not line or line.startswith("#"):
        return []

    line_cleaned = re.sub(r"^\d+[\.\)]\s*", "", line).strip()
    if not line_cleaned:
        return []

    pattern = re.compile(r"@\[([^\]]+)\]\((https?://[^)]+)\)|@([A-Za-z0-9._-]+)")
    matches = list(pattern.finditer(line_cleaned))

    dash_match = re.search(r"(?:\s+[–—-]\s+|[–—])", line_cleaned)
    dash_index = dash_match.start() if dash_match else len(line_cleaned)

    pre_dash = line_cleaned[:dash_index].strip() if dash_match else line_cleaned
    track_title = ""
    if dash_match:
        track_title = line_cleaned[dash_match.end():].strip()
        track_title = re.sub(pattern, "", track_title)
        track_title = re.sub(r"\s+", " ", track_title).strip(" -–—")

    artist_entries = []
    handles_before_dash = [m for m in matches if m.start() < dash_index]

    if handles_before_dash:
        artist_name_text = re.sub(pattern, "", pre_dash)
        artist_name_text = re.sub(r"\s+", " ", artist_name_text).strip(" -–—")
        artist_names = [
            part.strip()
            for part in re.split(r"\s*(?:&|,)\s*", artist_name_text)
            if part.strip()
        ]

        for index, artist_name in enumerate(artist_names):
            match = handles_before_dash[index] if index < len(handles_before_dash) else None
            if match is None:
                handle = ""
                url = ""
            elif match.group(1):
                handle = match.group(1)
                url = match.group(2)
            else:
                handle = match.group(3)
                url = f"https://soundcloud.com/{handle}"
            if not artist_name:
                artist_name = "Unknown artist"
            artist_entries.append({
                "artist_name": artist_name,
                "handle": handle,
                "url": url,
                "track_title": track_title,
                "source_text": line,
                "notes": "",
            })

    else:
        artist_name = re.sub(pattern, "", pre_dash)
        artist_name = re.sub(r"\s+", " ", artist_name).strip(" -–—")
        artist_name = artist_name or "Unknown artist"
        artist_entries.append({
            "artist_name": artist_name,
            "handle": "",
            "url": "",
            "track_title": track_title,
            "source_text": line,
            "notes": "",
        })

    return artist_entries


# =============================================================================
# PARSING FUNCTIONS
# These extract structured data from raw text input.
# =============================================================================


def parse_reference_list(text: str) -> list[dict]:
    """
    Parse the reference list to extract names and their @ handles.

    Expected input format (one entry per line):
        28. Fred Jones @fredejones – Dynamics presentation.
        29. Jane Smith @jsmith – Power Platform demo.

    The number at the start is optional. The @ handle can appear anywhere
    in the line. The name is extracted as everything BEFORE the @ handle.

    Returns:
        List of dicts, each with:
        - 'name': the person's name (e.g. "Fred Jones")
        - 'handle': their @ tag (e.g. "@fredejones")
    """
    entries = []
    for line in text.strip().splitlines():
        line = line.strip()
        if not line:
            continue  # skip blank lines

        # Strip leading numbering like "28." or "28)" so we're left with just the content
        line_cleaned = re.sub(r"^\d+[\.\)]\s*", "", line)

        # Look for an @ handle — matches @ followed by word characters, dots, or hyphens
        # Examples: @fredejones, @jane.smith, @some-user
        handle_match = re.search(r"@([\w\.\-]+)", line_cleaned)
        if not handle_match:
            continue  # no @ handle found on this line, skip it

        handle = handle_match.group(0)  # the full handle including the @ symbol

        # Everything before the @ handle is treated as the person's name
        name_part = line_cleaned[: handle_match.start()].strip()

        # Remove any trailing dashes/spaces that might be left over
        # (e.g. "Fred Jones – " becomes "Fred Jones")
        name_part = re.sub(r"[\s\-–—]+$", "", name_part).strip()

        # Only add if we got both a name and a handle
        if name_part and handle:
            entries.append({"name": name_part, "handle": handle})

    return entries


def load_reference_entries_from_db() -> list[dict]:
    """Load all artists from SQLite so missing handles can be flagged during matching."""
    init_artist_database()
    with get_db_connection() as conn:
        rows = conn.execute(
            "SELECT artist_name, COALESCE(handle, '') AS handle FROM artists ORDER BY artist_name"
        ).fetchall()
        return [{"name": row["artist_name"], "handle": row["handle"]} for row in rows]


def parse_input_list(text: str) -> list[dict]:
    """
    Parse the user's new list (the one WITHOUT @ handles).

    Expected input format (one entry per line, numbering optional):
        1. Fred Jones – Supply Chain Demo
        Jane Smith – Finance Overview
        Bob Roberts

    The tool splits each line on the first dash (–, —, or -) and uses
    the part BEFORE the dash as the name to look up.

    Returns:
        List of dicts, each with:
        - 'name': the person's name to match against (e.g. "Fred Jones")
        - 'full_line': the entire line content (for reconstructing output)
    """
    entries = []
    for line in text.strip().splitlines():
        line = line.strip()
        if not line:
            continue  # skip blank lines

        # Strip leading numbering like "1." or "1)"
        line_cleaned = re.sub(r"^\d+[\.\)]\s*", "", line)

        # Split on the first dash (en-dash –, em-dash —, or hyphen -)
        # We only split on the FIRST dash so that dashes in descriptions are preserved
        # The name to look up is everything before that first dash
        parts = re.split(r"\s*[–—\-]\s*", line_cleaned, maxsplit=1)
        name = parts[0].strip()

        if name:
            entries.append({"name": name, "full_line": line_cleaned})

    return entries


# =============================================================================
# MATCHING LOGIC
# This is the core of the tool — it compares names using fuzzy matching.
# =============================================================================


def find_best_matches(
    name: str, reference_entries: list[dict], threshold: int = 82
) -> list[dict]:
    """
    Find the best fuzzy match for a given name in the reference list.

    Uses three different fuzzy matching strategies from the 'thefuzz' library:
    1. fuzz.ratio — straight character-by-character similarity
       Good for: names that are very close (e.g. "Fred Jones" vs "Fred Jones")
    2. fuzz.token_sort_ratio — sorts words alphabetically before comparing
       Good for: names in different order (e.g. "Jones, Fred" vs "Fred Jones")
    3. fuzz.token_set_ratio — compares unique word overlap
       Good for: names where one version has extra words
       (slightly penalized with 0.95 multiplier to avoid over-matching)

    The highest score from all three strategies is used. If it meets the
    threshold, the match is returned.

    Args:
        name: The name to look up (from the user's new list)
        reference_entries: All entries from the reference list
        threshold: Minimum score (0-100) required to count as a match.
                   Default 82 means names must be ~82% similar.

    Returns:
        The best-matching reference entry dict, or None if no match meets threshold.
    """
    name_lower = name.lower().strip()
    matches: list[dict] = []

    for entry in reference_entries:
        ref_name_lower = entry["name"].lower().strip()

        score_ratio = fuzz.ratio(name_lower, ref_name_lower)
        score_token_sort = fuzz.token_sort_ratio(name_lower, ref_name_lower)
        score_token_set = fuzz.token_set_ratio(name_lower, ref_name_lower)
        score = max(score_ratio, score_token_sort, int(score_token_set * 0.95))

        if score >= threshold:
            matches.append({"entry": entry, "score": score})

    matches.sort(key=lambda item: item["score"], reverse=True)
    return [item["entry"] for item in matches[:3]]


# =============================================================================
# MAIN PROCESSING
# Ties together parsing and matching to produce the final output.
# =============================================================================


def process_lists(reference_entries: list[dict], input_text: str, threshold: int = 82):
    """
    Main processing function — takes raw text for both lists and produces results.

    Steps:
    1. Parse the reference list into structured entries (name + handle)
    2. Parse the user's input list into structured entries (name + description)
    3. For each entry in the input list, try to find a match in the reference list
    4. Build the output: numbered list with tags, plus a deduplicated tag list

    Args:
        reference_text: Raw text of the reference list (with @ handles)
        input_text: Raw text of the user's new list (without @ handles)
        threshold: Match sensitivity (0-100), passed to find_best_match

    Returns:
        Tuple of:
        - tagged_list: list of strings, each a numbered line with tag inserted
        - unique_tags: sorted, deduplicated list of @ handles that were matched
        - unmatched: list of names that had no match (so user can review them)
    """
    input_entries = parse_input_list(input_text)

    tagged_list = []
    matched_tags = set()
    unmatched = []

    for i, entry in enumerate(input_entries, 1):
        matches = find_best_matches(entry["name"], reference_entries, threshold)

        if matches:
            if any(d in entry["full_line"] for d in ["–", "—"]):
                parts = re.split(r"\s*[–—]\s*", entry["full_line"], maxsplit=1)
                name_part = parts[0].strip()
                desc_part = parts[1].strip() if len(parts) > 1 else ""
            elif "-" in entry["full_line"]:
                parts = re.split(r"\s*-\s*", entry["full_line"], maxsplit=1)
                name_part = parts[0].strip()
                desc_part = parts[1].strip() if len(parts) > 1 else ""
            else:
                name_part = entry["name"]
                desc_part = ""

            handles = " ".join(
                format_artist_handle(match.get("handle"))
                for match in matches
                if match.get("handle")
            )
            if handles:
                handle_suffix = handles
            else:
                handle_suffix = "⚠️ handle needed"

            if desc_part:
                tagged_line = f"{i}. {name_part} {handle_suffix} – {desc_part}".strip()
            else:
                tagged_line = f"{i}. {name_part} {handle_suffix}".strip()

            tagged_list.append(tagged_line)
            for match in matches:
                if match.get("handle"):
                    matched_tags.add(format_artist_handle(match["handle"]))
        else:
            tagged_list.append(f"{i}. {entry['full_line']}  ⚠️ no match found")
            unmatched.append(entry["name"])

    # Sort tags alphabetically (case-insensitive) for clean output
    unique_tags = sorted(matched_tags, key=lambda x: x.lower())
    return tagged_list, unique_tags, unmatched


# =============================================================================
# STREAMLIT WEB UI
# Everything below builds the browser-based interface.
# Streamlit re-runs this entire script from top to bottom on every user
# interaction (button click, slider change, etc.), so there's no separate
# "event loop" — the UI is declarative.
# =============================================================================

# Page configuration — sets browser tab title, icon, and layout width
st.set_page_config(page_title="Identify Existing Tags", page_icon="🏷️", layout="wide")
st.title("🏷️ Identify Existing Tags")
st.markdown(
    "Match names from your list against a master reference list to find existing @ handles."
)

# --- SIDEBAR: Settings and instructions ---
with st.sidebar:
    st.header("Settings")
    threshold = st.slider(
        "Match sensitivity",
        min_value=60,
        max_value=100,
        value=82,
        help="Higher = stricter matching. 82 is a good default for 'not too fuzzy'.",
    )

    st.markdown("---")

    # Show the master list file location so the user knows where it lives
    st.markdown("**Master list file:**")
    st.code(str(MASTER_LIST_PATH), language=None)

    master_text = load_master_list()
    master_entries = parse_reference_list(master_text)
    db_reference_entries = load_reference_entries_from_db()
    st.metric("Artists in database", len(db_reference_entries))

    st.markdown("---")
    st.markdown(
        "**How it works:**\n"
        "1. Your master list is loaded automatically from `master_reference_list.txt`\n"
        "2. Use **Find Tags** tab to match names\n"
        "3. Use **Manage Master List** tab to view/add entries"
    )


# --- FOUR TABS: main matching function, artist database import, database explorer, and master list management ---
tab_match, tab_database, tab_explore, tab_manage = st.tabs(["🔍 Find Tags", "🎵 Build Artist Database", "🧭 Explore Database", "📋 Manage Master List"])


# =============================================================================
# TAB 1: FIND TAGS (main matching function)
# =============================================================================
with tab_match:
    st.subheader("Match your list against the master reference list")

    # Show a note about the master list being loaded automatically
    if db_reference_entries:
        st.success(f"✅ Using **{len(db_reference_entries)}** artists from the database for matching")
    else:
        st.warning(
            "⚠️ No artists are in the database yet. Use the **Build Artist Database** tab to import a set first."
        )

    # --- INPUT: User's new list (WITHOUT @ handles) ---
    st.markdown("#### Paste or upload your list (without @ handles)")
    st.caption("One name per line. Numbering and descriptions after a dash are optional.")

    st.session_state.setdefault("clear_match_input", False)
    if st.session_state.get("clear_match_input", False):
        st.session_state["input_text"] = ""
        st.session_state["input_upload"] = None
        for key in ["result_tagged", "result_tags", "result_unmatched"]:
            st.session_state.pop(key, None)
        st.session_state["clear_match_input"] = False

    input_upload = st.file_uploader(
        "Upload your list", type=["txt", "csv"], key="input_upload"
    )
    input_text = st.text_area(
        "Or paste your list here:",
        height=300,
        placeholder="1. Fred Jones – Supply Chain Demo\n2. Jane Smith – Finance Overview\n3. Bob Roberts\n...",
        key="input_text",
    )

    if input_upload:
        input_text = input_upload.read().decode("utf-8", errors="replace")
        st.text_area("Uploaded content (preview):", input_text[:2000], height=150, disabled=True)

    # --- PROCESS BUTTONS ---
    find_col, clear_col = st.columns(2)
    find_tags = find_col.button("🔍 Find Tags", type="primary", use_container_width=True)
    clear_match = clear_col.button("🧹 Clear list and results", use_container_width=True)

    if clear_match:
        st.session_state["clear_match_input"] = True
        st.rerun()

    if find_tags:
        if not db_reference_entries:
            st.error("The artist database is empty. Import a set in the **Build Artist Database** tab first.")
        elif not input_text:
            st.error("Please provide your list of names to look up.")
        else:
            with st.spinner("Matching names..."):
                reference_entries = load_reference_entries_from_db()
                tagged_list, unique_tags, unmatched = process_lists(
                    reference_entries, input_text, threshold
                )

            # --- RESULTS ---
            st.markdown("---")
            st.subheader("✅ Results")

            result_col1, result_col2 = st.columns([3, 2])

            with result_col1:
                st.markdown("#### Your List with Tags")
                tagged_output = "\n".join(tagged_list)
                st.text_area(
                    "Tagged list (copy from here):",
                    tagged_output,
                    height=400,
                    key="result_tagged",
                )

            with result_col2:
                st.markdown("#### Unique Tags (deduplicated)")
                tags_output = "\n".join(unique_tags)
                st.text_area(
                    "All matched tags (copy from here):",
                    tags_output,
                    height=200,
                    key="result_tags",
                )

                if unmatched:
                    st.markdown("#### ⚠️ No Match Found")
                    st.text_area(
                        "These names had no match:",
                        "\n".join(unmatched),
                        height=150,
                        key="result_unmatched",
                    )

            # --- SUMMARY STATISTICS ---
            st.markdown("---")
            stat_cols = st.columns(4)
            stat_cols[0].metric("Total in your list", len(tagged_list))
            stat_cols[1].metric("Matched", len(tagged_list) - len(unmatched))
            stat_cols[2].metric("Unmatched", len(unmatched))
            stat_cols[3].metric("Unique tags", len(unique_tags))


# =============================================================================
# TAB 2: BUILD ARTIST DATABASE
# Lets the user paste SoundCloud-style artist/tag lists and save them to CSV.
# =============================================================================
with tab_database:
    st.subheader("Build your artist/tag database")
    st.markdown(
        "Paste one or more SoundCloud-style lines and save them as structured rows with artist name, handle, and full URL."
    )

    st.markdown("#### Paste your SoundCloud-style lines")
    st.caption("Paste one or more lines like the examples you shared. Each line will be imported as one track entry using the same DJ, set, date, and show context below. Artist locations will remain blank for now and can be filled in later.")

    for key, default in {
        "dj_name": "",
        "set_number": "",
        "entry_date": date.today(),
        "radio_show": "NO SIGNAL",
        "bulk_lines": "",
        "clear_bulk_input": False,
    }.items():
        st.session_state.setdefault(key, default)

    if st.session_state.get("clear_bulk_input", False):
        st.session_state["bulk_lines"] = ""
        st.session_state["dj_name"] = ""
        st.session_state["set_number"] = ""
        st.session_state["entry_date"] = date.today()
        st.session_state["radio_show"] = "NO SIGNAL"
        st.session_state["clear_bulk_input"] = False

    dj_name = st.text_input("DJ name", key="dj_name")
    set_number = st.text_input("Set number", key="set_number")
    entry_date = st.date_input("Date", key="entry_date", format="YYYY-MM-DD")
    radio_show = st.radio(
        "Radio show",
        ["NO SIGNAL", "ABYSS", "TRACKATHON", "TRACKATHON II"],
        key="radio_show",
    )
    pasted_lines = st.text_area(
        "Paste lines here",
        key="bulk_lines",
        height=260,
        placeholder="1. Mor Elian @[morelian](https://soundcloud.com/morelian) - Swerving Mantis\n2. Gobekli @[gobekli](https://soundcloud.com/gobekli) - Edfu Texts (Ronan Remix) @[ronan-music](https://soundcloud.com/ronan-music)",
    )

    import_col, clear_col = st.columns(2)
    paste_import = import_col.button("📥 Paste and import", use_container_width=True, type="primary")
    clear = clear_col.button("🧹 Clear input", use_container_width=True)

    if clear:
        if st.session_state.get("bulk_lines", "").strip():
            st.session_state["clear_bulk_input"] = True
            st.rerun()

    if paste_import:
        if not dj_name.strip():
            st.error("Please enter a DJ name.")
        else:
            lines = [line.strip() for line in pasted_lines.splitlines() if line.strip()]
            if not lines:
                st.error("Paste at least one line to import.")
            else:
                set_date_value = entry_date.isoformat() if isinstance(entry_date, date) else str(entry_date)
                with get_db_connection() as conn:
                    existing_set = find_existing_set(conn, dj_name.strip(), set_number.strip(), set_date_value, radio_show)

                if existing_set:
                    st.warning("This DJ/set already exists in the database. Import skipped to avoid duplicate track entries.")
                else:
                    parsed_rows = []
                    for line in lines:
                        parsed_rows.extend(parse_soundcloud_line(line))

                    if parsed_rows:
                        import_result = append_to_artist_database(
                            parsed_rows,
                            dj_name=dj_name.strip(),
                            set_number=set_number.strip(),
                            set_date=set_date_value,
                            radio_show=radio_show,
                        )
                        st.success(
                            f"✅ Parsed {len(lines)} line(s); created {import_result['created']} track appearance(s); skipped {import_result['skipped_duplicates']} duplicate artist credit(s)."
                        )
                        st.dataframe(load_artist_database(), use_container_width=True, hide_index=True)
                    else:
                        st.warning("No valid entries were found in the pasted text.")

    if st.button("🧹 Dedupe database", use_container_width=True):
        dedupe_result = dedupe_artist_database()
        st.success(
            "Dedupe complete. Existing duplicate records were merged; conflicting handles were preserved for review."
        )
        st.caption(
            f"Scanned {dedupe_result['djs']} DJ rows, {dedupe_result['artists']} artist rows, "
            f"{dedupe_result['sets']} set rows, and {dedupe_result['tracks']} track rows."
        )

    st.markdown("---")
    st.caption(f"Database file: {ARTIST_DATABASE_PATH}")
    if ARTIST_DATABASE_PATH.exists():
        st.download_button(
            "Download current CSV",
            data=export_artist_database_csv(),
            file_name="artist_database.csv",
            mime="text/csv",
        )


# =============================================================================
# TAB 3: EXPLORE ARTIST DATABASE
# Lets the user search and summarize artists by country, city, DJ, and appearance.
# =============================================================================
with tab_explore:
    st.subheader("Explore your artist database")
    st.markdown("Search, filter, and summarize the artists you have imported so far.")

    artist_rows = load_artist_database()
    conflict_rows = load_artist_handle_conflicts()
    normalized_tables = load_normalized_database_tables()
    if conflict_rows:
        st.warning(f"{len(conflict_rows)} conflicting handle(s) were preserved for review.")
        st.dataframe(
            [
                {
                    "Artist": clean_text(row.get("artist_name", "")),
                    "Conflicting handle": clean_text(row.get("handle", "")),
                    "URL": clean_text(row.get("url", "")),
                    "Source": clean_text(row.get("source_text", "")),
                }
                for row in conflict_rows
            ],
            use_container_width=True,
            hide_index=True,
        )

    st.markdown("#### Database tables")
    table_artists, table_tracks, table_credits, table_appearances = st.tabs(
        ["Artists", "Tracks", "Track Credits", "Set Appearances"]
    )
    with table_artists:
        st.dataframe(normalized_tables["artists"], use_container_width=True, hide_index=True)
    with table_tracks:
        st.dataframe(normalized_tables["tracks"], use_container_width=True, hide_index=True)
    with table_credits:
        st.dataframe(normalized_tables["credits"], use_container_width=True, hide_index=True)
    with table_appearances:
        st.dataframe(normalized_tables["appearances"], use_container_width=True, hide_index=True)

    if not artist_rows:
        st.info("No artists have been imported yet. Use the Build Artist Database tab to add your first batch.")
    else:
        st.markdown("#### Combined appearance view")
        search_term = st.text_input("Search by artist, track, DJ, country, or city", key="artist_search")
        filtered_rows = []
        for row in artist_rows:
            haystack = " ".join(
                [
                    clean_text(row.get("artist_name", "")),
                    clean_text(row.get("track_title", "")),
                    clean_text(row.get("dj_name", "")),
                    clean_text(row.get("set_number", "")),
                    clean_text(row.get("set_date", "")),
                    clean_text(row.get("radio_show", "")),
                    clean_text(row.get("location", "")),
                ]
            ).lower()
            if not search_term or search_term.lower() in haystack:
                filtered_rows.append(row)

        if filtered_rows:
            st.dataframe(
                [
                    {
                        "Artist": clean_text(row.get("artist_name", "")),
                        "Track": clean_text(row.get("track_title", "")),
                        "Handle": clean_text(row.get("handle", "")),
                        "URL": clean_text(row.get("url", "")),
                        "Location": clean_text(row.get("location", "")),
                        "Appearances": row.get("appearance_count", 0),
                        "DJ": clean_text(row.get("dj_name", "")),
                        "Set": clean_text(row.get("set_number", "")),
                        "Date": clean_text(row.get("set_date", "")),
                        "Show": clean_text(row.get("radio_show", "")),
                    }
                    for row in filtered_rows
                ],
                use_container_width=True,
                hide_index=True,
            )

            st.markdown("---")
            summary_cols = st.columns(4)
            summary_cols[0].metric("Total track entries", len(filtered_rows))
            summary_cols[1].metric(
                "Artists with location",
                sum(1 for row in filtered_rows if clean_text(row.get("location", ""))),
            )
            summary_cols[2].metric(
                "Unique DJs",
                len({clean_text(row.get("dj_name", "")) for row in filtered_rows if clean_text(row.get("dj_name", ""))}),
            )
            summary_cols[3].metric(
                "Most repeated",
                max((row.get("appearance_count", 0) for row in filtered_rows), default=0),
            )

            st.markdown("#### Location summary")
            location_counts = {}
            for row in filtered_rows:
                location = clean_text(row.get("location", "")) or "Unknown"
                location_counts[location] = location_counts.get(location, 0) + 1

            location_summary = sorted(location_counts.items(), key=lambda item: (-item[1], item[0]))

            col_location = st.columns(1)[0]
            with col_location:
                st.write("**Locations**")
                for location, count in location_summary[:20]:
                    st.write(f"- {location}: {count}")


# =============================================================================
# TAB 4: MANAGE MASTER LIST
# Lets the user view the current master list, add new entries, or bulk-paste
# a whole new batch. Changes are saved to master_reference_list.txt.
# =============================================================================
with tab_manage:
    st.subheader("View & grow your master reference list")
    st.markdown(
        "This is the file the app matches against: `master_reference_list.txt`. "
        "You can add entries here or edit the file directly in any text editor."
    )

    # --- VIEW CURRENT ENTRIES ---
    st.markdown("#### Current entries")
    if master_entries:
        # Show a clean table of all current name/handle pairs
        st.dataframe(
            [{"Name": e["name"], "Handle": e["handle"]} for e in master_entries],
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info("No entries yet. Add some below!")

    st.markdown("---")

    # --- ADD NEW ENTRIES ---
    # Two options: paste a batch of entries, or upload a file
    st.markdown("#### Add new entries")
    st.caption(
        "Paste entries below — one per line, each must include a name and an @ handle. "
        "Format: `Name @handle – optional description`"
    )

    new_entries_upload = st.file_uploader(
        "Upload a file with new entries", type=["txt", "csv"], key="new_entries_upload"
    )
    new_entries_text = st.text_area(
        "Or paste new entries here:",
        height=200,
        placeholder="Fred Jones @fredejones – Dynamics presentation\nJane Smith @jsmith – Power Platform demo\n...",
        key="new_entries_text",
    )

    if new_entries_upload:
        new_entries_text = new_entries_upload.read().decode("utf-8", errors="replace")
        st.text_area("Uploaded content (preview):", new_entries_text[:2000], height=150, disabled=True)

    if st.button("➕ Add to Master List", type="primary", use_container_width=True):
        if not new_entries_text or not new_entries_text.strip():
            st.error("Please provide entries to add.")
        elif "@" not in new_entries_text:
            st.error("No @ handles found. Each entry must include an @ handle (e.g. `@fredejones`).")
        else:
            count = append_to_master_list(new_entries_text)
            if count > 0:
                st.success(f"✅ Added **{count}** entries to the master list. Refresh the page to see them.")
                st.balloons()
            else:
                st.warning("No valid entries found. Each line needs a name and an @ handle.")

    # --- VIEW RAW FILE ---
    # Expandable section to see the raw file contents (for power users)
    with st.expander("🔧 View/edit raw file contents"):
        st.caption(f"File: `{MASTER_LIST_PATH}`")
        raw_content = load_master_list()
        st.text_area(
            "Raw file contents (read-only here — edit the file directly for changes):",
            raw_content if raw_content else "(empty)",
            height=300,
            disabled=True,
            key="raw_master",
        )
