import unittest

import pandas as pd

from finance_app.reports import FilterState, apply_filters, build_balance_table, build_monthly_income_expense


class ReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.transactions = pd.DataFrame(
            [
                {
                    "id": 1,
                    "date": "2026-01-05",
                    "description": "Salary",
                    "category": "Income",
                    "subcategory": "Salary",
                    "debit_account": "Cash",
                    "debit_type": "Asset",
                    "credit_account": "Income:Salary",
                    "credit_type": "Income",
                    "amount": 1000.0,
                    "source": "manual",
                    "created_at": "2026-01-05 10:00:00",
                    "updated_at": "2026-01-05 10:00:00",
                },
                {
                    "id": 2,
                    "date": "2026-01-07",
                    "description": "Groceries",
                    "category": "Food",
                    "subcategory": "Groceries",
                    "debit_account": "Expense",
                    "debit_type": "Expense",
                    "credit_account": "Cash",
                    "credit_type": "Asset",
                    "amount": 200.0,
                    "source": "manual",
                    "created_at": "2026-01-07 10:00:00",
                    "updated_at": "2026-01-07 10:00:00",
                },
            ]
        )
        self.accounts = pd.DataFrame(
            [
                {"name": "Cash", "account_type": "Asset", "is_active": True},
                {"name": "Expense", "account_type": "Expense", "is_active": True},
                {"name": "Income:Salary", "account_type": "Income", "is_active": True},
            ]
        )

    def test_apply_filters_by_month(self) -> None:
        filtered = apply_filters(self.transactions, FilterState(months=("2026-01",)))
        self.assertEqual(len(filtered), 2)

    def test_build_balance_table(self) -> None:
        balance = build_balance_table(self.transactions, self.accounts)
        cash_balance = float(balance.loc[balance["account"] == "Cash", "balance"].iloc[0])
        self.assertEqual(cash_balance, 800.0)

    def test_build_monthly_income_expense(self) -> None:
        summary = build_monthly_income_expense(self.transactions)
        self.assertEqual(float(summary.iloc[0]["Income"]), 1000.0)
        self.assertEqual(float(summary.iloc[0]["Expense"]), 200.0)


if __name__ == "__main__":
    unittest.main()
