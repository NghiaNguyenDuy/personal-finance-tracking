import sqlite3
import tempfile
import unittest
from pathlib import Path

from finance_app.db import FinanceRepository


def create_legacy_database(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE transactions (
            id INTEGER PRIMARY KEY,
            date TEXT,
            description TEXT,
            category TEXT,
            subcategory TEXT,
            debit_account TEXT,
            credit_account TEXT,
            amount REAL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE budgets (
            category TEXT PRIMARY KEY,
            monthly_limit REAL NOT NULL DEFAULT 0
        )
        """
    )
    conn.execute(
        """
        INSERT INTO transactions (id, date, description, category, subcategory, debit_account, credit_account, amount)
        VALUES (1, '2026-01-01', 'Salary payment', 'Income', 'Salary', 'Cash', 'Income:Salary', 1000)
        """
    )
    conn.execute("INSERT INTO budgets (category, monthly_limit) VALUES ('Food', 500)")
    conn.commit()
    conn.close()


class RepositoryTests(unittest.TestCase):
    def test_repository_bootstraps_empty_database(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            repo = FinanceRepository(
                db_path=base / "data" / "finance.db",
                legacy_db_path=base / "missing.db",
                backup_dir=base / "backups",
            )
            try:
                counts = repo.get_counts()
                self.assertEqual(counts["transactions"], 0)
                self.assertGreaterEqual(counts["accounts"], 1)
                self.assertTrue((base / "data" / "finance.db").exists())
            finally:
                repo.conn.close()

    def test_repository_imports_legacy_database_and_creates_backup(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            legacy_db = base / "finance.db"
            create_legacy_database(legacy_db)

            repo = FinanceRepository(
                db_path=base / "data" / "finance.db",
                legacy_db_path=legacy_db,
                backup_dir=base / "backups",
            )
            try:
                counts = repo.get_counts()
                self.assertEqual(counts["transactions"], 1)
                self.assertEqual(counts["budgets"], 1)
                self.assertTrue(any((base / "backups").glob("*.db")))
                self.assertIn("created_at", repo.get_transactions().columns)
            finally:
                repo.conn.close()

    def test_repository_crud_and_backup_flow(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            repo = FinanceRepository(
                db_path=base / "data" / "finance.db",
                legacy_db_path=base / "missing.db",
                backup_dir=base / "backups",
            )
            try:
                tx_id = repo.add_transaction(
                    tx_date="2026-03-01",
                    description="Groceries",
                    category="Food",
                    subcategory="Groceries",
                    debit_account="Expense",
                    credit_account="Cash",
                    amount=250,
                )
                repo.update_transaction(
                    tx_id,
                    tx_date="2026-03-02",
                    description="Groceries weekly",
                    category="Food",
                    subcategory="Groceries",
                    debit_account="Expense",
                    credit_account="Cash",
                    amount=300,
                )
                repo.upsert_budget("Food", 1000)
                repo.upsert_account(name="Asset:Wallet", account_type="Asset", is_active=True)

                transactions = repo.get_transactions()
                self.assertEqual(len(transactions), 1)
                self.assertEqual(float(transactions.iloc[0]["amount"]), 300)
                self.assertIn("Asset:Wallet", repo.get_accounts()["name"].tolist())

                backup_path = repo.create_backup("test")
                self.assertTrue(backup_path.exists())

                repo.delete_transaction(tx_id)
                self.assertEqual(repo.get_counts()["transactions"], 0)
            finally:
                repo.conn.close()


if __name__ == "__main__":
    unittest.main()
