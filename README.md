# Personal Finance Tracker

A local Streamlit + SQLite personal finance manager for double-entry style ledger tracking, staged statement imports, expense drill-down, planning, data-health repair, and lightweight investment performance.

## Run the App

From the repo root:

```powershell
.\PersonalFinApp.bat
```

Or run Streamlit directly:

```powershell
.\.venv\Scripts\streamlit.exe run system_v2.py
```

`system_v2.py` is the supported entrypoint.

## Current Features

- Dashboard focused on net worth, cash flow, expense pressure, and data-quality warnings.
- Expense analysis by category, sub-category, merchant, recurring drain, month-over-month change, and transaction drill-down.
- Investment performance using reviewed trades plus manual price snapshots, FIFO P&L, ROI, quality warnings, and ledger reconciliation.
- Manual transaction entry with canonical category and sub-category controls.
- Planning screen for category budgets, sub-category pressure, repeated overspend/underspend, and budget review suggestions.
- Staged imports for HSBC credit-card PDFs and TCB screenshots.
- Inline import review with combined `Category / Sub-category` classification, batch posting, blocker summaries, and common date fixes.
- Data Health controls for duplicate audit, safety backup, explicit repair, and audit/repair history.

## Data Files

- Active database: `finance.db`
- Source data folders:
  - HSBC PDFs: `raw_data/hsbc`
  - TCB images: `raw_data/tcb/images`
- Safety backups: `backups/`
- Legacy/secondary database path: `data/finance.db`

Secondary reconciliation is explicit. The app no longer silently imports from `data/finance.db` on startup.

## Import Setup

Install Python dependencies:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

TCB image OCR uses local Tesseract. If it is not on `PATH`, set the executable in `Imports > Settings`.

Example path:

```text
C:\Users\Admin\AppData\Local\Programs\Tesseract-OCR\tesseract.exe
```

HSBC PDFs can remain password-protected. The password is configured in `Imports > Settings`.

## Review and Posting Model

Imported statement rows first land in staged tables, not directly in the ledger.

Recommended flow:

1. Open `Imports`.
2. Scan source folders.
3. Review blockers and apply safe date fixes if needed.
4. Edit rows inline.
5. Use `Post All Visible Ready` for the filtered batch.
6. Use `Review & Edit > Data Health and Repair` for ledger-level audit and cleanup.

Fallback classification `Others / Other expense` is postable, but still marked as analytically risky and excluded from clean expense analytics by default.

## Recovery

Before duplicate repair, the app creates a database backup automatically. You can also create one manually from `Review & Edit > Data Health and Repair`.

To restore:

1. Close the Streamlit app.
2. Copy the desired `.db` file from `backups/`.
3. Replace `finance.db`.
4. Start the app again.

## Tests

Run the full test suite:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

The suite covers imports, parser fixtures, posting guards, chart-key guards, expense drill-down, planning analysis, investment FIFO performance, and data-health repair.

## Project Structure

- `system_v2.py`: supported Streamlit entrypoint.
- `finance_app/constants.py`: canonical categories, accounts, source folders, and defaults.
- `finance_app/importers.py`: HSBC PDF parsing, TCB OCR parsing, source scanning, and posting suggestions.
- `finance_app/repository.py`: SQLite schema, repository methods, import staging, posting, investments, and data-health repair.
- `finance_app/ui.py`: Streamlit navigation, dashboards, imports, review, planning, expenses, and investments.
- `tests/`: parser, workflow, repository, and UI guard tests.
