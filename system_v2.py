import sqlite3
from datetime import date

import matplotlib.pyplot as plt
import pandas as pd
import plotly.express as px
import seaborn as sns
import streamlit as st

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

DEBIT_ACCOUNTS = [
    "Cash",
    "Expense",
    "Asset:Receivable",
    "Asset:Savings",
    "Liability:Payable",
    "Equity:General",
]

CREDIT_ACCOUNTS = [
    "Cash",
    "Income:Salary",
    "Liability:Payable",
    "Equity:Opening Balance",
    "Equity:General",
    "Asset:Savings",
    "Asset:Receivable",
]

KNOWN_ACCOUNTS = [
    "Asset:Receivable",
    "Asset:Savings",
    "Cash",
    "Equity:Opening Balance",
    "Equity:General",
    "Expense",
    "Income:Salary",
    "Liability:Payable",
]


@st.cache_resource
def get_connection():
    return sqlite3.connect("finance.db", check_same_thread=False)


def init_db(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY,
            date TEXT,
            description TEXT,
            category TEXT,
            subcategory TEXT,
            debit_account TEXT,
            credit_account TEXT,
            amount REAL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS budgets (
            category TEXT PRIMARY KEY,
            monthly_limit REAL NOT NULL DEFAULT 0
        )
        """
    )
    conn.commit()


def record_transaction(conn, tx_date, desc, category, subcategory, debit, credit, amount):
    conn.execute(
        """
        INSERT INTO transactions (date, description, category, subcategory, debit_account, credit_account, amount)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (tx_date, desc, category, subcategory, debit, credit, amount),
    )
    conn.commit()


def update_transaction(conn, tx_id, tx_date, desc, category, subcategory, debit, credit, amount):
    conn.execute(
        """
        UPDATE transactions
        SET date = ?, description = ?, category = ?, subcategory = ?, debit_account = ?, credit_account = ?, amount = ?
        WHERE id = ?
        """,
        (tx_date, desc, category, subcategory, debit, credit, amount, tx_id),
    )
    conn.commit()


def delete_transaction(conn, tx_id):
    conn.execute("DELETE FROM transactions WHERE id = ?", (tx_id,))
    conn.commit()


def get_ledger(conn):
    return pd.read_sql_query("SELECT * FROM transactions ORDER BY date DESC, id DESC", conn)


def get_budgets(conn):
    return pd.read_sql_query("SELECT category, monthly_limit FROM budgets", conn)


def save_budget(conn, category, monthly_limit):
    conn.execute(
        """
        INSERT INTO budgets (category, monthly_limit)
        VALUES (?, ?)
        ON CONFLICT(category) DO UPDATE SET monthly_limit = excluded.monthly_limit
        """,
        (category, monthly_limit),
    )
    conn.commit()


def get_account_balance(conn):
    df = get_ledger(conn)
    if df.empty:
        return pd.DataFrame({"account": KNOWN_ACCOUNTS, "balance": [0.0] * len(KNOWN_ACCOUNTS)})

    debit = df.groupby("debit_account")["amount"].sum()
    credit = df.groupby("credit_account")["amount"].sum()

    for key in KNOWN_ACCOUNTS:
        if key not in debit.index:
            debit[key] = 0
        if key not in credit.index:
            credit[key] = 0

    balance = (debit.sort_index() - credit.sort_index()).fillna(0).to_frame("balance").reset_index()
    first_col = balance.columns[0]
    if first_col != "account":
        balance = balance.rename(columns={first_col: "account"})
    return balance[["account", "balance"]]


def classify_account_name(account_name):
    if account_name.startswith("Asset:"):
        return "Asset"
    if account_name.startswith("Liability:"):
        return "Liability"
    if account_name.startswith("Equity:"):
        return "Equity"
    if account_name.startswith("Income:"):
        return "Income"
    if account_name == "Cash":
        return "Asset"
    if account_name == "Expense":
        return "Expense"
    return "Expense"


def build_account_type_map(df):
    """
    Infer account classification from transaction-schema columns:
    date, description, category, subcategory, debit_account, credit_account, amount
    """
    account_types = {}
    if df.empty:
        return account_types

    accounts = pd.concat([df["debit_account"], df["credit_account"]], ignore_index=True).dropna().unique()
    for account in accounts:
        base_type = classify_account_name(str(account))
        debit_rows = df[df["debit_account"] == account]
        credit_rows = df[df["credit_account"] == account]

        if str(account).startswith(("Asset:", "Liability:", "Equity:", "Income:")) or account in {"Cash", "Expense"}:
            account_types[account] = base_type
            continue

        # Use category signals from the schema when account names do not encode type.
        income_side = (
            (not credit_rows.empty and credit_rows["category"].fillna("").eq("Income").any())
            or (not debit_rows.empty and debit_rows["category"].fillna("").eq("Income").any())
        )
        expense_side = (
            (not debit_rows.empty and ~debit_rows["category"].fillna("").eq("Income")).any()
            or (not credit_rows.empty and ~credit_rows["category"].fillna("").eq("Income")).any()
        )

        if income_side and not expense_side:
            account_types[account] = "Income"
        elif expense_side and not income_side:
            account_types[account] = "Expense"
        else:
            account_types[account] = base_type

    return account_types


