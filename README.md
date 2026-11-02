# Identify Existing Tags

A simple web tool that matches names from your list against a **master reference list** to find existing @ handles.

## Setup

```bash
pip install -r requirements.txt
```

## Run

```bash
streamlit run app.py
```

The app opens in your browser at `http://localhost:8501`.

## How It Works

The app has four tabs:

### Tab 1: Find Tags (main function)

1. The app loads artist names and handles directly from the SQLite artist database.
2. Paste or upload your new list of names (no @ handles needed):
   ```
   1. Fred Jones – Supply Chain Demo
   2. Jane Smith – Finance Overview
   ```
3. Click **Find Tags** — the tool returns:
   - Your list numbered with the matching @ tag inserted after each name
   - A deduplicated, unnumbered list of all matched @ handles
   - Artists found without handles flagged with `⚠️ handle needed`
   - Unknown artists flagged with `⚠️ no match found`

### Tab 2: Artist Lookup

- Search for one artist with typo-tolerant matching
- Review ranked candidates and match confidence
- Open the stored SoundCloud profile
- See every track and DJ set where the artist appears

### Tab 3: Build Artist Database

- Import SoundCloud-style track lines with DJ, set number, date, and show context
- Preview parsed lines and warnings before saving an import
- Undo the latest confirmed import without deleting shared artists or tracks
- Keep repeated appearances when the same track is played in different sets
- Store each track once, link it to every credited artist, and record each set appearance separately
- Use **Dedupe database** to merge duplicate records; this also runs automatically when the app loads
- Review conflicting handles in the Explore Database tab

The database uses separate tables for DJs, sets, artists, unique tracks, artist credits, and set appearances. A track is stored once in the catalog and can appear in many DJ sets.

The **Database health and backups** section in the Build Artist Database tab shows record counts, checks for broken links, and creates a downloadable SQLite backup.

Imports made after the undo feature was added can be reversed from the same section. Older imports are not assigned an undo record and are left unchanged.

### Tab 3: Explore Artist Database

- Search and summarize artists, tracks, DJs, sets, and locations
- Browse DJ sets by DJ, radio show, set number, or date and inspect an individual tracklist
- Edit DJ, set number, date, and radio show metadata without re-importing tracks
- Review conflicting handles preserved during deduplication

### Tab 4: Manage Master List

This is where you view and grow your master reference list over time.

- **View** all current entries in a searchable table
- **Add** new entries by pasting or uploading (one per line, must include an @ handle)
- **View raw file** contents in an expandable section

### Master Reference List (`master_reference_list.txt`)

This file is the heart of the tool — it stores all known names and their @ handles.

**Format** — one entry per line:
```
Fred Jones @fredejones – Dynamics presentation
Jane Smith @jsmith – Power Platform demo
```

**Two ways to add entries:**
1. Edit `master_reference_list.txt` directly in any text editor
2. Use the **Manage Master List** tab in the app

## Match Sensitivity

Use the sidebar slider to adjust how strict the matching is (default 82/100). Higher = stricter. If you're getting false matches, increase it. If names aren't matching when they should, decrease it.

## Development checks

Run the database behavior tests before making changes:

```bash
python -m unittest discover -s tests -v
```
