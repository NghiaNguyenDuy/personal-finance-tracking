import unittest

from finance_app.validation import validate_account, validate_budget, validate_transaction


class ValidationTests(unittest.TestCase):
    def test_transaction_validation_rejects_blank_description_and_same_account(self) -> None:
        errors = validate_transaction(
            description="   ",
            category="Food",
            subcategory="Groceries",
            debit_account="Cash",
            credit_account="Cash",
            amount=0,
            known_accounts=["Cash", "Expense"],
        )
        self.assertIn("Description is required.", errors)
        self.assertIn("Debit and credit accounts must be different.", errors)
        self.assertIn("Amount must be greater than zero.", errors)

    def test_budget_validation_rejects_negative_amount(self) -> None:
        errors = validate_budget("Food", -1)
        self.assertEqual(errors, ["Monthly budget cannot be negative."])

    def test_account_validation_rejects_duplicates(self) -> None:
        errors = validate_account("Cash", "Asset", ["Cash", "Expense"])
        self.assertEqual(errors, ["Account name already exists."])


if __name__ == "__main__":
    unittest.main()
