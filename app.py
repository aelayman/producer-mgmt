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
from datetime import datetime
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


def init_artist_database() -> None:
    """Create the artist database tables if they do not already exist and add missing columns for older databases."""
    with get_db_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS artist_appearances (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                artist_name TEXT NOT NULL,
                handle TEXT,
                url TEXT,
                track_title TEXT,
                appearance_count INTEGER NOT NULL DEFAULT 1,
                dj_names TEXT,
                set_names TEXT,
                source_texts TEXT,
                notes TEXT,
                origin_country TEXT,
                origin_city TEXT,
                first_seen TEXT,
                last_seen TEXT,
                UNIQUE(artist_name, track_title)
            )
            """
        )

        existing_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(artist_appearances)").fetchall()
        }
        for column_name in ["origin_country", "origin_city"]:
            if column_name not in existing_columns:
                conn.execute(f"ALTER TABLE artist_appearances ADD COLUMN {column_name} TEXT")

        conn.commit()


def append_unique_value(existing: str | None, new_value: str) -> str:
    """Append a value to a semicolon-separated list without duplicates."""
    if not new_value:
        return existing or ""

    values = [item.strip() for item in (existing or "").split(";") if item.strip()]
    if new_value.strip() not in values:
        values.append(new_value.strip())
    return "; ".join(values)


def load_artist_database() -> list[dict]:
    """Load the artist database from SQLite."""
    init_artist_database()
    with get_db_connection() as conn:
        rows = conn.execute(
            """
            SELECT artist_name, handle, url, track_title, appearance_count, dj_names,
                   set_names, source_texts, notes, origin_country, origin_city,
                   first_seen, last_seen
            FROM artist_appearances
            ORDER BY artist_name, track_title
            """
        ).fetchall()
        return [dict(row) for row in rows]


def append_to_artist_database(
    entries: list[dict],
    dj_name: str = "",
    set_name: str = "",
    origin_country: str = "",
    origin_city: str = "",
) -> int:
    """Upsert parsed artist/tag entries into the SQLite artist database."""
    if not entries:
        return 0

    init_artist_database()
    now = datetime.utcnow().isoformat()

    with get_db_connection() as conn:
        for entry in entries:
            artist_name = (entry.get("artist_name") or "Unknown artist").strip()
            track_title = (entry.get("track_title") or "").strip()
            handle = (entry.get("handle") or "").strip()
            url = (entry.get("url") or "").strip()
            source_text = (entry.get("source_text") or "").strip()
            notes = (entry.get("notes") or "").strip()

            existing = conn.execute(
                "SELECT id, appearance_count, dj_names, set_names, source_texts, notes, origin_country, origin_city FROM artist_appearances WHERE artist_name = ? AND track_title = ?",
                (artist_name, track_title),
            ).fetchone()

            if existing:
                appearance_count = existing["appearance_count"] + 1
                dj_names = append_unique_value(existing["dj_names"], dj_name)
                set_names = append_unique_value(existing["set_names"], set_name)
                source_texts = append_unique_value(existing["source_texts"], source_text)
                notes_value = append_unique_value(existing["notes"], notes) if notes else existing["notes"] or ""
                updated_country = existing["origin_country"] or origin_country
                updated_city = existing["origin_city"] or origin_city
                conn.execute(
                    """
                    UPDATE artist_appearances
                    SET handle = ?, url = ?, appearance_count = ?, dj_names = ?, set_names = ?,
                        source_texts = ?, notes = ?, origin_country = ?, origin_city = ?, last_seen = ?
                    WHERE id = ?
                    """,
                    (
                        handle,
                        url,
                        appearance_count,
                        dj_names,
                        set_names,
                        source_texts,
                        notes_value,
                        updated_country,
                        updated_city,
                        now,
                        existing["id"],
                    ),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO artist_appearances (
                        artist_name, handle, url, track_title, appearance_count, dj_names,
                        set_names, source_texts, notes, origin_country, origin_city, first_seen, last_seen
                    ) VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        artist_name,
                        handle,
                        url,
                        track_title,
                        dj_name or "",
                        set_name or "",
                        source_text or "",
                        notes or "",
                        origin_country or "",
                        origin_city or "",
                        now,
                        now,
                    ),
                )

        conn.commit()
    return len(entries)


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
            "track_title",
            "appearance_count",
            "dj_names",
            "set_names",
            "source_texts",
            "notes",
            "origin_country",
            "origin_city",
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
    Parse a SoundCloud-style line into one artist database row.

    Expected examples:
    - Mor Elian @[morelian](https://soundcloud.com/morelian) - Swerving Mantis
    - Gobekli @[gobekli](https://soundcloud.com/gobekli) - Edfu Texts (Ronan Remix) @[ronan-music](https://soundcloud.com/ronan-music)
    """
    line = line.strip()
    if not line or line.startswith("#"):
        return []

    line_cleaned = re.sub(r"^\d+[\.\)]\s*", "", line).strip()
    if not line_cleaned:
        return []

    pattern = re.compile(r"@\[([^\]]+)\]\((https?://[^)]+)\)|@([A-Za-z0-9._-]+)")
    matches = list(pattern.finditer(line_cleaned))

    dash_match = re.search(r"\s*[–—-]\s*", line_cleaned)
    dash_index = dash_match.start() if dash_match else len(line_cleaned)

    selected_match = None
    for match in matches:
        if match.start() < dash_index:
            selected_match = match
            break
    if selected_match is None and matches:
        selected_match = matches[0]

    if selected_match is not None:
        if selected_match.group(1):
            handle = selected_match.group(1)
            url = selected_match.group(2)
        else:
            handle = selected_match.group(3)
            url = f"https://soundcloud.com/{handle}"
    else:
        handle = ""
        url = ""

    artist_part = line_cleaned[:dash_index].strip() if dash_match else line_cleaned
    artist_name = re.sub(pattern, "", artist_part)
    artist_name = re.sub(r"\s+", " ", artist_name).strip(" -–—")
    artist_name = artist_name or "Unknown artist"

    track_title = ""
    if dash_match:
        track_title = line_cleaned[dash_match.end():].strip()
        track_title = re.sub(pattern, "", track_title)
        track_title = re.sub(r"\s+", " ", track_title).strip(" -–—")

    return [{
        "artist_name": artist_name,
        "handle": handle,
        "url": url,
        "track_title": track_title,
        "source_text": line,
        "notes": "",
    }]


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


