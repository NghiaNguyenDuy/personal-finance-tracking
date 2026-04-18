# Personal Finance Tracker

A local Streamlit + SQLite personal finance manager for recording double-entry style transactions, reviewing budgets, editing accounts, and exploring reporting dashboards.

## Supported App Entry Point

Run the supported app from the repo root:

```powershell
streamlit run system_v2.py
```

The app now stores active data in `data/finance.db` and keeps backups in `backups/`.

## Features

- Validated transaction entry with category and sub-category rules
- Explicit account metadata with account management in the UI
- Budget management with persistent monthly category limits
- Shared reporting filters in the sidebar
- CSV export for filtered transaction reviews
- Automatic backup of legacy data before first migration or import
- Manual backup creation from the System tab

## Setup

1. Create and activate a virtual environment.
2. Install dependencies:

```powershell
pip install -r requirements.txt
```

3. Start the app:

```powershell
streamlit run system_v2.py
```

## Data and Backups

- Active database: `data/finance.db`
- Legacy import source: `finance.db` if it exists in the repo root
- Automatic safety backups: `backups/`

On first run, the app creates a fresh database with the current schema. If a legacy `finance.db` exists, the app copies its data into `data/finance.db` and creates a safety backup before doing so.

## Recovery

To restore from backup:

1. Close the app.
2. Copy the desired file from `backups/` to `data/finance.db`.
3. Start the app again with `streamlit run system_v2.py`.

## Tests

Run the automated tests from the repo root:

```powershell
python -m unittest discover -s tests -v
```

## Project Structure

- `system_v2.py`: supported Streamlit entrypoint
- `finance_app/`: database, validation, reporting, and UI modules
- `system_v0.py`, `system_v1.py`, `system_v0.ipynb`: legacy references kept for history only

## Notes

- The app is designed for a single local user.
- Currency display remains in VND.
- Statement parsing and auto-import are intentionally out of scope for this version.