def render_transactions_tab(conn):
    st.title("Personal Finance Manager with T-Accounts")
    st.header("Add New Transaction")

    category = st.selectbox("Category", list(CATEGORY_MAP.keys()), key="category")
    subcategory = st.selectbox("Sub-category", CATEGORY_MAP[category], key="subcategory")

    with st.form("entry_form"):
        tx_date = st.date_input("Date", date.today()).strftime("%Y-%m-%d")
        description = st.text_input("Description")
        debit = st.selectbox("Debit Account", DEBIT_ACCOUNTS)
        credit = st.selectbox("Credit Account", CREDIT_ACCOUNTS)
        amount = st.number_input("Amount", min_value=0.0, format="%.0f")
        submitted = st.form_submit_button("Add Transaction")

        if submitted:
            record_transaction(conn, tx_date, description, category, subcategory, debit, credit, amount)
            st.success("Transaction recorded")

    ledger_df = get_ledger(conn)

    st.header("Ledger")
    st.dataframe(ledger_df.head(20).style.format({"amount": "{:,.0f}"}), use_container_width=True)

    st.header("Account Balances")
    balance = get_account_balance(conn)
    st.dataframe(balance.style.format({"balance": "{:,.0f}"}), use_container_width=True)

    account_type_map = build_account_type_map(ledger_df)
    balance["type"] = balance["account"].map(account_type_map).fillna(balance["account"].apply(classify_account_name))

    st.header("Balance Sheet")
    col1, col2 = st.columns(2)

    with col1:
        st.subheader("Assets")
        st.dataframe(
            balance.loc[balance["type"] == "Asset", ["account", "balance"]].style.format({"balance": "{:,.0f}"}),
            use_container_width=True,
        )

    with col2:
        st.subheader("Liabilities + Equity")
        st.dataframe(
            balance.loc[balance["type"].isin(["Liability", "Equity"]), ["account", "balance"]].style.format(
                {"balance": "{:,.0f}"}
            ),
            use_container_width=True,
        )

    st.markdown("---")
    assets_total = balance.loc[balance["type"] == "Asset", "balance"].sum()
    liab_equity_total = balance.loc[balance["type"].isin(["Liability", "Equity", "Income", "Expense"]), "balance"].sum()

    st.metric("Total Assets", f"{assets_total:,.0f}")
    st.metric("Total Liabilities + Equity", f"{liab_equity_total:,.0f}")


