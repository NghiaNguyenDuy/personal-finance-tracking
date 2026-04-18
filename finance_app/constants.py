from __future__ import annotations

from pathlib import Path


CATEGORY_MAP = {
    "Income": ["Salary", "Bonuses", "Outsourcing", "Investment income", "Side hustle revenue"],
    "Housing": ["Mortgage/rent", "Property taxes", "HOA fees", "Maintenance"],
    "Food": ["Groceries", "Dining out"],
    "Growth & Learning": ["Courses", "Books", "Workshops", "Certifications", "Tuition"],
    "Transportation": ["Car payments", "Gas", "Insurance", "Maintenance", "Public transit fees"],
    "Utilities": ["Electricity", "Internet", "Phone"],
    "Savings & Investing": ["Emergency fund", "Brokerage accounts"],
    "Debt Payments": ["Credit card debt", "Personal loans", "Bank loans"],
    "Healthcare": ["Insurance premiums", "Copays", "Prescriptions"],
    "Personal Care/Lifestyle": ["Clothing", "Grooming", "Entertainment", "Subscriptions"],
    "Family/Love/Dependents": ["Parental care", "Love", "Childcare", "School fees", "Pet care"],
    "Protection": ["Life insurance", "Disability insurance", "Estate planning"],
    "Others": ["Other expense"],
}

ACCOUNT_TYPES = ("Asset", "Liability", "Equity", "Income", "Expense")
SUPPORTED_CURRENCY = "VND"

DEFAULT_ACCOUNTS = [
    {"name": "Asset:Receivable", "account_type": "Asset"},
    {"name": "Asset:Savings", "account_type": "Asset"},
    {"name": "Cash", "account_type": "Asset"},
    {"name": "Equity:General", "account_type": "Equity"},
    {"name": "Equity:Opening Balance", "account_type": "Equity"},
    {"name": "Expense", "account_type": "Expense"},
    {"name": "Income:Salary", "account_type": "Income"},
    {"name": "Liability:Payable", "account_type": "Liability"},
]

DEFAULT_DEBIT_ACCOUNT = "Expense"
DEFAULT_CREDIT_ACCOUNT = "Cash"

ROOT_DIR = Path(__file__).resolve().parent.parent
ACTIVE_DB_PATH = ROOT_DIR / "data" / "finance.db"
BACKUP_DIR = ROOT_DIR / "backups"
LEGACY_DB_PATH = ROOT_DIR / "finance.db"
PRIMARY_ENTRYPOINT = "system_v2.py"


def infer_account_type_from_name(account_name: str) -> str:
    account_name = str(account_name or "").strip()
    if account_name.startswith("Asset:"):
        return "Asset"
    if account_name.startswith("Liability:"):
        return "Liability"
    if account_name.startswith("Equity:"):
        return "Equity"
    if account_name.startswith("Income:"):
        return "Income"
    if account_name.startswith("Expense:"):
        return "Expense"
    if account_name == "Cash":
        return "Asset"
    if account_name == "Expense":
        return "Expense"
    return "Expense"

