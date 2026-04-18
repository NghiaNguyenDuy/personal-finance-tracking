from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from finance_app.constants import SOURCE_HSBC, SOURCE_TCB_IMAGE
from finance_app.importers import scan_sources, suggest_posting
from finance_app.repository import FinanceRepository


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
                    "category": "Others",
                    "subcategory": "Other expense",
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


if __name__ == "__main__":
    unittest.main()