def render_edit_tab(conn):
    st.header("Edit or Delete Transactions")
    df = get_ledger(conn)
    if df.empty:
        st.info("No transactions found")
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
    selected_months = st.multiselect("Filter by Month", options=month_options, default=month_options[0])

    all_categories = sorted(df["category"].dropna().unique())
    select_all_categories = st.checkbox("Select All Categories", value=True)
    default_categories = all_categories if select_all_categories else []
    selected_categories = st.multiselect("Category", options=all_categories, default=default_categories)

    debit_options = sorted(df["debit_account"].dropna().unique())
    credit_options = sorted(df["credit_account"].dropna().unique())
    debit_filter = st.multiselect("Debit Account", options=debit_options, default=debit_options)
    credit_filter = st.multiselect("Credit Account", options=credit_options, default=credit_options)

    search_text = st.text_input("Search in Description")

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

    categories = list(CATEGORY_MAP.keys())
    for _, row in filtered_df.iterrows():
        with st.expander(f"{row['id']} | {row['date'].date()} | {row['description']} | {row['amount']:,.0f} VND"):
            with st.form(f"edit_form_{row['id']}"):
                c1, c2 = st.columns(2)
                with c1:
                    date_edit = st.date_input("Date", row["date"].date(), key=f"date_{row['id']}")
                    desc_edit = st.text_input("Description", row["description"], key=f"desc_{row['id']}")
                    cat_index = categories.index(row["category"]) if row["category"] in categories else 0
                    category_edit = st.selectbox("Category", categories, index=cat_index, key=f"cat_{row['id']}")
                with c2:
                    available_subcats = CATEGORY_MAP[category_edit]
                    sub_index = available_subcats.index(row["subcategory"]) if row["subcategory"] in available_subcats else 0
                    subcat_edit = st.selectbox(
                        "Sub-category", available_subcats, index=sub_index, key=f"subcat_{row['id']}"
                    )
                    debit_edit = st.text_input("Debit Account", row["debit_account"], key=f"debit_{row['id']}")
                    credit_edit = st.text_input("Credit Account", row["credit_account"], key=f"credit_{row['id']}")
                    amount_edit = st.number_input(
                        "Amount", min_value=0.0, value=float(row["amount"]), format="%.0f", key=f"amt_{row['id']}"
                    )

                ucol, dcol = st.columns(2)
                if ucol.form_submit_button("Update"):
                    update_transaction(
                        conn,
                        int(row["id"]),
                        date_edit.isoformat(),
                        desc_edit,
                        category_edit,
                        subcat_edit,
                        debit_edit,
                        credit_edit,
                        amount_edit,
                    )
                    st.success(f"Transaction {row['id']} updated")

                if dcol.form_submit_button("Delete"):
                    delete_transaction(conn, int(row["id"]))
                    st.warning(f"Transaction {row['id']} deleted")

    preview_df = filtered_df.copy()
    preview_df["date"] = preview_df["date"].dt.strftime("%Y-%m-%d")

    st.subheader(f"Filtered Transactions ({len(preview_df)} rows)")
    st.dataframe(preview_df.style.format({"amount": "{:,.0f}"}), use_container_width=True)


