from __future__ import annotations

import json
import re
from collections import deque
from datetime import date

import matplotlib.pyplot as plt
import pandas as pd
import plotly.express as px
import seaborn as sns
import streamlit as st

from .constants import ACCOUNT_OPTIONS, CATEGORY_MAP, DEFAULT_CREDIT_ACCOUNTS, DEFAULT_DEBIT_ACCOUNTS, SOURCE_HSBC, SOURCE_TCB_IMAGE
from .importers import clean_merchant_keyword, dependency_summary, scan_sources
from .repository import FALLBACK_CATEGORY, FALLBACK_SUBCATEGORY, FinanceRepository, classify_account_name


@st.cache_resource
def get_repository() -> FinanceRepository:
    return FinanceRepository()


def _account_index(options: list[str], preferred: str) -> int:
    if preferred in options:
        return options.index(preferred)
    return 0


def _chart_key(prefix: str, name: str) -> str:
    return f"{prefix}_{name}"


REVIEW_QUEUE_COLUMNS = [
    "selected",
    "category",
    "subcategory",
    "amount",
    "description",
    "merchant",
    "transaction_date",
    "confidence",
    "review_state",
    "suggestion_reason",
    "source_type",
    "row_type",
    "statement_month",
    "review_status",
    "id",
]
INLINE_REVIEW_COLUMNS = [
    "selected",
    "classification",
    "amount",
    "description",
    "transaction_date",
    "debit_account",
    "credit_account",
    "confidence",
    "review_state",
    "posting_blockers",
    "suggestion_reason",
    "merchant",
    "source_type",
    "statement_month",
    "review_status",
    "id",
]
INLINE_EDIT_COLUMNS = [
    "classification",
    "amount",
    "description",
    "transaction_date",
    "debit_account",
    "credit_account",
]

MANAGEMENT_NAVIGATION = ["Dashboard", "Expenses", "Investments", "Transactions", "Planning", "Imports", "Review & Edit"]
ESSENTIAL_CATEGORIES = {
    "Housing",
    "Food",
    "Transportation",
    "Utilities",
    "Debt Payments",
    "Healthcare",
    "Family/Love/Dependents",
    "Protection",
}
FIXED_COST_CATEGORIES = {"Housing", "Utilities", "Debt Payments", "Protection"}
CATEGORY_FOCUS_MAP = {
    "Housing": "Essentials",
    "Food": "Essentials",
    "Transportation": "Essentials",
    "Utilities": "Essentials",
    "Debt Payments": "Financial",
    "Healthcare": "Essentials",
    "Family/Love/Dependents": "Family",
    "Protection": "Financial",
    "Savings & Investing": "Financial",
    "Growth & Learning": "Growth",
    "Personal Care/Lifestyle": "Lifestyle",
    "Others": "Unclassified",
}
LEGACY_CATEGORY_SUGGESTIONS = {
    ("food&beverage", "Other expense"): ("Food", "Dining out"),
    ("investment", "Other expense"): ("Savings & Investing", "Brokerage accounts"),
    ("saving", "Other expense"): ("Savings & Investing", "Emergency fund"),
    ("salary", "Other expense"): ("Income", "Salary"),
    ("credit_payment", "Other expense"): ("Debt Payments", "Credit card debt"),
    ("parents", "Other expense"): ("Family/Love/Dependents", "Parental care"),
}
TRADE_ACTION_PATTERN = re.compile(r"\b(mua|buy|ban|bán|sell)\s+([\d,.]+)\s+([A-Z]{2,6})\b", re.IGNORECASE)
TRADE_SYMBOL_PATTERN = re.compile(r"\b([\d,.]+)\s+([A-Z]{2,6})\b")
BUY_WORDS = {"mua", "buy"}
SELL_WORDS = {"ban", "bán", "sell"}
INVESTMENT_CATEGORIES = {"Savings & Investing", "investment", "saving"}
REVIEWED_TRADE_STATUS = "reviewed"
IMPORT_REVIEW_FEEDBACK_KEY = "imports_review_feedback"


def _render_import_review_feedback() -> None:
    messages = st.session_state.pop(IMPORT_REVIEW_FEEDBACK_KEY, [])
    for level, message in messages:
        getattr(st, level, st.info)(message)


def _queue_import_review_feedback(messages: list[tuple[str, str]]) -> None:
    st.session_state[IMPORT_REVIEW_FEEDBACK_KEY] = messages


def _is_fallback_classification(row: pd.Series | dict) -> bool:
    getter = row.get if isinstance(row, dict) else row.__getitem__
    return (
        str(getter("category") or "").strip() == FALLBACK_CATEGORY
        and str(getter("subcategory") or "").strip() == FALLBACK_SUBCATEGORY
    )


def subcategory_options_for_category(category: str) -> list[str]:
    return FinanceRepository.valid_subcategories_for_category(category)


def normalize_review_subcategory(category: str, subcategory: str) -> str:
    _, normalized_subcategory = FinanceRepository.normalize_statement_category_subcategory(category, subcategory)
    return normalized_subcategory


def build_review_queue_columns() -> list[str]:
    return REVIEW_QUEUE_COLUMNS.copy()


def build_inline_review_columns() -> list[str]:
    return INLINE_REVIEW_COLUMNS.copy()


def build_navigation_sections() -> list[str]:
    return MANAGEMENT_NAVIGATION.copy()


def _review_slice_options() -> list[str]:
    return ["All rows", "Needs category", "Needs subcategory", "Low confidence", "Ready to post", "Posted", "Ignored"]


def _suggestion_reason_for_row(row: pd.Series, merchant_rule_keywords: set[str]) -> str:
    keyword = clean_merchant_keyword(row.get("merchant") or row.get("description") or "")
    if keyword and keyword in merchant_rule_keywords:
        return "Matched merchant rule"
    if row.get("source_type") == SOURCE_HSBC:
        if row.get("row_type") == "payment":
            return "Default HSBC payment rule"
        if row.get("row_type") in {"refund", "reversal"}:
            return "Default HSBC refund rule"
        if row.get("row_type") in {"fee", "installment"}:
            return "Default HSBC liability rule"
        return "Default HSBC purchase rule"
    if row.get("source_type") == SOURCE_TCB_IMAGE:
        if row.get("direction") == "inflow":
            return "Default TCB inflow rule"
        return "Default TCB outflow rule"
    return "Manual review needed"


def build_classification_options() -> list[str]:
    return [
        f"{category} / {subcategory}"
        for category, subcategories in CATEGORY_MAP.items()
        for subcategory in subcategories
    ]


def classification_label(category: str, subcategory: str) -> str:
    normalized_category, normalized_subcategory = FinanceRepository.normalize_statement_category_subcategory(
        category,
        subcategory,
    )
    return f"{normalized_category} / {normalized_subcategory}"


def parse_classification_label(label: str) -> tuple[str, str]:
    label = str(label or "").strip()
    if " / " not in label:
        return FinanceRepository.normalize_statement_category_subcategory(label, "")
    category, subcategory = label.split(" / ", 1)
    return FinanceRepository.normalize_statement_category_subcategory(category, subcategory)


def build_review_queue_df(review_df: pd.DataFrame, merchant_rule_keywords: set[str]) -> pd.DataFrame:
    if review_df.empty:
        return review_df

    queue_df = review_df.copy()
    queue_df["transaction_date"] = queue_df["transaction_date"].fillna("")
    queue_df["selected"] = False
    queue_df["suggestion_reason"] = queue_df.apply(
        lambda row: _suggestion_reason_for_row(row, merchant_rule_keywords),
        axis=1,
    )
    queue_df["review_state"] = queue_df["review_state"].str.replace("_", " ").str.title()
    queue_df["amount"] = queue_df["amount"].astype(float)
    queue_df = queue_df.sort_values(
        by=["review_status", "low_confidence", "statement_month", "transaction_date", "id"],
        ascending=[True, False, False, False, False],
    )
    return queue_df


def build_inline_review_editor_df(review_df: pd.DataFrame, merchant_rule_keywords: set[str]) -> pd.DataFrame:
    if review_df.empty:
        return pd.DataFrame(columns=build_inline_review_columns())

    editor_df = build_review_queue_df(review_df, merchant_rule_keywords)
    editor_df["classification"] = editor_df.apply(
        lambda row: classification_label(row.get("category"), row.get("subcategory")),
        axis=1,
    )
    editor_df["transaction_date"] = editor_df.apply(
        lambda row: str(row.get("transaction_date") or row.get("post_date") or ""),
        axis=1,
    )
    editor_df["posting_blockers"] = editor_df.apply(
        _posting_note_for_inline_row,
        axis=1,
    )
    return editor_df


def _posting_note_for_inline_row(row: pd.Series) -> str:
    if str(row.get("review_status") or "") != "pending":
        return ""
    blockers = FinanceRepository._statement_row_validation_errors(row.to_dict())
    if blockers:
        return "; ".join(blockers)
    if _is_fallback_classification(row):
        return "Unclassified but postable"
    return ""


def visible_ready_statement_row_ids(review_df: pd.DataFrame) -> list[int]:
    if review_df.empty:
        return []
    ready_df = review_df[
        review_df["review_status"].eq("pending")
        & review_df["review_state"].eq("ready_to_post")
    ]
    return ready_df["id"].astype(int).tolist()


def build_import_blocker_summary(review_df: pd.DataFrame) -> pd.DataFrame:
    columns = ["blocker", "row_count", "amount"]
    if review_df.empty:
        return pd.DataFrame(columns=columns)

    blocker_rows: list[dict[str, object]] = []
    checks = [
        ("Invalid date", "invalid_date"),
        ("Low confidence", "low_confidence"),
        ("Missing or invalid accounts", "needs_accounts"),
        ("Invalid amount", "invalid_amount"),
    ]
    for label, column in checks:
        if column not in review_df.columns:
            continue
        blocked = review_df[review_df[column].fillna(False)].copy()
        if blocked.empty:
            continue
        blocker_rows.append(
            {
                "blocker": label,
                "row_count": int(len(blocked)),
                "amount": float(blocked["amount"].sum()),
            }
        )

    duplicate_columns = ["source_type", "raw_text", "description", "amount"]
    if all(column in review_df.columns for column in duplicate_columns):
        duplicate_source = review_df[
            review_df["raw_text"].fillna("").astype(str).str.strip().ne("")
            & review_df["review_status"].fillna("").eq("pending")
        ].copy()
        if not duplicate_source.empty:
            duplicate_mask = duplicate_source.duplicated(duplicate_columns, keep=False)
            duplicate_risk = duplicate_source[duplicate_mask]
            if not duplicate_risk.empty:
                blocker_rows.append(
                    {
                        "blocker": "Duplicate imported raw row risk",
                        "row_count": int(len(duplicate_risk)),
                        "amount": float(duplicate_risk["amount"].sum()),
                    }
                )

    if not blocker_rows:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame(blocker_rows, columns=columns).sort_values(["row_count", "amount"], ascending=[False, False])


def build_common_date_fix_payload(review_df: pd.DataFrame) -> list[dict[str, object]]:
    if review_df.empty or "invalid_date" not in review_df.columns:
        return []

    payload: list[dict[str, object]] = []
    invalid_rows = review_df[
        review_df["review_status"].fillna("").eq("pending")
        & review_df["invalid_date"].fillna(False)
    ].copy()
    for _, row in invalid_rows.iterrows():
        post_date = str(row.get("post_date") or "").strip()
        statement_month = str(row.get("statement_month") or "").strip()
        if FinanceRepository._is_valid_transaction_date(post_date):
            repaired_date = post_date
        elif re.fullmatch(r"20\d{2}-\d{2}", statement_month):
            repaired_date = f"{statement_month}-01"
        else:
            continue
        payload.append(
            {
                "id": int(row["id"]),
                "transaction_date": repaired_date,
                "description": str(row.get("description") or "").strip(),
                "category": str(row.get("category") or FALLBACK_CATEGORY).strip(),
                "subcategory": str(row.get("subcategory") or FALLBACK_SUBCATEGORY).strip(),
                "debit_account": str(row.get("debit_account") or "").strip(),
                "credit_account": str(row.get("credit_account") or "").strip(),
                "amount": float(row.get("amount") or 0),
            }
        )
    return payload


def summarize_posting_messages(messages: list[str]) -> dict[str, int]:
    skipped = sum(1 for message in messages if "skipped" in message.lower())
    duplicate_skipped = sum(1 for message in messages if "duplicate" in message.lower())
    already_posted = sum(1 for message in messages if "already posted" in message.lower())
    return {
        "skipped": skipped,
        "duplicate_skipped": duplicate_skipped,
        "already_posted": already_posted,
    }


def extract_inline_review_edits(original_df: pd.DataFrame, edited_df: pd.DataFrame) -> list[dict[str, object]]:
    if original_df.empty or edited_df.empty:
        return []

    original_by_id = original_df.set_index("id", drop=False)
    payload: list[dict[str, object]] = []
    for _, edited_row in edited_df.iterrows():
        row_id = int(edited_row["id"])
        if row_id not in original_by_id.index:
            continue
        original_row = original_by_id.loc[row_id]
        if str(original_row.get("review_status") or "") != "pending":
            continue

        category, subcategory = parse_classification_label(str(edited_row.get("classification") or ""))
        changed = False
        changed = changed or category != str(original_row.get("category") or "").strip()
        changed = changed or subcategory != str(original_row.get("subcategory") or "").strip()
        for column in INLINE_EDIT_COLUMNS:
            original_value = original_row.get(column)
            edited_value = edited_row.get(column)
            if column == "classification":
                continue
            if column == "amount":
                try:
                    changed = changed or float(original_value or 0) != float(edited_value or 0)
                except (TypeError, ValueError):
                    changed = True
                continue
            changed = changed or str(original_value or "") != str(edited_value or "")
        if not changed:
            continue

        payload.append(
            {
                "id": row_id,
                "transaction_date": str(edited_row.get("transaction_date") or "").strip(),
                "description": str(edited_row.get("description") or "").strip(),
                "category": category,
                "subcategory": subcategory,
                "debit_account": str(edited_row.get("debit_account") or "").strip(),
                "credit_account": str(edited_row.get("credit_account") or "").strip(),
                "amount": float(edited_row.get("amount") or 0),
            }
        )
    return payload


def filter_review_queue(review_df: pd.DataFrame, review_slice: str) -> pd.DataFrame:
    if review_df.empty or review_slice == "All rows":
        return review_df
    if review_slice == "Needs category":
        return review_df[review_df["needs_category"]].copy()
    if review_slice == "Needs subcategory":
        return review_df[review_df["needs_subcategory"]].copy()
    if review_slice == "Low confidence":
        return review_df[review_df["low_confidence"]].copy()
    if review_slice == "Ready to post":
        return review_df[review_df["review_state"] == "ready_to_post"].copy()
    if review_slice == "Posted":
        return review_df[review_df["review_status"] == "posted"].copy()
    if review_slice == "Ignored":
        return review_df[review_df["review_status"] == "ignored"].copy()
    return review_df


def _mapping_lookup(category_mapping_df: pd.DataFrame | None) -> dict[tuple[str, str], tuple[str, str]]:
    if category_mapping_df is None or category_mapping_df.empty:
        return {}
    lookup: dict[tuple[str, str], tuple[str, str]] = {}
    for _, row in category_mapping_df.iterrows():
        lookup[
            (
                str(row.get("source_category") or "").strip(),
                str(row.get("source_subcategory") or "").strip(),
            )
        ] = (
            str(row.get("target_category") or "").strip(),
            str(row.get("target_subcategory") or "").strip(),
        )
    return lookup


def _apply_virtual_category_mappings(frame: pd.DataFrame, category_mapping_df: pd.DataFrame | None) -> pd.DataFrame:
    if frame.empty:
        return frame

    mapped = frame.copy()
    mapped["original_category"] = mapped["category"].fillna("").astype(str)
    mapped["original_subcategory"] = mapped["subcategory"].fillna("").astype(str)
    lookup = _mapping_lookup(category_mapping_df)
    if lookup:
        for idx, row in mapped.iterrows():
            key = (str(row["original_category"]).strip(), str(row["original_subcategory"]).strip())
            if key in lookup:
                target_category, target_subcategory = lookup[key]
                mapped.at[idx, "category"] = target_category
                mapped.at[idx, "subcategory"] = target_subcategory
                mapped.at[idx, "category_mapping_applied"] = True
    if "category_mapping_applied" not in mapped.columns:
        mapped["category_mapping_applied"] = False
    else:
        mapped["category_mapping_applied"] = mapped["category_mapping_applied"].apply(
            lambda value: False if value is None or pd.isna(value) else bool(value)
        )
    mapped["valid_category"] = mapped["category"].isin(CATEGORY_MAP.keys())
    mapped["valid_subcategory"] = mapped.apply(
        lambda row: row["subcategory"] in CATEGORY_MAP.get(row["category"], []),
        axis=1,
    )
    mapped["is_legacy_or_unclassified"] = (
        ~mapped["valid_category"]
        | ~mapped["valid_subcategory"]
        | mapped["category"].eq("Others")
        | mapped["subcategory"].eq("Other expense")
    )
    return mapped


