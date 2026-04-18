from __future__ import annotations

from typing import Iterable

from .constants import ACCOUNT_TYPES, CATEGORY_MAP


def normalize_text(value: str) -> str:
    return " ".join(str(value or "").split())


def validate_transaction(
    *,
    description: str,
    category: str,
    subcategory: str,
    debit_account: str,
    credit_account: str,
    amount: float,
    known_accounts: Iterable[str],
) -> list[str]:
    errors: list[str] = []
    normalized_description = normalize_text(description)
    if not normalized_description:
        errors.append("Description is required.")

    if category not in CATEGORY_MAP:
        errors.append("Category is invalid.")
    elif subcategory not in CATEGORY_MAP[category]:
        errors.append("Sub-category must belong to the selected category.")

    account_names = set(known_accounts)
    if not debit_account or debit_account not in account_names:
        errors.append("Debit account is invalid.")
    if not credit_account or credit_account not in account_names:
        errors.append("Credit account is invalid.")
    if debit_account and credit_account and debit_account == credit_account:
        errors.append("Debit and credit accounts must be different.")

    try:
        numeric_amount = float(amount)
    except (TypeError, ValueError):
        numeric_amount = 0
        errors.append("Amount must be a number.")

    if numeric_amount <= 0:
        errors.append("Amount must be greater than zero.")

    return errors


def validate_budget(category: str, monthly_limit: float) -> list[str]:
    errors: list[str] = []
    if category not in CATEGORY_MAP:
        errors.append("Category is invalid.")

    try:
        numeric_limit = float(monthly_limit)
    except (TypeError, ValueError):
        numeric_limit = -1
        errors.append("Monthly budget must be a number.")

    if numeric_limit < 0:
        errors.append("Monthly budget cannot be negative.")

    return errors


def validate_account(name: str, account_type: str, existing_names: Iterable[str], original_name: str | None = None) -> list[str]:
    errors: list[str] = []
    normalized_name = normalize_text(name)
    existing_name_set = set(existing_names)

    if not normalized_name:
        errors.append("Account name is required.")
    if account_type not in ACCOUNT_TYPES:
        errors.append("Account type is invalid.")
    if normalized_name != original_name and normalized_name in existing_name_set:
        errors.append("Account name already exists.")

    return errors
