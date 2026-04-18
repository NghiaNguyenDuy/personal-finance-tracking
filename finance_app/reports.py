from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd

from .constants import infer_account_type_from_name


@dataclass(frozen=True)
class FilterState:
    start_date: date | None = None
    end_date: date | None = None
    months: tuple[str, ...] | None = None


def prepare_transactions_frame(transactions: pd.DataFrame) -> pd.DataFrame:
    frame = transactions.copy()
    if frame.empty:
        return frame

    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame = frame.dropna(subset=["date"]).copy()
    frame["month"] = frame["date"].dt.to_period("M").astype(str)
    frame["month_ts"] = frame["date"].dt.to_period("M").dt.to_timestamp()
    return frame


def apply_filters(transactions: pd.DataFrame, filters: FilterState) -> pd.DataFrame:
    frame = prepare_transactions_frame(transactions)
    if frame.empty:
        return frame

    if filters.start_date:
        frame = frame[frame["date"].dt.date >= filters.start_date]
    if filters.end_date:
        frame = frame[frame["date"].dt.date <= filters.end_date]
    if filters.months is not None:
        if not filters.months:
            return frame.iloc[0:0].copy()
        frame = frame[frame["month"].isin(filters.months)]

    return frame.sort_values(by=["date", "id"], ascending=[False, False])


def prepare_transactions_for_display(transactions: pd.DataFrame) -> pd.DataFrame:
    frame = transactions.copy()
    if frame.empty:
        return frame

    if "date" in frame.columns:
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    return frame


def build_balance_table(transactions: pd.DataFrame, accounts: pd.DataFrame) -> pd.DataFrame:
    base_columns = ["account", "account_type", "is_active", "balance"]
    account_frame = accounts.copy()
    if account_frame.empty and transactions.empty:
        return pd.DataFrame(columns=base_columns)

    if not account_frame.empty:
        account_frame = account_frame[["name", "account_type", "is_active"]].rename(columns={"name": "account"})
    else:
        account_frame = pd.DataFrame(columns=["account", "account_type", "is_active"])

    ledger = prepare_transactions_frame(transactions)
    if ledger.empty:
        account_frame["balance"] = 0.0
        return account_frame[base_columns].sort_values(by=["account_type", "account"])

    debit = ledger.groupby("debit_account")["amount"].sum()
    credit = ledger.groupby("credit_account")["amount"].sum()

    missing_accounts = sorted((set(debit.index) | set(credit.index)) - set(account_frame["account"]))
    if missing_accounts:
        missing_frame = pd.DataFrame(
            {
                "account": missing_accounts,
                "account_type": [infer_account_type_from_name(name) for name in missing_accounts],
                "is_active": [True] * len(missing_accounts),
            }
        )
        account_frame = pd.concat([account_frame, missing_frame], ignore_index=True)

    account_frame["balance"] = account_frame["account"].map(debit).fillna(0) - account_frame["account"].map(credit).fillna(0)
    return account_frame[base_columns].sort_values(by=["account_type", "account"])


