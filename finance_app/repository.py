from __future__ import annotations

import json
import sqlite3
from collections import Counter
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterable

import pandas as pd

from .constants import ACCOUNT_OPTIONS, CATEGORY_MAP, DB_PATH, DEFAULT_SETTINGS, SECONDARY_DB_PATH


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
            if self.db_path.resolve() == DB_PATH.resolve():
                secondary_db_paths = (SECONDARY_DB_PATH,)
            else:
                secondary_db_paths = (self.db_path.parent / "data" / "finance.db",)
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
                    primary_signatures = Counter(self._transaction_signature(row) for row in primary_rows)
                    secondary_signatures = Counter(self._transaction_signature(row) for row in secondary_rows)
                    missing_rows = list((secondary_signatures - primary_signatures).elements())

                    for row in missing_rows:
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
                    summary["inserted_transactions"] += len(missing_rows)

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

    def get_setting(self, key: str, default: str = "") -> str:
        return self.get_settings().get(key, default)

    def get_ledger(self) -> pd.DataFrame:
        return pd.read_sql_query("SELECT * FROM transactions ORDER BY date DESC, id DESC", self.conn)

    def get_budgets(self) -> pd.DataFrame:
        return pd.read_sql_query(
            "SELECT category, monthly_limit FROM budgets ORDER BY category",
            self.conn,
        )

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

    def update_statement_row_edits(self, rows: Iterable[dict]) -> None:
        with self.transaction() as conn:
            for row in rows:
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
                        row.get("category") or "Others",
                        row.get("subcategory") or "Other expense",
                        row.get("debit_account") or "Expense",
                        row.get("credit_account") or "Cash",
                        float(row.get("amount") or 0),
                        utc_now(),
                        int(row["id"]),
                    ),
                )

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
        query = """
            SELECT
                id,
                source_type,
                statement_month,
                transaction_date,
                description,
                merchant,
                amount,
                direction,
                row_type,
                review_status,
                confidence,
                parse_notes,
                category,
                subcategory
            FROM statement_rows
            WHERE review_status != 'ignored'
            ORDER BY statement_month, transaction_date, id
        """
        return pd.read_sql_query(query, self.conn)
