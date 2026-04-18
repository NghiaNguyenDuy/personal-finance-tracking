from __future__ import annotations

import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

import pandas as pd

from .constants import ACTIVE_DB_PATH, BACKUP_DIR, DEFAULT_ACCOUNTS, LEGACY_DB_PATH, infer_account_type_from_name
from .validation import normalize_text, validate_account, validate_budget, validate_transaction


class FinanceRepository:
    def __init__(
        self,
        db_path: Path = ACTIVE_DB_PATH,
        legacy_db_path: Path = LEGACY_DB_PATH,
        backup_dir: Path = BACKUP_DIR,
    ) -> None:
        self.db_path = Path(db_path)
        self.legacy_db_path = Path(legacy_db_path)
        self.backup_dir = Path(backup_dir)
        self.bootstrap_note = ""

        self._prepare_database()
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self._upgrade_existing_database()

    @staticmethod
    def _now() -> str:
        return datetime.now().replace(microsecond=0).isoformat(sep=" ")

    def _append_note(self, message: str) -> None:
        if not message:
            return
        if self.bootstrap_note:
            self.bootstrap_note = f"{self.bootstrap_note}\n{message}"
        else:
            self.bootstrap_note = message

    def _prepare_database(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.backup_dir.mkdir(parents=True, exist_ok=True)

        if self.db_path.exists():
            return

        self._create_fresh_database(self.db_path)

        legacy_exists = self.legacy_db_path.exists() and self.legacy_db_path.resolve() != self.db_path.resolve()
        if not legacy_exists:
            self._append_note(f"Created a new empty database at {self.db_path}.")
            return

        legacy_backup = self._copy_file_backup(self.legacy_db_path, "legacy-source")
        imported = False
        try:
            imported = self._import_database(self.legacy_db_path, self.db_path)
        except Exception as exc:  # pragma: no cover - defensive startup path
            self._append_note(f"Legacy import skipped because of an error: {exc}")

        if imported:
            self._append_note(
                f"Imported legacy data from {self.legacy_db_path} into {self.db_path}. "
                f"A safety backup was created at {legacy_backup}."
            )
        else:
            self._append_note(
                f"Created a new empty database at {self.db_path}. "
                f"A safety backup of the legacy database is available at {legacy_backup}."
            )

    def _create_fresh_database(self, path: Path) -> None:
        conn = sqlite3.connect(path)
        try:
            self._initialize_schema(conn)
        finally:
            conn.close()

    def _initialize_schema(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS accounts (
                name TEXT PRIMARY KEY,
                account_type TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY,
                date TEXT NOT NULL,
                description TEXT NOT NULL,
                category TEXT NOT NULL,
                subcategory TEXT NOT NULL,
                debit_account TEXT NOT NULL,
                credit_account TEXT NOT NULL,
                amount REAL NOT NULL,
                source TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(debit_account) REFERENCES accounts(name),
                FOREIGN KEY(credit_account) REFERENCES accounts(name)
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
        self._seed_default_accounts(conn)
        conn.commit()

    def _seed_default_accounts(self, conn: sqlite3.Connection) -> None:
        now = self._now()
        conn.executemany(
            """
            INSERT INTO accounts (name, account_type, is_active, created_at, updated_at)
            VALUES (?, ?, 1, ?, ?)
            ON CONFLICT(name) DO NOTHING
            """,
            [(row["name"], row["account_type"], now, now) for row in DEFAULT_ACCOUNTS],
        )

    @staticmethod
    def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table_name,),
        ).fetchone()
        return row is not None

    @staticmethod
    def _table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
        if not FinanceRepository._table_exists(conn, table_name):
            return set()
        rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        return {row[1] for row in rows}

    def _needs_schema_migration(self, conn: sqlite3.Connection) -> bool:
        transaction_columns = self._table_columns(conn, "transactions")
        required_transaction_columns = {
            "date",
            "description",
            "category",
            "subcategory",
            "debit_account",
            "credit_account",
            "amount",
            "source",
            "created_at",
            "updated_at",
        }
        return (
            not self._table_exists(conn, "accounts")
            or not self._table_exists(conn, "budgets")
            or not required_transaction_columns.issubset(transaction_columns)
        )

    def _upgrade_existing_database(self) -> None:
        if self._needs_schema_migration(self.conn):
            if self.db_path.exists() and self.db_path.stat().st_size > 0:
                backup_path = self.create_backup("pre-migration")
                self._append_note(f"Created a pre-migration backup at {backup_path}.")
            self._run_schema_migration(self.conn)
            self._append_note(f"Upgraded the active database schema at {self.db_path}.")

        self._seed_default_accounts(self.conn)
        self._sync_accounts_from_transactions(self.conn)
        self.conn.commit()

    def _run_schema_migration(self, conn: sqlite3.Connection) -> None:
        if not self._table_exists(conn, "transactions"):
            self._initialize_schema(conn)
            return

        if not self._table_exists(conn, "accounts"):
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS accounts (
                    name TEXT PRIMARY KEY,
                    account_type TEXT NOT NULL,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

        if not self._table_exists(conn, "budgets"):
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS budgets (
                    category TEXT PRIMARY KEY,
                    monthly_limit REAL NOT NULL DEFAULT 0
                )
                """
            )

        transaction_columns = self._table_columns(conn, "transactions")
        if "source" not in transaction_columns:
            conn.execute("ALTER TABLE transactions ADD COLUMN source TEXT")
        if "created_at" not in transaction_columns:
            conn.execute("ALTER TABLE transactions ADD COLUMN created_at TEXT")
        if "updated_at" not in transaction_columns:
            conn.execute("ALTER TABLE transactions ADD COLUMN updated_at TEXT")

        now = self._now()
        conn.execute(
            """
            UPDATE transactions
            SET description = CASE
                WHEN TRIM(COALESCE(description, '')) = '' THEN 'Imported transaction'
                ELSE TRIM(description)
            END
            """
        )
        conn.execute(
            "UPDATE transactions SET source = COALESCE(NULLIF(source, ''), 'manual') WHERE source IS NULL OR source = ''"
        )
        conn.execute(
            "UPDATE transactions SET created_at = COALESCE(created_at, ?) WHERE created_at IS NULL OR created_at = ''",
            (now,),
        )
        conn.execute(
            """
            UPDATE transactions
            SET updated_at = COALESCE(updated_at, created_at, ?)
            WHERE updated_at IS NULL OR updated_at = ''
            """,
            (now,),
        )

    def _sync_accounts_from_transactions(self, conn: sqlite3.Connection) -> None:
        if not self._table_exists(conn, "transactions"):
            return

        ledger = pd.read_sql_query(
            """
            SELECT date, description, category, subcategory, debit_account, credit_account, amount
            FROM transactions
            """,
            conn,
        )
        inferred_types = self._infer_account_types_from_ledger(ledger)
        now = self._now()
        rows = [
            (name, account_type, now, now)
            for name, account_type in sorted(inferred_types.items())
            if normalize_text(name)
        ]
        if not rows:
            return

        conn.executemany(
            """
            INSERT INTO accounts (name, account_type, is_active, created_at, updated_at)
            VALUES (?, ?, 1, ?, ?)
            ON CONFLICT(name) DO NOTHING
            """,
            rows,
        )

    def _infer_account_types_from_ledger(self, ledger: pd.DataFrame) -> dict[str, str]:
        inferred = {row["name"]: row["account_type"] for row in DEFAULT_ACCOUNTS}
        if ledger.empty:
            return inferred

        account_values = pd.concat([ledger["debit_account"], ledger["credit_account"]], ignore_index=True).dropna().unique()
        for account_name in account_values:
            account_name = normalize_text(account_name)
            if not account_name:
                continue

            base_type = infer_account_type_from_name(account_name)
            if account_name.startswith(("Asset:", "Liability:", "Equity:", "Income:", "Expense:")) or account_name in {
                "Cash",
                "Expense",
            }:
                inferred[account_name] = base_type
                continue

            debit_rows = ledger[ledger["debit_account"] == account_name]
            credit_rows = ledger[ledger["credit_account"] == account_name]
            income_side = (
                (not credit_rows.empty and credit_rows["category"].fillna("").eq("Income").any())
                or (not debit_rows.empty and debit_rows["category"].fillna("").eq("Income").any())
            )
            expense_side = (
                (not debit_rows.empty and ~debit_rows["category"].fillna("").eq("Income")).any()
                or (not credit_rows.empty and ~credit_rows["category"].fillna("").eq("Income")).any()
            )

            if income_side and not expense_side:
                inferred[account_name] = "Income"
            elif expense_side and not income_side:
                inferred[account_name] = "Expense"
            else:
                inferred[account_name] = base_type

        return inferred

    def _import_database(self, source_path: Path, target_path: Path) -> bool:
        source_conn = sqlite3.connect(source_path)
        target_conn = sqlite3.connect(target_path)
        imported = False

        try:
            target_conn.row_factory = sqlite3.Row
            self._initialize_schema(target_conn)

            transactions = (
                pd.read_sql_query("SELECT * FROM transactions ORDER BY id", source_conn)
                if self._table_exists(source_conn, "transactions")
                else pd.DataFrame()
            )
            budgets = (
                pd.read_sql_query("SELECT * FROM budgets", source_conn)
                if self._table_exists(source_conn, "budgets")
                else pd.DataFrame()
            )
            source_accounts = (
                pd.read_sql_query("SELECT * FROM accounts", source_conn)
                if self._table_exists(source_conn, "accounts")
                else pd.DataFrame()
            )

            now = self._now()
            if not source_accounts.empty:
                account_rows = []
                for row in source_accounts.to_dict("records"):
                    account_rows.append(
                        (
                            normalize_text(row.get("name")),
                            row.get("account_type") or infer_account_type_from_name(row.get("name")),
                            int(bool(row.get("is_active", 1))),
                            row.get("created_at") or now,
                            row.get("updated_at") or row.get("created_at") or now,
                        )
                    )
                target_conn.executemany(
                    """
                    INSERT OR REPLACE INTO accounts (name, account_type, is_active, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    account_rows,
                )

            if not transactions.empty and source_accounts.empty:
                inferred_types = self._infer_account_types_from_ledger(transactions)
                target_conn.executemany(
                    """
                    INSERT INTO accounts (name, account_type, is_active, created_at, updated_at)
                    VALUES (?, ?, 1, ?, ?)
                    ON CONFLICT(name) DO NOTHING
                    """,
                    [(name, account_type, now, now) for name, account_type in inferred_types.items()],
                )

            if not transactions.empty:
                transaction_rows = []
                for row in transactions.to_dict("records"):
                    description = normalize_text(row.get("description")) or "Imported transaction"
                    transaction_rows.append(
                        (
                            row.get("id"),
                            str(row.get("date") or ""),
                            description,
                            row.get("category") or "Others",
                            row.get("subcategory") or "Other expense",
                            normalize_text(row.get("debit_account")),
                            normalize_text(row.get("credit_account")),
                            float(row.get("amount") or 0),
                            row.get("source") or "manual",
                            row.get("created_at") or now,
                            row.get("updated_at") or row.get("created_at") or now,
                        )
                    )

                target_conn.executemany(
                    """
                    INSERT INTO transactions (
                        id, date, description, category, subcategory,
                        debit_account, credit_account, amount, source, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    transaction_rows,
                )
                imported = True

            if not budgets.empty and {"category", "monthly_limit"}.issubset(budgets.columns):
                budget_rows = [
                    (row["category"], float(row["monthly_limit"]))
                    for _, row in budgets.iterrows()
                ]
                target_conn.executemany(
                    """
                    INSERT OR REPLACE INTO budgets (category, monthly_limit)
                    VALUES (?, ?)
                    """,
                    budget_rows,
                )
                imported = imported or bool(budget_rows)

            target_conn.commit()
            return imported
        finally:
            source_conn.close()
            target_conn.close()

    def _copy_file_backup(self, source_path: Path, reason: str) -> Path:
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_path = self.backup_dir / f"{source_path.stem}_{reason}_{timestamp}{source_path.suffix}"
        shutil.copy2(source_path, backup_path)
        return backup_path

    def create_backup(self, reason: str = "manual") -> Path:
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_path = self.backup_dir / f"{self.db_path.stem}_{reason}_{timestamp}{self.db_path.suffix}"
        backup_conn = sqlite3.connect(backup_path)
        try:
            self.conn.commit()
            self.conn.backup(backup_conn)
        finally:
            backup_conn.close()
        return backup_path

    def list_backups(self, limit: int = 5) -> list[Path]:
        backups = sorted(self.backup_dir.glob("*.db"), key=lambda path: path.stat().st_mtime, reverse=True)
        return backups[:limit]

    def get_counts(self) -> dict[str, int]:
        return {
            "transactions": int(self.conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]),
            "budgets": int(self.conn.execute("SELECT COUNT(*) FROM budgets").fetchone()[0]),
            "accounts": int(self.conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]),
        }

    def get_transactions(self) -> pd.DataFrame:
        query = """
            SELECT
                t.id,
                t.date,
                t.description,
                t.category,
                t.subcategory,
                t.debit_account,
                COALESCE(da.account_type, '') AS debit_type,
                t.credit_account,
                COALESCE(ca.account_type, '') AS credit_type,
                t.amount,
                COALESCE(t.source, 'manual') AS source,
                t.created_at,
                t.updated_at
            FROM transactions AS t
            LEFT JOIN accounts AS da ON da.name = t.debit_account
            LEFT JOIN accounts AS ca ON ca.name = t.credit_account
            ORDER BY t.date DESC, t.id DESC
        """
        return pd.read_sql_query(query, self.conn)

    def get_budgets(self) -> pd.DataFrame:
        return pd.read_sql_query(
            "SELECT category, monthly_limit FROM budgets ORDER BY category",
            self.conn,
        )

    def get_accounts(self, active_only: bool = False) -> pd.DataFrame:
        query = """
            SELECT
                a.name,
                a.account_type,
                a.is_active,
                a.created_at,
                a.updated_at,
                COALESCE(usage_counts.usage_count, 0) AS usage_count
            FROM accounts AS a
            LEFT JOIN (
                SELECT account_name, COUNT(*) AS usage_count
                FROM (
                    SELECT debit_account AS account_name FROM transactions
                    UNION ALL
                    SELECT credit_account AS account_name FROM transactions
                )
                GROUP BY account_name
            ) AS usage_counts
            ON usage_counts.account_name = a.name
        """
        if active_only:
            query += " WHERE a.is_active = 1"
        query += " ORDER BY a.is_active DESC, a.account_type, a.name"

        accounts = pd.read_sql_query(query, self.conn)
        if accounts.empty:
            return accounts

        accounts["is_active"] = accounts["is_active"].astype(bool)
        accounts["usage_count"] = accounts["usage_count"].astype(int)
        return accounts

    def get_account_lookup(self, active_only: bool = False) -> dict[str, str]:
        accounts = self.get_accounts(active_only=active_only)
        if accounts.empty:
            return {}
        return dict(zip(accounts["name"], accounts["account_type"]))

    def add_transaction(
        self,
        *,
        tx_date: str,
        description: str,
        category: str,
        subcategory: str,
        debit_account: str,
        credit_account: str,
        amount: float,
        source: str = "manual",
    ) -> int:
        errors = validate_transaction(
            description=description,
            category=category,
            subcategory=subcategory,
            debit_account=debit_account,
            credit_account=credit_account,
            amount=amount,
            known_accounts=self.get_account_lookup(active_only=True).keys(),
        )
        if errors:
            raise ValueError("\n".join(errors))

        now = self._now()
        description = normalize_text(description)
        with self.conn:
            cursor = self.conn.execute(
                """
                INSERT INTO transactions (
                    date, description, category, subcategory,
                    debit_account, credit_account, amount, source, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tx_date,
                    description,
                    category,
                    subcategory,
                    debit_account,
                    credit_account,
                    float(amount),
                    source or "manual",
                    now,
                    now,
                ),
            )
        return int(cursor.lastrowid)

    def update_transaction(
        self,
        tx_id: int,
        *,
        tx_date: str,
        description: str,
        category: str,
        subcategory: str,
        debit_account: str,
        credit_account: str,
        amount: float,
        source: str = "manual",
    ) -> None:
        errors = validate_transaction(
            description=description,
            category=category,
            subcategory=subcategory,
            debit_account=debit_account,
            credit_account=credit_account,
            amount=amount,
            known_accounts=self.get_account_lookup(active_only=False).keys(),
        )
        if errors:
            raise ValueError("\n".join(errors))

        with self.conn:
            self.conn.execute(
                """
                UPDATE transactions
                SET date = ?, description = ?, category = ?, subcategory = ?,
                    debit_account = ?, credit_account = ?, amount = ?, source = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    tx_date,
                    normalize_text(description),
                    category,
                    subcategory,
                    debit_account,
                    credit_account,
                    float(amount),
                    source or "manual",
                    self._now(),
                    int(tx_id),
                ),
            )

    def delete_transaction(self, tx_id: int) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM transactions WHERE id = ?", (int(tx_id),))

    def upsert_budget(self, category: str, monthly_limit: float) -> None:
        errors = validate_budget(category, monthly_limit)
        if errors:
            raise ValueError("\n".join(errors))

        with self.conn:
            self.conn.execute(
                """
                INSERT INTO budgets (category, monthly_limit)
                VALUES (?, ?)
                ON CONFLICT(category) DO UPDATE SET monthly_limit = excluded.monthly_limit
                """,
                (category, float(monthly_limit)),
            )

    def delete_budget(self, category: str) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM budgets WHERE category = ?", (category,))

    def get_account_usage_count(self, account_name: str) -> int:
        row = self.conn.execute(
            """
            SELECT COUNT(*) AS usage_count
            FROM transactions
            WHERE debit_account = ? OR credit_account = ?
            """,
            (account_name, account_name),
        ).fetchone()
        return int(row["usage_count"]) if row else 0

    def upsert_account(
        self,
        *,
        name: str,
        account_type: str,
        is_active: bool,
        original_name: str | None = None,
    ) -> None:
        normalized_name = normalize_text(name)
        original_name = normalize_text(original_name) if original_name else None
        errors = validate_account(
            normalized_name,
            account_type,
            self.get_accounts(active_only=False)["name"].tolist(),
            original_name=original_name,
        )
        if errors:
            raise ValueError("\n".join(errors))

        if original_name and normalized_name != original_name and self.get_account_usage_count(original_name) > 0:
            raise ValueError("Accounts that already have transactions cannot be renamed.")

        now = self._now()
        with self.conn:
            if original_name:
                if normalized_name != original_name:
                    self.conn.execute(
                        """
                        UPDATE accounts
                        SET name = ?, account_type = ?, is_active = ?, updated_at = ?
                        WHERE name = ?
                        """,
                        (normalized_name, account_type, int(bool(is_active)), now, original_name),
                    )
                else:
                    self.conn.execute(
                        """
                        UPDATE accounts
                        SET account_type = ?, is_active = ?, updated_at = ?
                        WHERE name = ?
                        """,
                        (account_type, int(bool(is_active)), now, original_name),
                    )
            else:
                self.conn.execute(
                    """
                    INSERT INTO accounts (name, account_type, is_active, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (normalized_name, account_type, int(bool(is_active)), now, now),
                )

    def delete_account(self, account_name: str) -> None:
        if self.get_account_usage_count(account_name) > 0:
            raise ValueError("Accounts with existing transactions cannot be deleted.")

        with self.conn:
            self.conn.execute("DELETE FROM accounts WHERE name = ?", (account_name,))

    def export_transactions_csv(self, transactions: pd.DataFrame) -> bytes:
        export_df = transactions.copy()
        if export_df.empty:
            return "id,date,description,category,subcategory,debit_account,credit_account,amount,source\n".encode("utf-8")

        if "date" in export_df.columns:
            export_df["date"] = pd.to_datetime(export_df["date"], errors="coerce").dt.strftime("%Y-%m-%d")
        return export_df.to_csv(index=False).encode("utf-8")