def build_balance_sections(balance_table: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    if balance_table.empty:
        return balance_table.copy(), balance_table.copy()

    assets = balance_table.loc[balance_table["account_type"] == "Asset", ["account", "balance"]].copy()
    liabilities_equity = balance_table.loc[
        balance_table["account_type"].isin(["Liability", "Equity", "Income", "Expense"]),
        ["account", "balance"],
    ].copy()
    return assets, liabilities_equity


def build_monthly_income_expense(transactions: pd.DataFrame) -> pd.DataFrame:
    ledger = prepare_transactions_frame(transactions)
    if ledger.empty:
        return pd.DataFrame(columns=["month_ts", "Income", "Expense"])

    income_df = ledger[ledger["credit_type"] == "Income"].copy()
    expense_df = ledger[ledger["debit_type"] == "Expense"].copy()

    monthly_income = income_df.groupby("month_ts")["amount"].sum().reset_index(name="Income")
    monthly_expense = expense_df.groupby("month_ts")["amount"].sum().reset_index(name="Expense")
    summary = pd.merge(monthly_income, monthly_expense, on="month_ts", how="outer").fillna(0)
    return summary.sort_values("month_ts")


def build_expense_heatmap(transactions: pd.DataFrame, month_window: int = 3) -> pd.DataFrame:
    ledger = prepare_transactions_frame(transactions)
    expense_df = ledger[ledger["debit_type"] == "Expense"].copy()
    if expense_df.empty:
        return pd.DataFrame()

    latest_months = sorted(expense_df["month"].unique())[-month_window:]
    recent_expenses = expense_df[expense_df["month"].isin(latest_months)]
    return recent_expenses.pivot_table(
        index="category",
        columns="month",
        values="amount",
        aggfunc="sum",
        fill_value=0,
    )


def build_net_balance_heatmap(transactions: pd.DataFrame, month_window: int = 3) -> pd.DataFrame:
    ledger = prepare_transactions_frame(transactions)
    if ledger.empty:
        return pd.DataFrame()

    latest_months = sorted(ledger["month"].unique())[-month_window:]
    ledger = ledger[ledger["month"].isin(latest_months)]
    debit_bal = ledger.groupby(["debit_account", "month"])["amount"].sum().unstack(fill_value=0)
    credit_bal = ledger.groupby(["credit_account", "month"])["amount"].sum().unstack(fill_value=0) * -1
    balances = debit_bal.add(credit_bal, fill_value=0)
    balances.index.name = "account"
    return balances.sort_index()


def build_investment_summary(transactions: pd.DataFrame) -> pd.DataFrame:
    ledger = prepare_transactions_frame(transactions)
    if ledger.empty:
        return pd.DataFrame(columns=["month", "Inflow", "Outflow", "Net"])

    investment_df = ledger[ledger["category"].fillna("").str.contains("invest", case=False)].copy()
    if investment_df.empty:
        return pd.DataFrame(columns=["month", "Inflow", "Outflow", "Net"])

    def tag_investment_direction(row: pd.Series) -> str:
        if str(row["debit_account"]).startswith("Cash"):
            return "Inflow"
        if str(row["credit_account"]).startswith("Cash"):
            return "Outflow"
        return "Neutral"

    investment_df["direction"] = investment_df.apply(tag_investment_direction, axis=1)
    investment_df["signed_amount"] = investment_df.apply(
        lambda row: row["amount"] if row["direction"] == "Inflow" else (-row["amount"] if row["direction"] == "Outflow" else 0),
        axis=1,
    )

    monthly = investment_df.groupby(["month", "direction"])["signed_amount"].sum().reset_index()
    summary = monthly.pivot(index="month", columns="direction", values="signed_amount").fillna(0)
    summary["Net"] = summary.get("Inflow", 0) + summary.get("Outflow", 0)
    return summary.reset_index()


def build_expense_pivot(transactions: pd.DataFrame) -> pd.DataFrame:
    ledger = prepare_transactions_frame(transactions)
    expense_df = ledger[ledger["debit_type"] == "Expense"].copy()
    if expense_df.empty:
        return pd.DataFrame()

    return expense_df.groupby(["category", "subcategory", "month"])["amount"].sum().unstack(fill_value=0)


def build_budget_vs_actual(transactions: pd.DataFrame, budgets: pd.DataFrame) -> pd.DataFrame:
    ledger = prepare_transactions_frame(transactions)
    expense_df = ledger[ledger["debit_type"] == "Expense"].copy()
    if expense_df.empty or budgets.empty:
        return pd.DataFrame(columns=["month", "category", "amount", "monthly_limit"])

    monthly_actual = expense_df.groupby(["month", "category"])["amount"].sum().reset_index()
    merged = pd.merge(monthly_actual, budgets, on="category", how="left")
    merged = merged.dropna(subset=["monthly_limit"])
    return merged.sort_values(by=["month", "category"])
