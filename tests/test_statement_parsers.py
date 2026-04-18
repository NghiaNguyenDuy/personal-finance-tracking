from __future__ import annotations

import unittest
from pathlib import Path

from finance_app.importers import (
    dependency_summary,
    extract_image_text,
    parse_hsbc_pdf,
    parse_tcb_ocr_text,
)


class StatementParserTests(unittest.TestCase):
    def test_parse_hsbc_fixture_extracts_metadata_and_rows(self) -> None:
        fixture = Path("D:/WS_AI_AGENT/personal-finance-tracking/raw_data/hsbc/20260322.pdf")
        metadata, rows = parse_hsbc_pdf(fixture, "16Jan2001281717")
        self.assertEqual(metadata["statement_date"], "22/03/2026")
        self.assertEqual(metadata["payment_due_date"], "16/04/2026")
        self.assertGreaterEqual(len(rows), 20)
        self.assertEqual(rows[0]["statement_month"], "2026-03")
        self.assertIn("description", rows[0])

    def test_parse_tcb_ocr_text_extracts_notification_cards(self) -> None:
        sample_text = """
        Biến động số dư
        Tài khoản 1601777999
        Số tiền GD: - 3,010,890
        Số dư: 5,726,826
        RUT TIEN TAI ATM SO THE 478097...7925
        NGAY 30/03/2026
        18:16

        Chủ Nhật, 29 Thg 3, 2026
        Tài khoản 1601777999
        Số tiền GD: + 500,000
        Số dư: 10,342,708
        NGUYEN DUY NGHIA em gui tien thap huong
        16:08
        """
        metadata, rows = parse_tcb_ocr_text(sample_text, image_name="IMG_2537.PNG")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["direction"], "outflow")
        self.assertEqual(rows[0]["transaction_date"], "2026-03-30")
        self.assertEqual(rows[1]["direction"], "inflow")
        self.assertEqual(metadata["statement_month"], "2026-03")

    def test_parse_tcb_ocr_text_handles_realistic_ocr_noise_and_time_tail(self) -> None:
        sample_text = """
        09:21
        Bien dong s6 du
        0383213327 - So tien 50000 VND - Ngay
        30/03/2026 21:33:36

        Tai khoan 1601777999
        S6 tién GD: - 3,010,890
        S6 du: 5,726,826
        RUT TIEN TAI ATM SO THE 478097...7925
        NGAY 30/03/2026

        Tai khoan 1601777999
        S6 tién GD: - 11,605,129
        S6 du: 8,737,716
        Cat tien TK Ngan hang de thuc hien nghia vu
        thanh toan CK

        Tai khoan 1601777999
        S6 tién GD: + 10,000,137
        S6 du: 20,342,845
        TT GOC + LAI TIET KIEM ONLINE:
        STK:14602621335777

        Chu Nhat, 29 Thg 3, 2026
        Tai khoan 1601777999
        S6 tién GD: - 500,000
        S6 du: 10,342,708
        NGUYEN DUY NGHIA em gui tien thap huong
        ong ba. Ma tham chieu 884852

        Tai khoan 1601777999
        S6 tién GD: + 478
        S6 du: 10,842,708
        1601777999

        21:33
        18:16
        17:41
        13:43
        16:08
        01:41
        """
        metadata, rows = parse_tcb_ocr_text(sample_text, image_name="IMG_2537.PNG")
        self.assertEqual(len(rows), 5)
        self.assertEqual(rows[0]["event_time"], "18:16")
        self.assertEqual(rows[1]["transaction_date"], "2026-03-30")
        self.assertEqual(rows[2]["direction"], "inflow")
        self.assertIn("RUT TIEN TAI ATM", rows[0]["description"])
        self.assertNotIn("S6 tién GD", rows[0]["description"])
        self.assertEqual(metadata["statement_month"], "2026-03")

    def test_tcb_image_ocr_fixture_runs_when_tesseract_is_available(self) -> None:
        deps = dependency_summary("")
        if not deps["tesseract_binary"]:
            self.skipTest("Tesseract is not configured in this environment.")

        fixture = Path("D:/WS_AI_AGENT/personal-finance-tracking/raw_data/tcb/images/IMG_2537.PNG")
        text = extract_image_text(fixture)
        self.assertTrue(text.strip())
        metadata, rows = parse_tcb_ocr_text(text, image_name=fixture.name)
        self.assertTrue(rows)
        self.assertIn("statement_month", metadata)


if __name__ == "__main__":
    unittest.main()