def find_best_match(
    name: str, reference_entries: list[dict], threshold: int = 82
) -> dict | None:
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
    best_score = 0
    best_match = None

    name_lower = name.lower().strip()

    for entry in reference_entries:
        ref_name_lower = entry["name"].lower().strip()

        # Strategy 1: Direct character similarity (e.g. "Fred Jones" vs "Fred Jonas" = ~90%)
        score_ratio = fuzz.ratio(name_lower, ref_name_lower)

        # Strategy 2: Sort words first, then compare (handles "Last, First" vs "First Last")
        score_token_sort = fuzz.token_sort_ratio(name_lower, ref_name_lower)

        # Strategy 3: Compare word overlap (handles extra middle names, titles, etc.)
        # Multiplied by 0.95 to slightly penalize this — it can be too generous
        score_token_set = fuzz.token_set_ratio(name_lower, ref_name_lower)

        # Take the best score from all strategies
        score = max(score_ratio, score_token_sort, int(score_token_set * 0.95))

        if score > best_score:
            best_score = score
            best_match = entry

    # Only return a match if it meets our confidence threshold
    if best_score >= threshold:
        return best_match
    return None


# =============================================================================
# MAIN PROCESSING
# Ties together parsing and matching to produce the final output.
# =============================================================================


def process_lists(reference_text: str, input_text: str, threshold: int = 82):
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
    reference_entries = parse_reference_list(reference_text)
    input_entries = parse_input_list(input_text)

    tagged_list = []       # The final numbered output with @ handles inserted
    matched_tags = set()   # Set of unique tags found (auto-deduplicates)
    unmatched = []         # Names we couldn't find a match for

    for i, entry in enumerate(input_entries, 1):
        match = find_best_match(entry["name"], reference_entries, threshold)

        if match:
            # We found a matching @ handle! Now reconstruct the line with the
            # handle inserted between the name and the description.
            # Format goal: "1. Fred Jones @fredejones – Supply Chain Demo"

            # Check what type of dash was used in the original line
            if any(d in entry["full_line"] for d in ["–", "—"]):
                # Split on en-dash or em-dash to separate name from description
                parts = re.split(r"\s*[–—]\s*", entry["full_line"], maxsplit=1)
                tagged_line = f"{i}. {parts[0].strip()} {match['handle']} – {parts[1].strip()}"
            elif "-" in entry["full_line"]:
                # Split on regular hyphen (with spaces around it acting as a dash)
                parts = re.split(r"\s*-\s*", entry["full_line"], maxsplit=1)
                tagged_line = f"{i}. {parts[0].strip()} {match['handle']} – {parts[1].strip()}"
            else:
                # No dash/description — just name + handle
                tagged_line = f"{i}. {entry['name']} {match['handle']}"

            tagged_list.append(tagged_line)
            matched_tags.add(match["handle"])
        else:
            # No match found — still include the line but flag it with a warning
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

    # Show a count of how many entries are currently in the master list
    master_text = load_master_list()
    master_entries = parse_reference_list(master_text)
    st.metric("Entries in master list", len(master_entries))

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
    if master_entries:
        st.success(f"✅ Master list loaded: **{len(master_entries)}** entries with @ handles")
    else:
        st.warning(
            "⚠️ Master list is empty. Go to the **Manage Master List** tab to add entries, "
            "or edit `master_reference_list.txt` directly."
        )

    # --- INPUT: User's new list (WITHOUT @ handles) ---
    st.markdown("#### Paste or upload your list (without @ handles)")
    st.caption("One name per line. Numbering and descriptions after a dash are optional.")

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

    # --- PROCESS BUTTON ---
    if st.button("🔍 Find Tags", type="primary", use_container_width=True):
        if not master_entries:
            st.error("Master list is empty. Add entries in the **Manage Master List** tab first.")
        elif not input_text:
            st.error("Please provide your list of names to look up.")
        else:
            with st.spinner("Matching names..."):
                tagged_list, unique_tags, unmatched = process_lists(
                    master_text, input_text, threshold
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

    st.markdown("#### Import a new setlist batch")
    st.caption("Each batch can be tagged with the DJ and the set/date so it can be tracked over time.")

    db_upload = st.file_uploader("Upload a list of SoundCloud entries", type=["txt", "csv"], key="db_upload")
    db_text = st.text_area(
        "Or paste your list here:",
        height=300,
        placeholder="1. Mor Elian @[morelian](https://soundcloud.com/morelian) - Swerving Mantis\n2. Gobekli @[gobekli](https://soundcloud.com/gobekli) - Edfu Texts (Ronan Remix) @[ronan-music](https://soundcloud.com/ronan-music)",
        key="db_text",
    )
    dj_name = st.text_input("DJ who played this set", value="", key="dj_name")
    set_name = st.text_input("Set / date / notes", value="", key="set_name")
    origin_country = st.text_input("Artist origin country (optional)", value="", key="origin_country")
    origin_city = st.text_input("Artist origin city (optional)", value="", key="origin_city")

    if db_upload:
        db_text = db_upload.read().decode("utf-8", errors="replace")
        st.text_area("Uploaded content (preview):", db_text[:2000], height=150, disabled=True)

    if st.button("💾 Save to artist database", type="primary", use_container_width=True):
        if not db_text or not db_text.strip():
            st.error("Please provide some entries to save.")
        else:
            parsed_entries = []
            for line in db_text.splitlines():
                parsed_entries.extend(parse_soundcloud_line(line))

            if not parsed_entries:
                st.warning("No artist/tag entries were found. Make sure each line contains an @ handle or SoundCloud link.")
            else:
                count = append_to_artist_database(
                    parsed_entries,
                    dj_name=dj_name,
                    set_name=set_name,
                    origin_country=origin_country,
                    origin_city=origin_city,
                )
                st.success(f"✅ Saved **{count}** entries to the artist database.")
                st.dataframe(load_artist_database(), use_container_width=True, hide_index=True)

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
    if not artist_rows:
        st.info("No artists have been imported yet. Use the Build Artist Database tab to add your first batch.")
    else:
        search_term = st.text_input("Search by artist, track, DJ, country, or city", key="artist_search")
        filtered_rows = []
        for row in artist_rows:
            haystack = " ".join(
                [
                    row.get("artist_name", ""),
                    row.get("track_title", ""),
                    row.get("dj_names", ""),
                    row.get("set_names", ""),
                    row.get("origin_country", ""),
                    row.get("origin_city", ""),
                ]
            ).lower()
            if not search_term or search_term.lower() in haystack:
                filtered_rows.append(row)

        if filtered_rows:
            st.dataframe(
                [
                    {
                        "Artist": row.get("artist_name", ""),
                        "Track": row.get("track_title", ""),
                        "Handle": row.get("handle", ""),
                        "URL": row.get("url", ""),
                        "Appearances": row.get("appearance_count", 0),
                        "Country": row.get("origin_country", ""),
                        "City": row.get("origin_city", ""),
                        "DJs": row.get("dj_names", ""),
                        "Sets": row.get("set_names", ""),
                    }
                    for row in filtered_rows
                ],
                use_container_width=True,
                hide_index=True,
            )

            st.markdown("---")
            summary_cols = st.columns(4)
            summary_cols[0].metric("Total artist/track rows", len(filtered_rows))
            summary_cols[1].metric(
                "Artists with country",
                sum(1 for row in filtered_rows if row.get("origin_country", "")),
            )
            summary_cols[2].metric(
                "Artists with city",
                sum(1 for row in filtered_rows if row.get("origin_city", "")),
            )
            summary_cols[3].metric(
                "Most repeated",
                max((row.get("appearance_count", 0) for row in filtered_rows), default=0),
            )

            st.markdown("#### Geography summary")
            country_counts = {}
            city_counts = {}
            for row in filtered_rows:
                country = (row.get("origin_country") or "Unknown").strip()
                city = (row.get("origin_city") or "Unknown").strip()
                country_counts[country] = country_counts.get(country, 0) + 1
                city_counts[city] = city_counts.get(city, 0) + 1

            country_summary = sorted(country_counts.items(), key=lambda item: (-item[1], item[0]))
            city_summary = sorted(city_counts.items(), key=lambda item: (-item[1], item[0]))

            col_country, col_city = st.columns(2)
            with col_country:
                st.write("**Countries**")
                for country, count in country_summary[:15]:
                    st.write(f"- {country}: {count}")
            with col_city:
                st.write("**Cities**")
                for city, count in city_summary[:15]:
                    st.write(f"- {city}: {count}")


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
