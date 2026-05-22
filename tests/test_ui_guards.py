from __future__ import annotations

import ast
import unittest
from pathlib import Path

import pandas as pd

from finance_app.constants import CATEGORY_MAP
from finance_app.ui import (
    build_budget_pattern_frame,
    build_budget_review_suggestions,
    build_classification_options,
    build_common_date_fix_payload,
    build_expense_drilldown_frame,
    build_budget_status_frame,
    build_expense_category_comparison_frame,
    build_expense_change_summary_frame,
    build_expense_merchant_summary_frame,
    build_expense_merchant_summary_from_frame,
    build_inline_review_columns,
    build_inline_review_editor_df,
    build_import_blocker_summary,
    build_expense_subcategory_delta_frame,
    build_expense_volatility_frame,
    build_investment_pnl_trend_frame,
    build_investment_performance_frames,
    build_investment_quality_summary,
    build_investment_reconciliation_frame,
    build_navigation_sections,
    build_recurring_merchant_frame,
    build_subcategory_budget_status_frame,
    classification_label,
    compute_management_snapshot,
    extract_inline_review_edits,
    parse_classification_label,
    parse_investment_trade_candidate,
    normalize_review_subcategory,
    subcategory_options_for_category,
    visible_ready_statement_row_ids,
)


class UiGuardTests(unittest.TestCase):
    def test_all_plotly_charts_have_explicit_keys(self) -> None:
        ui_path = Path("D:/WS_AI_AGENT/personal-finance-tracking/finance_app/ui.py")
        tree = ast.parse(ui_path.read_text(encoding="utf-8"))
        missing_key_lines: list[int] = []

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Attribute):
                continue
            if func.attr != "plotly_chart":
                continue
            has_key = any(keyword.arg == "key" for keyword in node.keywords if keyword.arg is not None)
            if not has_key:
                missing_key_lines.append(node.lineno)

        self.assertEqual(
            missing_key_lines,
            [],
            msg=f"Every st.plotly_chart call must set an explicit key. Missing at lines: {missing_key_lines}",
        )

    def test_inline_review_table_keeps_operational_columns_first(self) -> None:
        self.assertEqual(
            build_inline_review_columns()[:5],
            ["selected", "classification", "amount", "description", "transaction_date"],
        )

    def test_subcategory_options_follow_selected_category(self) -> None:
        self.assertEqual(
            subcategory_options_for_category("Food"),
            CATEGORY_MAP["Food"],
        )
        self.assertEqual(
            normalize_review_subcategory("Food", "Love"),
            CATEGORY_MAP["Food"][0],
        )

    def test_classification_options_cover_every_category_pair(self) -> None:
        expected_count = sum(len(subcategories) for subcategories in CATEGORY_MAP.values())
        options = build_classification_options()

        self.assertEqual(len(options), expected_count)
        self.assertIn("Food / Groceries", options)
        self.assertIn("Others / Other expense", options)

    def test_classification_label_and_parser_use_valid_pairs(self) -> None:
        self.assertEqual(classification_label("Food", "Groceries"), "Food / Groceries")
        self.assertEqual(classification_label("Food", "Love"), "Food / Groceries")
        self.assertEqual(parse_classification_label("Food / Dining out"), ("Food", "Dining out"))
        self.assertEqual(parse_classification_label("not valid"), ("Others", "Other expense"))

    def test_extract_inline_review_edits_returns_changed_pending_rows(self) -> None:
        review_df = pd.DataFrame(
            [
                {
                    "id": 1,
                    "source_file_id": 10,
                    "source_type": "hsbc",
                    "statement_month": "2026-03",
                    "transaction_date": "2026-03-01",
                    "post_date": "2026-03-01",
                    "event_time": "",
                    "description": "Shopee",
                    "merchant": "Shopee",
                    "amount": 100000.0,
                    "direction": "outflow",
                    "running_balance": None,
                    "account_ref": "",
                    "row_type": "purchase",
                    "review_status": "pending",
                    "confidence": 0.95,
                    "parse_notes": "",
                    "category": "Others",
                    "subcategory": "Other expense",
                    "debit_account": "Expense",
                    "credit_account": "Liability:Payable",
                    "raw_text": "fixture",
                    "needs_category": True,
                    "needs_subcategory": True,
                    "low_confidence": False,
                    "review_state": "needs_review",
                    "is_fallback": True,
                },
                {
                    "id": 2,
                    "source_file_id": 10,
                    "source_type": "hsbc",
                    "statement_month": "2026-03",
                    "transaction_date": "2026-03-02",
                    "post_date": "2026-03-02",
                    "event_time": "",
                    "description": "Posted row",
                    "merchant": "Posted row",
                    "amount": 200000.0,
                    "direction": "outflow",
                    "running_balance": None,
                    "account_ref": "",
                    "row_type": "purchase",
                    "review_status": "posted",
                    "confidence": 0.95,
                    "parse_notes": "",
                    "category": "Food",
                    "subcategory": "Groceries",
                    "debit_account": "Expense",
                    "credit_account": "Liability:Payable",
                    "raw_text": "fixture",
                    "needs_category": False,
                    "needs_subcategory": False,
                    "low_confidence": False,
                    "review_state": "posted",
                    "is_fallback": False,
                },
            ]
        )
        original = build_inline_review_editor_df(review_df, set())
        edited = original[build_inline_review_columns()].copy()
        edited.loc[edited["id"] == 1, "classification"] = "Food / Groceries"
        edited.loc[edited["id"] == 1, "description"] = "Shopee groceries"
        edited.loc[edited["id"] == 2, "classification"] = "Food / Dining out"

        payload = extract_inline_review_edits(original, edited)

        self.assertEqual(
            original.loc[original["id"] == 1, "posting_blockers"].iloc[0],
            "Unclassified but postable",
        )
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]["id"], 1)
        self.assertEqual(payload[0]["category"], "Food")
        self.assertEqual(payload[0]["subcategory"], "Groceries")
        self.assertEqual(payload[0]["description"], "Shopee groceries")

    def test_visible_ready_statement_row_ids_include_postable_fallback_only(self) -> None:
        review_df = pd.DataFrame(
            [
                {"id": 1, "review_status": "pending", "review_state": "ready_to_post"},
                {"id": 2, "review_status": "pending", "review_state": "needs_review"},
                {"id": 3, "review_status": "posted", "review_state": "posted"},
                {"id": 4, "review_status": "ignored", "review_state": "ignored"},
                {"id": 5, "review_status": "pending", "review_state": "ready_to_post"},
            ]
        )

        self.assertEqual(visible_ready_statement_row_ids(review_df), [1, 5])

    def test_import_blocker_summary_and_common_date_fix_payload(self) -> None:
        review_df = pd.DataFrame(
            [
                {
                    "id": 1,
                    "review_status": "pending",
                    "invalid_date": True,
                    "low_confidence": False,
                    "needs_accounts": False,
                    "invalid_amount": False,
                    "post_date": "2026-04-02",
                    "statement_month": "2026-04",
                    "description": "Missing transaction date",
                    "category": "Food",
                    "subcategory": "Groceries",
                    "debit_account": "Expense",
                    "credit_account": "Cash",
                    "amount": 100000.0,
                    "source_type": "tcb_image",
                    "raw_text": "raw duplicate",
                },
                {
                    "id": 2,
                    "review_status": "pending",
                    "invalid_date": True,
                    "low_confidence": True,
                    "needs_accounts": False,
                    "invalid_amount": False,
                    "post_date": "",
                    "statement_month": "2026-04",
                    "description": "Missing transaction date",
                    "category": "Others",
                    "subcategory": "Other expense",
                    "debit_account": "Expense",
                    "credit_account": "Cash",
                    "amount": 100000.0,
                    "source_type": "tcb_image",
                    "raw_text": "raw duplicate",
                },
            ]
        )

        blockers = build_import_blocker_summary(review_df)
        fixes = build_common_date_fix_payload(review_df)

        self.assertIn("Invalid date", blockers["blocker"].tolist())
        self.assertIn("Low confidence", blockers["blocker"].tolist())
        self.assertIn("Duplicate imported raw row risk", blockers["blocker"].tolist())
        self.assertEqual(fixes[0]["transaction_date"], "2026-04-02")
        self.assertEqual(fixes[1]["transaction_date"], "2026-04-01")

    def test_extract_inline_review_edits_repairs_invalid_stored_pair(self) -> None:
        review_df = pd.DataFrame(
            [
                {
                    "id": 1,
                    "source_file_id": 10,
                    "source_type": "hsbc",
                    "statement_month": "2026-03",
                    "transaction_date": "2026-03-01",
                    "post_date": "2026-03-01",
                    "event_time": "",
                    "description": "Legacy invalid pair",
                    "merchant": "Legacy invalid pair",
                    "amount": 100000.0,
                    "direction": "outflow",
                    "running_balance": None,
                    "account_ref": "",
                    "row_type": "purchase",
                    "review_status": "pending",
                    "confidence": 0.95,
                    "parse_notes": "",
                    "category": "Food",
                    "subcategory": "Love",
                    "debit_account": "Expense",
                    "credit_account": "Liability:Payable",
                    "raw_text": "fixture",
                    "needs_category": False,
                    "needs_subcategory": True,
                    "low_confidence": False,
                    "review_state": "needs_review",
                    "is_fallback": False,
                }
            ]
        )
        original = build_inline_review_editor_df(review_df, set())
        edited = original[build_inline_review_columns()].copy()

        payload = extract_inline_review_edits(original, edited)

        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]["category"], "Food")
        self.assertEqual(payload[0]["subcategory"], "Groceries")

    def test_navigation_is_management_first(self) -> None:
        self.assertEqual(
            build_navigation_sections(),
            ["Dashboard", "Expenses", "Investments", "Transactions", "Planning", "Imports", "Review & Edit"],
        )

    def test_management_snapshot_handles_ledger_without_statement_rows(self) -> None:
        ledger_df = pd.DataFrame(
            [
                {
                    "id": 1,
                    "date": "2026-03-01",
                    "description": "salary",
                    "category": "Income",
                    "subcategory": "Salary",
                    "debit_account": "Cash",
                    "credit_account": "Income:Salary",
                    "amount": 30000000,
                },
                {
                    "id": 2,
                    "date": "2026-03-02",
                    "description": "groceries",
                    "category": "Food",
                    "subcategory": "Groceries",
                    "debit_account": "Expense",
                    "credit_account": "Cash",
                    "amount": 2000000,
                },
                {
                    "id": 3,
                    "date": "2026-03-03",
                    "description": "card purchase",
                    "category": "Personal Care/Lifestyle",
                    "subcategory": "Entertainment",
                    "debit_account": "Expense",
                    "credit_account": "Liability:Payable",
                    "amount": 1000000,
                },
            ]
        )
        snapshot = compute_management_snapshot(ledger_df, pd.DataFrame())
        self.assertEqual(snapshot["assets_total"], 28000000)
        self.assertEqual(snapshot["liabilities_total"], 1000000)
        self.assertEqual(snapshot["net_worth"], 27000000)
        self.assertEqual(snapshot["current_month_income"], 30000000)
        self.assertEqual(snapshot["current_month_expense"], 3000000)
        self.assertEqual(snapshot["fallback_count"], 0)

    def test_budget_status_frame_compares_budget_and_actual(self) -> None:
        ledger_df = pd.DataFrame(
            [
                {
                    "id": 1,
                    "date": "2026-03-02",
                    "description": "groceries",
                    "category": "Food",
                    "subcategory": "Groceries",
                    "debit_account": "Expense",
                    "credit_account": "Cash",
                    "amount": 2000000,
                },
                {
                    "id": 2,
                    "date": "2026-03-05",
                    "description": "movie",
                    "category": "Personal Care/Lifestyle",
                    "subcategory": "Entertainment",
                    "debit_account": "Expense",
                    "credit_account": "Cash",
                    "amount": 500000,
                },
            ]
        )
        budgets = pd.DataFrame(
            [
                {"category": "Food", "monthly_limit": 1500000},
                {"category": "Personal Care/Lifestyle", "monthly_limit": 700000},
            ]
        )
        status = build_budget_status_frame(ledger_df, budgets, "2026-03")
        food_row = status[status["category"] == "Food"].iloc[0]
        self.assertEqual(float(food_row["actual_amount"]), 2000000)
        self.assertEqual(float(food_row["remaining_budget"]), -500000)
        self.assertEqual(food_row["status"], "Over budget")

    def test_planning_subcategory_patterns_and_suggestions(self) -> None:
        ledger_df = pd.DataFrame(
            [
                {
                    "id": 1,
                    "date": "2026-02-02",
                    "description": "groceries",
                    "category": "Food",
                    "subcategory": "Groceries",
                    "debit_account": "Expense",
                    "credit_account": "Cash",
                    "amount": 2000000,
                },
                {
                    "id": 2,
                    "date": "2026-03-02",
                    "description": "dinner",
                    "category": "Food",
                    "subcategory": "Dining out",
                    "debit_account": "Expense",
                    "credit_account": "Cash",
                    "amount": 2500000,
                },
                {
                    "id": 3,
                    "date": "2026-03-03",
                    "description": "movie",
                    "category": "Personal Care/Lifestyle",
                    "subcategory": "Entertainment",
                    "debit_account": "Expense",
                    "credit_account": "Cash",
                    "amount": 500000,
                },
            ]
        )
        budgets = pd.DataFrame([{"category": "Food", "monthly_limit": 1500000}])

        subcategory_status = build_subcategory_budget_status_frame(ledger_df, budgets, "2026-03")
        patterns = build_budget_pattern_frame(ledger_df, budgets)
        suggestions = build_budget_review_suggestions(ledger_df, budgets, "2026-03")

        self.assertEqual(subcategory_status.iloc[0]["subcategory"], "Dining out")
        self.assertEqual(float(subcategory_status.iloc[0]["category_remaining_budget"]), -1000000)
        self.assertEqual(int(patterns.iloc[0]["overspend_months"]), 2)
        self.assertIn("No budget with spend", suggestions["reason"].tolist())
        self.assertIn("Over budget", suggestions["reason"].tolist())

    def test_expense_category_comparison_tracks_current_vs_previous_month(self) -> None:
        ledger_df = pd.DataFrame(
            [
                {
                    "id": 1,
                    "date": "2026-02-02",
                    "description": "rent",
                    "category": "Housing",
                    "subcategory": "Rent",
                    "debit_account": "Expense",
                    "credit_account": "Cash",
                    "amount": 6000000,
                },
                {
                    "id": 2,
                    "date": "2026-03-02",
                    "description": "rent",
                    "category": "Housing",
                    "subcategory": "Rent",
                    "debit_account": "Expense",
                    "credit_account": "Cash",
                    "amount": 7000000,
                },
                {
                    "id": 3,
                    "date": "2026-03-05",
                    "description": "groceries",
                    "category": "Food",
                    "subcategory": "Groceries",
                    "debit_account": "Expense",
                    "credit_account": "Cash",
                    "amount": 2000000,
                },
            ]
        )

        comparison = build_expense_category_comparison_frame(ledger_df)
        housing_row = comparison[comparison["category"] == "Housing"].iloc[0]
        self.assertEqual(housing_row["current_month"], "2026-03")
        self.assertEqual(housing_row["previous_month"], "2026-02")
        self.assertEqual(float(housing_row["current_amount"]), 7000000)
        self.assertEqual(float(housing_row["previous_amount"]), 6000000)
        self.assertEqual(float(housing_row["delta"]), 1000000)

    def test_expense_volatility_ranks_categories_by_monthly_variation(self) -> None:
        ledger_df = pd.DataFrame(
            [
                {
                    "id": 1,
                    "date": "2026-01-02",
                    "description": "groceries",
                    "category": "Food",
                    "subcategory": "Groceries",
                    "debit_account": "Expense",
                    "credit_account": "Cash",
                    "amount": 1000000,
                },
                {
                    "id": 2,
                    "date": "2026-02-02",
                    "description": "groceries",
                    "category": "Food",
                    "subcategory": "Groceries",
                    "debit_account": "Expense",
                    "credit_account": "Cash",
                    "amount": 3000000,
                },
                {
                    "id": 3,
                    "date": "2026-01-05",
                    "description": "movie",
                    "category": "Personal Care/Lifestyle",
                    "subcategory": "Entertainment",
                    "debit_account": "Expense",
                    "credit_account": "Cash",
                    "amount": 500000,
                },
                {
                    "id": 4,
                    "date": "2026-02-05",
                    "description": "movie",
                    "category": "Personal Care/Lifestyle",
                    "subcategory": "Entertainment",
                    "debit_account": "Expense",
                    "credit_account": "Cash",
                    "amount": 500000,
                },
            ]
        )

        volatility = build_expense_volatility_frame(ledger_df)
        self.assertEqual(volatility.iloc[0]["category"], "Food")
        self.assertGreater(float(volatility.iloc[0]["volatility"]), 0)
        lifestyle_row = volatility[volatility["category"] == "Personal Care/Lifestyle"].iloc[0]
        self.assertEqual(float(lifestyle_row["volatility"]), 0.0)

    def test_expense_merchant_summary_aggregates_selected_month(self) -> None:
        ledger_df = pd.DataFrame(
            [
                {
                    "id": 1,
                    "date": "2026-03-02",
                    "description": "Grab Food",
                    "category": "Food",
                    "subcategory": "Groceries",
                    "debit_account": "Expense",
                    "credit_account": "Cash",
                    "amount": 120000,
                },
                {
                    "id": 2,
                    "date": "2026-03-08",
                    "description": "Grab Food",
                    "category": "Food",
                    "subcategory": "Groceries",
                    "debit_account": "Expense",
                    "credit_account": "Cash",
                    "amount": 180000,
                },
                {
                    "id": 3,
                    "date": "2026-02-08",
                    "description": "Grab Food",
                    "category": "Food",
                    "subcategory": "Groceries",
                    "debit_account": "Expense",
                    "credit_account": "Cash",
                    "amount": 500000,
                },
            ]
        )

        merchant_summary = build_expense_merchant_summary_frame(ledger_df, month="2026-03", top_n=5)
        grab_row = merchant_summary.iloc[0]
        self.assertEqual(grab_row["merchant_or_description"], "Grab Food")
        self.assertEqual(float(grab_row["total_spend"]), 300000)
        self.assertEqual(int(grab_row["transaction_count"]), 2)
        self.assertEqual(float(grab_row["avg_ticket"]), 150000)

    def test_expense_change_summary_and_recurring_merchants(self) -> None:
        ledger_df = pd.DataFrame(
            [
                {
                    "id": 1,
                    "date": "2026-02-02",
                    "description": "Grab",
                    "category": "Transportation",
                    "subcategory": "Public transit fees",
                    "debit_account": "Expense",
                    "credit_account": "Cash",
                    "amount": 200000,
                },
                {
                    "id": 2,
                    "date": "2026-03-02",
                    "description": "Grab",
                    "category": "Transportation",
                    "subcategory": "Public transit fees",
                    "debit_account": "Expense",
                    "credit_account": "Cash",
                    "amount": 500000,
                },
                {
                    "id": 3,
                    "date": "2026-03-03",
                    "description": "Coffee",
                    "category": "Food",
                    "subcategory": "Dining out",
                    "debit_account": "Expense",
                    "credit_account": "Cash",
                    "amount": 100000,
                },
            ]
        )
        expense_df = build_expense_drilldown_frame(ledger_df, include_unclassified=True)

        changes = build_expense_change_summary_frame(expense_df, "2026-03", "2026-02")
        recurring = build_recurring_merchant_frame(expense_df)

        self.assertEqual(changes.iloc[0]["driver_type"], "Category")
        self.assertEqual(changes.iloc[0]["driver"], "Transportation")
        self.assertEqual(float(changes.iloc[0]["delta"]), 300000)
        self.assertEqual(recurring.iloc[0]["merchant_or_description"], "Grab")
        self.assertEqual(int(recurring.iloc[0]["active_months"]), 2)

    def test_expense_drilldown_applies_saved_legacy_mapping_virtually(self) -> None:
        ledger_df = pd.DataFrame(
            [
                {
                    "id": 1,
                    "date": "2026-03-02",
                    "description": "lunch",
                    "category": "food&beverage",
                    "subcategory": "Other expense",
                    "debit_account": "Expense",
                    "credit_account": "Cash",
                    "amount": 120000,
                }
            ]
        )
        mappings = pd.DataFrame(
            [
                {
                    "source_category": "food&beverage",
                    "source_subcategory": "Other expense",
                    "target_category": "Food",
                    "target_subcategory": "Dining out",
                }
            ]
        )

        clean_without_mapping = build_expense_drilldown_frame(ledger_df, pd.DataFrame(), include_unclassified=False)
        clean_with_mapping = build_expense_drilldown_frame(ledger_df, mappings, include_unclassified=False)

        self.assertTrue(clean_without_mapping.empty)
        self.assertEqual(clean_with_mapping.iloc[0]["category"], "Food")
        self.assertEqual(clean_with_mapping.iloc[0]["subcategory"], "Dining out")

    def test_subcategory_delta_and_merchant_summary_respect_filtered_drilldown(self) -> None:
        ledger_df = pd.DataFrame(
            [
                {
                    "id": 1,
                    "date": "2026-02-02",
                    "description": "Grab Food",
                    "category": "Food",
                    "subcategory": "Dining out",
                    "debit_account": "Expense",
                    "credit_account": "Cash",
                    "amount": 100000,
                },
                {
                    "id": 2,
                    "date": "2026-03-02",
                    "description": "Grab Food",
                    "category": "Food",
                    "subcategory": "Dining out",
                    "debit_account": "Expense",
                    "credit_account": "Cash",
                    "amount": 250000,
                },
            ]
        )
        expense_df = build_expense_drilldown_frame(ledger_df, include_unclassified=False)
        delta = build_expense_subcategory_delta_frame(expense_df, "2026-03", "2026-02")
        merchant = build_expense_merchant_summary_from_frame(expense_df[expense_df["month"] == "2026-03"])

        self.assertEqual(float(delta.iloc[0]["delta"]), 150000)
        self.assertEqual(merchant.iloc[0]["merchant_or_description"], "Grab Food")
        self.assertEqual(float(merchant.iloc[0]["total_spend"]), 250000)

    def test_trade_parser_handles_vietnamese_and_english_actions(self) -> None:
        buy_trade = parse_investment_trade_candidate(
            {
                "id": 1,
                "date": "2026-03-03",
                "description": "mua 500 HPG",
                "debit_account": "Asset:Savings",
                "credit_account": "Cash",
                "amount": 14175000,
            }
        )
        sell_trade = parse_investment_trade_candidate(
            {
                "id": 2,
                "date": "2026-03-04",
                "description": "sell 300 STB",
                "debit_account": "Cash",
                "credit_account": "Asset:Savings",
                "amount": 15839000,
            }
        )

        self.assertEqual(buy_trade["action"], "buy")
        self.assertEqual(buy_trade["ticker"], "HPG")
        self.assertEqual(float(buy_trade["quantity"]), 500)
        self.assertEqual(sell_trade["action"], "sell")
        self.assertEqual(sell_trade["ticker"], "STB")

    def test_trade_parser_marks_multi_ticker_rows_for_review(self) -> None:
        trade = parse_investment_trade_candidate(
            {
                "id": 1,
                "date": "2026-03-03",
                "description": "1000 HPG; 1000 HSG; 200 STB",
                "debit_account": "Asset:Savings",
                "credit_account": "Cash",
                "amount": 50355000,
            }
        )

        self.assertEqual(trade["review_status"], "needs_review")
        self.assertIn("Multiple tickers", trade["notes"])

    def test_investment_performance_uses_fifo_and_latest_manual_price(self) -> None:
        trades = pd.DataFrame(
            [
                {
                    "id": 1,
                    "trade_date": "2026-01-01",
                    "action": "buy",
                    "ticker": "HPG",
                    "quantity": 100,
                    "amount": 1000000,
                    "fees": 0,
                    "review_status": "reviewed",
                },
                {
                    "id": 2,
                    "trade_date": "2026-02-01",
                    "action": "buy",
                    "ticker": "HPG",
                    "quantity": 100,
                    "amount": 1200000,
                    "fees": 0,
                    "review_status": "reviewed",
                },
                {
                    "id": 3,
                    "trade_date": "2026-03-01",
                    "action": "sell",
                    "ticker": "HPG",
                    "quantity": 50,
                    "amount": 700000,
                    "fees": 0,
                    "review_status": "reviewed",
                },
            ]
        )
        prices = pd.DataFrame(
            [
                {"ticker": "HPG", "price_date": "2026-03-31", "price": 13000},
            ]
        )

        performance = build_investment_performance_frames(trades, prices)
        holdings = performance["holdings"]
        realized = performance["realized"]

        self.assertEqual(float(realized.iloc[0]["realized_pnl"]), 200000)
        self.assertEqual(float(holdings.iloc[0]["quantity"]), 150)
        self.assertEqual(float(holdings.iloc[0]["market_value"]), 1950000)
        self.assertEqual(float(holdings.iloc[0]["unrealized_pnl"]), 250000)

    def test_investment_quality_reconciliation_and_pnl_trend(self) -> None:
        ledger_df = pd.DataFrame(
            [
                {
                    "id": 1,
                    "date": "2026-03-01",
                    "description": "mua 100 HPG",
                    "category": "Savings & Investing",
                    "subcategory": "Brokerage accounts",
                    "debit_account": "Asset:Savings",
                    "credit_account": "Cash",
                    "amount": 1000000,
                }
            ]
        )
        trades = pd.DataFrame(
            [
                {
                    "id": 1,
                    "trade_date": "2026-03-01",
                    "action": "buy",
                    "ticker": "HPG",
                    "quantity": 100,
                    "amount": 1000000,
                    "fees": 0,
                    "review_status": "reviewed",
                    "notes": "",
                },
                {
                    "id": 2,
                    "trade_date": "2026-03-02",
                    "action": "buy",
                    "ticker": "UNKNOWN",
                    "quantity": 0,
                    "amount": 500000,
                    "fees": 0,
                    "review_status": "needs_review",
                    "notes": "Multiple tickers found without per-trade amounts.",
                },
            ]
        )
        performance = build_investment_performance_frames(trades, pd.DataFrame())
        holdings = performance["holdings"]

        quality = build_investment_quality_summary(trades, holdings)
        reconciliation = build_investment_reconciliation_frame(ledger_df, trades)
        pnl_trend = build_investment_pnl_trend_frame(performance["realized"], holdings)

        self.assertEqual(quality["unreviewed_trades"], 1)
        self.assertEqual(quality["ambiguous_trades"], 1)
        self.assertEqual(quality["missing_price_positions"], 1)
        self.assertIn("Ledger vs reviewed variance", reconciliation["metric"].tolist())
        self.assertEqual(float(pnl_trend.iloc[-1]["unrealized_pnl"]), 0.0)


if __name__ == "__main__":
    unittest.main()