def prepare_ledger_analysis_frame(
    ledger_df: pd.DataFrame,
    category_mapping_df: pd.DataFrame | None = None,
) -> pd.DataFrame:
    if ledger_df.empty:
        return ledger_df.copy()

    frame = ledger_df.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame = frame.dropna(subset=["date"]).copy()
    frame["category"] = frame["category"].fillna("")
    frame["subcategory"] = frame["subcategory"].fillna("Other expense")
    frame = _apply_virtual_category_mappings(frame, category_mapping_df)
    frame["month"] = frame["date"].dt.to_period("M").astype(str)
    frame["month_ts"] = frame["date"].dt.to_period("M").dt.to_timestamp()
    frame["debit_type"] = frame["debit_account"].fillna("").apply(classify_account_name)
    frame["credit_type"] = frame["credit_account"].fillna("").apply(classify_account_name)
    frame["expense_focus"] = frame["category"].map(CATEGORY_FOCUS_MAP).fillna("Other")
    frame["expense_nature"] = frame["category"].apply(
        lambda value: "Essential" if value in ESSENTIAL_CATEGORIES else ("Unclassified" if value == "Others" else "Lifestyle")
    )
    frame["cost_structure"] = frame["category"].apply(
        lambda value: "Fixed" if value in FIXED_COST_CATEGORIES else "Variable"
    )
    return frame


def build_balance_table_from_ledger(ledger_df: pd.DataFrame) -> pd.DataFrame:
    if ledger_df.empty:
        return pd.DataFrame(columns=["account", "account_type", "balance"])

    debit = ledger_df.groupby("debit_account")["amount"].sum()
    credit = ledger_df.groupby("credit_account")["amount"].sum()
    accounts = sorted(set(debit.index) | set(credit.index))
    rows: list[dict[str, object]] = []
    for account in accounts:
        rows.append(
            {
                "account": account,
                "account_type": classify_account_name(account),
                "balance": float(debit.get(account, 0) - credit.get(account, 0)),
            }
        )
    return pd.DataFrame(rows).sort_values(by=["account_type", "account"])


def build_monthly_cash_flow_frame(ledger_df: pd.DataFrame) -> pd.DataFrame:
    prepared = prepare_ledger_analysis_frame(ledger_df)
    if prepared.empty:
        return pd.DataFrame(columns=["month", "month_ts", "income", "expense", "net_cash_flow"])

    income = prepared[prepared["credit_type"] == "Income"].groupby(["month", "month_ts"])["amount"].sum().reset_index(name="income")
    expense = prepared[prepared["debit_type"] == "Expense"].groupby(["month", "month_ts"])["amount"].sum().reset_index(name="expense")
    monthly = pd.merge(income, expense, on=["month", "month_ts"], how="outer").fillna(0)
    monthly["net_cash_flow"] = monthly["income"] - monthly["expense"]
    return monthly.sort_values("month_ts")


def build_net_worth_trend_frame(ledger_df: pd.DataFrame) -> pd.DataFrame:
    prepared = prepare_ledger_analysis_frame(ledger_df)
    if prepared.empty:
        return pd.DataFrame(columns=["month", "month_ts", "assets", "liabilities", "net_worth", "payable_balance"])

    monthly_rows: list[dict[str, object]] = []
    for month, month_frame in prepared.sort_values("date").groupby("month", sort=True):
        up_to_month = prepared[prepared["month"] <= month]
        balance = build_balance_table_from_ledger(up_to_month)
        assets_total = float(balance.loc[balance["account_type"] == "Asset", "balance"].sum())
        liabilities_total = float(abs(balance.loc[balance["account_type"] == "Liability", "balance"].sum()))
        payable_balance = 0.0
        if not balance.empty and "Liability:Payable" in balance["account"].values:
            payable_balance = float(abs(balance.loc[balance["account"] == "Liability:Payable", "balance"].iloc[0]))
        monthly_rows.append(
            {
                "month": month,
                "month_ts": month_frame["month_ts"].iloc[0],
                "assets": assets_total,
                "liabilities": liabilities_total,
                "net_worth": assets_total - liabilities_total,
                "payable_balance": payable_balance,
            }
        )
    return pd.DataFrame(monthly_rows).sort_values("month_ts")