def render_insights_tab(conn):
    st.header("Finance Insights Dashboard")

    df = pd.read_sql_query("SELECT * FROM transactions", conn)
    if df.empty:
        st.info("No transaction data to analyze")
        return

    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"]).copy()
    df["month"] = df["date"].dt.to_period("M").astype(str)
    df["month_ts"] = df["date"].dt.to_period("M").dt.to_timestamp()

    expense_df = df[df["debit_account"].str.startswith("Expense", na=False)].copy()
    income_df = df[df["credit_account"].str.startswith("Income", na=False)].copy()

    monthly_income = income_df.groupby("month_ts")["amount"].sum().reset_index(name="Income")
    monthly_expense = expense_df.groupby("month_ts")["amount"].sum().reset_index(name="Expense")
    monthly_summary = pd.merge(monthly_income, monthly_expense, on="month_ts", how="outer").fillna(0)
    monthly_summary = monthly_summary.sort_values("month_ts")

    st.subheader("Set Monthly Budget per Category")
    category_input = st.selectbox("Select Category", sorted(CATEGORY_MAP.keys()))
    budget_input = st.number_input("Monthly Budget (VND)", min_value=0.0, format="%.0f")

    if st.button("Save Budget"):
        save_budget(conn, category_input, budget_input)
        st.success(f"Budget saved for {category_input}")

    st.subheader("Monthly Income vs Expense")
    if not monthly_summary.empty:
        plot_df = monthly_summary.melt(
            id_vars="month_ts", value_vars=["Income", "Expense"], var_name="Type", value_name="Amount"
        )
        fig = px.line(
            plot_df,
            x="month_ts",
            y="Amount",
            color="Type",
            color_discrete_map={"Income": "green", "Expense": "red"},
            markers=True,
        )
        fig.update_traces(line={"width": 2})
        fig.update_layout(xaxis_title="Month", yaxis_title="Amount", legend_title="Type")
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("Not enough income/expense data to draw monthly trend")

    st.header("Monthly Expenses by Category")
    if expense_df.empty:
        st.info("No expense transactions found")
    else:
        latest_months = sorted(expense_df["month"].unique())[-3:]
        recent_expenses = expense_df[expense_df["month"].isin(latest_months)]

        pivot_df = recent_expenses.pivot_table(
            index="category",
            columns="month",
            values="amount",
            aggfunc="sum",
            fill_value=0,
        )

        fig, ax = plt.subplots(figsize=(12, 6))
        sns.heatmap(pivot_df, annot=True, fmt=",.0f", cmap="YlOrRd", linewidths=0.5, cbar=True, ax=ax)
        ax.set_title("Monthly Expenses by Category")
        ax.set_xlabel("Month")
        ax.set_ylabel("Category")
        plt.xticks(rotation=45)
        plt.tight_layout()
        st.pyplot(fig)

        st.header("Net Monthly Balance by Account (All Types)")
        three_month_df = df[df["month"].isin(latest_months)]
        debit_bal = three_month_df.groupby(["debit_account", "month"])["amount"].sum().unstack(fill_value=0)
        credit_bal = (
            three_month_df.groupby(["credit_account", "month"])["amount"].sum().unstack(fill_value=0) * -1
        )
        all_balances = debit_bal.add(credit_bal, fill_value=0)
        all_balances.index.name = "account"

        fig, ax = plt.subplots(figsize=(12, 6))
        sns.heatmap(
            all_balances,
            annot=all_balances.applymap(lambda x: f"{x:,.0f}"),
            fmt="",
            cmap="RdYlGn",
            linewidths=0.5,
            cbar=True,
            ax=ax,
        )
        ax.set_title("Net Balance by Account and Month")
        ax.set_xlabel("Month")
        ax.set_ylabel("Account")
        plt.xticks(rotation=45)
        plt.tight_layout()
        st.pyplot(fig)

    st.header("Investment Overview")
    inv_df = df[df["category"].fillna("").str.contains("invest", case=False)].copy()

    def tag_investment_direction(row):
        if str(row["debit_account"]).startswith("Cash"):
            return "Inflow"
        if str(row["credit_account"]).startswith("Cash"):
            return "Outflow"
        return "Neutral"

    if inv_df.empty:
        st.info("No investment-category transactions found")
    else:
        inv_df["direction"] = inv_df.apply(tag_investment_direction, axis=1)
        inv_df["signed_amount"] = inv_df.apply(
            lambda r: r["amount"]
            if r["direction"] == "Inflow"
            else (-r["amount"] if r["direction"] == "Outflow" else 0),
            axis=1,
        )

        monthly_inv = inv_df.groupby(["month", "direction"])["signed_amount"].sum().reset_index()
        inv_summary = monthly_inv.pivot(index="month", columns="direction", values="signed_amount").fillna(0)
        inv_summary["Net"] = inv_summary.get("Inflow", 0) + inv_summary.get("Outflow", 0)
        inv_summary = inv_summary.reset_index()

        fig = px.bar(
            inv_summary,
            x="month",
            y=["Inflow", "Outflow", "Net"],
            title="Investment Efficiency Over Time",
            labels={"value": "Amount", "month": "Month"},
            barmode="group",
            color_discrete_map={"Inflow": "green", "Outflow": "red", "Net": "blue"},
        )
        fig.update_traces(texttemplate="%{y:,.0f}", textposition="outside")
        fig.update_layout(xaxis_tickangle=-45, yaxis_title="VND", legend_title="")
        st.plotly_chart(fig, use_container_width=True)

    st.header("Pivot Table: Monthly Expenses by Category and Sub-category")
    if expense_df.empty:
        st.info("No expense data for pivot table")
    else:
        pivot = expense_df.groupby(["category", "subcategory", "month"])["amount"].sum().unstack(fill_value=0)
        styled_table = pivot.style.format("{:,.0f}").background_gradient(cmap="YlOrRd", axis=None)
        st.dataframe(styled_table, use_container_width=True)

    st.header("Budget vs. Actual Chart")
    budget_df = get_budgets(conn)
    if budget_df.empty or expense_df.empty:
        st.info("Need both budget entries and expense transactions to render this chart")
    else:
        monthly_actual = expense_df.groupby(["month", "category"])["amount"].sum().reset_index()
        merged_budget = pd.merge(monthly_actual, budget_df, on="category", how="left")
        merged_budget = merged_budget.dropna(subset=["monthly_limit"])

        if merged_budget.empty:
            st.info("No overlapping categories between budget and expense data")
        else:
            latest_month = merged_budget["month"].max()
            latest_data = merged_budget[merged_budget["month"] == latest_month]

            fig = px.bar(
                latest_data,
                x="category",
                y=["monthly_limit", "amount"],
                barmode="group",
                title=f"Budget vs Actual Expenses ({latest_month})",
                labels={"value": "VND"},
                color_discrete_map={"monthly_limit": "gray", "amount": "red"},
            )
            fig.update_traces(texttemplate="%{y:,.0f}", textposition="outside")
            fig.update_layout(xaxis_tickangle=-45, yaxis_title="VND", legend_title="")
            st.plotly_chart(fig, use_container_width=True)


if __name__ == "__main__":
    st.set_page_config(page_title="Personal Finance Manager", layout="wide")
    conn = get_connection()
    init_db(conn)

    tab_transactions, tab_insights, tab_edit = st.tabs(["Transactions", "Insights", "Edit Transactions"])

    with tab_transactions:
        render_transactions_tab(conn)

    with tab_insights:
        render_insights_tab(conn)

    with tab_edit:
        render_edit_tab(conn)
