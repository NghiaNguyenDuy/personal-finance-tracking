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

ACCOUNT_OPTIONS = [
    "Asset:Receivable",
    "Asset:Savings",
    "Cash",
    "Equity:General",
    "Equity:Opening Balance",
    "Expense",
    "Income:Salary",
    "Liability:Payable",
]

DEFAULT_DEBIT_ACCOUNTS = [
    "Cash",
    "Expense",
    "Asset:Receivable",
    "Asset:Savings",
    "Liability:Payable",
    "Equity:General",
]

DEFAULT_CREDIT_ACCOUNTS = [
    "Cash",
    "Income:Salary",
    "Liability:Payable",
    "Equity:Opening Balance",
    "Equity:General",
    "Asset:Savings",
    "Asset:Receivable",
]

ROOT_DIR = Path(__file__).resolve().parent.parent
DB_PATH = ROOT_DIR / "finance.db"

SOURCE_TCB_IMAGE = "tcb_image"
SOURCE_HSBC = "hsbc"

DEFAULT_SETTINGS = {
    "hsbc_password": "16Jan2001281717",
    "hsbc_folder": str(ROOT_DIR / "raw_data" / "hsbc"),
    "tcb_image_folder": str(ROOT_DIR / "raw_data" / "tcb" / "images"),
    "tesseract_cmd": "",
    "default_tcb_cash_account": "Cash",
    "default_tcb_offset_account": "Expense",
    "default_hsbc_liability_account": "Liability:Payable",
}

HSBC_PAYMENT_KEYWORDS = ("CARDHOLDER PAYMENT", "PAYMENT RECEIVED", "PAYMENT", "TRANSFER")
HSBC_CREDIT_KEYWORDS = ("CASHBACK", "REFUND", "REVERSAL", "CREDITED")
HSBC_FEE_KEYWORDS = ("FEE", "LATE CHARGE", "FINANCE CHARGE", "INTEREST")
HSBC_INSTALLMENT_KEYWORDS = ("TRA GOP", "INSTALLMENT")

MERCHANT_CATEGORY_HINTS = {
    "SHOPEE": ("Personal Care/Lifestyle", "Entertainment"),
    "GRAB": ("Transportation", "Public transit fees"),
    "BACHHOAXANH": ("Food", "Groceries"),
    "LONGCHAU": ("Healthcare", "Prescriptions"),
    "CIRCLE K": ("Food", "Groceries"),
    "EMART": ("Food", "Groceries"),
    "YOUTUBE": ("Personal Care/Lifestyle", "Subscriptions"),
    "ZALOPAY": ("Personal Care/Lifestyle", "Subscriptions"),
    "YSL": ("Family/Love/Dependents", "Love"),
    "PAYOO": ("Others", "Other expense"),
}