def build_budget_editor_frame(budget_df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    budget_map = {}
    if not budget_df.empty:
        budget_map = {row["category"]: float(row["monthly_limit"]) for _, row in budget_df.iterrows()}
    for category in sorted(CATEGORY_MAP.keys()):
        rows.append({"category": category, "monthly_limit": budget_map.get(category, 0.0)})
    return pd.DataFrame(rows)


def build_budget_status_frame(ledger_df: pd.DataFrame, budget_df: pd.DataFrame, month: str) -> pd.DataFrame:
    prepared = prepare_ledger_analysis_frame(ledger_df)
    if prepared.empty:
        return pd.DataFrame(columns=["category", "monthly_limit", "actual_amount", "remaining_budget", "status"])

    expense_df = prepared[(prepared["debit_type"] == "Expense") & (prepared["month"] == month)].copy()
    actual = expense_df.groupby("category")["amount"].sum().reset_index(name="actual_amount")
    budget_frame = build_budget_editor_frame(budget_df)
    merged = pd.merge(budget_frame, actual, on="category", how="left").fillna({"actual_amount": 0.0})
    merged["remaining_budget"] = merged["monthly_limit"] - merged["actual_amount"]
    merged["status"] = merged["remaining_budget"].apply(lambda value: "Over budget" if value < 0 else "On track")
    return merged.sort_values(by=["status", "actual_amount"], ascending=[True, False])


def build_expense_category_comparison_frame(
    ledger_df: pd.DataFrame,
    category_mapping_df: pd.DataFrame | None = None,
) -> pd.DataFrame:
    prepared = prepare_ledger_analysis_frame(ledger_df, category_mapping_df)
    expense_df = prepared[prepared["debit_type"] == "Expense"].copy()
    if expense_df.empty:
        return pd.DataFrame(columns=["category", "current_amount", "previous_amount", "delta", "delta_pct", "current_month", "previous_month"])

    month_options = sorted(expense_df["month"].unique())
    current_month = month_options[-1]
    previous_month = month_options[-2] if len(month_options) > 1 else ""
    current = expense_df[expense_df["month"] == current_month].groupby("category")["amount"].sum().reset_index(name="current_amount")
    previous = (
        expense_df[expense_df["month"] == previous_month].groupby("category")["amount"].sum().reset_index(name="previous_amount")
        if previous_month
        else pd.DataFrame(columns=["category", "previous_amount"])
    )
    comparison = pd.merge(current, previous, on="category", how="outer").fillna(0)
    comparison["delta"] = comparison["current_amount"] - comparison["previous_amount"]
    comparison["delta_pct"] = comparison.apply(
        lambda row: (row["delta"] / row["previous_amount"]) if row["previous_amount"] else 0,
        axis=1,
    )
    comparison["current_month"] = current_month
    comparison["previous_month"] = previous_month
    return comparison.sort_values(by="current_amount", ascending=False)


def build_expense_volatility_frame(
    ledger_df: pd.DataFrame,
    category_mapping_df: pd.DataFrame | None = None,
) -> pd.DataFrame:
    prepared = prepare_ledger_analysis_frame(ledger_df, category_mapping_df)
    expense_df = prepared[prepared["debit_type"] == "Expense"].copy()
    if expense_df.empty:
        return pd.DataFrame(columns=["category", "avg_monthly_spend", "volatility", "latest_month_spend", "volatility_ratio"])

    pivot = expense_df.groupby(["category", "month"])["amount"].sum().unstack(fill_value=0)
    summary = pd.DataFrame(
        {
            "category": pivot.index,
            "avg_monthly_spend": pivot.mean(axis=1),
            "volatility": pivot.std(axis=1).fillna(0),
            "latest_month_spend": pivot.iloc[:, -1] if not pivot.empty else 0,
        }
    ).reset_index(drop=True)
    summary["volatility_ratio"] = summary.apply(
        lambda row: (row["volatility"] / row["avg_monthly_spend"]) if row["avg_monthly_spend"] else 0,
        axis=1,
    )
    return summary.sort_values(by=["volatility", "latest_month_spend"], ascending=[False, False])


def build_expense_merchant_summary_frame(
    ledger_df: pd.DataFrame,
    month: str = "",
    top_n: int = 10,
    category_mapping_df: pd.DataFrame | None = None,
) -> pd.DataFrame:
    prepared = prepare_ledger_analysis_frame(ledger_df, category_mapping_df)
    expense_df = prepared[prepared["debit_type"] == "Expense"].copy()
    if month:
        expense_df = expense_df[expense_df["month"] == month].copy()
    if expense_df.empty:
        return pd.DataFrame(columns=["merchant_or_description", "total_spend", "transaction_count", "avg_ticket"])

    summary = (
        expense_df.groupby("description")["amount"]
        .agg(["sum", "count", "mean"])
        .reset_index()
        .rename(
            columns={
                "description": "merchant_or_description",
                "sum": "total_spend",
                "count": "transaction_count",
                "mean": "avg_ticket",
            }
        )
        .sort_values(by=["total_spend", "transaction_count"], ascending=[False, False])
        .head(top_n)
    )
    return summary


def build_expense_drilldown_frame(
    ledger_df: pd.DataFrame,
    category_mapping_df: pd.DataFrame | None = None,
    include_unclassified: bool = False,
) -> pd.DataFrame:
    prepared = prepare_ledger_analysis_frame(ledger_df, category_mapping_df)
    if prepared.empty:
        return pd.DataFrame()
    expense_df = prepared[prepared["debit_type"] == "Expense"].copy()
    if not include_unclassified and "is_legacy_or_unclassified" in expense_df.columns:
        expense_df = expense_df[~expense_df["is_legacy_or_unclassified"]].copy()
    return expense_df


def build_expense_subcategory_monthly_frame(expense_df: pd.DataFrame) -> pd.DataFrame:
    if expense_df.empty:
        return pd.DataFrame(columns=["month", "month_ts", "category", "subcategory", "amount"])
    return (
        expense_df.groupby(["month", "month_ts", "category", "subcategory"])["amount"]
        .sum()
        .reset_index()
        .sort_values(["month_ts", "category", "subcategory"])
    )


def build_expense_subcategory_delta_frame(
    expense_df: pd.DataFrame,
    current_month: str,
    comparison_month: str,
) -> pd.DataFrame:
    columns = ["category", "subcategory", "current_amount", "comparison_amount", "delta", "delta_pct"]
    if expense_df.empty or not current_month:
        return pd.DataFrame(columns=columns)
    current = (
        expense_df[expense_df["month"] == current_month]
        .groupby(["category", "subcategory"])["amount"]
        .sum()
        .reset_index(name="current_amount")
    )
    comparison = (
        expense_df[expense_df["month"] == comparison_month]
        .groupby(["category", "subcategory"])["amount"]
        .sum()
        .reset_index(name="comparison_amount")
        if comparison_month
        else pd.DataFrame(columns=["category", "subcategory", "comparison_amount"])
    )
    delta = pd.merge(current, comparison, on=["category", "subcategory"], how="outer").fillna(0)
    delta["delta"] = delta["current_amount"] - delta["comparison_amount"]
    delta["delta_pct"] = delta.apply(
        lambda row: (row["delta"] / row["comparison_amount"]) if row["comparison_amount"] else 0,
        axis=1,
    )
    return delta[columns].sort_values(["delta", "current_amount"], ascending=[False, False])


def build_expense_merchant_summary_from_frame(expense_df: pd.DataFrame, top_n: int = 10) -> pd.DataFrame:
    if expense_df.empty:
        return pd.DataFrame(columns=["merchant_or_description", "total_spend", "transaction_count", "avg_ticket"])
    return (
        expense_df.groupby("description")["amount"]
        .agg(["sum", "count", "mean"])
        .reset_index()
        .rename(
            columns={
                "description": "merchant_or_description",
                "sum": "total_spend",
                "count": "transaction_count",
                "mean": "avg_ticket",
            }
        )
        .sort_values(["total_spend", "transaction_count"], ascending=[False, False])
        .head(top_n)
    )


def build_expense_change_summary_frame(
    expense_df: pd.DataFrame,
    current_month: str,
    comparison_month: str,
) -> pd.DataFrame:
    columns = ["driver_type", "driver", "current_amount", "comparison_amount", "delta"]
    if expense_df.empty or not current_month:
        return pd.DataFrame(columns=columns)

    rows: list[dict[str, object]] = []
    driver_specs = [
        ("Category", ["category"]),
        ("Sub-category", ["category", "subcategory"]),
        ("Merchant", ["description"]),
    ]
    for driver_type, group_columns in driver_specs:
        current = (
            expense_df[expense_df["month"] == current_month]
            .groupby(group_columns)["amount"]
            .sum()
            .reset_index(name="current_amount")
        )
        comparison = (
            expense_df[expense_df["month"] == comparison_month]
            .groupby(group_columns)["amount"]
            .sum()
            .reset_index(name="comparison_amount")
            if comparison_month
            else pd.DataFrame(columns=[*group_columns, "comparison_amount"])
        )
        merged = pd.merge(current, comparison, on=group_columns, how="outer").fillna(0)
        if merged.empty:
            continue
        merged["delta"] = merged["current_amount"] - merged["comparison_amount"]
        merged = merged.sort_values(["delta", "current_amount"], ascending=[False, False]).head(3)
        for _, row in merged.iterrows():
            if group_columns == ["category"]:
                driver = str(row["category"])
            elif group_columns == ["category", "subcategory"]:
                driver = f"{row['category']} / {row['subcategory']}"
            else:
                driver = str(row["description"])
            rows.append(
                {
                    "driver_type": driver_type,
                    "driver": driver,
                    "current_amount": float(row["current_amount"]),
                    "comparison_amount": float(row["comparison_amount"]),
                    "delta": float(row["delta"]),
                }
            )
    if not rows:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame(rows, columns=columns).sort_values(["delta", "current_amount"], ascending=[False, False])


def build_recurring_merchant_frame(expense_df: pd.DataFrame, min_months: int = 2, top_n: int = 15) -> pd.DataFrame:
    columns = ["merchant_or_description", "active_months", "transaction_count", "total_spend", "avg_monthly_spend", "latest_month"]
    if expense_df.empty:
        return pd.DataFrame(columns=columns)

    grouped = (
        expense_df.groupby("description")
        .agg(
            active_months=("month", "nunique"),
            transaction_count=("id", "count"),
            total_spend=("amount", "sum"),
            latest_month=("month", "max"),
        )
        .reset_index()
        .rename(columns={"description": "merchant_or_description"})
    )
    recurring = grouped[grouped["active_months"] >= min_months].copy()
    if recurring.empty:
        return pd.DataFrame(columns=columns)
    recurring["avg_monthly_spend"] = recurring["total_spend"] / recurring["active_months"]
    return recurring[columns].sort_values(["total_spend", "active_months"], ascending=[False, False]).head(top_n)


def build_subcategory_budget_status_frame(ledger_df: pd.DataFrame, budget_df: pd.DataFrame, month: str) -> pd.DataFrame:
    columns = [
        "category",
        "subcategory",
        "actual_amount",
        "category_actual",
        "category_budget",
        "subcategory_share",
        "category_remaining_budget",
    ]
    prepared = prepare_ledger_analysis_frame(ledger_df)
    if prepared.empty or not month:
        return pd.DataFrame(columns=columns)

    expense_df = prepared[(prepared["debit_type"] == "Expense") & (prepared["month"] == month)].copy()
    if expense_df.empty:
        return pd.DataFrame(columns=columns)
    subcategory_actual = (
        expense_df.groupby(["category", "subcategory"])["amount"]
        .sum()
        .reset_index(name="actual_amount")
    )
    category_actual = expense_df.groupby("category")["amount"].sum().reset_index(name="category_actual")
    budget_map = budget_df.set_index("category")["monthly_limit"].to_dict() if not budget_df.empty else {}
    merged = pd.merge(subcategory_actual, category_actual, on="category", how="left")
    merged["category_budget"] = merged["category"].map(budget_map).fillna(0).astype(float)
    merged["subcategory_share"] = merged.apply(
        lambda row: row["actual_amount"] / row["category_actual"] if row["category_actual"] else 0,
        axis=1,
    )
    merged["category_remaining_budget"] = merged["category_budget"] - merged["category_actual"]
    return merged[columns].sort_values(["category_remaining_budget", "actual_amount"], ascending=[True, False])


def build_budget_pattern_frame(
    ledger_df: pd.DataFrame,
    budget_df: pd.DataFrame,
    recent_months: int = 3,
) -> pd.DataFrame:
    columns = ["category", "monthly_limit", "months_observed", "overspend_months", "underspend_months", "avg_remaining_budget"]
    prepared = prepare_ledger_analysis_frame(ledger_df)
    if prepared.empty or budget_df.empty:
        return pd.DataFrame(columns=columns)

    expense_df = prepared[prepared["debit_type"] == "Expense"].copy()
    if expense_df.empty:
        return pd.DataFrame(columns=columns)
    latest_months = sorted(expense_df["month"].unique())[-recent_months:]
    actual = expense_df[expense_df["month"].isin(latest_months)].groupby(["category", "month"])["amount"].sum().reset_index()
    budget_map = budget_df.set_index("category")["monthly_limit"].to_dict()
    actual["monthly_limit"] = actual["category"].map(budget_map).fillna(0).astype(float)
    actual = actual[actual["monthly_limit"] > 0].copy()
    if actual.empty:
        return pd.DataFrame(columns=columns)
    actual["remaining_budget"] = actual["monthly_limit"] - actual["amount"]
    summary = (
        actual.groupby("category")
        .agg(
            monthly_limit=("monthly_limit", "max"),
            months_observed=("month", "nunique"),
            overspend_months=("remaining_budget", lambda values: int((values < 0).sum())),
            underspend_months=("remaining_budget", lambda values: int((values > 0).sum())),
            avg_remaining_budget=("remaining_budget", "mean"),
        )
        .reset_index()
    )
    return summary[columns].sort_values(["overspend_months", "avg_remaining_budget"], ascending=[False, True])


def build_budget_review_suggestions(
    ledger_df: pd.DataFrame,
    budget_df: pd.DataFrame,
    month: str,
) -> pd.DataFrame:
    columns = ["reason", "category", "amount", "suggested_action"]
    prepared = prepare_ledger_analysis_frame(ledger_df)
    if prepared.empty or not month:
        return pd.DataFrame(columns=columns)

    expense_month = prepared[(prepared["debit_type"] == "Expense") & (prepared["month"] == month)].copy()
    if expense_month.empty:
        return pd.DataFrame(columns=columns)
    budget_map = budget_df.set_index("category")["monthly_limit"].to_dict() if not budget_df.empty else {}
    actual = expense_month.groupby("category")["amount"].sum().reset_index()
    rows: list[dict[str, object]] = []
    for _, row in actual.iterrows():
        category = str(row["category"])
        amount = float(row["amount"])
        monthly_limit = float(budget_map.get(category, 0) or 0)
        if monthly_limit <= 0 and amount > 0:
            rows.append(
                {
                    "reason": "No budget with spend",
                    "category": category,
                    "amount": amount,
                    "suggested_action": "Set a monthly budget or confirm this category is intentionally unplanned.",
                }
            )
        elif amount > monthly_limit:
            rows.append(
                {
                    "reason": "Over budget",
                    "category": category,
                    "amount": amount - monthly_limit,
                    "suggested_action": "Review this category cap or reduce spend next month.",
                }
            )
    if not rows:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame(rows, columns=columns).sort_values("amount", ascending=False)


def build_legacy_cleanup_candidates(
    ledger_df: pd.DataFrame,
    category_mapping_df: pd.DataFrame | None = None,
) -> pd.DataFrame:
    prepared = prepare_ledger_analysis_frame(ledger_df, category_mapping_df)
    if prepared.empty:
        return pd.DataFrame(
            columns=[
                "source_category",
                "source_subcategory",
                "row_count",
                "total_amount",
                "suggested_category",
                "suggested_subcategory",
                "mapping_status",
            ]
        )
    raw = prepared[
        ~prepared["original_category"].isin(CATEGORY_MAP.keys())
        | ~prepared.apply(lambda row: row["original_subcategory"] in CATEGORY_MAP.get(row["original_category"], []), axis=1)
    ].copy()
    if raw.empty:
        return pd.DataFrame(
            columns=[
                "source_category",
                "source_subcategory",
                "row_count",
                "total_amount",
                "suggested_category",
                "suggested_subcategory",
                "mapping_status",
            ]
        )
    grouped = (
        raw.groupby(["original_category", "original_subcategory"])["amount"]
        .agg(["count", "sum"])
        .reset_index()
        .rename(
            columns={
                "original_category": "source_category",
                "original_subcategory": "source_subcategory",
                "count": "row_count",
                "sum": "total_amount",
            }
        )
    )
    mapping_keys = set(_mapping_lookup(category_mapping_df).keys())
    grouped["suggested_category"] = grouped.apply(
        lambda row: LEGACY_CATEGORY_SUGGESTIONS.get(
            (row["source_category"], row["source_subcategory"]),
            ("Others", "Other expense"),
        )[0],
        axis=1,
    )
    grouped["suggested_subcategory"] = grouped.apply(
        lambda row: LEGACY_CATEGORY_SUGGESTIONS.get(
            (row["source_category"], row["source_subcategory"]),
            ("Others", "Other expense"),
        )[1],
        axis=1,
    )
    grouped["mapping_status"] = grouped.apply(
        lambda row: "Saved mapping" if (row["source_category"], row["source_subcategory"]) in mapping_keys else "Needs mapping",
        axis=1,
    )
    return grouped.sort_values(["mapping_status", "total_amount"], ascending=[False, False])


def _parse_quantity(value: str) -> float:
    try:
        return float(str(value or "").replace(",", "").replace(".", ""))
    except ValueError:
        return 0.0


def _infer_trade_action_from_accounts(debit_account: str, credit_account: str) -> str:
    debit_type = classify_account_name(debit_account)
    credit_type = classify_account_name(credit_account)
    if debit_type == "Asset" and credit_account == "Cash":
        return "buy"
    if debit_account == "Cash" and credit_type == "Asset":
        return "sell"
    return ""


def parse_investment_trade_candidate(row: pd.Series | dict) -> dict[str, object]:
    getter = row.get if isinstance(row, dict) else row.__getitem__
    description = str(getter("description") or "")
    debit_account = str(getter("debit_account") or "")
    credit_account = str(getter("credit_account") or "")
    amount = float(getter("amount") or 0)
    raw_trade_date = getter("date")
    parsed_trade_date = pd.to_datetime(raw_trade_date, errors="coerce")
    trade_date = parsed_trade_date.strftime("%Y-%m-%d") if not pd.isna(parsed_trade_date) else str(raw_trade_date or "")
    notes: list[str] = []
    matches = TRADE_ACTION_PATTERN.findall(description)
    symbol_matches = TRADE_SYMBOL_PATTERN.findall(description)
    inferred_action = _infer_trade_action_from_accounts(debit_account, credit_account)

    action = ""
    ticker = ""
    quantity = 0.0
    confidence = 0.0
    review_status = "needs_review"

    if len(matches) == 1:
        raw_action, raw_quantity, raw_ticker = matches[0]
        action_word = raw_action.lower()
        action = "buy" if action_word in BUY_WORDS else "sell"
        ticker = raw_ticker.upper()
        quantity = _parse_quantity(raw_quantity)
        confidence = 0.95
        review_status = "ready"
        if inferred_action and inferred_action != action:
            notes.append("Keyword action conflicts with account movement.")
            review_status = "needs_review"
            confidence = 0.55
    elif len(matches) > 1:
        notes.append("Multiple explicit trades found in one ledger row; split amounts require review.")
        confidence = 0.45
    elif len(symbol_matches) == 1 and inferred_action:
        raw_quantity, raw_ticker = symbol_matches[0]
        action = inferred_action
        ticker = raw_ticker.upper()
        quantity = _parse_quantity(raw_quantity)
        confidence = 0.7
        review_status = "needs_review"
        notes.append("Action inferred from account movement; review before using in performance.")
    elif len(symbol_matches) > 1:
        action = inferred_action or ""
        notes.append("Multiple tickers found without per-trade amounts.")
        confidence = 0.35
    else:
        action = inferred_action
        notes.append("No ticker and quantity pattern found.")
        confidence = 0.2

    if not ticker:
        ticker = "UNKNOWN"
    if not action:
        action = "buy"
    if quantity <= 0:
        notes.append("Quantity is missing or invalid.")
        review_status = "needs_review"
    if ticker in {"ASS"}:
        notes.append("Ticker may be a typo; review symbol spelling.")
        review_status = "needs_review"
        confidence = min(confidence, 0.6)

    return {
        "transaction_id": int(getter("id")) if getter("id") not in (None, "") else None,
        "trade_date": trade_date,
        "action": action,
        "ticker": ticker,
        "quantity": quantity,
        "amount": amount,
        "fees": 0.0,
        "currency": "VND",
        "parse_confidence": confidence,
        "review_status": review_status,
        "notes": " ".join(notes).strip(),
    }


def is_investment_trade_candidate(row: pd.Series | dict) -> bool:
    getter = row.get if isinstance(row, dict) else row.__getitem__
    category = str(getter("category") or "")
    subcategory = str(getter("subcategory") or "")
    description = str(getter("description") or "")
    if category in INVESTMENT_CATEGORIES and subcategory in {"Brokerage accounts", "Other expense"}:
        return bool(TRADE_ACTION_PATTERN.search(description) or TRADE_SYMBOL_PATTERN.search(description))
    return bool(TRADE_ACTION_PATTERN.search(description))


def build_investment_trade_candidates(ledger_df: pd.DataFrame) -> list[dict[str, object]]:
    prepared = prepare_ledger_analysis_frame(ledger_df)
    if prepared.empty:
        return []
    candidates: list[dict[str, object]] = []
    for _, row in prepared.iterrows():
        if is_investment_trade_candidate(row):
            candidates.append(parse_investment_trade_candidate(row))
    return candidates


def build_investment_performance_frames(
    trades_df: pd.DataFrame,
    price_snapshots_df: pd.DataFrame,
) -> dict[str, object]:
    empty_holdings = pd.DataFrame(
        columns=[
            "ticker",
            "quantity",
            "cost_basis",
            "latest_price",
            "price_date",
            "market_value",
            "unrealized_pnl",
            "realized_pnl",
            "total_pnl",
            "roi_pct",
            "price_missing",
        ]
    )
    empty_realized = pd.DataFrame(columns=["ticker", "trade_date", "quantity", "proceeds", "cost_basis", "realized_pnl"])
    if trades_df.empty:
        return {"holdings": empty_holdings, "realized": empty_realized, "summary": {}}

    trades = trades_df[trades_df["review_status"].eq(REVIEWED_TRADE_STATUS)].copy()
    if trades.empty:
        return {"holdings": empty_holdings, "realized": empty_realized, "summary": {}}

    trades["trade_date"] = pd.to_datetime(trades["trade_date"], errors="coerce")
    trades = trades.dropna(subset=["trade_date"]).sort_values(["trade_date", "id"])
    lots: dict[str, deque[dict[str, float]]] = {}
    realized_rows: list[dict[str, object]] = []

    for _, trade in trades.iterrows():
        ticker = str(trade["ticker"]).upper()
        action = str(trade["action"]).lower()
        quantity = float(trade["quantity"] or 0)
        amount = float(trade["amount"] or 0)
        fees = float(trade.get("fees", 0) or 0)
        if quantity <= 0 or amount <= 0:
            continue
        if action == "buy":
            lots.setdefault(ticker, deque()).append(
                {
                    "quantity": quantity,
                    "unit_cost": (amount + fees) / quantity,
                }
            )
            continue
        if action != "sell":
            continue

        remaining = quantity
        cost_sold = 0.0
        ticker_lots = lots.setdefault(ticker, deque())
        while remaining > 0 and ticker_lots:
            lot = ticker_lots[0]
            consumed = min(remaining, lot["quantity"])
            cost_sold += consumed * lot["unit_cost"]
            lot["quantity"] -= consumed
            remaining -= consumed
            if lot["quantity"] <= 0:
                ticker_lots.popleft()
        proceeds = amount - fees
        realized_rows.append(
            {
                "ticker": ticker,
                "trade_date": trade["trade_date"].strftime("%Y-%m-%d"),
                "quantity": quantity,
                "proceeds": proceeds,
                "cost_basis": cost_sold,
                "realized_pnl": proceeds - cost_sold,
            }
        )

    latest_prices = {}
    if not price_snapshots_df.empty:
        prices = price_snapshots_df.copy()
        prices["price_date"] = pd.to_datetime(prices["price_date"], errors="coerce")
        prices = prices.dropna(subset=["price_date"]).sort_values("price_date")
        for _, price_row in prices.iterrows():
            latest_prices[str(price_row["ticker"]).upper()] = {
                "price": float(price_row["price"]),
                "price_date": price_row["price_date"].strftime("%Y-%m-%d"),
            }

    realized_df = pd.DataFrame(realized_rows, columns=empty_realized.columns)
    realized_by_ticker = realized_df.groupby("ticker")["realized_pnl"].sum().to_dict() if not realized_df.empty else {}
    holding_rows: list[dict[str, object]] = []
    for ticker, ticker_lots in lots.items():
        quantity = sum(float(lot["quantity"]) for lot in ticker_lots)
        if quantity <= 0:
            continue
        cost_basis = sum(float(lot["quantity"]) * float(lot["unit_cost"]) for lot in ticker_lots)
        latest = latest_prices.get(ticker, {})
        latest_price = float(latest.get("price", 0) or 0)
        price_missing = latest_price <= 0
        market_value = quantity * latest_price if not price_missing else cost_basis
        unrealized_pnl = market_value - cost_basis if not price_missing else 0.0
        realized_pnl = float(realized_by_ticker.get(ticker, 0))
        total_pnl = realized_pnl + unrealized_pnl
        holding_rows.append(
            {
                "ticker": ticker,
                "quantity": quantity,
                "cost_basis": cost_basis,
                "latest_price": latest_price,
                "price_date": latest.get("price_date", ""),
                "market_value": market_value,
                "unrealized_pnl": unrealized_pnl,
                "realized_pnl": realized_pnl,
                "total_pnl": total_pnl,
                "roi_pct": total_pnl / cost_basis if cost_basis else 0.0,
                "price_missing": price_missing,
            }
        )
    holdings_df = pd.DataFrame(holding_rows, columns=empty_holdings.columns).sort_values("market_value", ascending=False)
    summary = {
        "cost_basis": float(holdings_df["cost_basis"].sum()) if not holdings_df.empty else 0.0,
        "market_value": float(holdings_df["market_value"].sum()) if not holdings_df.empty else 0.0,
        "unrealized_pnl": float(holdings_df["unrealized_pnl"].sum()) if not holdings_df.empty else 0.0,
        "realized_pnl": float(realized_df["realized_pnl"].sum()) if not realized_df.empty else 0.0,
        "total_pnl": (
            float(holdings_df["unrealized_pnl"].sum()) if not holdings_df.empty else 0.0
        )
        + (float(realized_df["realized_pnl"].sum()) if not realized_df.empty else 0.0),
    }
    summary["roi_pct"] = summary["total_pnl"] / summary["cost_basis"] if summary["cost_basis"] else 0.0
    return {"holdings": holdings_df, "realized": realized_df, "summary": summary}


def build_investment_quality_summary(
    trades_df: pd.DataFrame,
    holdings_df: pd.DataFrame,
) -> dict[str, int]:
    if trades_df.empty:
        return {
            "unreviewed_trades": 0,
            "ambiguous_trades": 0,
            "missing_price_positions": int(holdings_df["price_missing"].sum()) if not holdings_df.empty else 0,
            "open_positions": int(len(holdings_df)),
        }

    active_trades = trades_df[~trades_df["review_status"].isin([REVIEWED_TRADE_STATUS, "ignored"])].copy()
    notes = trades_df["notes"].fillna("").astype(str).str.lower() if "notes" in trades_df.columns else pd.Series(dtype=str)
    ambiguous_mask = notes.str.contains("multiple|ambiguous|missing|conflict|typo", regex=True, na=False)
    return {
        "unreviewed_trades": int(len(active_trades)),
        "ambiguous_trades": int(ambiguous_mask.sum()),
        "missing_price_positions": int(holdings_df["price_missing"].sum()) if not holdings_df.empty else 0,
        "open_positions": int(len(holdings_df)),
    }


def build_investment_reconciliation_frame(ledger_df: pd.DataFrame, trades_df: pd.DataFrame) -> pd.DataFrame:
    columns = ["metric", "amount"]
    prepared = prepare_ledger_analysis_frame(ledger_df)
    if prepared.empty:
        return pd.DataFrame(columns=columns)

    investment_cash_flow = prepared[
        prepared["category"].isin(INVESTMENT_CATEGORIES)
        | prepared["subcategory"].isin(["Brokerage accounts", "Emergency fund"])
    ].copy()
    ledger_outflow = float(investment_cash_flow.loc[investment_cash_flow["debit_type"] == "Expense", "amount"].sum())
    ledger_asset_increase = float(investment_cash_flow.loc[investment_cash_flow["debit_type"] == "Asset", "amount"].sum())
    ledger_cash_signal = ledger_asset_increase + ledger_outflow

    reviewed = trades_df[trades_df["review_status"].eq(REVIEWED_TRADE_STATUS)].copy() if not trades_df.empty else pd.DataFrame()
    buy_cash = 0.0
    sell_cash = 0.0
    if not reviewed.empty:
        buy_cash = float(reviewed.loc[reviewed["action"].str.lower().eq("buy"), "amount"].sum())
        sell_cash = float(reviewed.loc[reviewed["action"].str.lower().eq("sell"), "amount"].sum())
    trade_net_outflow = buy_cash - sell_cash
    return pd.DataFrame(
        [
            {"metric": "Ledger investment cash signal", "amount": ledger_cash_signal},
            {"metric": "Reviewed buy cash", "amount": buy_cash},
            {"metric": "Reviewed sell cash", "amount": sell_cash},
            {"metric": "Reviewed net trade outflow", "amount": trade_net_outflow},
            {"metric": "Ledger vs reviewed variance", "amount": ledger_cash_signal - trade_net_outflow},
        ],
        columns=columns,
    )


def build_investment_pnl_trend_frame(realized_df: pd.DataFrame, holdings_df: pd.DataFrame) -> pd.DataFrame:
    columns = ["month", "realized_pnl", "unrealized_pnl", "total_pnl"]
    realized_monthly = pd.DataFrame(columns=["month", "realized_pnl"])
    if not realized_df.empty:
        realized = realized_df.copy()
        realized["trade_date"] = pd.to_datetime(realized["trade_date"], errors="coerce")
        realized = realized.dropna(subset=["trade_date"])
        if not realized.empty:
            realized["month"] = realized["trade_date"].dt.to_period("M").astype(str)
            realized_monthly = realized.groupby("month")["realized_pnl"].sum().reset_index()

    unrealized_pnl = float(holdings_df["unrealized_pnl"].sum()) if not holdings_df.empty else 0.0
    latest_month = realized_monthly["month"].max() if not realized_monthly.empty else date.today().strftime("%Y-%m")
    if realized_monthly.empty:
        realized_monthly = pd.DataFrame([{"month": latest_month, "realized_pnl": 0.0}])
    realized_monthly["unrealized_pnl"] = 0.0
    realized_monthly.loc[realized_monthly["month"].eq(latest_month), "unrealized_pnl"] = unrealized_pnl
    realized_monthly["total_pnl"] = realized_monthly["realized_pnl"] + realized_monthly["unrealized_pnl"]
    return realized_monthly[columns].sort_values("month")


def compute_management_snapshot(ledger_df: pd.DataFrame, statement_df: pd.DataFrame | None = None) -> dict[str, float]:
    monthly = build_monthly_cash_flow_frame(ledger_df)
    net_worth = build_net_worth_trend_frame(ledger_df)
    balance = build_balance_table_from_ledger(ledger_df)
    prepared = prepare_ledger_analysis_frame(ledger_df)

    assets_total = float(balance.loc[balance["account_type"] == "Asset", "balance"].sum()) if not balance.empty else 0.0
    liabilities_total = float(abs(balance.loc[balance["account_type"] == "Liability", "balance"].sum())) if not balance.empty else 0.0
    current_month = monthly["month"].iloc[-1] if not monthly.empty else ""
    current_income = float(monthly["income"].iloc[-1]) if not monthly.empty else 0.0
    current_expense = float(monthly["expense"].iloc[-1]) if not monthly.empty else 0.0
    current_net = float(monthly["net_cash_flow"].iloc[-1]) if not monthly.empty else 0.0
    savings_rate = (current_net / current_income) if current_income else 0.0
    positive_cash_flow_months = int((monthly["net_cash_flow"] > 0).sum()) if not monthly.empty else 0
    latest_payable = float(net_worth["payable_balance"].iloc[-1]) if not net_worth.empty else 0.0
    prior_payable = float(net_worth["payable_balance"].iloc[-2]) if len(net_worth) > 1 else latest_payable
    liability_pressure = latest_payable - prior_payable

    essential_spend = float(
        prepared.loc[(prepared["debit_type"] == "Expense") & (prepared["category"].isin(ESSENTIAL_CATEGORIES)), "amount"].sum()
    ) if not prepared.empty else 0.0
    discretionary_spend = float(
        prepared.loc[(prepared["debit_type"] == "Expense") & (~prepared["category"].isin(ESSENTIAL_CATEGORIES)) & (prepared["category"] != "Others"), "amount"].sum()
    ) if not prepared.empty else 0.0
    total_expense = float(prepared.loc[prepared["debit_type"] == "Expense", "amount"].sum()) if not prepared.empty else 0.0
    essential_share = (essential_spend / total_expense) if total_expense else 0.0
    discretionary_share = (discretionary_spend / total_expense) if total_expense else 0.0
    cash_accounts = balance.loc[balance["account"].isin(["Cash", "Asset:Savings", "Asset:Receivable"]), ["account", "balance"]] if not balance.empty else pd.DataFrame(columns=["account", "balance"])
    liquidity_total = float(cash_accounts["balance"].sum()) if not cash_accounts.empty else 0.0

    fallback_amount = 0.0
    fallback_count = 0
    review_backlog = 0
    if statement_df is not None and not statement_df.empty:
        fallback_df = statement_df[statement_df["is_fallback"] | statement_df["needs_subcategory"]]
        fallback_amount = float(fallback_df["amount"].sum())
        fallback_count = int(len(fallback_df))
        review_backlog = int((statement_df["review_state"] == "needs_review").sum())

    return {
        "assets_total": assets_total,
        "liabilities_total": liabilities_total,
        "net_worth": assets_total - liabilities_total,
        "current_month_income": current_income,
        "current_month_expense": current_expense,
        "current_month_net_cash_flow": current_net,
        "savings_rate": savings_rate,
        "positive_cash_flow_months": positive_cash_flow_months,
        "liability_pressure": liability_pressure,
        "discretionary_spend_share": discretionary_share,
        "essential_spend_share": essential_share,
        "liquidity_total": liquidity_total,
        "fallback_amount": fallback_amount,
        "fallback_count": fallback_count,
        "review_backlog": review_backlog,
        "current_month": current_month,
    }


def render_expenses_tab(repository: FinanceRepository) -> None:
    st.header("Expenses")
    st.caption("Drill from category to sub-category, merchant, and transaction using posted ledger rows by default.")

    ledger_df = repository.get_ledger()
    mapping_df = repository.get_legacy_category_mappings_df()
    statement_df = repository.get_statement_review_df()
    pending_statement_count = 0
    if not statement_df.empty:
        pending_statement_count = int(statement_df[~statement_df["review_status"].isin(["posted", "ignored"])].shape[0])
    if pending_statement_count:
        st.warning(f"{pending_statement_count:,} pending statement rows are not included in clean expense analytics.")

    include_unclassified = st.checkbox(
        "Include unclassified/legacy rows",
        value=False,
        key="expenses_include_unclassified",
        help="Default off keeps charts focused on canonical category/sub-category rows.",
    )
    expense_df = build_expense_drilldown_frame(ledger_df, mapping_df, include_unclassified=include_unclassified)
    if expense_df.empty:
        st.info("No posted ledger expenses match the current clean-data setting.")
        _render_legacy_cleanup_panel(repository, ledger_df, mapping_df)
        return

    month_options = sorted(expense_df["month"].unique(), reverse=True)
    selected_month = st.selectbox("Expense month", month_options, index=0, key="expense_month")
    comparison_options = [month for month in month_options if month != selected_month]
    comparison_month = st.selectbox(
        "Compare with",
        [""] + comparison_options,
        index=1 if comparison_options else 0,
        format_func=lambda value: value or "No comparison",
        key="expense_comparison_month",
    )
    category_options = ["All"] + sorted(expense_df["category"].dropna().unique())
    selected_category = st.selectbox("Category", category_options, key="expense_category")
    category_filtered = expense_df if selected_category == "All" else expense_df[expense_df["category"] == selected_category].copy()
    subcategory_options = ["All"] + sorted(category_filtered["subcategory"].dropna().unique())
    selected_subcategory = st.selectbox("Sub-category", subcategory_options, key="expense_subcategory")
    merchant_search = st.text_input("Merchant / description search", key="expense_merchant_search")

    filtered = category_filtered.copy()
    if selected_subcategory != "All":
        filtered = filtered[filtered["subcategory"] == selected_subcategory].copy()
    if merchant_search:
        filtered = filtered[filtered["description"].fillna("").str.contains(merchant_search, case=False, na=False)].copy()

    current_df = filtered[filtered["month"] == selected_month].copy()
    comparison_df = filtered[filtered["month"] == comparison_month].copy() if comparison_month else pd.DataFrame(columns=filtered.columns)
    all_current_with_unclassified = build_expense_drilldown_frame(ledger_df, mapping_df, include_unclassified=True)
    unclassified_current = all_current_with_unclassified[
        (all_current_with_unclassified["month"] == selected_month) & all_current_with_unclassified["is_legacy_or_unclassified"]
    ].copy()

    current_total = float(current_df["amount"].sum())
    comparison_total = float(comparison_df["amount"].sum()) if not comparison_df.empty else 0.0
    top_category = current_df.groupby("category")["amount"].sum().sort_values(ascending=False).head(1)
    top_subcategory = current_df.groupby("subcategory")["amount"].sum().sort_values(ascending=False).head(1)
    top_merchant = build_expense_merchant_summary_from_frame(current_df, top_n=1)
    change_summary = build_expense_change_summary_frame(filtered, selected_month, comparison_month)
    recurring_merchants = build_recurring_merchant_frame(filtered)
    recurring_drain = float(recurring_merchants["total_spend"].sum()) if not recurring_merchants.empty else 0.0

    card1, card2, card3, card4, card5, card6 = st.columns(6)
    card1.metric("Total Expense", f"{current_total:,.0f} VND", f"{current_total - comparison_total:,.0f} VND" if comparison_month else None)
    card2.metric("Top Category", top_category.index[0] if not top_category.empty else "N/A", f"{float(top_category.iloc[0]):,.0f} VND" if not top_category.empty else None)
    card3.metric("Top Sub-category", top_subcategory.index[0] if not top_subcategory.empty else "N/A", f"{float(top_subcategory.iloc[0]):,.0f} VND" if not top_subcategory.empty else None)
    card4.metric("Top Merchant", str(top_merchant.iloc[0]["merchant_or_description"]) if not top_merchant.empty else "N/A")
    card5.metric("Unclassified Amount", f"{unclassified_current['amount'].sum():,.0f} VND")
    card6.metric("Recurring Drain", f"{recurring_drain:,.0f} VND", f"{len(recurring_merchants):,} merchants")

    change_col, recurring_col = st.columns(2)
    with change_col:
        st.subheader("What Changed This Month")
        if change_summary.empty:
            st.info("Choose a comparison month to see movement drivers.")
        else:
            st.dataframe(
                change_summary.style.format("{:,.0f}", subset=["current_amount", "comparison_amount", "delta"]),
                use_container_width=True,
            )
    with recurring_col:
        st.subheader("Recurring Merchant Drain")
        if recurring_merchants.empty:
            st.info("No merchants repeat across enough months in this filtered view.")
        else:
            st.dataframe(
                recurring_merchants.style.format("{:,.0f}", subset=["total_spend", "avg_monthly_spend"]),
                use_container_width=True,
            )

    drill_col, trend_col = st.columns(2)
    with drill_col:
        st.subheader("Category -> Sub-category -> Merchant")
        if current_df.empty:
            st.info("No expenses match the active drill-down filters.")
        else:
            fig = px.treemap(
                current_df,
                path=["category", "subcategory", "description"],
                values="amount",
                color="amount",
                color_continuous_scale="Reds",
            )
            st.plotly_chart(fig, use_container_width=True, key=_chart_key("expenses", "treemap"))
    with trend_col:
        st.subheader("Sub-category Trend")
        monthly_subcategories = build_expense_subcategory_monthly_frame(filtered)
        if monthly_subcategories.empty:
            st.info("No sub-category trend available.")
        else:
            trend_source = monthly_subcategories.sort_values("amount", ascending=False).head(12)
            focus_pairs = set(zip(trend_source["category"], trend_source["subcategory"]))
            trend_df = monthly_subcategories[
                monthly_subcategories.apply(lambda row: (row["category"], row["subcategory"]) in focus_pairs, axis=1)
            ].copy()
            trend_df["label"] = trend_df["category"] + " / " + trend_df["subcategory"]
            fig = px.line(trend_df, x="month_ts", y="amount", color="label", markers=True)
            st.plotly_chart(fig, use_container_width=True, key=_chart_key("expenses", "subcategory_trend"))

    delta_col, merchant_col = st.columns(2)
    with delta_col:
        st.subheader("Category/Sub-category Delta")
        delta_df = build_expense_subcategory_delta_frame(filtered, selected_month, comparison_month)
        if delta_df.empty:
            st.info("Choose a comparison month to see deltas.")
        else:
            st.dataframe(
                delta_df.style.format("{:,.0f}", subset=["current_amount", "comparison_amount", "delta"]).format(
                    "{:.1%}",
                    subset=["delta_pct"],
                ),
                use_container_width=True,
            )
    with merchant_col:
        st.subheader("Merchant Concentration")
        merchant_df = build_expense_merchant_summary_from_frame(current_df, top_n=20)
        if merchant_df.empty:
            st.info("No merchant rows match the active filters.")
        else:
            st.dataframe(
                merchant_df.style.format("{:,.0f}", subset=["total_spend", "avg_ticket"]),
                use_container_width=True,
            )

    st.subheader("Transactions in Active Drill-down")
    display_df = current_df[["date", "description", "category", "subcategory", "amount", "debit_account", "credit_account"]].copy()
    if not display_df.empty:
        display_df["date"] = display_df["date"].dt.strftime("%Y-%m-%d")
    st.dataframe(display_df.style.format({"amount": "{:,.0f}"}), use_container_width=True)

    _render_legacy_cleanup_panel(repository, ledger_df, mapping_df)


def _render_legacy_cleanup_panel(
    repository: FinanceRepository,
    ledger_df: pd.DataFrame,
    mapping_df: pd.DataFrame,
) -> None:
    with st.expander("Legacy Category Cleanup"):
        candidates = build_legacy_cleanup_candidates(ledger_df, mapping_df)
        if candidates.empty:
            st.success("No non-canonical category/sub-category pairs were found.")
            return
        st.caption("Save mappings to clean analytics virtually, or apply one mapping to rewrite matching historical ledger rows.")
        st.dataframe(
            candidates.style.format({"total_amount": "{:,.0f}"}),
            use_container_width=True,
        )
        candidate_labels = [
            f"{row.source_category} / {row.source_subcategory} ({int(row.row_count)} rows)"
            for row in candidates.itertuples()
        ]
        selected_label = st.selectbox("Legacy pair", candidate_labels, key="legacy_cleanup_pair")
        selected_index = candidate_labels.index(selected_label)
        selected_row = candidates.iloc[selected_index]
        suggested_category = selected_row["suggested_category"]
        if suggested_category not in CATEGORY_MAP:
            suggested_category = "Others"
        category_options = list(CATEGORY_MAP.keys())
        target_category = st.selectbox(
            "Target category",
            category_options,
            index=category_options.index(suggested_category),
            key="legacy_cleanup_target_category",
        )
        suggested_subcategory = selected_row["suggested_subcategory"]
        subcategory_options = CATEGORY_MAP[target_category]
        if suggested_subcategory not in subcategory_options:
            suggested_subcategory = subcategory_options[0]
        target_subcategory = st.selectbox(
            "Target sub-category",
            subcategory_options,
            index=subcategory_options.index(suggested_subcategory),
            key="legacy_cleanup_target_subcategory",
        )
        save_col, apply_col = st.columns(2)
        if save_col.button("Save Virtual Mapping", key="save_legacy_mapping"):
            repository.upsert_legacy_category_mapping(
                selected_row["source_category"],
                selected_row["source_subcategory"],
                target_category,
                target_subcategory,
            )
            st.success("Saved mapping for analytics.")
            st.rerun()
        if apply_col.button("Apply Mapping To Ledger Rows", key="apply_legacy_mapping"):
            updated = repository.apply_legacy_category_mapping(
                selected_row["source_category"],
                selected_row["source_subcategory"],
                target_category,
                target_subcategory,
            )
            st.success(f"Updated {updated:,} matching ledger rows.")
            st.rerun()


def render_investments_tab(repository: FinanceRepository) -> None:
    st.header("Investments")
    st.caption("Track simple P&L and ROI from reviewed trades plus manual market-price snapshots.")

    ledger_df = repository.get_ledger()
    trades_df = repository.get_investment_trades_df()
    prices_df = repository.get_investment_price_snapshots_df()
    performance = build_investment_performance_frames(trades_df, prices_df)
    holdings_df = performance["holdings"]
    realized_df = performance["realized"]
    summary = performance["summary"]
    quality_summary = build_investment_quality_summary(trades_df, holdings_df)
    reconciliation_df = build_investment_reconciliation_frame(ledger_df, trades_df)
    pnl_trend_df = build_investment_pnl_trend_frame(realized_df, holdings_df)

    summary_col1, summary_col2, summary_col3, summary_col4, summary_col5 = st.columns(5)
    summary_col1.metric("Market Value", f"{summary.get('market_value', 0):,.0f} VND")
    summary_col2.metric("Cost Basis", f"{summary.get('cost_basis', 0):,.0f} VND")
    summary_col3.metric("Unrealized P&L", f"{summary.get('unrealized_pnl', 0):,.0f} VND")
    summary_col4.metric("Realized P&L", f"{summary.get('realized_pnl', 0):,.0f} VND")
    summary_col5.metric("ROI", f"{summary.get('roi_pct', 0) * 100:.1f}%")

    quality_col1, quality_col2, quality_col3, quality_col4 = st.columns(4)
    quality_col1.metric("Unreviewed Trades", f"{quality_summary['unreviewed_trades']:,}")
    quality_col2.metric("Ambiguous Trades", f"{quality_summary['ambiguous_trades']:,}")
    quality_col3.metric("Missing Prices", f"{quality_summary['missing_price_positions']:,}")
    quality_col4.metric("Open Positions", f"{quality_summary['open_positions']:,}")

    overview_tab, review_tab, prices_tab = st.tabs(["Portfolio", "Trade Review", "Price Snapshots"])
    with overview_tab:
        holding_col, realized_col = st.columns(2)
        with holding_col:
            st.subheader("Holdings")
            if holdings_df.empty:
                st.info("No reviewed open positions yet. Scan and review investment trades to populate holdings.")
            else:
                st.dataframe(
                    holdings_df.style.format(
                        "{:,.0f}",
                        subset=["quantity", "cost_basis", "latest_price", "market_value", "unrealized_pnl", "realized_pnl", "total_pnl"],
                    ).format("{:.1%}", subset=["roi_pct"]),
                    use_container_width=True,
                )
                if holdings_df["price_missing"].any():
                    st.warning("Some holdings have no manual price snapshot; their market value is held at cost basis.")
        with realized_col:
            st.subheader("Realized P&L")
            if realized_df.empty:
                st.info("No reviewed sells have produced realized P&L yet.")
            else:
                st.dataframe(
                    realized_df.style.format("{:,.0f}", subset=["quantity", "proceeds", "cost_basis", "realized_pnl"]),
                    use_container_width=True,
                )

        st.subheader("Investment Cash-flow Timeline")
        prepared = prepare_ledger_analysis_frame(ledger_df)
        if prepared.empty:
            st.info("No ledger rows available.")
        else:
            investment_cash_flow = prepared[
                prepared["category"].isin(INVESTMENT_CATEGORIES)
                | prepared["subcategory"].isin(["Brokerage accounts", "Emergency fund"])
            ].copy()
            if investment_cash_flow.empty:
                st.info("No investment-related ledger cash flows found.")
            else:
                timeline = investment_cash_flow.groupby(["month", "month_ts", "category", "subcategory"])["amount"].sum().reset_index()
                fig = px.bar(timeline, x="month", y="amount", color="subcategory", barmode="stack")
                st.plotly_chart(fig, use_container_width=True, key=_chart_key("investments", "cash_flow_timeline"))

        pnl_col, reconcile_col = st.columns(2)
        with pnl_col:
            st.subheader("P&L Trend")
            if pnl_trend_df.empty:
                st.info("Review trades and add prices to build a P&L trend.")
            else:
                plot_df = pnl_trend_df.melt(
                    id_vars="month",
                    value_vars=["realized_pnl", "unrealized_pnl"],
                    var_name="pnl_type",
                    value_name="amount",
                )
                fig = px.bar(plot_df, x="month", y="amount", color="pnl_type", barmode="stack")
                st.plotly_chart(fig, use_container_width=True, key=_chart_key("investments", "pnl_trend"))
        with reconcile_col:
            st.subheader("Ledger vs Trade Reconciliation")
            if reconciliation_df.empty:
                st.info("No investment-related ledger rows are available for reconciliation.")
            else:
                st.dataframe(reconciliation_df.style.format({"amount": "{:,.0f}"}), use_container_width=True)

    with review_tab:
        scan_col, status_col = st.columns([1, 2])
        with scan_col:
            if st.button("Scan Ledger For Trade Candidates", key="scan_investment_trades"):
                candidates = build_investment_trade_candidates(ledger_df)
                for candidate in candidates:
                    repository.upsert_investment_trade(candidate)
                st.success(f"Scanned {len(candidates):,} investment trade candidates.")
                st.rerun()
        with status_col:
            status_counts = trades_df.groupby("review_status")["id"].count().to_dict() if not trades_df.empty else {}
            st.write({"trade_status_counts": status_counts})

        if trades_df.empty:
            st.info("No investment trades have been scanned yet.")
        else:
            queue_df = trades_df.copy()
            queue_df["selected"] = False
            st.dataframe(
                queue_df[["id", "trade_date", "action", "ticker", "quantity", "amount", "fees", "parse_confidence", "review_status", "notes"]],
                use_container_width=True,
            )
            editable_ids = queue_df["id"].astype(int).tolist()
            selected_trade_id = st.selectbox("Edit trade", editable_ids, key="investment_trade_edit_id")
            trade = queue_df[queue_df["id"] == selected_trade_id].iloc[0]
            with st.form(f"investment_trade_form_{selected_trade_id}"):
                edit_col1, edit_col2, edit_col3 = st.columns(3)
                with edit_col1:
                    trade_date = st.text_input("Trade date", value=str(trade["trade_date"]))
                    action = st.selectbox("Action", ["buy", "sell"], index=0 if trade["action"] == "buy" else 1)
                    ticker = st.text_input("Ticker", value=str(trade["ticker"]))
                with edit_col2:
                    quantity = st.number_input("Quantity", min_value=0.0, value=float(trade["quantity"]), format="%.0f")
                    amount = st.number_input("Amount", min_value=0.0, value=float(trade["amount"]), format="%.0f")
                    fees = st.number_input("Fees", min_value=0.0, value=float(trade["fees"]), format="%.0f")
                with edit_col3:
                    status_options = ["needs_review", "ready", REVIEWED_TRADE_STATUS, "ignored"]
                    current_status = trade["review_status"] if trade["review_status"] in status_options else "needs_review"
                    review_status = st.selectbox("Review status", status_options, index=status_options.index(current_status))
                    notes = st.text_area("Notes", value=str(trade["notes"] or ""))
                save_trade = st.form_submit_button("Save Trade")
                if save_trade:
                    repository.update_investment_trade(
                        int(selected_trade_id),
                        trade_date,
                        action,
                        ticker,
                        quantity,
                        amount,
                        fees,
                        review_status,
                        notes,
                    )
                    st.success("Saved investment trade.")
                    st.rerun()

    with prices_tab:
        st.subheader("Manual Price Snapshot")
        with st.form("investment_price_form"):
            price_col1, price_col2, price_col3 = st.columns(3)
            with price_col1:
                ticker = st.text_input("Ticker").upper()
                price_date = st.date_input("Price date", date.today()).isoformat()
            with price_col2:
                price = st.number_input("Price per share", min_value=0.0, format="%.0f")
                currency = st.text_input("Currency", value="VND")
            with price_col3:
                notes = st.text_area("Notes")
            save_price = st.form_submit_button("Save Price Snapshot")
            if save_price and ticker and price > 0:
                repository.upsert_investment_price_snapshot(ticker, price_date, price, currency, notes)
                st.success("Saved price snapshot.")
                st.rerun()

        if prices_df.empty:
            st.info("No manual prices saved yet.")
        else:
            st.dataframe(prices_df.style.format({"price": "{:,.0f}"}), use_container_width=True)


def render_transactions_tab(repository: FinanceRepository) -> None:
    st.header("Transactions")
    st.caption("Capture new entries, then browse the ledger with period and category filters.")
    st.subheader("Add New Transaction")

    category = st.selectbox("Category", list(CATEGORY_MAP.keys()), key="manual_category")
    subcategory = st.selectbox("Sub-category", CATEGORY_MAP[category], key="manual_subcategory")

    with st.form("entry_form"):
        tx_date = st.date_input("Date", date.today()).strftime("%Y-%m-%d")
        description = st.text_input("Description")
        debit = st.selectbox("Debit Account", DEFAULT_DEBIT_ACCOUNTS)
        credit = st.selectbox("Credit Account", DEFAULT_CREDIT_ACCOUNTS)
        amount = st.number_input("Amount", min_value=0.0, format="%.0f")
        submitted = st.form_submit_button("Add Transaction")

        if submitted:
            repository.record_transaction(tx_date, description, category, subcategory, debit, credit, amount)
            st.success("Transaction recorded.")
            st.rerun()

    ledger_df = repository.get_ledger()
    prepared = prepare_ledger_analysis_frame(ledger_df)
    st.subheader("Browse Transactions")
    if prepared.empty:
        st.info("No manual or posted ledger transactions yet.")
    else:
        month_options = sorted(prepared["month"].unique(), reverse=True)
        default_month = month_options[:1] or month_options
        filter_col1, filter_col2, filter_col3, filter_col4 = st.columns(4)
        with filter_col1:
            selected_months = st.multiselect("Months", options=month_options, default=default_month, key="transactions_month_filter")
        with filter_col2:
            category_options = sorted(prepared["category"].dropna().unique())
            selected_categories = st.multiselect("Categories", options=category_options, default=category_options, key="transactions_category_filter")
        with filter_col3:
            account_options = sorted(set(prepared["debit_account"].dropna().unique()) | set(prepared["credit_account"].dropna().unique()))
            account_filter = st.multiselect("Accounts", options=account_options, default=account_options, key="transactions_account_filter")
        with filter_col4:
            search_text = st.text_input("Search", key="transactions_search_filter")

        filtered = prepared[
            prepared["month"].isin(selected_months)
            & prepared["category"].isin(selected_categories)
            & (
                prepared["debit_account"].isin(account_filter)
                | prepared["credit_account"].isin(account_filter)
            )
            & prepared["description"].fillna("").str.contains(search_text, case=False, na=False)
        ].copy()

        current_income = float(filtered.loc[filtered["credit_type"] == "Income", "amount"].sum())
        current_expense = float(filtered.loc[filtered["debit_type"] == "Expense", "amount"].sum())
        current_net = current_income - current_expense
        comparison_col1, comparison_col2, comparison_col3 = st.columns(3)
        comparison_col1.metric("Filtered Inflow", f"{current_income:,.0f} VND")
        comparison_col2.metric("Filtered Outflow", f"{current_expense:,.0f} VND")
        comparison_col3.metric("Filtered Net Cash Flow", f"{current_net:,.0f} VND")

        if len(selected_months) == 1:
            current_month = selected_months[0]
            month_list = sorted(month_options)
            if current_month in month_list:
                current_index = month_list.index(current_month)
                prior_month = month_list[current_index - 1] if current_index > 0 else ""
                if prior_month:
                    prior_df = prepared[prepared["month"] == prior_month].copy()
                    prior_income = float(prior_df.loc[prior_df["credit_type"] == "Income", "amount"].sum())
                    prior_expense = float(prior_df.loc[prior_df["debit_type"] == "Expense", "amount"].sum())
                    st.caption(
                        f"Compared with {prior_month}: inflow {current_income - prior_income:,.0f} VND, "
                        f"outflow {current_expense - prior_expense:,.0f} VND."
                    )

        preview_df = filtered[
            ["date", "description", "category", "subcategory", "debit_account", "credit_account", "amount"]
        ].copy()
        preview_df["date"] = preview_df["date"].dt.strftime("%Y-%m-%d")
        st.dataframe(preview_df.head(50).style.format({"amount": "{:,.0f}"}), use_container_width=True)

    balance = repository.get_account_balance()
    account_type_map = repository.build_account_type_map(ledger_df)
    balance["type"] = balance["account"].map(account_type_map).fillna(balance["account"].apply(classify_account_name))

    st.subheader("Account Balances")
    st.dataframe(balance.style.format({"balance": "{:,.0f}"}), use_container_width=True)

    assets = balance.loc[balance["type"] == "Asset", ["account", "balance"]]
    liab_eq = balance.loc[balance["type"].isin(["Liability", "Equity", "Income", "Expense"]), ["account", "balance"]]
    col1, col2 = st.columns(2)
    with col1:
        st.markdown("### Assets")
        st.dataframe(assets.style.format({"balance": "{:,.0f}"}), use_container_width=True)
    with col2:
        st.markdown("### Liabilities, Equity, and Results")
        st.dataframe(liab_eq.style.format({"balance": "{:,.0f}"}), use_container_width=True)


def render_dashboard_tab(repository: FinanceRepository) -> None:
    st.header("Dashboard")
    st.caption("Track where money goes first, then connect expense pressure back to cash flow, net worth, and review quality.")

    ledger_df = repository.get_ledger()
    if ledger_df.empty:
        st.info("Add or import transactions to see your financial dashboard.")
        return

    statement_df = repository.get_statement_review_df()
    prepared = prepare_ledger_analysis_frame(ledger_df)
    snapshot = compute_management_snapshot(ledger_df, statement_df)
    monthly = build_monthly_cash_flow_frame(ledger_df)
    net_worth_trend = build_net_worth_trend_frame(ledger_df)
    balance = build_balance_table_from_ledger(ledger_df)
    expense_comparison = build_expense_category_comparison_frame(ledger_df)
    expense_volatility = build_expense_volatility_frame(ledger_df)
    latest_expense_month = expense_comparison["current_month"].iloc[0] if not expense_comparison.empty else snapshot["current_month"]
    merchant_summary = build_expense_merchant_summary_frame(ledger_df, latest_expense_month, top_n=12)
    expense_df = prepared[prepared["debit_type"] == "Expense"].copy()

    top1, top2, top3, top4, top5, top6 = st.columns(6)
    top1.metric("Assets", f"{snapshot['assets_total']:,.0f} VND")
    top2.metric("Liabilities", f"{snapshot['liabilities_total']:,.0f} VND")
    top3.metric("Net Worth", f"{snapshot['net_worth']:,.0f} VND")
    top4.metric("Current Inflow", f"{snapshot['current_month_income']:,.0f} VND")
    top5.metric("Current Outflow", f"{snapshot['current_month_expense']:,.0f} VND")
    top6.metric("Current Net Cash Flow", f"{snapshot['current_month_net_cash_flow']:,.0f} VND")

    behavior1, behavior2, behavior3, behavior4, behavior5 = st.columns(5)
    behavior1.metric("Savings Rate", f"{snapshot['savings_rate'] * 100:.1f}%")
    behavior2.metric("Liability Pressure", f"{snapshot['liability_pressure']:,.0f} VND")
    behavior3.metric("Discretionary Share", f"{snapshot['discretionary_spend_share'] * 100:.1f}%")
    behavior4.metric("Essential Share", f"{snapshot['essential_spend_share'] * 100:.1f}%")
    behavior5.metric("Positive Cash Flow Months", f"{snapshot['positive_cash_flow_months']:,}")

    alerts: list[str] = []
    largest_increase_row = pd.Series(dtype=object)
    if not expense_comparison.empty:
        largest_increase_row = expense_comparison.sort_values(by="delta", ascending=False).iloc[0]
    if snapshot["current_month_net_cash_flow"] < 0:
        alerts.append("Current month cash flow is negative.")
    if snapshot["savings_rate"] < 0.1 and snapshot["current_month_income"] > 0:
        alerts.append("Savings rate is below 10% this month.")
    if snapshot["liability_pressure"] > 0:
        alerts.append("Liability:Payable increased compared with the previous month.")
    if not expense_comparison.empty and float(largest_increase_row["delta"]) > 0:
        alerts.append(
            f"{largest_increase_row['category']} is the biggest monthly expense increase "
            f"({float(largest_increase_row['delta']):,.0f} VND)."
        )
    if snapshot["fallback_count"] > 0:
        alerts.append(
            f"{snapshot['fallback_count']} statement rows are excluded from clean metrics "
            f"({snapshot['fallback_amount']:,.0f} VND)."
        )
    if snapshot["review_backlog"] > 0:
        alerts.append(f"{snapshot['review_backlog']} statement rows still need review.")

    st.subheader("Actionable Callouts")
    if alerts:
        for message in alerts[:5]:
            st.warning(message)
        if snapshot["review_backlog"] > 0 or snapshot["fallback_count"] > 0:
            if st.button("Open Imports Review", key="dashboard_open_imports"):
                st.session_state["active_finance_section"] = "Imports"
                st.rerun()
    else:
        st.success("No major financial-management alerts are currently flagged.")

    st.subheader("Expense Analysis")
    expense_metric1, expense_metric2, expense_metric3, expense_metric4, expense_metric5 = st.columns(5)
    top_category_name = ""
    top_category_amount = 0.0
    if not expense_comparison.empty:
        top_category_name = str(expense_comparison.iloc[0]["category"])
        top_category_amount = float(expense_comparison.iloc[0]["current_amount"])
    increase_name = ""
    increase_value = 0.0
    if not expense_comparison.empty:
        increase_name = str(largest_increase_row["category"])
        increase_value = float(largest_increase_row["delta"])
    volatility_name = ""
    volatility_value = 0.0
    if not expense_volatility.empty:
        volatility_name = str(expense_volatility.iloc[0]["category"])
        volatility_value = float(expense_volatility.iloc[0]["volatility"])
    merchant_name = ""
    merchant_value = 0.0
    if not merchant_summary.empty:
        merchant_name = str(merchant_summary.iloc[0]["merchant_or_description"])
        merchant_value = float(merchant_summary.iloc[0]["total_spend"])
    expense_metric1.metric("Top Expense Category", top_category_name or "N/A", f"{top_category_amount:,.0f} VND" if top_category_name else None)
    expense_metric2.metric("Largest Increase", increase_name or "N/A", f"{increase_value:,.0f} VND" if increase_name else None)
    expense_metric3.metric("Most Volatile Category", volatility_name or "N/A", f"{volatility_value:,.0f} VND" if volatility_name else None)
    expense_metric4.metric("Top Merchant Drain", merchant_name or "N/A", f"{merchant_value:,.0f} VND" if merchant_name else None)
    expense_metric5.metric("Fallback Expense Rows", f"{snapshot['fallback_count']:,}", f"{snapshot['fallback_amount']:,.0f} VND")

    expense_col1, expense_col2 = st.columns(2)
    with expense_col1:
        st.subheader("Largest Expense Increases")
        if not expense_comparison.empty:
            st.dataframe(
                expense_comparison.sort_values(by=["delta", "current_amount"], ascending=[False, False]).head(10).style.format(
                    "{:,.0f}",
                    subset=["current_amount", "previous_amount", "delta"],
                ).format("{:.1%}", subset=["delta_pct"]),
                use_container_width=True,
            )
        st.subheader("Expense Mix by Life Area")
        if not expense_df.empty:
            focus_mix = expense_df.groupby("expense_focus")["amount"].sum().reset_index().sort_values("amount", ascending=False)
            fig = px.pie(focus_mix, names="expense_focus", values="amount")
            st.plotly_chart(fig, use_container_width=True, key=_chart_key("dashboard", "expense_focus_mix"))
    with expense_col2:
        st.subheader(f"Recurring Merchant Concentration ({latest_expense_month or 'Latest'})")
        if not merchant_summary.empty:
            st.dataframe(
                merchant_summary.style.format(
                    "{:,.0f}",
                    subset=["total_spend", "avg_ticket"],
                ),
                use_container_width=True,
            )
        st.subheader("Expense Volatility")
        if not expense_volatility.empty:
            st.dataframe(
                expense_volatility.head(10).style.format(
                    "{:,.0f}",
                    subset=["avg_monthly_spend", "volatility", "latest_month_spend"],
                ).format("{:.1%}", subset=["volatility_ratio"]),
                use_container_width=True,
            )

    spending_mix_col, monthly_expense_col = st.columns(2)
    with spending_mix_col:
        st.subheader("Essential vs Lifestyle Spend")
        if not expense_df.empty:
            nature_mix = expense_df.groupby("expense_nature")["amount"].sum().reset_index()
            st.dataframe(nature_mix.style.format({"amount": "{:,.0f}"}), use_container_width=True)
    with monthly_expense_col:
        st.subheader("Monthly Expense Trend")
        if not monthly.empty:
            monthly_expense = monthly[["month", "expense"]].copy()
            monthly_expense["expense_delta"] = monthly_expense["expense"].diff().fillna(0)
            fig = px.bar(
                monthly_expense,
                x="month",
                y="expense",
                text="expense",
                color="expense_delta",
                color_continuous_scale="Reds",
                labels={"expense": "VND", "expense_delta": "Change vs prior"},
            )
            st.plotly_chart(fig, use_container_width=True, key=_chart_key("dashboard", "monthly_expense_only"))

    focus_col1, focus_col2 = st.columns(2)
    with focus_col1:
        st.subheader("Category Pressure Table")
        if not expense_comparison.empty:
            focus_table = expense_comparison[["category", "current_amount", "previous_amount", "delta", "delta_pct"]].copy()
            st.dataframe(
                focus_table.style.format("{:,.0f}", subset=["current_amount", "previous_amount", "delta"]).format(
                    "{:.1%}",
                    subset=["delta_pct"],
                ),
                use_container_width=True,
            )
    with focus_col2:
        st.subheader("Merchant Spend This Month")
        if not merchant_summary.empty:
            fig = px.bar(
                merchant_summary.head(8),
                x="total_spend",
                y="merchant_or_description",
                orientation="h",
                text="total_spend",
                labels={"total_spend": "VND", "merchant_or_description": "Merchant"},
            )
            st.plotly_chart(fig, use_container_width=True, key=_chart_key("dashboard", "merchant_spend"))

    finance_col, cashflow_col = st.columns(2)
    with finance_col:
        st.subheader("Financial Position")
        display_balance = balance.copy()
        if not display_balance.empty:
            display_balance["display_balance"] = display_balance.apply(
                lambda row: abs(row["balance"]) if row["account_type"] == "Liability" else row["balance"],
                axis=1,
            )
            st.dataframe(
                display_balance[["account", "account_type", "display_balance"]].rename(columns={"display_balance": "balance"})
                .style.format({"balance": "{:,.0f}"}),
                use_container_width=True,
            )
        st.subheader("Net Worth Trend")
        if not net_worth_trend.empty:
            fig = px.line(net_worth_trend, x="month_ts", y="net_worth", markers=True)
            st.plotly_chart(fig, use_container_width=True, key=_chart_key("dashboard", "net_worth_trend"))
    with cashflow_col:
        st.subheader("Monthly Inflow vs Outflow")
        if not monthly.empty:
            plot_df = monthly.melt(id_vars=["month", "month_ts"], value_vars=["income", "expense"], var_name="type", value_name="amount")
            fig = px.bar(plot_df, x="month", y="amount", color="type", barmode="group")
            st.plotly_chart(fig, use_container_width=True, key=_chart_key("dashboard", "monthly_cash_flow"))
        st.subheader("Liability / Payable Trend")
        if not net_worth_trend.empty:
            fig = px.bar(net_worth_trend, x="month", y="payable_balance", labels={"payable_balance": "VND"})
            st.plotly_chart(fig, use_container_width=True, key=_chart_key("dashboard", "payable_trend"))

    ops_col1, ops_col2 = st.columns(2)
    with ops_col1:
        st.subheader("Month-over-Month Summary")
        if not monthly.empty:
            month_table = monthly[["month", "income", "expense", "net_cash_flow"]].copy()
            month_table["income_delta"] = month_table["income"].diff().fillna(0)
            month_table["expense_delta"] = month_table["expense"].diff().fillna(0)
            month_table["net_delta"] = month_table["net_cash_flow"].diff().fillna(0)
            st.dataframe(month_table.tail(6).style.format("{:,.0f}", subset=["income", "expense", "net_cash_flow", "income_delta", "expense_delta", "net_delta"]), use_container_width=True)
    with ops_col2:
        st.subheader("Operations Widget")
        if statement_df.empty:
            st.info("No statement-review data is currently available.")
        else:
            ops1, ops2, ops3 = st.columns(3)
            ops1.metric("Needs Review", f"{int((statement_df['review_state'] == 'needs_review').sum()):,}")
            ops2.metric("Fallback Rows", f"{int(statement_df['is_fallback'].sum()):,}")
            ops3.metric("Posted Rows", f"{int((statement_df['review_status'] == 'posted').sum()):,}")
            backlog = statement_df[statement_df["review_state"] == "needs_review"][
                ["statement_month", "description", "amount", "category", "subcategory"]
            ].head(8)
            if backlog.empty:
                st.success("Statement review backlog is clear.")
            else:
                st.dataframe(backlog, use_container_width=True)


def render_planning_tab(repository: FinanceRepository) -> None:
    st.header("Planning")
    st.caption("Plan category spend, catch overspending early, and focus budgets on the expense areas that move the most.")

    ledger_df = repository.get_ledger()
    budget_df = repository.get_budgets()
    budget_editor = build_budget_editor_frame(budget_df)

    with st.form("planning_budget_editor"):
        edited_budget_df = st.data_editor(
            budget_editor,
            use_container_width=True,
            hide_index=True,
            column_config={
                "category": st.column_config.TextColumn("Category", disabled=True),
                "monthly_limit": st.column_config.NumberColumn("Monthly Budget", format="%.0f"),
            },
            disabled=["category"],
            key="planning_budget_editor_grid",
        )
        save_budgets = st.form_submit_button("Save Budget Plan")
        if save_budgets:
            for row in edited_budget_df.to_dict("records"):
                repository.save_budget(row["category"], float(row["monthly_limit"] or 0))
            st.success("Budget plan updated.")
            st.rerun()

    prepared = prepare_ledger_analysis_frame(ledger_df)
    if prepared.empty:
        st.info("Budget planning insights will appear after you record expense transactions.")
        return

    month_options = sorted(prepared["month"].unique(), reverse=True)
    selected_month = st.selectbox("Planning month", month_options, index=0, key="planning_month")
    budget_status = build_budget_status_frame(ledger_df, budget_df, selected_month)
    subcategory_budget_status = build_subcategory_budget_status_frame(ledger_df, budget_df, selected_month)
    budget_patterns = build_budget_pattern_frame(ledger_df, budget_df)
    budget_suggestions = build_budget_review_suggestions(ledger_df, budget_df, selected_month)
    expense_comparison = build_expense_category_comparison_frame(ledger_df)
    expense_volatility = build_expense_volatility_frame(ledger_df)
    merchant_summary = build_expense_merchant_summary_frame(ledger_df, selected_month, top_n=10)
    expense_month = prepared[(prepared["month"] == selected_month) & (prepared["debit_type"] == "Expense")].copy()

    budgeted_rows = budget_status[budget_status["monthly_limit"] > 0].copy()
    adherence_rate = float((budgeted_rows["remaining_budget"] >= 0).mean()) if not budgeted_rows.empty else 0.0
    overspend_rows = budget_status[budget_status["remaining_budget"] < 0].copy()
    no_budget_rows = budget_status[(budget_status["monthly_limit"] <= 0) & (budget_status["actual_amount"] > 0)].copy()
    fixed_spend = float(expense_month.loc[expense_month["cost_structure"] == "Fixed", "amount"].sum())
    variable_spend = float(expense_month.loc[expense_month["cost_structure"] == "Variable", "amount"].sum())
    total_selected_expense = float(expense_month["amount"].sum())
    overspend_amount = float(overspend_rows["remaining_budget"].abs().sum()) if not overspend_rows.empty else 0.0

    plan1, plan2, plan3, plan4 = st.columns(4)
    plan1.metric("Budget Adherence", f"{adherence_rate * 100:.1f}%")
    plan2.metric("Over Budget Categories", f"{len(overspend_rows):,}")
    plan3.metric("No-Budget Spend Categories", f"{len(no_budget_rows):,}")
    plan4.metric("Selected Month Spend", f"{total_selected_expense:,.0f} VND")

    pressure1, pressure2, pressure3, pressure4 = st.columns(4)
    pressure1.metric("Over Budget Amount", f"{overspend_amount:,.0f} VND")
    pressure2.metric("Fixed Spend Share", f"{(fixed_spend / total_selected_expense * 100) if total_selected_expense else 0:.1f}%")
    pressure3.metric("Variable Spend Share", f"{(variable_spend / total_selected_expense * 100) if total_selected_expense else 0:.1f}%")
    pressure4.metric("Top Merchant Count", f"{len(merchant_summary):,}")

    table_col, structure_col = st.columns(2)
    with table_col:
        st.subheader("Budget vs Actual")
        st.dataframe(
            budget_status[["category", "monthly_limit", "actual_amount", "remaining_budget", "status"]]
            .style.format("{:,.0f}", subset=["monthly_limit", "actual_amount", "remaining_budget"]),
            use_container_width=True,
        )
    with structure_col:
        st.subheader("Fixed vs Variable Expense Structure")
        structure_df = pd.DataFrame(
            [
                {"structure": "Fixed", "amount": fixed_spend},
                {"structure": "Variable", "amount": variable_spend},
            ]
        )
        fig = px.bar(structure_df, x="structure", y="amount", text="amount")
        st.plotly_chart(fig, use_container_width=True, key=_chart_key("planning", "fixed_variable"))

    subcategory_col, pattern_col = st.columns(2)
    with subcategory_col:
        st.subheader("Sub-category Budget Pressure")
        if subcategory_budget_status.empty:
            st.info("No sub-category spend is available for this planning month.")
        else:
            st.dataframe(
                subcategory_budget_status.head(20)
                .style.format(
                    "{:,.0f}",
                    subset=["actual_amount", "category_actual", "category_budget", "category_remaining_budget"],
                )
                .format("{:.1%}", subset=["subcategory_share"]),
                use_container_width=True,
            )
    with pattern_col:
        st.subheader("Repeated Budget Pattern")
        if budget_patterns.empty:
            st.info("Save category budgets and record more months to see repeated patterns.")
        else:
            st.dataframe(
                budget_patterns.style.format(
                    "{:,.0f}",
                    subset=["monthly_limit", "avg_remaining_budget"],
                ),
                use_container_width=True,
            )

    alert_col, gap_col = st.columns(2)
    with alert_col:
        st.subheader("Overspend Alerts")
        if overspend_rows.empty:
            st.success("No categories are over budget for the selected month.")
        else:
            st.dataframe(
                overspend_rows[["category", "actual_amount", "monthly_limit", "remaining_budget"]]
                .style.format("{:,.0f}", subset=["actual_amount", "monthly_limit", "remaining_budget"]),
                use_container_width=True,
            )
    with gap_col:
        st.subheader("Budget Gaps")
        if budget_suggestions.empty:
            st.success("All spending categories with activity have a budget.")
        else:
            st.dataframe(
                budget_suggestions.style.format({"amount": "{:,.0f}"}),
                use_container_width=True,
            )

    st.subheader("Expense Deep Dive")
    deep_col1, deep_col2 = st.columns(2)
    with deep_col1:
        st.write("Largest expense increases")
        if not expense_comparison.empty:
            rising = expense_comparison.sort_values("delta", ascending=False).head(10)
            st.dataframe(
                rising[["category", "current_amount", "previous_amount", "delta", "delta_pct"]]
                .style.format("{:,.0f}", subset=["current_amount", "previous_amount", "delta"])
                .format("{:.1%}", subset=["delta_pct"]),
                use_container_width=True,
            )
    with deep_col2:
        st.write("High-volatility categories")
        if not expense_volatility.empty:
            st.dataframe(
                expense_volatility.head(10)[["category", "avg_monthly_spend", "volatility", "volatility_ratio"]]
                .style.format("{:,.0f}", subset=["avg_monthly_spend", "volatility"])
                .format("{:.1%}", subset=["volatility_ratio"]),
                use_container_width=True,
            )

    merchant_col, budget_gap_col = st.columns(2)
    with merchant_col:
        st.subheader(f"Top Merchants in {selected_month}")
        if merchant_summary.empty:
            st.info("No expense merchants found for the selected month.")
        else:
            st.dataframe(
                merchant_summary.style.format("{:,.0f}", subset=["total_spend", "avg_ticket"]),
                use_container_width=True,
            )
    with budget_gap_col:
        st.subheader("Highest Category Pressure")
        if budget_status.empty:
            st.info("No category pressure is available for the selected month.")
        else:
            pressure_table = budget_status.copy()
            pressure_table["pressure_ratio"] = pressure_table.apply(
                lambda row: (row["actual_amount"] / row["monthly_limit"]) if row["monthly_limit"] > 0 else 0,
                axis=1,
            )
            pressure_table = pressure_table.sort_values(
                by=["pressure_ratio", "actual_amount"],
                ascending=[False, False],
            )
            st.dataframe(
                pressure_table[["category", "actual_amount", "monthly_limit", "remaining_budget", "pressure_ratio"]]
                .style.format("{:,.0f}", subset=["actual_amount", "monthly_limit", "remaining_budget"])
                .format("{:.1%}", subset=["pressure_ratio"]),
                use_container_width=True,
            )


def render_ledger_insights_tab(repository: FinanceRepository, key_prefix: str = "ledger_insights") -> None:
    st.header("Ledger Insights")
    df = repository.get_ledger()
    if df.empty:
        st.info("Post imported rows or add manual transactions to see ledger insights.")
        return

    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"]).copy()
    df["month"] = df["date"].dt.to_period("M").astype(str)
    df["month_ts"] = df["date"].dt.to_period("M").dt.to_timestamp()

    expense_df = df[df["debit_account"].fillna("").str.startswith("Expense")].copy()
    income_df = df[df["credit_account"].fillna("").str.startswith("Income")].copy()

    monthly_income = income_df.groupby("month_ts")["amount"].sum().reset_index(name="Income")
    monthly_expense = expense_df.groupby("month_ts")["amount"].sum().reset_index(name="Expense")
    monthly_summary = pd.merge(monthly_income, monthly_expense, on="month_ts", how="outer").fillna(0).sort_values("month_ts")

    budget_df = repository.get_budgets()

    st.subheader("Monthly Income vs Expense")
    if monthly_summary.empty:
        st.info("Not enough income/expense data to draw the monthly trend.")
    else:
        plot_df = monthly_summary.melt(id_vars="month_ts", value_vars=["Income", "Expense"], var_name="Type", value_name="Amount")
        fig = px.line(plot_df, x="month_ts", y="Amount", color="Type", markers=True)
        st.plotly_chart(fig, use_container_width=True, key=_chart_key(key_prefix, "monthly_income_expense"))

    st.subheader("Monthly Expenses by Category")
    if expense_df.empty:
        st.info("No expense transactions found in the ledger.")
    else:
        latest_months = sorted(expense_df["month"].unique())[-3:]
        recent_expenses = expense_df[expense_df["month"].isin(latest_months)]
        pivot_df = recent_expenses.pivot_table(index="category", columns="month", values="amount", aggfunc="sum", fill_value=0)
        fig, ax = plt.subplots(figsize=(12, 6))
        sns.heatmap(pivot_df, annot=True, fmt=",.0f", cmap="YlOrRd", linewidths=0.5, cbar=True, ax=ax)
        st.pyplot(fig)
        plt.close(fig)

    st.subheader("Budget vs Actual")
    if budget_df.empty or expense_df.empty:
        st.info("Need both saved budgets and expense transactions to render this chart.")
    else:
        monthly_actual = expense_df.groupby(["month", "category"])["amount"].sum().reset_index()
        merged = pd.merge(monthly_actual, budget_df, on="category", how="left").dropna(subset=["monthly_limit"])
        if merged.empty:
            st.info("No overlapping categories between budget and actual expense data.")
        else:
            latest_month = merged["month"].max()
            latest_data = merged[merged["month"] == latest_month]
            fig = px.bar(latest_data, x="category", y=["monthly_limit", "amount"], barmode="group")
            st.plotly_chart(fig, use_container_width=True, key=_chart_key(key_prefix, "budget_vs_actual"))


def render_statement_insights_tab(repository: FinanceRepository, key_prefix: str = "statement_insights") -> None:
    st.header("Statement Insights")
    statement_df = repository.get_statement_insights()
    if statement_df.empty:
        st.info("No parsed statement rows yet. Use the Imports tab to scan the raw statement folders.")
        return

    statement_df["transaction_date"] = pd.to_datetime(statement_df["transaction_date"], errors="coerce")
    statement_df["statement_month"] = statement_df["statement_month"].fillna("")
    merchant_rule_keywords = {row["keyword"] for row in repository.get_merchant_rules()}
    statement_df["merchant_key"] = statement_df.apply(
        lambda row: clean_merchant_keyword(row["merchant"] or row["description"]),
        axis=1,
    )
    statement_df["missing_rule"] = statement_df["merchant_key"].ne("") & ~statement_df["merchant_key"].isin(merchant_rule_keywords)
    statement_df["attention_needed"] = statement_df["review_state"].eq("needs_review")

    hsbc_df = statement_df[statement_df["source_type"] == SOURCE_HSBC].copy()
    tcb_df = statement_df[statement_df["source_type"] == SOURCE_TCB_IMAGE].copy()
    fallback_df = statement_df[statement_df["is_fallback"]].copy()
    low_confidence = statement_df[statement_df["low_confidence"]].copy()

    grouped_categories = (
        statement_df[statement_df["merchant_key"].ne("")]
        .groupby("merchant_key")["category"]
        .nunique()
        .reset_index(name="category_count")
    )
    inconsistent_merchants = grouped_categories[grouped_categories["category_count"] > 1]
    latest_month = statement_df["statement_month"].max()
    attention_this_month = statement_df[
        (statement_df["statement_month"] == latest_month) & statement_df["attention_needed"]
    ]
    largest_unposted = statement_df[statement_df["review_status"].isin(["pending", "ready_to_post"] if "ready_to_post" in statement_df["review_status"].tolist() else ["pending"])].copy()
    if largest_unposted.empty:
        largest_unposted = statement_df[statement_df["review_state"] != "posted"].copy()

    top1, top2, top3, top4, top5 = st.columns(5)
    top1.metric("Parsed Statement Rows", f"{len(statement_df):,}")
    top2.metric("Needs Review", f"{int(statement_df['attention_needed'].sum()):,}")
    top3.metric("Fallback Rows", f"{len(fallback_df):,}", f"{fallback_df['amount'].sum():,.0f} VND")
    top4.metric("Low Confidence", f"{len(low_confidence):,}")
    top5.metric("Inconsistent Merchants", f"{len(inconsistent_merchants):,}")

    review_col, merchant_col = st.columns(2)
    with review_col:
        st.subheader("Rows Requiring Attention")
        st.dataframe(
            statement_df[
                statement_df["attention_needed"]
            ][["statement_month", "transaction_date", "description", "amount", "category", "subcategory", "confidence", "review_state"]].head(15),
            use_container_width=True,
        )
    with merchant_col:
        st.subheader("Top Unposted Rows")
        st.dataframe(
            largest_unposted.sort_values("amount", ascending=False)[
                ["statement_month", "transaction_date", "description", "amount", "review_state", "category", "subcategory"]
            ].head(15),
            use_container_width=True,
        )

    st.subheader("Review Signals")
    signal_col1, signal_col2 = st.columns(2)
    with signal_col1:
        st.write(f"Rows needing attention in {latest_month or 'current month'}: {len(attention_this_month):,}")
        st.dataframe(
            low_confidence[["source_type", "statement_month", "transaction_date", "description", "amount", "confidence", "parse_notes"]].head(10),
            use_container_width=True,
        )
    with signal_col2:
        missing_rule_df = (
            statement_df[statement_df["missing_rule"]]
            .groupby(["merchant_key", "merchant"])["amount"]
            .sum()
            .reset_index()
            .sort_values("amount", ascending=False)
        )
        st.write("Top merchants missing rules")
        st.dataframe(missing_rule_df[["merchant", "amount"]].head(10), use_container_width=True)

    include_unclassified = st.checkbox("Include fallback/unclassified rows in analytics", value=False, key=f"{key_prefix}_include_unclassified")
    analytic_df = statement_df.copy()
    if not include_unclassified:
        analytic_df = analytic_df[~analytic_df["is_fallback"] & ~analytic_df["needs_subcategory"]].copy()
    if analytic_df.empty:
        st.warning("No classified rows are available for analytics with the current setting.")
        return

    hsbc_df = analytic_df[analytic_df["source_type"] == SOURCE_HSBC].copy()
    tcb_df = analytic_df[analytic_df["source_type"] == SOURCE_TCB_IMAGE].copy()

    if not hsbc_df.empty:
        hsbc_outflows = hsbc_df[hsbc_df["direction"] == "outflow"].copy()
        monthly_spend = hsbc_outflows.groupby("statement_month")["amount"].sum().reset_index()
        st.subheader("HSBC Monthly Spend")
        fig = px.bar(monthly_spend, x="statement_month", y="amount", text="amount", labels={"amount": "VND"})
        st.plotly_chart(fig, use_container_width=True, key=_chart_key(key_prefix, "hsbc_monthly_spend"))

        merchant_spend = hsbc_outflows.groupby("merchant")["amount"].sum().reset_index().sort_values("amount", ascending=False).head(10)
        st.subheader("Top HSBC Merchants")
        if not merchant_spend.empty:
            fig = px.bar(merchant_spend, x="amount", y="merchant", orientation="h", labels={"amount": "VND"})
            st.plotly_chart(fig, use_container_width=True, key=_chart_key(key_prefix, "hsbc_top_merchants"))

        type_summary = hsbc_df.groupby(["statement_month", "row_type"])["amount"].sum().reset_index()
        st.subheader("HSBC Fees, Refunds, Payments, and Purchases")
        fig = px.bar(type_summary, x="statement_month", y="amount", color="row_type", barmode="group")
        st.plotly_chart(fig, use_container_width=True, key=_chart_key(key_prefix, "hsbc_type_summary"))

        by_category = hsbc_outflows.groupby(["category", "subcategory"])["amount"].sum().reset_index().sort_values("amount", ascending=False)
        st.subheader("HSBC Spend by Category/Sub-category")
        st.dataframe(by_category.style.format({"amount": "{:,.0f}"}), use_container_width=True)

    if not tcb_df.empty:
        tcb_summary = (
            tcb_df.groupby(["statement_month", "direction"])["amount"].sum().reset_index().sort_values(["statement_month", "direction"])
        )
        st.subheader("TCB Inflow vs Outflow")
        fig = px.bar(tcb_summary, x="statement_month", y="amount", color="direction", barmode="group")
        st.plotly_chart(fig, use_container_width=True, key=_chart_key(key_prefix, "tcb_inflow_outflow"))
    st.subheader("Merchant Category Consistency")
    if inconsistent_merchants.empty:
        st.success("No merchant category inconsistencies are currently flagged.")
    else:
        inconsistent_detail = (
            statement_df[statement_df["merchant_key"].isin(inconsistent_merchants["merchant_key"])]
            .sort_values(by=["merchant_key", "review_status", "statement_month", "transaction_date"], ascending=[True, True, False, False])
        )
        st.dataframe(
            inconsistent_detail[["merchant", "category", "subcategory", "review_status", "statement_month", "amount"]].head(30),
            use_container_width=True,
        )


def render_imports_tab(repository: FinanceRepository) -> None:
    review_tab, insights_tab, settings_tab = st.tabs(["Scan & Review", "Statement Insights", "Settings"])

    with review_tab:
        st.header("Imports")
        settings = repository.get_settings()
        deps = dependency_summary(settings.get("tesseract_cmd", ""))
        messages = []
        if not deps["pdfplumber"]:
            messages.append("`pdfplumber` is missing, so HSBC PDFs cannot be parsed.")
        if not deps["pytesseract"]:
            messages.append("`pytesseract` is missing, so TCB screenshots cannot be OCR'd.")
        if deps["pytesseract"] and not deps["tesseract_binary"]:
            messages.append("Tesseract is not configured yet. TCB image OCR will stay disabled until `tesseract_cmd` is set in Settings.")
        for message in messages:
            st.warning(message)

        action_col, force_col = st.columns([3, 2])
        with action_col:
            if st.button("Scan Source Folders"):
                summary = scan_sources(repository, force_reprocess=False)
                st.success(f"Scan complete. Processed {summary['processed']} files and created {summary['rows']} rows.")
                if summary["errors"]:
                    st.warning(" ; ".join(summary["errors"]))
                st.rerun()
        with force_col:
            if st.button("Force Reprocess All Files"):
                summary = scan_sources(repository, force_reprocess=True)
                st.success(f"Forced reprocess complete. Processed {summary['processed']} files and created {summary['rows']} rows.")
                if summary["errors"]:
                    st.warning(" ; ".join(summary["errors"]))
                st.rerun()

        files_df = repository.get_source_files_df()
        batches_df = repository.get_import_batches_df()
        batch_col, file_col = st.columns([1, 2])
        with batch_col:
            st.subheader("Import Batches")
            if batches_df.empty:
                st.info("No import batches yet.")
            else:
                st.dataframe(batches_df, use_container_width=True)
        with file_col:
            st.subheader("Source Files")
            if files_df.empty:
                st.info("No source files processed yet.")
            else:
                st.dataframe(files_df, use_container_width=True)

        st.subheader("Review Imported Rows")
        _render_import_review_feedback()
        filter_col1, filter_col2, filter_col3 = st.columns(3)
        with filter_col1:
            source_filter = st.selectbox("Source", ["", SOURCE_HSBC, SOURCE_TCB_IMAGE], format_func=lambda value: value or "All sources")
        with filter_col2:
            status_filter = st.selectbox("Status", ["", "pending", "posted", "ignored"], index=1, format_func=lambda value: value or "All statuses")
        with filter_col3:
            month_options = [""]
            if not files_df.empty:
                month_options.extend(sorted(m for m in files_df["statement_month"].dropna().unique().tolist() if m))
            month_filter = st.selectbox("Statement month", month_options, format_func=lambda value: value or "All months")

        review_slice = st.selectbox("Review slice", _review_slice_options())
        review_df = repository.get_statement_review_df(source_filter, status_filter, month_filter)
        review_df = filter_review_queue(review_df, review_slice)
        if review_df.empty:
            st.info("No statement rows match the current filters.")
            for column, default_value in {
                "review_status": "",
                "low_confidence": False,
                "is_fallback": False,
                "review_state": "",
            }.items():
                if column not in review_df.columns:
                    review_df[column] = pd.Series(dtype=type(default_value))

        visible_ready_ids = visible_ready_statement_row_ids(review_df)
        visible_pending_count = int((review_df["review_status"] == "pending").sum())
        visible_fallback_count = int(
            (review_df["review_status"].eq("pending") & review_df["is_fallback"]).sum()
        )
        visible_blocker_count = max(visible_pending_count - len(visible_ready_ids), 0)
        summary_col1, summary_col2, summary_col3, summary_col4 = st.columns(4)
        summary_col1.metric("Visible Pending", f"{visible_pending_count:,}")
        summary_col2.metric("Visible Ready", f"{len(visible_ready_ids):,}")
        summary_col3.metric("Visible Unclassified", f"{visible_fallback_count:,}")
        summary_col4.metric("Visible Blockers", f"{visible_blocker_count:,}")

        blocker_summary = build_import_blocker_summary(review_df)
        date_fix_payload = build_common_date_fix_payload(review_df)
        blocker_col, date_fix_col = st.columns([2, 1])
        with blocker_col:
            st.markdown("### Fix Blockers")
            if blocker_summary.empty:
                st.success("No hard posting blockers are visible in this filtered review batch.")
            else:
                st.dataframe(blocker_summary.style.format({"amount": "{:,.0f}"}), use_container_width=True)
        with date_fix_col:
            st.markdown("### Common Date Fix")
            if date_fix_payload:
                st.caption("Previewed fixes use valid post dates first, then statement-month day 01 as a fallback.")
                st.dataframe(
                    pd.DataFrame(date_fix_payload)[["id", "transaction_date", "description", "amount"]].head(12)
                    .style.format({"amount": "{:,.0f}"}),
                    use_container_width=True,
                )
                if st.button("Apply Visible Date Fixes", key="apply_visible_date_fixes"):
                    repository.update_statement_row_edits(date_fix_payload)
                    _queue_import_review_feedback([("success", f"Applied date fixes to {len(date_fix_payload):,} visible rows.")])
                    st.rerun()
            else:
                st.info("No safe date fixes are available for the visible rows.")

        merchant_rule_keywords = {row["keyword"] for row in repository.get_merchant_rules()}
        inline_editor_df = build_inline_review_editor_df(review_df, merchant_rule_keywords)
        editable_columns = ["selected", *INLINE_EDIT_COLUMNS]
        disabled_columns = [column for column in build_inline_review_columns() if column not in editable_columns]
        st.markdown("### Inline Review Table")
        st.caption("Edit staged rows directly. `Classification` stores a valid category and sub-category pair.")
        inline_table = st.data_editor(
            inline_editor_df[build_inline_review_columns()],
            use_container_width=True,
            num_rows="fixed",
            hide_index=True,
            column_config={
                "selected": st.column_config.CheckboxColumn("Select"),
                "classification": st.column_config.SelectboxColumn(
                    "Classification",
                    options=build_classification_options(),
                    required=True,
                ),
                "amount": st.column_config.NumberColumn("Amount", format="%.0f"),
                "description": st.column_config.TextColumn("Description"),
                "transaction_date": st.column_config.TextColumn("Transaction Date"),
                "debit_account": st.column_config.SelectboxColumn("Debit Account", options=ACCOUNT_OPTIONS, required=True),
                "credit_account": st.column_config.SelectboxColumn("Credit Account", options=ACCOUNT_OPTIONS, required=True),
                "confidence": st.column_config.NumberColumn("Confidence", format="%.2f", disabled=True),
                "posting_blockers": st.column_config.TextColumn(
                    "Posting Note",
                    disabled=True,
                    help="Shows hard blockers or non-blocking notes such as unclassified-but-postable rows.",
                ),
                "id": st.column_config.NumberColumn("Row ID", disabled=True),
            },
            disabled=disabled_columns,
            key="statement_inline_review_editor",
        )
        visible_table_ids = inline_table["id"].astype(int).tolist()
        selected_ids = inline_table.loc[inline_table["selected"], "id"].astype(int).tolist()
        selected_pending_ids = inline_table.loc[
            inline_table["selected"] & inline_table["review_status"].eq("pending"),
            "id",
        ].astype(int).tolist()
        pending_edits = extract_inline_review_edits(inline_editor_df, inline_table)

        post_all_col, save_col, post_col, ignore_col = st.columns([1.45, 1, 1, 1])
        if post_all_col.button("Post All Visible Ready", key="post_all_visible_ready", type="primary"):
            try:
                feedback: list[tuple[str, str]] = []
                if pending_edits:
                    repository.update_statement_row_edits(pending_edits)
                    feedback.append(("info", f"Saved {len(pending_edits):,} edited rows before posting."))

                refreshed_review_df = repository.get_statement_review_df(source_filter, status_filter, month_filter)
                if not refreshed_review_df.empty:
                    refreshed_review_df = refreshed_review_df[
                        refreshed_review_df["id"].isin(visible_table_ids)
                    ].copy()
                post_all_ids = visible_ready_statement_row_ids(refreshed_review_df)
                if not post_all_ids:
                    feedback.append(("warning", "No visible ready rows are available to post."))
                    if pending_edits:
                        _queue_import_review_feedback(feedback)
                        st.rerun()
                    for level, message in feedback:
                        getattr(st, level, st.info)(message)
                else:
                    attempted = len(post_all_ids)
                    before_ledger_count = len(repository.get_ledger())
                    posted, messages = repository.post_statement_rows(post_all_ids, clean_merchant_keyword)
                    after_ledger_count = len(repository.get_ledger())
                    skipped = attempted - posted
                    posting_summary = summarize_posting_messages(messages)
                    after_post_df = repository.get_statement_review_df()
                    fallback_posted = 0
                    if not after_post_df.empty:
                        fallback_posted = int(
                            (
                                after_post_df["id"].isin(post_all_ids)
                                & after_post_df["review_status"].eq("posted")
                                & after_post_df["is_fallback"]
                            ).sum()
                        )
                    if posted:
                        feedback.append(("success", f"Posted {posted:,} of {attempted:,} visible ready rows."))
                    if fallback_posted:
                        feedback.append(("warning", f"Posted {fallback_posted:,} unclassified rows as requested."))
                    if skipped:
                        feedback.append(("warning", f"Skipped {skipped:,} rows during posting."))
                    feedback.append(
                        (
                            "info",
                            f"Ledger row delta: {after_ledger_count - before_ledger_count:+,}. "
                            f"Duplicate skipped: {posting_summary['duplicate_skipped']:,}.",
                        )
                    )
                    for message in messages:
                        level = "warning" if any(token in message.lower() for token in ["skipped", "duplicate"]) else "info"
                        feedback.append((level, message))
                    _queue_import_review_feedback(feedback)
                    st.rerun()
            except ValueError as exc:
                st.error(str(exc))
        if save_col.button("Save Table Edits", key="save_inline_review_edits"):
            if not pending_edits:
                st.info("No pending table edits to save.")
            else:
                try:
                    repository.update_statement_row_edits(pending_edits)
                    _queue_import_review_feedback([("success", f"Saved {len(pending_edits):,} edited rows.")])
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))
        if post_col.button("Post Selected Rows", key="post_inline_selected_rows"):
            if not selected_ids:
                st.warning("Select at least one row to post.")
            else:
                try:
                    selected_edits = [row for row in pending_edits if int(row["id"]) in selected_pending_ids]
                    if selected_edits:
                        repository.update_statement_row_edits(selected_edits)
                    before_ledger_count = len(repository.get_ledger())
                    posted, messages = repository.post_statement_rows(selected_ids, clean_merchant_keyword)
                    after_ledger_count = len(repository.get_ledger())
                    posting_summary = summarize_posting_messages(messages)
                    feedback: list[tuple[str, str]] = []
                    if posted:
                        feedback.append(("success", f"Posted {posted} rows into the ledger."))
                    feedback.append(
                        (
                            "info",
                            f"Ledger row delta: {after_ledger_count - before_ledger_count:+,}. "
                            f"Duplicate skipped: {posting_summary['duplicate_skipped']:,}.",
                        )
                    )
                    for message in messages:
                        level = "warning" if "skipped" in message.lower() else "info"
                        feedback.append((level, message))
                    if posted:
                        _queue_import_review_feedback(feedback)
                        st.rerun()
                    for level, message in feedback:
                        getattr(st, level, st.info)(message)
                except ValueError as exc:
                    st.error(str(exc))
        if ignore_col.button("Ignore Selected Rows", key="ignore_inline_selected_rows"):
            if not selected_pending_ids:
                st.warning("Select at least one pending row to ignore.")
            else:
                ignored = repository.set_statement_status(selected_pending_ids, "ignored")
                feedback = [("success", f"Ignored {ignored} rows.")]
                skipped = len(selected_ids) - len(selected_pending_ids)
                if skipped:
                    feedback.append(("info", f"Skipped {skipped} posted or already ignored rows."))
                _queue_import_review_feedback(feedback)
                st.rerun()

        with st.expander("Row Details"):
            detail_source = review_df[review_df["id"].isin(selected_ids)].copy() if selected_ids else review_df.head(20).copy()
            detail_columns = [
                "id",
                "source_file_id",
                "source_type",
                "row_type",
                "statement_month",
                "post_date",
                "event_time",
                "merchant",
                "direction",
                "account_ref",
                "parse_notes",
                "raw_text",
            ]
            detail_columns = [column for column in detail_columns if column in detail_source.columns]
            st.dataframe(detail_source[detail_columns], use_container_width=True)

    with insights_tab:
        render_statement_insights_tab(repository, key_prefix="imports_statement_insights")

    with settings_tab:
        st.header("Import Settings")
        settings = repository.get_settings()
        with st.form("settings_form"):
            hsbc_password = st.text_input("HSBC PDF Password", value=settings.get("hsbc_password", ""), type="password")
            hsbc_folder = st.text_input("HSBC Folder", value=settings.get("hsbc_folder", ""))
            tcb_folder = st.text_input("TCB Image Folder", value=settings.get("tcb_image_folder", ""))
            tesseract_cmd = st.text_input(
                "Tesseract Command",
                value=settings.get("tesseract_cmd", ""),
                help="Set this if Tesseract is installed but not on PATH, e.g. C:\\Program Files\\Tesseract-OCR\\tesseract.exe",
            )
            liability_account = st.selectbox(
                "Default HSBC Liability Account",
                ACCOUNT_OPTIONS,
                index=_account_index(ACCOUNT_OPTIONS, settings.get("default_hsbc_liability_account", "Liability:Payable")),
            )
            tcb_cash_account = st.selectbox(
                "Default TCB Cash Account",
                ACCOUNT_OPTIONS,
                index=_account_index(ACCOUNT_OPTIONS, settings.get("default_tcb_cash_account", "Cash")),
            )
            tcb_offset_account = st.selectbox(
                "Default TCB Outflow Offset Account",
                ACCOUNT_OPTIONS,
                index=_account_index(ACCOUNT_OPTIONS, settings.get("default_tcb_offset_account", "Expense")),
            )
            submitted = st.form_submit_button("Save Settings")
            if submitted:
                repository.upsert_setting("hsbc_password", hsbc_password)
                repository.upsert_setting("hsbc_folder", hsbc_folder)
                repository.upsert_setting("tcb_image_folder", tcb_folder)
                repository.upsert_setting("tesseract_cmd", tesseract_cmd)
                repository.upsert_setting("default_hsbc_liability_account", liability_account)
                repository.upsert_setting("default_tcb_cash_account", tcb_cash_account)
                repository.upsert_setting("default_tcb_offset_account", tcb_offset_account)
                st.success("Settings saved.")
                st.rerun()

        deps = dependency_summary(settings.get("tesseract_cmd", ""))
        st.markdown("### Runtime Dependencies")
        st.write(
            {
                "pdfplumber": deps["pdfplumber"],
                "pytesseract": deps["pytesseract"],
            "tesseract_binary": deps["tesseract_binary"],
        }
    )


