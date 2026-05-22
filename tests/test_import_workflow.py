from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from finance_app.constants import SOURCE_HSBC, SOURCE_TCB_IMAGE
from finance_app.importers import scan_sources, suggest_posting
from finance_app.repository import FinanceRepository
from finance_app.ui import build_inline_review_columns, build_inline_review_editor_df, extract_inline_review_edits


class ImportWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base = Path(self.temp_dir.name)
        self.repo = FinanceRepository(self.base / "test_finance.db")
        self.hsbc_dir = self.base / "hsbc"
        self.tcb_dir = self.base / "tcb"
        self.hsbc_dir.mkdir()
        self.tcb_dir.mkdir()
        (self.hsbc_dir / "20260322.pdf").write_bytes(b"hsbc-fixture")
        (self.tcb_dir / "IMG_0001.PNG").write_bytes(b"image-fixture")
        self.repo.upsert_setting("hsbc_folder", str(self.hsbc_dir))
        self.repo.upsert_setting("tcb_image_folder", str(self.tcb_dir))

    def tearDown(self) -> None:
        self.repo.close()
        self.temp_dir.cleanup()

    def test_scan_is_idempotent_without_force_reprocess(self) -> None:
        hsbc_rows = [
            {
                "source_type": SOURCE_HSBC,
                "row_index": 1,
                "statement_month": "2026-03",
                "transaction_date": "2026-03-01",
                "post_date": "2026-03-03",
                "event_time": "",
                "description": "Shopee Test",
                "merchant": "Shopee Test",
                "amount": 100000.0,
                "currency": "VND",
                "direction": "outflow",
                "running_balance": None,
                "account_ref": "",
                "row_type": "purchase",
                "confidence": 0.9,
                "parse_notes": "",
                "raw_text": "fixture",
                "row_fingerprint": "hsbc-row-1",
            }
        ]
        tcb_rows = [
            {
                "source_type": SOURCE_TCB_IMAGE,
                "row_index": 1,
                "statement_month": "2026-03",
                "transaction_date": "2026-03-30",
                "post_date": "2026-03-30",
                "event_time": "18:16",
                "description": "ATM withdrawal",
                "merchant": "ATM withdrawal",
                "amount": 3010890.0,
                "currency": "VND",
                "direction": "outflow",
                "running_balance": 5726826.0,
                "account_ref": "1601777999",
                "row_type": "outflow",
                "confidence": 0.85,
                "parse_notes": "",
                "raw_text": "fixture",
                "row_fingerprint": "tcb-row-1",
            }
        ]

        with patch("finance_app.importers.parse_hsbc_pdf", return_value=({"statement_month": "2026-03"}, hsbc_rows)), patch(
            "finance_app.importers.parse_tcb_image",
            return_value=({"statement_month": "2026-03"}, tcb_rows),
        ):
            first = scan_sources(self.repo, force_reprocess=False)
            second = scan_sources(self.repo, force_reprocess=False)
            forced = scan_sources(self.repo, force_reprocess=True)

        self.assertEqual(first["processed"], 2)
        self.assertEqual(first["rows"], 2)
        self.assertEqual(second["processed"], 0)
        self.assertEqual(second["skipped"], 2)
        self.assertEqual(forced["processed"], 2)

        files_df = self.repo.get_source_files_df()
        rows_df = self.repo.get_statement_rows_df()
        self.assertEqual(len(files_df), 2)
        self.assertEqual(len(rows_df), 2)

    def test_posting_same_statement_row_twice_does_not_duplicate_transactions(self) -> None:
        self.repo.upsert_setting("default_hsbc_liability_account", "Liability:Payable")
        source_file_id = self.repo.upsert_source_file(
            batch_id=self.repo.create_import_batch(),
            source_type=SOURCE_HSBC,
            file_name="20260322.pdf",
            file_path=str(self.hsbc_dir / "20260322.pdf"),
            file_hash="abc123",
            statement_month="2026-03",
            parse_status="parsed",
            parse_notes="",
            extraction_engine="fixture",
            raw_metadata={"statement_month": "2026-03"},
        )
        self.repo.replace_statement_rows(
            source_file_id,
            [
                {
                    "source_type": SOURCE_HSBC,
                    "row_fingerprint": "posted-row",
                    "row_index": 1,
                    "statement_month": "2026-03",
                    "transaction_date": "2026-03-01",
                    "post_date": "2026-03-01",
                    "event_time": "",
                    "description": "Shopee Test",
                    "merchant": "Shopee Test",
                    "amount": 100000.0,
                    "currency": "VND",
                    "direction": "outflow",
                    "running_balance": None,
                    "account_ref": "",
                    "row_type": "purchase",
                    "confidence": 0.95,
                    "parse_notes": "",
                    "raw_text": "fixture",
                    "category": "Food",
                    "subcategory": "Groceries",
                    "debit_account": "Expense",
                    "credit_account": "Liability:Payable",
                }
            ],
        )
        row_id = int(self.repo.get_statement_rows_df().iloc[0]["id"])
        first_posted, _ = self.repo.post_statement_rows([row_id], lambda value: "SHOPEE TEST")
        second_posted, messages = self.repo.post_statement_rows([row_id], lambda value: "SHOPEE TEST")

        ledger_df = self.repo.get_ledger()
        self.assertEqual(first_posted, 1)
        self.assertEqual(second_posted, 0)
        self.assertEqual(len(ledger_df), 1)
        self.assertTrue(messages)

    def test_post_statement_rows_skips_duplicate_imported_raw_text(self) -> None:
        source_file_id = self.repo.upsert_source_file(
            batch_id=self.repo.create_import_batch(),
            source_type=SOURCE_TCB_IMAGE,
            file_name="IMG_0001.PNG",
            file_path=str(self.tcb_dir / "IMG_0001.PNG"),
            file_hash="tcb-dup-raw-text",
            statement_month="2026-04",
            parse_status="parsed",
            parse_notes="",
            extraction_engine="fixture",
            raw_metadata={"statement_month": "2026-04"},
        )
        duplicate_raw_text = "Tai khoan 1601777999\nSo tien GD: - 141,887\nThanh toan no the tin dung"
        self.repo.replace_statement_rows(
            source_file_id,
            [
                {
                    "source_type": SOURCE_TCB_IMAGE,
                    "row_fingerprint": "duplicate-notification-a",
                    "row_index": 1,
                    "statement_month": "2026-04",
                    "transaction_date": "2026-04-23",
                    "post_date": "2026-04-23",
                    "event_time": "",
                    "description": "Thanh toan no the tin dung _ So tien 141887",
                    "merchant": "Thanh toan no the tin dung _ So tien 141887",
                    "amount": 141887.0,
                    "currency": "VND",
                    "direction": "outflow",
                    "running_balance": None,
                    "account_ref": "1601777999",
                    "row_type": "outflow",
                    "confidence": 0.95,
                    "parse_notes": "",
                    "raw_text": duplicate_raw_text,
                    "category": "Debt Payments",
                    "subcategory": "Credit card debt",
                    "debit_account": "Expense",
                    "credit_account": "Cash",
                },
                {
                    "source_type": SOURCE_TCB_IMAGE,
                    "row_fingerprint": "duplicate-notification-b",
                    "row_index": 2,
                    "statement_month": "2026-04",
                    "transaction_date": "2026-04-23",
                    "post_date": "2026-04-23",
                    "event_time": "",
                    "description": "Thanh toan no the tin dung _ So tien 141887",
                    "merchant": "Thanh toan no the tin dung _ So tien 141887",
                    "amount": 141887.0,
                    "currency": "VND",
                    "direction": "outflow",
                    "running_balance": None,
                    "account_ref": "1601777999",
                    "row_type": "outflow",
                    "confidence": 0.95,
                    "parse_notes": "",
                    "raw_text": duplicate_raw_text,
                    "category": "Debt Payments",
                    "subcategory": "Credit card debt",
                    "debit_account": "Expense",
                    "credit_account": "Cash",
                },
            ],
        )
        row_ids = self.repo.get_statement_rows_df()["id"].astype(int).tolist()

        posted, messages = self.repo.post_statement_rows(row_ids, lambda value: "CARD PAYMENT")

        self.assertEqual(posted, 1)
        self.assertEqual(len(self.repo.get_ledger()), 1)
        self.assertTrue(any("duplicates posted statement row" in message for message in messages))

    def test_update_statement_row_edits_rejects_category_subcategory_mismatch(self) -> None:
        source_file_id = self.repo.upsert_source_file(
            batch_id=self.repo.create_import_batch(),
            source_type=SOURCE_HSBC,
            file_name="20260322.pdf",
            file_path=str(self.hsbc_dir / "20260322.pdf"),
            file_hash="mismatch123",
            statement_month="2026-03",
            parse_status="parsed",
            parse_notes="",
            extraction_engine="fixture",
            raw_metadata={"statement_month": "2026-03"},
        )
        self.repo.replace_statement_rows(
            source_file_id,
            [
                {
                    "source_type": SOURCE_HSBC,
                    "row_fingerprint": "mismatch-row",
                    "row_index": 1,
                    "statement_month": "2026-03",
                    "transaction_date": "2026-03-01",
                    "post_date": "2026-03-01",
                    "event_time": "",
                    "description": "Mismatch row",
                    "merchant": "Mismatch row",
                    "amount": 50000.0,
                    "currency": "VND",
                    "direction": "outflow",
                    "running_balance": None,
                    "account_ref": "",
                    "row_type": "purchase",
                    "confidence": 0.95,
                    "parse_notes": "",
                    "raw_text": "fixture",
                    "category": "Food",
                    "subcategory": "Groceries",
                    "debit_account": "Expense",
                    "credit_account": "Liability:Payable",
                }
            ],
        )
        row_id = int(self.repo.get_statement_rows_df().iloc[0]["id"])
        with self.assertRaisesRegex(ValueError, "Sub-category must belong to the selected category"):
            self.repo.update_statement_row_edits(
                [
                    {
                        "id": row_id,
                        "transaction_date": "2026-03-01",
                        "description": "Mismatch row",
                        "category": "Food",
                        "subcategory": "Love",
                        "debit_account": "Expense",
                        "credit_account": "Liability:Payable",
                        "amount": 50000,
                    }
                ]
            )

    def test_inline_review_edits_update_statement_classification(self) -> None:
        source_file_id = self.repo.upsert_source_file(
            batch_id=self.repo.create_import_batch(),
            source_type=SOURCE_HSBC,
            file_name="20260322.pdf",
            file_path=str(self.hsbc_dir / "20260322.pdf"),
            file_hash="inline123",
            statement_month="2026-03",
            parse_status="parsed",
            parse_notes="",
            extraction_engine="fixture",
            raw_metadata={"statement_month": "2026-03"},
        )
        self.repo.replace_statement_rows(
            source_file_id,
            [
                {
                    "source_type": SOURCE_HSBC,
                    "row_fingerprint": "inline-row",
                    "row_index": 1,
                    "statement_month": "2026-03",
                    "transaction_date": "2026-03-01",
                    "post_date": "2026-03-01",
                    "event_time": "",
                    "description": "Shopee Test",
                    "merchant": "Shopee Test",
                    "amount": 100000.0,
                    "currency": "VND",
                    "direction": "outflow",
                    "running_balance": None,
                    "account_ref": "",
                    "row_type": "purchase",
                    "confidence": 0.95,
                    "parse_notes": "",
                    "raw_text": "fixture",
                    "category": "Others",
                    "subcategory": "Other expense",
                    "debit_account": "Expense",
                    "credit_account": "Liability:Payable",
                }
            ],
        )
        review_df = self.repo.get_statement_review_df(status="pending")
        original = build_inline_review_editor_df(review_df, set())
        edited = original[build_inline_review_columns()].copy()
        edited.loc[0, "classification"] = "Food / Groceries"
        edited.loc[0, "description"] = "Shopee groceries"

        payload = extract_inline_review_edits(original, edited)
        self.repo.update_statement_row_edits(payload)

        updated = self.repo.get_statement_review_df(status="pending")
        first_row = updated.iloc[0]
        self.assertEqual(first_row["category"], "Food")
        self.assertEqual(first_row["subcategory"], "Groceries")
        self.assertEqual(first_row["description"], "Shopee groceries")
        self.assertEqual(first_row["review_state"], "ready_to_post")

    def test_post_statement_rows_allows_valid_fallback_rows(self) -> None:
        source_file_id = self.repo.upsert_source_file(
            batch_id=self.repo.create_import_batch(),
            source_type=SOURCE_HSBC,
            file_name="20260322.pdf",
            file_path=str(self.hsbc_dir / "20260322.pdf"),
            file_hash="fallback-postable123",
            statement_month="2026-03",
            parse_status="parsed",
            parse_notes="",
            extraction_engine="fixture",
            raw_metadata={"statement_month": "2026-03"},
        )
        self.repo.replace_statement_rows(
            source_file_id,
            [
                {
                    "source_type": SOURCE_HSBC,
                    "row_fingerprint": "fallback-postable-row",
                    "row_index": 1,
                    "statement_month": "2026-03",
                    "transaction_date": "2026-03-01",
                    "post_date": "2026-03-01",
                    "event_time": "",
                    "description": "Fallback but reviewed enough",
                    "merchant": "Fallback but reviewed enough",
                    "amount": 70000.0,
                    "currency": "VND",
                    "direction": "outflow",
                    "running_balance": None,
                    "account_ref": "",
                    "row_type": "purchase",
                    "confidence": 0.95,
                    "parse_notes": "",
                    "raw_text": "fixture",
                    "category": "Others",
                    "subcategory": "Other expense",
                    "debit_account": "Expense",
                    "credit_account": "Liability:Payable",
                }
            ],
        )
        review_df = self.repo.get_statement_review_df(status="pending")
        row_id = int(review_df.iloc[0]["id"])

        posted, messages = self.repo.post_statement_rows([row_id], lambda value: "FALLBACK")

        ledger = self.repo.get_ledger()
        self.assertEqual(review_df.iloc[0]["review_state"], "ready_to_post")
        self.assertEqual(posted, 1)
        self.assertFalse(any("Fallback category" in message for message in messages))
        self.assertEqual(len(ledger), 1)
        self.assertEqual(ledger.iloc[0]["category"], "Others")
        self.assertEqual(ledger.iloc[0]["subcategory"], "Other expense")

    def test_post_statement_rows_posts_only_explicit_visible_ids(self) -> None:
        source_file_id = self.repo.upsert_source_file(
            batch_id=self.repo.create_import_batch(),
            source_type=SOURCE_HSBC,
            file_name="20260322.pdf",
            file_path=str(self.hsbc_dir / "20260322.pdf"),
            file_hash="explicit-visible123",
            statement_month="2026-03",
            parse_status="parsed",
            parse_notes="",
            extraction_engine="fixture",
            raw_metadata={"statement_month": "2026-03"},
        )
        rows = []
        for index, description in enumerate(["Visible ready row", "Hidden ready row"], start=1):
            rows.append(
                {
                    "source_type": SOURCE_HSBC,
                    "row_fingerprint": f"visible-scope-row-{index}",
                    "row_index": index,
                    "statement_month": "2026-03",
                    "transaction_date": f"2026-03-0{index}",
                    "post_date": f"2026-03-0{index}",
                    "event_time": "",
                    "description": description,
                    "merchant": description,
                    "amount": 50000.0 + index,
                    "currency": "VND",
                    "direction": "outflow",
                    "running_balance": None,
                    "account_ref": "",
                    "row_type": "purchase",
                    "confidence": 0.95,
                    "parse_notes": "",
                    "raw_text": f"fixture {index}",
                    "category": "Food",
                    "subcategory": "Groceries",
                    "debit_account": "Expense",
                    "credit_account": "Liability:Payable",
                }
            )
        self.repo.replace_statement_rows(source_file_id, rows)
        review_df = self.repo.get_statement_review_df(status="pending").sort_values("description")
        visible_id = int(review_df[review_df["description"] == "Visible ready row"].iloc[0]["id"])
        hidden_id = int(review_df[review_df["description"] == "Hidden ready row"].iloc[0]["id"])

        posted, _ = self.repo.post_statement_rows([visible_id], lambda value: "VISIBLE")

        refreshed = self.repo.get_statement_review_df()
        self.assertEqual(posted, 1)
        self.assertEqual(len(self.repo.get_ledger()), 1)
        self.assertEqual(
            refreshed[refreshed["id"] == visible_id].iloc[0]["review_status"],
            "posted",
        )
        self.assertEqual(
            refreshed[refreshed["id"] == hidden_id].iloc[0]["review_status"],
            "pending",
        )

    def test_post_statement_rows_blocks_low_confidence_rows(self) -> None:
        source_file_id = self.repo.upsert_source_file(
            batch_id=self.repo.create_import_batch(),
            source_type=SOURCE_HSBC,
            file_name="20260322.pdf",
            file_path=str(self.hsbc_dir / "20260322.pdf"),
            file_hash="fallback123",
            statement_month="2026-03",
            parse_status="parsed",
            parse_notes="",
            extraction_engine="fixture",
            raw_metadata={"statement_month": "2026-03"},
        )
        self.repo.replace_statement_rows(
            source_file_id,
            [
                {
                    "source_type": SOURCE_HSBC,
                    "row_fingerprint": "fallback-row",
                    "row_index": 1,
                    "statement_month": "2026-03",
                    "transaction_date": "2026-03-01",
                    "post_date": "2026-03-01",
                    "event_time": "",
                    "description": "Fallback row",
                    "merchant": "Fallback row",
                    "amount": 70000.0,
                    "currency": "VND",
                    "direction": "outflow",
                    "running_balance": None,
                    "account_ref": "",
                    "row_type": "purchase",
                    "confidence": 0.42,
                    "parse_notes": "needs review",
                    "raw_text": "fixture",
                    "category": "Others",
                    "subcategory": "Other expense",
                    "debit_account": "Expense",
                    "credit_account": "Liability:Payable",
                }
            ],
        )
        row_id = int(self.repo.get_statement_rows_df().iloc[0]["id"])
        posted, messages = self.repo.post_statement_rows([row_id], lambda value: "FALLBACK")
        self.assertEqual(posted, 0)
        self.assertTrue(messages)
        self.assertEqual(len(self.repo.get_ledger()), 0)

    def test_suggest_posting_defaults_cover_hsbc_and_tcb(self) -> None:
        rules = []
        settings = self.repo.get_settings()
        hsbc_purchase = suggest_posting(
            source_type=SOURCE_HSBC,
            description="Shopee shopee.vn VN",
            amount=150000,
            direction="outflow",
            row_type="purchase",
            merchant_rules=rules,
            settings=settings,
        )
        hsbc_payment = suggest_posting(
            source_type=SOURCE_HSBC,
            description="OTHER BANK CARDHOLDER PAYMENT",
            amount=2000000,
            direction="inflow",
            row_type="payment",
            merchant_rules=rules,
            settings=settings,
        )
        tcb_outflow = suggest_posting(
            source_type=SOURCE_TCB_IMAGE,
            description="ATM withdrawal",
            amount=3010890,
            direction="outflow",
            row_type="outflow",
            merchant_rules=rules,
            settings=settings,
        )
        tcb_inflow = suggest_posting(
            source_type=SOURCE_TCB_IMAGE,
            description="Salary transfer",
            amount=25000000,
            direction="inflow",
            row_type="inflow",
            merchant_rules=rules,
            settings=settings,
        )

        self.assertEqual(hsbc_purchase["credit_account"], "Liability:Payable")
        self.assertEqual(hsbc_payment["debit_account"], "Liability:Payable")
        self.assertEqual(tcb_outflow["credit_account"], "Cash")
        self.assertEqual(tcb_inflow["debit_account"], "Cash")

    def test_statement_insights_includes_confidence_and_parse_notes(self) -> None:
        source_file_id = self.repo.upsert_source_file(
            batch_id=self.repo.create_import_batch(),
            source_type=SOURCE_HSBC,
            file_name="20260322.pdf",
            file_path=str(self.hsbc_dir / "20260322.pdf"),
            file_hash="insights123",
            statement_month="2026-03",
            parse_status="parsed",
            parse_notes="fixture notes",
            extraction_engine="fixture",
            raw_metadata={"statement_month": "2026-03"},
        )
        self.repo.replace_statement_rows(
            source_file_id,
            [
                {
                    "source_type": SOURCE_HSBC,
                    "row_fingerprint": "insights-row",
                    "row_index": 1,
                    "statement_month": "2026-03",
                    "transaction_date": "2026-03-01",
                    "post_date": "2026-03-02",
                    "event_time": "",
                    "description": "Fixture merchant",
                    "merchant": "Fixture merchant",
                    "amount": 123456.0,
                    "currency": "VND",
                    "direction": "outflow",
                    "running_balance": None,
                    "account_ref": "",
                    "row_type": "purchase",
                    "confidence": 0.42,
                    "parse_notes": "low OCR confidence",
                    "raw_text": "fixture row",
                    "category": "Others",
                    "subcategory": "Other expense",
                    "debit_account": "Expense",
                    "credit_account": "Liability:Payable",
                }
            ],
        )

        insights_df = self.repo.get_statement_insights()
        self.assertIn("confidence", insights_df.columns)
        self.assertIn("parse_notes", insights_df.columns)
        first_row = insights_df.iloc[0]
        self.assertEqual(float(first_row["confidence"]), 0.42)
        self.assertEqual(first_row["parse_notes"], "low OCR confidence")

    def test_repository_reconciles_secondary_database_transactions(self) -> None:
        primary_path = self.base / "primary.db"
        secondary_path = self.base / "data" / "finance.db"
        secondary_path.parent.mkdir(parents=True, exist_ok=True)

        bootstrap_repo = FinanceRepository(primary_path, secondary_db_paths=[])
        bootstrap_repo.record_transaction(
            "2026-03-01",
            "shared tx",
            "Food",
            "",
            "Expense",
            "Cash",
            100000,
        )
        bootstrap_repo.close()

        secondary_conn = sqlite3.connect(secondary_path)
        secondary_conn.execute(
            """
            CREATE TABLE transactions (
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
        secondary_conn.execute(
            """
            CREATE TABLE budgets (
                category TEXT PRIMARY KEY,
                monthly_limit REAL NOT NULL DEFAULT 0
            )
            """
        )
        secondary_conn.execute(
            """
            INSERT INTO transactions (date, description, category, debit_account, credit_account, amount, subcategory)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ("2026-03-01", "shared tx", "Food", "Expense", "Cash", 100000, "Dining out"),
        )
        secondary_conn.execute(
            """
            INSERT INTO transactions (date, description, category, debit_account, credit_account, amount, subcategory)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ("2026-03-28", "new tx", "Transportation", "Expense", "Liability:Payable", 285000, "Car payments"),
        )
        secondary_conn.execute(
            """
            INSERT INTO budgets (category, monthly_limit) VALUES (?, ?)
            """,
            ("Transportation", 5000000),
        )
        secondary_conn.commit()
        secondary_conn.close()

        reconciled_repo = FinanceRepository(primary_path, secondary_db_paths=[secondary_path])
        try:
            ledger_df = reconciled_repo.get_ledger()
            shared_rows = ledger_df[ledger_df["description"] == "shared tx"]
            new_rows = ledger_df[ledger_df["description"] == "new tx"]
            budgets_df = reconciled_repo.get_budgets()

            self.assertEqual(reconciled_repo.reconciliation_summary["inserted_transactions"], 1)
            self.assertEqual(reconciled_repo.reconciliation_summary["updated_subcategories"], 1)
            self.assertEqual(len(shared_rows), 1)
            self.assertEqual(shared_rows.iloc[0]["subcategory"], "Dining out")
            self.assertEqual(len(new_rows), 1)
            self.assertIn("Transportation", budgets_df["category"].tolist())
        finally:
            reconciled_repo.close()

    def test_repository_does_not_reconcile_secondary_database_by_default(self) -> None:
        primary_path = self.base / "primary_no_auto_reconcile.db"
        secondary_path = self.base / "data" / "finance.db"
        secondary_path.parent.mkdir(parents=True, exist_ok=True)

        bootstrap_repo = FinanceRepository(primary_path, secondary_db_paths=[])
        bootstrap_repo.record_transaction(
            "2026-03-01",
            "primary only",
            "Food",
            "Groceries",
            "Expense",
            "Cash",
            100000,
        )
        bootstrap_repo.close()

        secondary_conn = sqlite3.connect(secondary_path)
        secondary_conn.execute(
            """
            CREATE TABLE transactions (
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
        secondary_conn.execute(
            """
            INSERT INTO transactions (date, description, category, debit_account, credit_account, amount, subcategory)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ("2026-03-02", "secondary should not auto import", "Food", "Expense", "Cash", 50000, "Dining out"),
        )
        secondary_conn.commit()
        secondary_conn.close()

        repo = FinanceRepository(primary_path)
        try:
            ledger_df = repo.get_ledger()
            self.assertEqual(len(ledger_df), 1)
            self.assertEqual(repo.reconciliation_summary["databases_checked"], 0)
            self.assertNotIn("secondary should not auto import", ledger_df["description"].tolist())
        finally:
            repo.close()

    def test_secondary_reconciliation_does_not_duplicate_recategorized_transactions(self) -> None:
        primary_path = self.base / "primary_recategorized.db"
        secondary_path = self.base / "data" / "legacy_finance.db"
        secondary_path.parent.mkdir(parents=True, exist_ok=True)

        bootstrap_repo = FinanceRepository(primary_path, secondary_db_paths=[])
        bootstrap_repo.record_transaction(
            "2025-06-02",
            "Retail VNM HO CHI MINH MPGS BEGROUP",
            "Food",
            "Groceries",
            "Expense",
            "Liability:Payable",
            372000,
        )
        bootstrap_repo.close()

        secondary_conn = sqlite3.connect(secondary_path)
        secondary_conn.execute(
            """
            CREATE TABLE transactions (
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
        secondary_conn.execute(
            """
            INSERT INTO transactions (date, description, category, debit_account, credit_account, amount, subcategory)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "2025-06-02",
                "Retail VNM HO CHI MINH MPGS BEGROUP",
                "food&beverage",
                "Expense",
                "Liability:Payable",
                372000,
                "Other expense",
            ),
        )
        secondary_conn.commit()
        secondary_conn.close()

        reconciled_repo = FinanceRepository(primary_path, secondary_db_paths=[secondary_path])
        try:
            ledger = reconciled_repo.get_ledger()

            self.assertEqual(reconciled_repo.reconciliation_summary["inserted_transactions"], 0)
            self.assertEqual(len(ledger), 1)
            self.assertEqual(ledger.iloc[0]["category"], "Food")
            self.assertEqual(ledger.iloc[0]["subcategory"], "Groceries")
        finally:
            reconciled_repo.close()

    def test_data_quality_audit_and_duplicate_repair_are_review_first(self) -> None:
        first_id = self.repo.record_transaction(
            "2026-04-01",
            "duplicate coffee",
            "Food",
            "Dining out",
            "Expense",
            "Cash",
            45000,
        )
        duplicate_id = self.repo.record_transaction(
            "2026-04-01",
            "duplicate coffee",
            "Others",
            "Other expense",
            "Expense",
            "Cash",
            45000,
        )

        preview = self.repo.preview_duplicate_transaction_repair()
        self.assertEqual(len(preview), 1)
        self.assertEqual(int(preview.iloc[0]["keep_id"]), first_id)
        self.assertIn(str(duplicate_id), preview.iloc[0]["duplicate_ids"])

        audit = self.repo.run_data_quality_audit("unit test")
        self.assertEqual(audit["duplicate_groups"], 1)
        self.assertEqual(audit["duplicate_extra_rows"], 1)
        self.assertEqual(len(self.repo.get_data_quality_runs_df()), 1)
        self.assertEqual(len(self.repo.get_data_quality_findings_df(audit["run_id"])), 1)

        before_repair_count = len(self.repo.get_ledger())
        result = self.repo.apply_duplicate_transaction_repair([duplicate_id], "unit test repair")
        after_repair_count = len(self.repo.get_ledger())

        self.assertEqual(result["deleted_count"], 1)
        self.assertTrue(Path(result["backup_path"]).exists())
        self.assertEqual(before_repair_count - after_repair_count, 1)
        self.assertEqual(len(self.repo.get_ledger_repair_actions_df()), 1)
        self.assertTrue(self.repo.preview_duplicate_transaction_repair().empty)

    def test_repository_saves_legacy_mapping_and_can_apply_to_transactions(self) -> None:
        tx_id = self.repo.record_transaction(
            "2026-03-02",
            "legacy lunch",
            "food&beverage",
            "Other expense",
            "Expense",
            "Cash",
            120000,
        )

        self.repo.upsert_legacy_category_mapping(
            "food&beverage",
            "Other expense",
            "Food",
            "Dining out",
        )
        mappings = self.repo.get_legacy_category_mappings_df()
        self.assertEqual(mappings.iloc[0]["target_category"], "Food")

        updated = self.repo.apply_legacy_category_mapping(
            "food&beverage",
            "Other expense",
            "Food",
            "Dining out",
        )
        ledger = self.repo.get_ledger()
        row = ledger[ledger["id"] == tx_id].iloc[0]
        self.assertEqual(updated, 1)
        self.assertEqual(row["category"], "Food")
        self.assertEqual(row["subcategory"], "Dining out")

    def test_repository_saves_investment_trades_and_price_snapshots(self) -> None:
        trade_id = self.repo.upsert_investment_trade(
            {
                "transaction_id": 123,
                "trade_date": "2026-03-03",
                "action": "buy",
                "ticker": "HPG",
                "quantity": 500,
                "amount": 14175000,
                "fees": 0,
                "parse_confidence": 0.95,
                "review_status": "ready",
                "notes": "",
            }
        )
        self.repo.upsert_investment_trade(
            {
                "transaction_id": 123,
                "trade_date": "2026-03-03",
                "action": "buy",
                "ticker": "HPG",
                "quantity": 500,
                "amount": 14175000,
                "fees": 1000,
                "parse_confidence": 0.95,
                "review_status": "ready",
                "notes": "updated",
            }
        )
        self.repo.set_investment_trade_status([trade_id], "reviewed")
        self.repo.upsert_investment_price_snapshot("HPG", "2026-03-31", 30000, notes="manual")

        trades = self.repo.get_investment_trades_df()
        prices = self.repo.get_investment_price_snapshots_df()
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades.iloc[0]["review_status"], "reviewed")
        self.assertEqual(float(trades.iloc[0]["fees"]), 1000)
        self.assertEqual(prices.iloc[0]["ticker"], "HPG")
        self.assertEqual(float(prices.iloc[0]["price"]), 30000)


if __name__ == "__main__":
    unittest.main()
