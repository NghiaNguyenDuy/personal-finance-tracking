from __future__ import annotations

import json
import shutil
import sqlite3
from collections import Counter
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterable

import pandas as pd

from .constants import ACCOUNT_OPTIONS, CATEGORY_MAP, DB_PATH, DEFAULT_SETTINGS, SECONDARY_DB_PATH

FALLBACK_CATEGORY = "Others"
FALLBACK_SUBCATEGORY = "Other expense"
LOW_CONFIDENCE_THRESHOLD = 0.8


def utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat(sep=" ")


def classify_account_name(account_name: str) -> str:
    account_name = str(account_name or "")
    if account_name.startswith("Asset:"):
        return "Asset"
    if account_name.startswith("Liability:"):
        return "Liability"
    if account_name.startswith("Equity:"):
        return "Equity"
    if account_name.startswith("Income:"):
        return "Income"
    if account_name == "Cash":
        return "Asset"
    if account_name == "Expense":
        return "Expense"
    return "Expense"


class FinanceRepository:
    def __init__(
        self,
        db_path: str | Path = DB_PATH,
        secondary_db_paths: Iterable[str | Path] | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        if secondary_db_paths is None:
            secondary_db_paths = ()
        self.secondary_db_paths = [Path(path) for path in secondary_db_paths]
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.init_db()
        self.reconciliation_summary = self.reconcile_secondary_databases()

    @contextmanager
    def transaction(self):
        try:
            yield self.conn
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def init_db(self) -> None:
        with self.transaction() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS transactions (
                    id INTEGER PRIMARY KEY,
                    date TEXT,
                    description TEXT,
                    category TEXT,
                    debit_account TEXT,
                    credit_account TEXT,
                    amount REAL,
                    subcategory TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS budgets (
                    category TEXT PRIMARY KEY,
                    monthly_limit REAL NOT NULL DEFAULT 0
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS app_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS import_batches (
                    id INTEGER PRIMARY KEY,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    status TEXT NOT NULL,
                    notes TEXT DEFAULT ''
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS source_files (
                    id INTEGER PRIMARY KEY,
                    batch_id INTEGER,
                    source_type TEXT NOT NULL,
                    file_name TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    file_hash TEXT NOT NULL,
                    statement_month TEXT,
                    parse_status TEXT NOT NULL DEFAULT 'pending',
                    parse_notes TEXT DEFAULT '',
                    extraction_engine TEXT DEFAULT '',
                    raw_metadata TEXT DEFAULT '{}',
                    last_processed_at TEXT NOT NULL,
                    UNIQUE(source_type, file_hash)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS statement_rows (
                    id INTEGER PRIMARY KEY,
                    source_file_id INTEGER NOT NULL,
                    source_type TEXT NOT NULL,
                    row_fingerprint TEXT NOT NULL,
                    row_index INTEGER NOT NULL,
                    statement_month TEXT,
                    transaction_date TEXT,
                    post_date TEXT,
                    event_time TEXT,
                    description TEXT NOT NULL,
                    merchant TEXT DEFAULT '',
                    amount REAL NOT NULL,
                    currency TEXT NOT NULL DEFAULT 'VND',
                    direction TEXT NOT NULL,
                    running_balance REAL,
                    account_ref TEXT DEFAULT '',
                    row_type TEXT NOT NULL DEFAULT 'purchase',
                    review_status TEXT NOT NULL DEFAULT 'pending',
                    confidence REAL NOT NULL DEFAULT 0,
                    parse_notes TEXT DEFAULT '',
                    raw_text TEXT DEFAULT '',
                    category TEXT DEFAULT 'Others',
                    subcategory TEXT DEFAULT 'Other expense',
                    debit_account TEXT DEFAULT 'Expense',
                    credit_account TEXT DEFAULT 'Cash',
                    posted_transaction_id INTEGER,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(source_file_id, row_fingerprint)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS posted_links (
                    statement_row_id INTEGER PRIMARY KEY,
                    transaction_id INTEGER NOT NULL UNIQUE,
                    posted_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS merchant_rules (
                    keyword TEXT PRIMARY KEY,
                    category TEXT NOT NULL,
                    subcategory TEXT NOT NULL,
                    debit_account TEXT NOT NULL,
                    credit_account TEXT NOT NULL,
                    use_count INTEGER NOT NULL DEFAULT 1,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS legacy_category_mappings (
                    source_category TEXT NOT NULL,
                    source_subcategory TEXT NOT NULL DEFAULT '',
                    target_category TEXT NOT NULL,
                    target_subcategory TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (source_category, source_subcategory)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS investment_trades (
                    id INTEGER PRIMARY KEY,
                    transaction_id INTEGER UNIQUE,
                    trade_date TEXT NOT NULL,
                    action TEXT NOT NULL,
                    ticker TEXT NOT NULL,
                    quantity REAL NOT NULL DEFAULT 0,
                    amount REAL NOT NULL DEFAULT 0,
                    fees REAL NOT NULL DEFAULT 0,
                    currency TEXT NOT NULL DEFAULT 'VND',
                    parse_confidence REAL NOT NULL DEFAULT 0,
                    review_status TEXT NOT NULL DEFAULT 'needs_review',
                    notes TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS investment_price_snapshots (
                    id INTEGER PRIMARY KEY,
                    ticker TEXT NOT NULL,
                    price_date TEXT NOT NULL,
                    price REAL NOT NULL,
                    currency TEXT NOT NULL DEFAULT 'VND',
                    notes TEXT DEFAULT '',
                    updated_at TEXT NOT NULL,
                    UNIQUE(ticker, price_date)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS data_quality_runs (
                    id INTEGER PRIMARY KEY,
                    run_type TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    status TEXT NOT NULL,
                    notes TEXT DEFAULT '',
                    details_json TEXT DEFAULT '{}'
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS data_quality_findings (
                    id INTEGER PRIMARY KEY,
                    run_id INTEGER NOT NULL,
                    finding_type TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    transaction_ids TEXT DEFAULT '',
                    finding_key TEXT DEFAULT '',
                    row_count INTEGER NOT NULL DEFAULT 0,
                    amount REAL NOT NULL DEFAULT 0,
                    details_json TEXT DEFAULT '{}'
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS ledger_repair_actions (
                    id INTEGER PRIMARY KEY,
                    action_type TEXT NOT NULL,
                    backup_path TEXT NOT NULL,
                    affected_transaction_ids TEXT NOT NULL,
                    affected_count INTEGER NOT NULL,
                    notes TEXT DEFAULT '',
                    created_at TEXT NOT NULL
                )
                """
            )
            columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(transactions)").fetchall()
            }
            if "subcategory" not in columns:
                conn.execute("ALTER TABLE transactions ADD COLUMN subcategory TEXT")

        for key, value in DEFAULT_SETTINGS.items():
            self.upsert_setting(key, value, commit=False)
        self.conn.commit()

    @staticmethod
    def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table_name,),
        ).fetchone()
        return row is not None

    @staticmethod
    def _normalize_subcategory(value: str | None) -> str:
        return str(value or "").strip() or "Other expense"

    @classmethod
    def _transaction_signature(cls, row: sqlite3.Row | tuple) -> tuple:
        if isinstance(row, sqlite3.Row):
            return (
                str(row["date"] or ""),
                str(row["description"] or ""),
                str(row["category"] or ""),
                cls._normalize_subcategory(row["subcategory"]),
                str(row["debit_account"] or ""),
                str(row["credit_account"] or ""),
                float(row["amount"] or 0),
            )
        return (
            str(row[0] or ""),
            str(row[1] or ""),
            str(row[2] or ""),
            cls._normalize_subcategory(row[3]),
            str(row[4] or ""),
            str(row[5] or ""),
            float(row[6] or 0),
        )

    @staticmethod
    def _transaction_identity(row: sqlite3.Row | tuple) -> tuple:
        if isinstance(row, sqlite3.Row):
            return (
                str(row["date"] or ""),
                str(row["description"] or ""),
                str(row["debit_account"] or ""),
                str(row["credit_account"] or ""),
                float(row["amount"] or 0),
            )
        return (
            str(row[0] or ""),
            str(row[1] or ""),
            str(row[4] or ""),
            str(row[5] or ""),
            float(row[6] or 0),
        )

    def reconcile_secondary_databases(self) -> dict[str, int]:
        summary = {
            "databases_checked": 0,
            "inserted_transactions": 0,
            "updated_subcategories": 0,
            "upserted_budgets": 0,
        }

        for secondary_path in self.secondary_db_paths:
            secondary_path = Path(secondary_path)
            if not secondary_path.exists():
                continue
            if secondary_path.resolve() == self.db_path.resolve():
                continue

            summary["databases_checked"] += 1
            secondary_conn = sqlite3.connect(secondary_path)
            secondary_conn.row_factory = sqlite3.Row
            try:
                if not self._table_exists(secondary_conn, "transactions"):
                    continue

                secondary_rows = secondary_conn.execute(
                    """
                    SELECT date, description, category, subcategory, debit_account, credit_account, amount
                    FROM transactions
                    ORDER BY date, id
                    """
                ).fetchall()

                with self.transaction() as conn:
                    for row in secondary_rows:
                        subcategory = str(row["subcategory"] or "").strip()
                        if not subcategory:
                            continue
                        cursor = conn.execute(
                            """
                            UPDATE transactions
                            SET subcategory = ?
                            WHERE date = ?
                              AND COALESCE(description, '') = ?
                              AND COALESCE(category, '') = ?
                              AND COALESCE(debit_account, '') = ?
                              AND COALESCE(credit_account, '') = ?
                              AND COALESCE(amount, 0) = ?
                              AND COALESCE(subcategory, '') = ''
                            """,
                            (
                                subcategory,
                                str(row["date"] or ""),
                                str(row["description"] or ""),
                                str(row["category"] or ""),
                                str(row["debit_account"] or ""),
                                str(row["credit_account"] or ""),
                                float(row["amount"] or 0),
                            ),
                        )
                        summary["updated_subcategories"] += int(cursor.rowcount or 0)

                    primary_rows = conn.execute(
                        """
                        SELECT date, description, category, subcategory, debit_account, credit_account, amount
                        FROM transactions
                        """
                    ).fetchall()
                    primary_identities = Counter(self._transaction_identity(row) for row in primary_rows)
                    secondary_identities = Counter(self._transaction_identity(row) for row in secondary_rows)
                    missing_identities = secondary_identities - primary_identities

                    inserted = 0
                    for row in secondary_rows:
                        identity = self._transaction_identity(row)
                        if missing_identities[identity] <= 0:
                            continue
                        conn.execute(
                            """
                            INSERT INTO transactions (
                                date, description, category, subcategory, debit_account, credit_account, amount
                            )
                            VALUES (?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                row[0],
                                row[1],
                                row[2],
                                self._normalize_subcategory(row[3]),
                                row[4],
                                row[5],
                                float(row[6]),
                            ),
                        )
                        missing_identities[identity] -= 1
                        inserted += 1
                    summary["inserted_transactions"] += inserted

                    if self._table_exists(secondary_conn, "budgets"):
                        budget_rows = secondary_conn.execute(
                            "SELECT category, monthly_limit FROM budgets"
                        ).fetchall()
                        for budget_row in budget_rows:
                            conn.execute(
                                """
                                INSERT INTO budgets (category, monthly_limit)
                                VALUES (?, ?)
                                ON CONFLICT(category) DO UPDATE SET monthly_limit = excluded.monthly_limit
                                """,
                                (budget_row["category"], float(budget_row["monthly_limit"] or 0)),
                            )
                        summary["upserted_budgets"] += len(budget_rows)
            finally:
                secondary_conn.close()

        return summary

    def close(self) -> None:
        self.conn.close()

    def upsert_setting(self, key: str, value: str, commit: bool = True) -> None:
        self.conn.execute(
            """
            INSERT INTO app_settings (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """,
            (key, str(value), utc_now()),
        )
        if commit:
            self.conn.commit()

    def get_settings(self) -> dict[str, str]:
        rows = self.conn.execute("SELECT key, value FROM app_settings").fetchall()
        settings = {row["key"]: row["value"] for row in rows}
        for key, value in DEFAULT_SETTINGS.items():
            settings.setdefault(key, value)
        return settings

    @staticmethod
    def valid_subcategories_for_category(category: str) -> list[str]:
        category = str(category or "").strip()
        return list(CATEGORY_MAP.get(category, CATEGORY_MAP[FALLBACK_CATEGORY]))

    @classmethod
    def normalize_statement_category_subcategory(cls, category: str, subcategory: str) -> tuple[str, str]:
        normalized_category = str(category or "").strip() or FALLBACK_CATEGORY
        if normalized_category not in CATEGORY_MAP:
            normalized_category = FALLBACK_CATEGORY

        valid_subcategories = cls.valid_subcategories_for_category(normalized_category)
        normalized_subcategory = str(subcategory or "").strip()
        if normalized_subcategory not in valid_subcategories:
            normalized_subcategory = valid_subcategories[0]
        return normalized_category, normalized_subcategory

    @classmethod
    def validate_statement_classification(cls, category: str, subcategory: str) -> list[str]:
        errors: list[str] = []
        normalized_category = str(category or "").strip()
        normalized_subcategory = str(subcategory or "").strip()

        if normalized_category not in CATEGORY_MAP:
            errors.append("Category is invalid.")
            return errors

        if normalized_subcategory not in CATEGORY_MAP[normalized_category]:
            errors.append("Sub-category must belong to the selected category.")
        return errors

    @staticmethod
    def _is_valid_transaction_date(value: str) -> bool:
        try:
            datetime.strptime(str(value or "").strip(), "%Y-%m-%d")
        except ValueError:
            return False
        return True

    @staticmethod
    def _is_valid_amount(value: float | int | str) -> bool:
        try:
            return float(value) > 0
        except (TypeError, ValueError):
            return False

    @classmethod
    def _statement_row_validation_errors(cls, row: sqlite3.Row | dict) -> list[str]:
        getter = row.get if isinstance(row, dict) else row.__getitem__
        category = getter("category") if isinstance(row, dict) else row["category"]
        subcategory = getter("subcategory") if isinstance(row, dict) else row["subcategory"]
        debit_account = getter("debit_account") if isinstance(row, dict) else row["debit_account"]
        credit_account = getter("credit_account") if isinstance(row, dict) else row["credit_account"]
        transaction_date = getter("transaction_date") if isinstance(row, dict) else row["transaction_date"]
        post_date = getter("post_date") if isinstance(row, dict) else row["post_date"]
        amount = getter("amount") if isinstance(row, dict) else row["amount"]
        confidence = getter("confidence") if isinstance(row, dict) else row["confidence"]

        errors = cls.validate_statement_classification(category, subcategory)
        if not debit_account or str(debit_account).strip() not in ACCOUNT_OPTIONS:
            errors.append("Debit account is invalid.")
        if not credit_account or str(credit_account).strip() not in ACCOUNT_OPTIONS:
            errors.append("Credit account is invalid.")
        effective_date = str(transaction_date or post_date or "").strip()
        if not cls._is_valid_transaction_date(effective_date):
            errors.append("Transaction date is invalid.")
        if not cls._is_valid_amount(amount):
            errors.append("Amount must be greater than zero.")
        try:
            numeric_confidence = float(confidence)
        except (TypeError, ValueError):
            numeric_confidence = 0
        if numeric_confidence < LOW_CONFIDENCE_THRESHOLD:
            errors.append("Confidence is below the review threshold.")
        return errors

    @classmethod
    def _statement_row_state(cls, row: sqlite3.Row | dict) -> str:
        getter = row.get if isinstance(row, dict) else row.__getitem__
        review_status = str(getter("review_status") if isinstance(row, dict) else row["review_status"] or "")
        if review_status == "posted":
            return "posted"
        if review_status == "ignored":
            return "ignored"
        if cls._statement_row_validation_errors(row):
            return "needs_review"
        return "ready_to_post"

    def get_setting(self, key: str, default: str = "") -> str:
        return self.get_settings().get(key, default)

    def get_ledger(self) -> pd.DataFrame:
        return pd.read_sql_query("SELECT * FROM transactions ORDER BY date DESC, id DESC", self.conn)

    def get_budgets(self) -> pd.DataFrame:
        return pd.read_sql_query(
            "SELECT category, monthly_limit FROM budgets ORDER BY category",
            self.conn,
        )

    def get_legacy_category_mappings_df(self) -> pd.DataFrame:
        return pd.read_sql_query(
            """
            SELECT source_category, source_subcategory, target_category, target_subcategory, updated_at
            FROM legacy_category_mappings
            ORDER BY source_category, source_subcategory
            """,
            self.conn,
        )

    def upsert_legacy_category_mapping(
        self,
        source_category: str,
        source_subcategory: str,
        target_category: str,
        target_subcategory: str,
    ) -> None:
        errors = self.validate_statement_classification(target_category, target_subcategory)
        if errors:
            raise ValueError(" ".join(errors))
        self.conn.execute(
            """
            INSERT INTO legacy_category_mappings (
                source_category, source_subcategory, target_category, target_subcategory, updated_at
            )
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(source_category, source_subcategory) DO UPDATE SET
                target_category = excluded.target_category,
                target_subcategory = excluded.target_subcategory,
                updated_at = excluded.updated_at
            """,
            (
                str(source_category or "").strip(),
                str(source_subcategory or "").strip(),
                target_category,
                target_subcategory,
                utc_now(),
            ),
        )
        self.conn.commit()

    def apply_legacy_category_mapping(
        self,
        source_category: str,
        source_subcategory: str,
        target_category: str,
        target_subcategory: str,
    ) -> int:
        errors = self.validate_statement_classification(target_category, target_subcategory)
        if errors:
            raise ValueError(" ".join(errors))
        cursor = self.conn.execute(
            """
            UPDATE transactions
            SET category = ?, subcategory = ?
            WHERE COALESCE(category, '') = ?
              AND COALESCE(subcategory, '') = ?
            """,
            (
                target_category,
                target_subcategory,
                str(source_category or "").strip(),
                str(source_subcategory or "").strip(),
            ),
        )
        self.conn.commit()
        return int(cursor.rowcount or 0)

    def save_budget(self, category: str, monthly_limit: float) -> None:
        self.conn.execute(
            """
            INSERT INTO budgets (category, monthly_limit)
            VALUES (?, ?)
            ON CONFLICT(category) DO UPDATE SET monthly_limit = excluded.monthly_limit
            """,
            (category, float(monthly_limit)),
        )
        self.conn.commit()

    def record_transaction(
        self,
        tx_date: str,
        description: str,
        category: str,
        subcategory: str,
        debit_account: str,
        credit_account: str,
        amount: float,
    ) -> int:
        cursor = self.conn.execute(
            """
            INSERT INTO transactions (date, description, category, subcategory, debit_account, credit_account, amount)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (tx_date, description, category, subcategory, debit_account, credit_account, float(amount)),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def update_transaction(
        self,
        tx_id: int,
        tx_date: str,
        description: str,
        category: str,
        subcategory: str,
        debit_account: str,
        credit_account: str,
        amount: float,
    ) -> None:
        self.conn.execute(
            """
            UPDATE transactions
            SET date = ?, description = ?, category = ?, subcategory = ?, debit_account = ?, credit_account = ?, amount = ?
            WHERE id = ?
            """,
            (tx_date, description, category, subcategory, debit_account, credit_account, float(amount), int(tx_id)),
        )
        self.conn.commit()

    def delete_transaction(self, tx_id: int) -> None:
        self.conn.execute("DELETE FROM transactions WHERE id = ?", (int(tx_id),))
        self.conn.commit()

    def build_account_type_map(self, ledger_df: pd.DataFrame) -> dict[str, str]:
        account_types: dict[str, str] = {}
        if ledger_df.empty:
            return account_types

        accounts = pd.concat(
            [ledger_df["debit_account"], ledger_df["credit_account"]],
            ignore_index=True,
        ).dropna().unique()
        for account in accounts:
            account = str(account)
            base_type = classify_account_name(account)
            if account in ACCOUNT_OPTIONS or account.startswith(("Asset:", "Liability:", "Equity:", "Income:")):
                account_types[account] = base_type
                continue

            debit_rows = ledger_df[ledger_df["debit_account"] == account]
            credit_rows = ledger_df[ledger_df["credit_account"] == account]
            income_side = (
                (not credit_rows.empty and credit_rows["category"].fillna("").eq("Income").any())
                or (not debit_rows.empty and debit_rows["category"].fillna("").eq("Income").any())
            )
            expense_side = (
                (not debit_rows.empty and ~debit_rows["category"].fillna("").eq("Income")).any()
                or (not credit_rows.empty and ~credit_rows["category"].fillna("").eq("Income")).any()
            )
            if income_side and not expense_side:
                account_types[account] = "Income"
            elif expense_side and not income_side:
                account_types[account] = "Expense"
            else:
                account_types[account] = base_type
        return account_types

    def get_account_balance(self) -> pd.DataFrame:
        ledger_df = self.get_ledger()
        if ledger_df.empty:
            return pd.DataFrame({"account": ACCOUNT_OPTIONS, "balance": [0.0] * len(ACCOUNT_OPTIONS)})

        debit = ledger_df.groupby("debit_account")["amount"].sum()
        credit = ledger_df.groupby("credit_account")["amount"].sum()
        for key in ACCOUNT_OPTIONS:
            if key not in debit.index:
                debit[key] = 0
            if key not in credit.index:
                credit[key] = 0
        balance = (debit.sort_index() - credit.sort_index()).fillna(0).to_frame("balance").reset_index()
        first_col = balance.columns[0]
        if first_col != "account":
            balance = balance.rename(columns={first_col: "account"})
        return balance[["account", "balance"]]

    def create_database_backup(self, label: str = "manual") -> str:
        self.conn.commit()
        backup_dir = self.db_path.parent / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        safe_label = "".join(character if character.isalnum() else "-" for character in str(label or "manual")).strip("-")
        safe_label = safe_label or "manual"
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_path = backup_dir / f"{self.db_path.stem}_{timestamp}_{safe_label}.db"
        shutil.copy2(self.db_path, backup_path)
        return str(backup_path)

    def get_data_health_summary(self) -> dict[str, object]:
        def table_count(table_name: str) -> int:
            if not self._table_exists(self.conn, table_name):
                return 0
            return int(self.conn.execute(f"SELECT COUNT(*) AS count FROM {table_name}").fetchone()["count"])

        latest_row = self.conn.execute("SELECT MAX(date) AS latest_date FROM transactions").fetchone()
        backup_dir = self.db_path.parent / "backups"
        backup_files = sorted(backup_dir.glob("*.db"), key=lambda path: path.stat().st_mtime, reverse=True) if backup_dir.exists() else []
        duplicates_df = self.get_duplicate_transaction_groups()
        return {
            "active_db_path": str(self.db_path.resolve()),
            "secondary_db_paths": [str(path.resolve()) for path in self.secondary_db_paths],
            "transactions": table_count("transactions"),
            "statement_rows": table_count("statement_rows"),
            "posted_links": table_count("posted_links"),
            "source_files": table_count("source_files"),
            "import_batches": table_count("import_batches"),
            "investment_trades": table_count("investment_trades"),
            "latest_transaction_date": latest_row["latest_date"] if latest_row else "",
            "duplicate_groups": int(len(duplicates_df)),
            "duplicate_extra_rows": int(duplicates_df["duplicate_count"].sub(1).sum()) if not duplicates_df.empty else 0,
            "latest_backup_path": str(backup_files[0]) if backup_files else "",
            "latest_backup_count": len(backup_files),
            "reconciliation_summary": dict(self.reconciliation_summary),
        }

    def get_duplicate_transaction_groups(self) -> pd.DataFrame:
        columns = [
            "group_id",
            "date",
            "description",
            "debit_account",
            "credit_account",
            "amount",
            "duplicate_count",
            "keep_id",
            "duplicate_ids",
            "duplicate_ids_json",
            "category_set",
            "subcategory_set",
            "total_amount",
        ]
        ledger_df = self.get_ledger()
        if ledger_df.empty:
            return pd.DataFrame(columns=columns)

        normalized = ledger_df.copy()
        for column in ["date", "description", "debit_account", "credit_account", "category", "subcategory"]:
            normalized[column] = normalized[column].fillna("").astype(str)
        normalized["amount"] = normalized["amount"].fillna(0).astype(float).round(2)
        identity_columns = ["date", "description", "debit_account", "credit_account", "amount"]
        rows: list[dict[str, object]] = []
        group_id = 0
        for identity, group in normalized.groupby(identity_columns, dropna=False, sort=False):
            if len(group) < 2:
                continue
            group = group.sort_values("id")
            ids = [int(value) for value in group["id"].tolist()]
            duplicate_ids = ids[1:]
            group_id += 1
            date_value, description, debit_account, credit_account, amount = identity
            rows.append(
                {
                    "group_id": group_id,
                    "date": date_value,
                    "description": description,
                    "debit_account": debit_account,
                    "credit_account": credit_account,
                    "amount": float(amount),
                    "duplicate_count": len(ids),
                    "keep_id": ids[0],
                    "duplicate_ids": ", ".join(str(value) for value in duplicate_ids),
                    "duplicate_ids_json": json.dumps(duplicate_ids),
                    "category_set": " | ".join(sorted(set(group["category"].tolist()))),
                    "subcategory_set": " | ".join(sorted(set(group["subcategory"].tolist()))),
                    "total_amount": float(group["amount"].sum()),
                }
            )
        return pd.DataFrame(rows, columns=columns).sort_values(
            by=["duplicate_count", "date", "amount"],
            ascending=[False, False, False],
        )

    def preview_duplicate_transaction_repair(self) -> pd.DataFrame:
        return self.get_duplicate_transaction_groups()

    def run_data_quality_audit(self, notes: str = "") -> dict[str, object]:
        started_at = utc_now()
        duplicates_df = self.get_duplicate_transaction_groups()
        details = {
            "duplicate_groups": int(len(duplicates_df)),
            "duplicate_extra_rows": int(duplicates_df["duplicate_count"].sub(1).sum()) if not duplicates_df.empty else 0,
        }
        with self.transaction() as conn:
            cursor = conn.execute(
                """
                INSERT INTO data_quality_runs (run_type, started_at, completed_at, status, notes, details_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                ("ledger_duplicate_audit", started_at, utc_now(), "completed", notes, json.dumps(details)),
            )
            run_id = int(cursor.lastrowid)
            for row in duplicates_df.to_dict("records"):
                transaction_ids = [int(row["keep_id"]), *json.loads(row["duplicate_ids_json"])]
                finding_key = "|".join(
                    [
                        str(row["date"]),
                        str(row["description"]),
                        str(row["debit_account"]),
                        str(row["credit_account"]),
                        str(row["amount"]),
                    ]
                )
                conn.execute(
                    """
                    INSERT INTO data_quality_findings (
                        run_id, finding_type, severity, transaction_ids, finding_key, row_count, amount, details_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        "duplicate_transaction",
                        "warning",
                        json.dumps(transaction_ids),
                        finding_key,
                        int(row["duplicate_count"]),
                        float(row["total_amount"]),
                        json.dumps(row),
                    ),
                )
        return {"run_id": run_id, **details}

    def apply_duplicate_transaction_repair(
        self,
        duplicate_transaction_ids: Iterable[int] | None = None,
        notes: str = "",
    ) -> dict[str, object]:
        preview_df = self.get_duplicate_transaction_groups()
        candidate_ids: set[int] = set()
        for row in preview_df.to_dict("records"):
            candidate_ids.update(int(value) for value in json.loads(row["duplicate_ids_json"]))

        requested_ids = (
            {int(value) for value in duplicate_transaction_ids}
            if duplicate_transaction_ids is not None
            else candidate_ids
        )
        eligible_ids = sorted(candidate_ids & requested_ids)
        if not eligible_ids:
            return {
                "deleted_count": 0,
                "requested_count": len(requested_ids),
                "skipped_linked_count": 0,
                "backup_path": "",
            }

        placeholders = ",".join("?" for _ in eligible_ids)
        linked_rows = self.conn.execute(
            f"SELECT transaction_id FROM posted_links WHERE transaction_id IN ({placeholders})",
            eligible_ids,
        ).fetchall()
        linked_ids = {int(row["transaction_id"]) for row in linked_rows}
        repair_ids = [row_id for row_id in eligible_ids if row_id not in linked_ids]
        if not repair_ids:
            return {
                "deleted_count": 0,
                "requested_count": len(requested_ids),
                "skipped_linked_count": len(linked_ids),
                "backup_path": "",
            }

        backup_path = self.create_database_backup("before-duplicate-repair")
        repair_placeholders = ",".join("?" for _ in repair_ids)
        with self.transaction() as conn:
            cursor = conn.execute(
                f"DELETE FROM transactions WHERE id IN ({repair_placeholders})",
                repair_ids,
            )
            deleted_count = int(cursor.rowcount or 0)
            conn.execute(
                """
                INSERT INTO ledger_repair_actions (
                    action_type, backup_path, affected_transaction_ids, affected_count, notes, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    "delete_duplicate_transactions",
                    backup_path,
                    json.dumps(repair_ids),
                    deleted_count,
                    notes,
                    utc_now(),
                ),
            )
        return {
            "deleted_count": deleted_count,
            "requested_count": len(requested_ids),
            "skipped_linked_count": len(linked_ids),
            "backup_path": backup_path,
        }

    def get_data_quality_runs_df(self) -> pd.DataFrame:
        return pd.read_sql_query(
            """
            SELECT id, run_type, started_at, completed_at, status, notes, details_json
            FROM data_quality_runs
            ORDER BY id DESC
            """,
            self.conn,
        )

    def get_data_quality_findings_df(self, run_id: int | None = None) -> pd.DataFrame:
        where = ""
        params: list[int] = []
        if run_id is not None:
            where = "WHERE run_id = ?"
            params.append(int(run_id))
        return pd.read_sql_query(
            f"""
            SELECT id, run_id, finding_type, severity, transaction_ids, finding_key, row_count, amount, details_json
            FROM data_quality_findings
            {where}
            ORDER BY id DESC
            """,
            self.conn,
            params=params,
        )

    def get_ledger_repair_actions_df(self) -> pd.DataFrame:
        return pd.read_sql_query(
            """
            SELECT id, action_type, backup_path, affected_transaction_ids, affected_count, notes, created_at
            FROM ledger_repair_actions
            ORDER BY id DESC
            """,
            self.conn,
        )

    def get_investment_trades_df(self, status: str = "") -> pd.DataFrame:
        where = ""
        params: list[str] = []
        if status:
            where = "WHERE review_status = ?"
            params.append(status)
        return pd.read_sql_query(
            f"""
            SELECT
                id, transaction_id, trade_date, action, ticker, quantity, amount, fees,
                currency, parse_confidence, review_status, notes, created_at, updated_at
            FROM investment_trades
            {where}
            ORDER BY trade_date DESC, id DESC
            """,
            self.conn,
            params=params,
        )

    def upsert_investment_trade(self, trade: dict) -> int:
        timestamp = utc_now()
        transaction_id = trade.get("transaction_id")
        payload = (
            int(transaction_id) if transaction_id not in (None, "") else None,
            str(trade.get("trade_date") or "").strip(),
            str(trade.get("action") or "").strip().lower(),
            str(trade.get("ticker") or "").strip().upper(),
            float(trade.get("quantity") or 0),
            float(trade.get("amount") or 0),
            float(trade.get("fees") or 0),
            str(trade.get("currency") or "VND").strip() or "VND",
            float(trade.get("parse_confidence") or 0),
            str(trade.get("review_status") or "needs_review").strip() or "needs_review",
            str(trade.get("notes") or ""),
            timestamp,
            timestamp,
        )
        if payload[0] is not None:
            cursor = self.conn.execute(
                """
                INSERT INTO investment_trades (
                    transaction_id, trade_date, action, ticker, quantity, amount, fees, currency,
                    parse_confidence, review_status, notes, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(transaction_id) DO UPDATE SET
                    trade_date = excluded.trade_date,
                    action = excluded.action,
                    ticker = excluded.ticker,
                    quantity = excluded.quantity,
                    amount = excluded.amount,
                    fees = excluded.fees,
                    currency = excluded.currency,
                    parse_confidence = excluded.parse_confidence,
                    review_status = CASE
                        WHEN investment_trades.review_status = 'reviewed' THEN investment_trades.review_status
                        ELSE excluded.review_status
                    END,
                    notes = excluded.notes,
                    updated_at = excluded.updated_at
                """,
                payload,
            )
            self.conn.commit()
            existing = self.conn.execute(
                "SELECT id FROM investment_trades WHERE transaction_id = ?",
                (payload[0],),
            ).fetchone()
            return int(existing["id"] if existing else cursor.lastrowid)

        cursor = self.conn.execute(
            """
            INSERT INTO investment_trades (
                transaction_id, trade_date, action, ticker, quantity, amount, fees, currency,
                parse_confidence, review_status, notes, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            payload,
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def update_investment_trade(
        self,
        trade_id: int,
        trade_date: str,
        action: str,
        ticker: str,
        quantity: float,
        amount: float,
        fees: float,
        review_status: str,
        notes: str = "",
    ) -> None:
        self.conn.execute(
            """
            UPDATE investment_trades
            SET trade_date = ?, action = ?, ticker = ?, quantity = ?, amount = ?, fees = ?,
                review_status = ?, notes = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                trade_date,
                action,
                ticker.upper(),
                float(quantity),
                float(amount),
                float(fees),
                review_status,
                notes,
                utc_now(),
                int(trade_id),
            ),
        )
        self.conn.commit()

    def set_investment_trade_status(self, trade_ids: Iterable[int], status: str) -> int:
        trade_ids = [int(trade_id) for trade_id in trade_ids]
        if not trade_ids:
            return 0
        placeholders = ",".join("?" for _ in trade_ids)
        cursor = self.conn.execute(
            f"UPDATE investment_trades SET review_status = ?, updated_at = ? WHERE id IN ({placeholders})",
            [status, utc_now(), *trade_ids],
        )
        self.conn.commit()
        return int(cursor.rowcount or 0)

    def get_investment_price_snapshots_df(self) -> pd.DataFrame:
        return pd.read_sql_query(
            """
            SELECT id, ticker, price_date, price, currency, notes, updated_at
            FROM investment_price_snapshots
            ORDER BY price_date DESC, ticker
            """,
            self.conn,
        )

    def upsert_investment_price_snapshot(
        self,
        ticker: str,
        price_date: str,
        price: float,
        currency: str = "VND",
        notes: str = "",
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO investment_price_snapshots (ticker, price_date, price, currency, notes, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(ticker, price_date) DO UPDATE SET
                price = excluded.price,
                currency = excluded.currency,
                notes = excluded.notes,
                updated_at = excluded.updated_at
            """,
            (
                str(ticker or "").strip().upper(),
                str(price_date or "").strip(),
                float(price),
                str(currency or "VND").strip() or "VND",
                str(notes or ""),
                utc_now(),
            ),
        )
        self.conn.commit()

    def create_import_batch(self) -> int:
        cursor = self.conn.execute(
            "INSERT INTO import_batches (started_at, status, notes) VALUES (?, 'running', '')",
            (utc_now(),),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def finalize_import_batch(self, batch_id: int, status: str, notes: str = "") -> None:
        self.conn.execute(
            "UPDATE import_batches SET completed_at = ?, status = ?, notes = ? WHERE id = ?",
            (utc_now(), status, notes, int(batch_id)),
        )
        self.conn.commit()

    def get_source_file_by_hash(self, source_type: str, file_hash: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM source_files WHERE source_type = ? AND file_hash = ?",
            (source_type, file_hash),
        ).fetchone()

    def upsert_source_file(
        self,
        *,
        batch_id: int,
        source_type: str,
        file_name: str,
        file_path: str,
        file_hash: str,
        statement_month: str,
        parse_status: str,
        parse_notes: str,
        extraction_engine: str,
        raw_metadata: dict,
    ) -> int:
        existing = self.get_source_file_by_hash(source_type, file_hash)
        payload = (
            int(batch_id),
            source_type,
            file_name,
            file_path,
            file_hash,
            statement_month,
            parse_status,
            parse_notes,
            extraction_engine,
            json.dumps(raw_metadata, ensure_ascii=False),
            utc_now(),
        )
        if existing:
            self.conn.execute(
                """
                UPDATE source_files
                SET batch_id = ?, file_name = ?, file_path = ?, statement_month = ?, parse_status = ?, parse_notes = ?,
                    extraction_engine = ?, raw_metadata = ?, last_processed_at = ?
                WHERE id = ?
                """,
                (
                    int(batch_id),
                    file_name,
                    file_path,
                    statement_month,
                    parse_status,
                    parse_notes,
                    extraction_engine,
                    json.dumps(raw_metadata, ensure_ascii=False),
                    utc_now(),
                    int(existing["id"]),
                ),
            )
            self.conn.commit()
            return int(existing["id"])

        cursor = self.conn.execute(
            """
            INSERT INTO source_files (
                batch_id, source_type, file_name, file_path, file_hash, statement_month, parse_status,
                parse_notes, extraction_engine, raw_metadata, last_processed_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            payload,
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def replace_statement_rows(self, source_file_id: int, rows: Iterable[dict]) -> None:
        timestamp = utc_now()
        with self.transaction() as conn:
            conn.execute("DELETE FROM posted_links WHERE statement_row_id IN (SELECT id FROM statement_rows WHERE source_file_id = ?)", (int(source_file_id),))
            conn.execute("DELETE FROM statement_rows WHERE source_file_id = ?", (int(source_file_id),))
            for row in rows:
                conn.execute(
                    """
                    INSERT INTO statement_rows (
                        source_file_id, source_type, row_fingerprint, row_index, statement_month, transaction_date,
                        post_date, event_time, description, merchant, amount, currency, direction, running_balance,
                        account_ref, row_type, review_status, confidence, parse_notes, raw_text, category, subcategory,
                        debit_account, credit_account, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        int(source_file_id),
                        row["source_type"],
                        row["row_fingerprint"],
                        int(row["row_index"]),
                        row.get("statement_month") or "",
                        row.get("transaction_date") or "",
                        row.get("post_date") or "",
                        row.get("event_time") or "",
                        row.get("description") or "",
                        row.get("merchant") or "",
                        float(row.get("amount") or 0),
                        row.get("currency") or "VND",
                        row.get("direction") or "outflow",
                        row.get("running_balance"),
                        row.get("account_ref") or "",
                        row.get("row_type") or "purchase",
                        row.get("review_status") or "pending",
                        float(row.get("confidence") or 0),
                        row.get("parse_notes") or "",
                        row.get("raw_text") or "",
                        row.get("category") or "Others",
                        row.get("subcategory") or "Other expense",
                        row.get("debit_account") or "Expense",
                        row.get("credit_account") or "Cash",
                        timestamp,
                        timestamp,
                    ),
                )

    def get_import_batches_df(self) -> pd.DataFrame:
        return pd.read_sql_query(
            "SELECT * FROM import_batches ORDER BY id DESC",
            self.conn,
        )

    def get_source_files_df(self) -> pd.DataFrame:
        query = """
            SELECT
                sf.*,
                COALESCE(sr.row_count, 0) AS row_count,
                COALESCE(sr.pending_count, 0) AS pending_count,
                COALESCE(sr.posted_count, 0) AS posted_count,
                COALESCE(sr.ignored_count, 0) AS ignored_count
            FROM source_files AS sf
            LEFT JOIN (
                SELECT
                    source_file_id,
                    COUNT(*) AS row_count,
                    SUM(CASE WHEN review_status = 'pending' THEN 1 ELSE 0 END) AS pending_count,
                    SUM(CASE WHEN review_status = 'posted' THEN 1 ELSE 0 END) AS posted_count,
                    SUM(CASE WHEN review_status = 'ignored' THEN 1 ELSE 0 END) AS ignored_count
                FROM statement_rows
                GROUP BY source_file_id
            ) AS sr
            ON sr.source_file_id = sf.id
            ORDER BY sf.last_processed_at DESC, sf.id DESC
        """
        return pd.read_sql_query(query, self.conn)

    def get_statement_rows_df(
        self,
        source_type: str = "",
        status: str = "",
        month: str = "",
    ) -> pd.DataFrame:
        where = []
        params: list[str] = []
        if source_type:
            where.append("source_type = ?")
            params.append(source_type)
        if status:
            where.append("review_status = ?")
            params.append(status)
        if month:
            where.append("statement_month = ?")
            params.append(month)
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        query = f"""
            SELECT
                id, source_file_id, source_type, statement_month, transaction_date, post_date, event_time, description,
                merchant, amount, direction, running_balance, account_ref, row_type, review_status, confidence,
                parse_notes, category, subcategory, debit_account, credit_account, raw_text
            FROM statement_rows
            {clause}
            ORDER BY statement_month DESC, transaction_date DESC, id DESC
        """
        return pd.read_sql_query(query, self.conn, params=params)

    def get_statement_review_df(
        self,
        source_type: str = "",
        status: str = "",
        month: str = "",
    ) -> pd.DataFrame:
        frame = self.get_statement_rows_df(source_type, status, month)
        if frame.empty:
            return frame

        frame = frame.copy()
        frame["transaction_date"] = frame["transaction_date"].fillna("")
        frame["post_date"] = frame["post_date"].fillna("")
        frame["category"] = frame["category"].fillna(FALLBACK_CATEGORY)
        frame["subcategory"] = frame["subcategory"].fillna(FALLBACK_SUBCATEGORY)
        frame["needs_category"] = ~frame["category"].isin(CATEGORY_MAP.keys()) | frame["category"].eq(FALLBACK_CATEGORY)
        frame["valid_subcategory"] = frame.apply(
            lambda row: row["subcategory"] in CATEGORY_MAP.get(row["category"], []),
            axis=1,
        )
        frame["needs_subcategory"] = ~frame["valid_subcategory"] | (
            frame["category"].eq(FALLBACK_CATEGORY) & frame["subcategory"].eq(FALLBACK_SUBCATEGORY)
        )
        frame["needs_accounts"] = ~frame["debit_account"].isin(ACCOUNT_OPTIONS) | ~frame["credit_account"].isin(ACCOUNT_OPTIONS)
        frame["invalid_amount"] = ~frame["amount"].apply(self._is_valid_amount)
        frame["invalid_date"] = ~frame.apply(
            lambda row: self._is_valid_transaction_date(row["transaction_date"] or row["post_date"]),
            axis=1,
        )
        frame["low_confidence"] = frame["confidence"].fillna(0).astype(float) < LOW_CONFIDENCE_THRESHOLD
        frame["review_state"] = frame.apply(self._statement_row_state, axis=1)
        frame["is_fallback"] = frame["category"].eq(FALLBACK_CATEGORY) & frame["subcategory"].eq(FALLBACK_SUBCATEGORY)
        frame["ready_to_post"] = frame["review_state"].eq("ready_to_post")
        return frame

    def update_statement_row_edits(self, rows: Iterable[dict]) -> None:
        validation_errors: list[str] = []
        with self.transaction() as conn:
            for row in rows:
                category = str(row.get("category") or FALLBACK_CATEGORY).strip() or FALLBACK_CATEGORY
                subcategory = str(row.get("subcategory") or FALLBACK_SUBCATEGORY).strip() or FALLBACK_SUBCATEGORY
                row_errors = self.validate_statement_classification(category, subcategory)
                if row_errors:
                    validation_errors.append(f"Row {row.get('id')}: {' '.join(row_errors)}")
                    continue
                conn.execute(
                    """
                    UPDATE statement_rows
                    SET transaction_date = ?, description = ?, category = ?, subcategory = ?, debit_account = ?,
                        credit_account = ?, amount = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        row.get("transaction_date") or "",
                        row.get("description") or "",
                        category,
                        subcategory,
                        row.get("debit_account") or "Expense",
                        row.get("credit_account") or "Cash",
                        float(row.get("amount") or 0),
                        utc_now(),
                        int(row["id"]),
                    ),
                )
        if validation_errors:
            raise ValueError(" ".join(validation_errors))

    def set_statement_status(self, row_ids: Iterable[int], status: str) -> int:
        row_ids = [int(row_id) for row_id in row_ids]
        if not row_ids:
            return 0
        placeholders = ",".join("?" for _ in row_ids)
        params = [status, utc_now(), *row_ids]
        cursor = self.conn.execute(
            f"UPDATE statement_rows SET review_status = ?, updated_at = ? WHERE id IN ({placeholders})",
            params,
        )
        self.conn.commit()
        return int(cursor.rowcount)

    def get_merchant_rules(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM merchant_rules ORDER BY use_count DESC, keyword",
        ).fetchall()

    def upsert_merchant_rule(
        self,
        keyword: str,
        category: str,
        subcategory: str,
        debit_account: str,
        credit_account: str,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO merchant_rules (keyword, category, subcategory, debit_account, credit_account, use_count, updated_at)
            VALUES (?, ?, ?, ?, ?, 1, ?)
            ON CONFLICT(keyword) DO UPDATE SET
                category = excluded.category,
                subcategory = excluded.subcategory,
                debit_account = excluded.debit_account,
                credit_account = excluded.credit_account,
                use_count = merchant_rules.use_count + 1,
                updated_at = excluded.updated_at
            """,
            (keyword, category, subcategory, debit_account, credit_account, utc_now()),
        )
        self.conn.commit()

    def post_statement_rows(self, row_ids: Iterable[int], merchant_keyword_func) -> tuple[int, list[str]]:
        posted = 0
        messages: list[str] = []
        row_ids = [int(row_id) for row_id in row_ids]
        if not row_ids:
            return posted, messages

        with self.transaction() as conn:
            for row_id in row_ids:
                row = conn.execute("SELECT * FROM statement_rows WHERE id = ?", (row_id,)).fetchone()
                if row is None:
                    continue
                if row["review_status"] == "posted":
                    messages.append(f"Row {row_id} was already posted.")
                    continue
                if row["review_status"] == "ignored":
                    messages.append(f"Row {row_id} is ignored and was skipped.")
                    continue
                existing = conn.execute(
                    "SELECT transaction_id FROM posted_links WHERE statement_row_id = ?",
                    (row_id,),
                ).fetchone()
                if existing is not None:
                    messages.append(f"Row {row_id} was already linked to transaction {existing['transaction_id']}.")
                    continue
                row_errors = self._statement_row_validation_errors(row)
                if row_errors:
                    messages.append(f"Row {row_id} was skipped: {' '.join(row_errors)}")
                    continue
                effective_date = row["transaction_date"] or row["post_date"] or ""
                raw_text = str(row["raw_text"] or "").strip()
                if raw_text:
                    duplicate_posted_row = conn.execute(
                        """
                        SELECT id, posted_transaction_id
                        FROM statement_rows
                        WHERE id != ?
                          AND review_status = 'posted'
                          AND posted_transaction_id IS NOT NULL
                          AND COALESCE(source_type, '') = ?
                          AND COALESCE(raw_text, '') = ?
                          AND COALESCE(NULLIF(transaction_date, ''), NULLIF(post_date, ''), '') = ?
                          AND COALESCE(description, '') = ?
                          AND COALESCE(category, '') = ?
                          AND COALESCE(subcategory, '') = ?
                          AND COALESCE(debit_account, '') = ?
                          AND COALESCE(credit_account, '') = ?
                          AND COALESCE(amount, 0) = ?
                        LIMIT 1
                        """,
                        (
                            row_id,
                            row["source_type"] or "",
                            raw_text,
                            effective_date,
                            row["description"] or "",
                            row["category"] or "",
                            row["subcategory"] or "",
                            row["debit_account"] or "",
                            row["credit_account"] or "",
                            float(row["amount"] or 0),
                        ),
                    ).fetchone()
                    if duplicate_posted_row is not None:
                        messages.append(
                            f"Row {row_id} duplicates posted statement row {duplicate_posted_row['id']} "
                            f"linked to transaction {duplicate_posted_row['posted_transaction_id']}."
                        )
                        continue

                cursor = conn.execute(
                    """
                    INSERT INTO transactions (date, description, category, subcategory, debit_account, credit_account, amount)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row["transaction_date"] or row["post_date"] or "",
                        row["description"],
                        row["category"] or "Others",
                        row["subcategory"] or "Other expense",
                        row["debit_account"] or "Expense",
                        row["credit_account"] or "Cash",
                        float(row["amount"]),
                    ),
                )
                transaction_id = int(cursor.lastrowid)
                conn.execute(
                    "INSERT INTO posted_links (statement_row_id, transaction_id, posted_at) VALUES (?, ?, ?)",
                    (row_id, transaction_id, utc_now()),
                )
                conn.execute(
                    "UPDATE statement_rows SET review_status = 'posted', posted_transaction_id = ?, updated_at = ? WHERE id = ?",
                    (transaction_id, utc_now(), row_id),
                )

                keyword = merchant_keyword_func(row["merchant"] or row["description"])
                if keyword:
                    conn.execute(
                        """
                        INSERT INTO merchant_rules (keyword, category, subcategory, debit_account, credit_account, use_count, updated_at)
                        VALUES (?, ?, ?, ?, ?, 1, ?)
                        ON CONFLICT(keyword) DO UPDATE SET
                            category = excluded.category,
                            subcategory = excluded.subcategory,
                            debit_account = excluded.debit_account,
                            credit_account = excluded.credit_account,
                            use_count = merchant_rules.use_count + 1,
                            updated_at = excluded.updated_at
                        """,
                        (
                            keyword,
                            row["category"] or "Others",
                            row["subcategory"] or "Other expense",
                            row["debit_account"] or "Expense",
                            row["credit_account"] or "Cash",
                            utc_now(),
                        ),
                    )
                posted += 1
        return posted, messages

    def get_statement_insights(self) -> pd.DataFrame:
        frame = self.get_statement_review_df()
        if frame.empty:
            return frame
        frame = frame[frame["review_status"] != "ignored"].copy()
        sort_order = {"needs_review": 0, "ready_to_post": 1, "posted": 2, "ignored": 3}
        frame["review_priority"] = frame["review_state"].map(sort_order).fillna(9)
        return frame.sort_values(
            by=["review_priority", "low_confidence", "statement_month", "transaction_date", "id"],
            ascending=[True, False, False, False, False],
        )
