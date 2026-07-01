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

The app has two tabs:

### Tab 1: Find Tags (main function)

1. The app automatically loads your master list from `master_reference_list.txt` (no need to paste it each time)
2. Paste or upload your new list of names (no @ handles needed):
   ```
   1. Fred Jones – Supply Chain Demo
   2. Jane Smith – Finance Overview
   ```
3. Click **Find Tags** — the tool returns:
   - Your list numbered with the matching @ tag inserted after each name
   - A deduplicated, unnumbered list of all matched @ handles
   - Any unmatched names flagged with ⚠️

### Tab 2: Manage Master List

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
