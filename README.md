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

### Tab 2: Build Artist Database

- Import SoundCloud-style track lines with DJ, set number, date, and show context
- Keep repeated appearances when the same track is played in different sets
- Store each track once, link it to every credited artist, and record each set appearance separately
- Use **Dedupe database** to merge duplicate records; this also runs automatically when the app loads
- Review conflicting handles in the Explore Database tab

### Tab 3: Explore Artist Database

- Search and summarize artists, tracks, DJs, sets, and locations
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