def render_data_health_section(repository: FinanceRepository) -> None:
    with st.expander("Data Health and Repair", expanded=True):
        summary = repository.get_data_health_summary()
        st.caption("Correctness-first controls. Audits are safe; repairs create a backup and require explicit confirmation.")

        metric1, metric2, metric3, metric4, metric5 = st.columns(5)
        metric1.metric("Ledger Rows", f"{summary['transactions']:,}")
        metric2.metric("Statement Rows", f"{summary['statement_rows']:,}")
        metric3.metric("Posted Links", f"{summary['posted_links']:,}")
        metric4.metric("Duplicate Groups", f"{summary['duplicate_groups']:,}")
        metric5.metric("Duplicate Extra Rows", f"{summary['duplicate_extra_rows']:,}")

        path_col, backup_col = st.columns(2)
        with path_col:
            st.write(
                {
                    "active_db_path": summary["active_db_path"],
                    "secondary_db_paths": summary["secondary_db_paths"],
                    "latest_transaction_date": summary["latest_transaction_date"],
                    "last_reconciliation": summary["reconciliation_summary"],
                }
            )
        with backup_col:
            st.write(
                {
                    "latest_backup_path": summary["latest_backup_path"] or "No backups found",
                    "backup_count": summary["latest_backup_count"],
                }
            )
            if st.button("Create Safety Backup", key="data_health_create_backup"):
                backup_path = repository.create_database_backup("manual-data-health")
                st.success(f"Created backup: {backup_path}")
                st.rerun()

        duplicate_df = repository.preview_duplicate_transaction_repair()
        st.subheader("Duplicate Ledger Preview")
        if duplicate_df.empty:
            st.success("No duplicate ledger groups were detected by economic identity.")
        else:
            st.warning("Review these duplicate groups before applying repair. The lowest transaction ID is kept.")
            st.dataframe(
                duplicate_df[
                    [
                        "group_id",
                        "date",
                        "description",
                        "amount",
                        "debit_account",
                        "credit_account",
                        "duplicate_count",
                        "keep_id",
                        "duplicate_ids",
                        "category_set",
                        "subcategory_set",
                    ]
                ],
                use_container_width=True,
            )

        audit_col, repair_col, reconcile_col = st.columns(3)
        if audit_col.button("Run Duplicate Audit", key="run_duplicate_audit"):
            audit = repository.run_data_quality_audit("Manual audit from Review & Edit.")
            st.success(
                f"Audit {audit['run_id']} completed: {audit['duplicate_groups']} duplicate groups, "
                f"{audit['duplicate_extra_rows']} extra rows."
            )
            st.rerun()

        confirm_repair = repair_col.checkbox(
            "I reviewed duplicate preview",
            key="confirm_duplicate_repair",
            help="Required before deleting duplicate ledger rows. A database backup is created first.",
        )
        if repair_col.button("Apply Duplicate Repair", key="apply_duplicate_repair", disabled=not confirm_repair or duplicate_df.empty):
            repair_ids: list[int] = []
            for row in duplicate_df.to_dict("records"):
                repair_ids.extend(json.loads(row["duplicate_ids_json"]))
            result = repository.apply_duplicate_transaction_repair(repair_ids, "Manual duplicate repair from Data Health.")
            st.success(
                f"Deleted {result['deleted_count']:,} duplicate rows. Backup: {result['backup_path'] or 'not needed'}"
            )
            if result["skipped_linked_count"]:
                st.warning(f"Skipped {result['skipped_linked_count']:,} rows linked to posted statement rows.")
            st.rerun()

        if reconcile_col.button("Run Secondary Reconciliation Now", key="run_secondary_reconciliation"):
            result = repository.reconcile_secondary_databases()
            st.info(f"Reconciliation result: {result}")
            st.rerun()

        history_col, repair_history_col = st.columns(2)
        with history_col:
            st.subheader("Audit History")
            runs_df = repository.get_data_quality_runs_df()
            if runs_df.empty:
                st.info("No data-quality audits have been recorded yet.")
            else:
                st.dataframe(runs_df.head(10), use_container_width=True)
        with repair_history_col:
            st.subheader("Repair History")
            actions_df = repository.get_ledger_repair_actions_df()
            if actions_df.empty:
                st.info("No ledger repair actions have been applied.")
            else:
                st.dataframe(actions_df.head(10), use_container_width=True)


def render_edit_tab(repository: FinanceRepository) -> None:
    st.header("Review & Edit")
    st.caption("Audit recent activity, inspect large transactions, and clean up default-classified ledger rows.")
    render_data_health_section(repository)

    df = repository.get_ledger()
    if df.empty:
        st.info("No transactions found.")
        return

    df = df.copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"])
    df["month"] = df["date"].dt.to_period("M").astype(str)

    col1, col2 = st.columns(2)
    with col1:
        start_date = st.date_input("Start Date", df["date"].min().date())
    with col2:
        end_date = st.date_input("End Date", df["date"].max().date())

    month_options = sorted(df["month"].unique(), reverse=True)
    selected_months = st.multiselect("Filter by Month", options=month_options, default=month_options[:1] or month_options)
    all_categories = sorted(df["category"].dropna().unique())
    selected_categories = st.multiselect("Category", options=all_categories, default=all_categories)
    debit_filter = st.multiselect("Debit Account", options=sorted(df["debit_account"].dropna().unique()), default=sorted(df["debit_account"].dropna().unique()))
    credit_filter = st.multiselect("Credit Account", options=sorted(df["credit_account"].dropna().unique()), default=sorted(df["credit_account"].dropna().unique()))
    search_text = st.text_input("Search in Description")
    task_view = st.selectbox(
        "Audit View",
        ["All filtered", "Recent", "Large", "Default-classified", "Needs correction"],
        index=0,
    )

    filtered_df = df[
        (df["date"].dt.date >= start_date)
        & (df["date"].dt.date <= end_date)
        & (df["month"].isin(selected_months))
        & (df["category"].isin(selected_categories))
        & (df["debit_account"].isin(debit_filter))
        & (df["credit_account"].isin(credit_filter))
        & (df["description"].fillna("").str.contains(search_text, case=False, na=False))
    ].copy()

    filtered_df = filtered_df.sort_values(by="id", ascending=False)
    if task_view == "Recent":
        filtered_df = filtered_df.head(25)
    elif task_view == "Large":
        filtered_df = filtered_df.sort_values(by="amount", ascending=False).head(25)
    elif task_view in {"Default-classified", "Needs correction"}:
        filtered_df = filtered_df[
            (filtered_df["category"] == "Others")
            | (filtered_df["subcategory"] == "Other expense")
        ].copy()

    edit1, edit2, edit3 = st.columns(3)
    edit1.metric("Filtered Rows", f"{len(filtered_df):,}")
    edit2.metric("Filtered Amount", f"{filtered_df['amount'].sum():,.0f} VND")
    edit3.metric("Default-classified Rows", f"{int(((filtered_df['category'] == 'Others') | (filtered_df['subcategory'] == 'Other expense')).sum()):,}")
    categories = list(CATEGORY_MAP.keys())

    for _, row in filtered_df.iterrows():
        with st.expander(f"{row['id']} | {row['date'].date()} | {row['description']} | {row['amount']:,.0f} VND"):
            with st.form(f"edit_form_{row['id']}"):
                left, right = st.columns(2)
                with left:
                    date_edit = st.date_input("Date", row["date"].date(), key=f"date_{row['id']}")
                    desc_edit = st.text_input("Description", row["description"], key=f"desc_{row['id']}")
                    cat_index = categories.index(row["category"]) if row["category"] in categories else 0
                    category_edit = st.selectbox("Category", categories, index=cat_index, key=f"cat_{row['id']}")
                with right:
                    available_subcats = CATEGORY_MAP[category_edit]
                    sub_index = available_subcats.index(row["subcategory"]) if row["subcategory"] in available_subcats else 0
                    subcategory_edit = st.selectbox("Sub-category", available_subcats, index=sub_index, key=f"subcat_{row['id']}")
                    debit_edit = st.selectbox("Debit Account", ACCOUNT_OPTIONS, index=_account_index(ACCOUNT_OPTIONS, row["debit_account"]), key=f"debit_{row['id']}")
                    credit_edit = st.selectbox("Credit Account", ACCOUNT_OPTIONS, index=_account_index(ACCOUNT_OPTIONS, row["credit_account"]), key=f"credit_{row['id']}")
                    amount_edit = st.number_input("Amount", min_value=0.0, value=float(row["amount"]), format="%.0f", key=f"amt_{row['id']}")

                update_col, delete_col = st.columns(2)
                if update_col.form_submit_button("Update"):
                    repository.update_transaction(
                        int(row["id"]),
                        date_edit.isoformat(),
                        desc_edit,
                        category_edit,
                        subcategory_edit,
                        debit_edit,
                        credit_edit,
                        amount_edit,
                    )
                    st.success(f"Updated transaction {row['id']}.")
                    st.rerun()

                if delete_col.form_submit_button("Delete"):
                    repository.delete_transaction(int(row["id"]))
                    st.warning(f"Deleted transaction {row['id']}.")
                    st.rerun()

    preview_df = filtered_df.copy()
    preview_df["date"] = preview_df["date"].dt.strftime("%Y-%m-%d")
    st.subheader(f"Filtered Transactions ({len(preview_df)} rows)")
    st.dataframe(preview_df.style.format({"amount": "{:,.0f}"}), use_container_width=True)


def run_app() -> None:
    repository = get_repository()
    sections = build_navigation_sections()
    default_section = st.session_state.get("active_finance_section", sections[0])
    if default_section not in sections:
        default_section = sections[0]
    active_section = st.radio(
        "Navigate",
        options=sections,
        index=sections.index(default_section),
        horizontal=True,
        key="active_finance_section",
        label_visibility="collapsed",
    )
    if active_section == "Dashboard":
        render_dashboard_tab(repository)
    elif active_section == "Expenses":
        render_expenses_tab(repository)
    elif active_section == "Investments":
        render_investments_tab(repository)
    elif active_section == "Transactions":
        render_transactions_tab(repository)
    elif active_section == "Planning":
        render_planning_tab(repository)
    elif active_section == "Imports":
        render_imports_tab(repository)
    else:
        render_edit_tab(repository)
