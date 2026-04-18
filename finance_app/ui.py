from __future__ import annotations

from datetime import date

import matplotlib.pyplot as plt
import pandas as pd
import plotly.express as px
import seaborn as sns
import streamlit as st

from .constants import ACCOUNT_TYPES, CATEGORY_MAP, DEFAULT_CREDIT_ACCOUNT, DEFAULT_DEBIT_ACCOUNT, LEGACY_DB_PATH, SUPPORTED_CURRENCY
from .db import FinanceRepository
from .reports import (
    FilterState,
    apply_filters,
    build_balance_sections,
    build_balance_table,
    build_budget_vs_actual,
    build_expense_heatmap,
    build_expense_pivot,
    build_investment_summary,
    build_monthly_income_expense,
    build_net_balance_heatmap,
    prepare_transactions_for_display,
)


@st.cache_resource
def get_repository() -> FinanceRepository:
    return FinanceRepository()


def _account_index(options: list[str], preferred: str) -> int:
    if preferred in options:
        return options.index(preferred)
    return 0


def render_sidebar_filters(transactions: pd.DataFrame) -> FilterState:
    st.sidebar.header("Reporting Filters")
    if transactions.empty:
        st.sidebar.info("Filters will appear after you record your first transaction.")
        return FilterState()

    prepared = transactions.copy()
    prepared["date"] = pd.to_datetime(prepared["date"], errors="coerce")
    prepared = prepared.dropna(subset=["date"]).copy()
    prepared["month"] = prepared["date"].dt.to_period("M").astype(str)

    min_date = prepared["date"].min().date()
    max_date = prepared["date"].max().date()
    month_options = sorted(prepared["month"].unique(), reverse=True)

    start_date = st.sidebar.date_input(
        "Start date",
        value=min_date,
        min_value=min_date,
        max_value=max_date,
        key="shared_start_date",
    )
    end_date = st.sidebar.date_input(
        "End date",
        value=max_date,
        min_value=min_date,
        max_value=max_date,
        key="shared_end_date",
    )
    selected_months = st.sidebar.multiselect(
        "Months",
        options=month_options,
        default=month_options,
        key="shared_months",
    )

    if start_date > end_date:
        st.sidebar.warning("Start date cannot be later than end date. Using the full date range instead.")
        start_date, end_date = min_date, max_date

    return FilterState(start_date=start_date, end_date=end_date, months=tuple(selected_months))


def render_overview(repo: FinanceRepository, filtered_transactions: pd.DataFrame) -> None:
    counts = repo.get_counts()
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Transactions", f"{counts['transactions']:,}")
    col2.metric("Accounts", f"{counts['accounts']:,}")
    col3.metric("Budgets", f"{counts['budgets']:,}")
    col4.metric("Filtered Rows", f"{len(filtered_transactions):,}")


def render_transactions_tab(repo: FinanceRepository, transactions: pd.DataFrame, filtered_transactions: pd.DataFrame, accounts: pd.DataFrame) -> None:
    st.header("Record Transaction")
    st.caption("Use the sidebar filters for reporting views. Current balances below always use the full ledger.")

    active_accounts = repo.get_accounts(active_only=True)
    active_account_names = active_accounts["name"].tolist() if not active_accounts.empty else []

    if len(active_account_names) < 2:
        st.warning("You need at least two active accounts before adding a transaction.")
    else:
        category = st.selectbox("Category", list(CATEGORY_MAP.keys()), key="entry_category")
        subcategory = st.selectbox("Sub-category", CATEGORY_MAP[category], key="entry_subcategory")

        with st.form("entry_form", clear_on_submit=True):
            tx_date = st.date_input("Date", value=date.today())
            description = st.text_input("Description")
            debit_account = st.selectbox(
                "Debit account",
                active_account_names,
                index=_account_index(active_account_names, DEFAULT_DEBIT_ACCOUNT),
            )
            credit_account = st.selectbox(
                "Credit account",
                active_account_names,
                index=_account_index(active_account_names, DEFAULT_CREDIT_ACCOUNT),
            )
            amount = st.number_input("Amount (VND)", min_value=0.0, format="%.0f")
            submitted = st.form_submit_button("Add transaction")

            if submitted:
                try:
                    repo.add_transaction(
                        tx_date=tx_date.isoformat(),
                        description=description,
                        category=category,
                        subcategory=subcategory,
                        debit_account=debit_account,
                        credit_account=credit_account,
                        amount=amount,
                    )
                    st.success("Transaction recorded.")
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))

    st.subheader("Filtered Ledger Preview")
    preview_df = prepare_transactions_for_display(filtered_transactions.head(20))
    if preview_df.empty:
        st.info("No transactions match the current reporting filters.")
    else:
        st.dataframe(preview_df, use_container_width=True)

    st.subheader("Current Account Balances")
    balance_table = build_balance_table(transactions, accounts)
    if balance_table.empty:
        st.info("Balances will appear after you record transactions.")
        return

    st.dataframe(
        balance_table[["account", "account_type", "balance"]].style.format({"balance": "{:,.0f}"}),
        use_container_width=True,
    )

    assets, liabilities_equity = build_balance_sections(balance_table)
    left, right = st.columns(2)
    with left:
        st.markdown("### Assets")
        st.dataframe(assets.style.format({"balance": "{:,.0f}"}), use_container_width=True)
    with right:
        st.markdown("### Liabilities, Equity, and Results")
        st.dataframe(liabilities_equity.style.format({"balance": "{:,.0f}"}), use_container_width=True)

    assets_total = assets["balance"].sum() if not assets.empty else 0.0
    liabilities_equity_total = liabilities_equity["balance"].sum() if not liabilities_equity.empty else 0.0
    metric_left, metric_right = st.columns(2)
    metric_left.metric("Total Assets", f"{assets_total:,.0f} {SUPPORTED_CURRENCY}")
    metric_right.metric("Liabilities + Equity + Results", f"{liabilities_equity_total:,.0f} {SUPPORTED_CURRENCY}")


def render_insights_tab(filtered_transactions: pd.DataFrame, budgets: pd.DataFrame) -> None:
    st.header("Insights")
    if filtered_transactions.empty:
        st.info("No transaction data matches the current filters.")
        return

    monthly_summary = build_monthly_income_expense(filtered_transactions)
    st.subheader("Monthly Income vs Expense")
    if monthly_summary.empty:
        st.info("Not enough income and expense data to draw a monthly trend.")
    else:
        plot_df = monthly_summary.melt(id_vars="month_ts", value_vars=["Income", "Expense"], var_name="Type", value_name="Amount")
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

    st.subheader("Monthly Expenses by Category")
    expense_heatmap = build_expense_heatmap(filtered_transactions)
    if expense_heatmap.empty:
        st.info("No expense transactions are available in the current filter range.")
    else:
        fig, ax = plt.subplots(figsize=(12, 6))
        sns.heatmap(expense_heatmap, annot=True, fmt=",.0f", cmap="YlOrRd", linewidths=0.5, cbar=True, ax=ax)
        ax.set_title("Monthly Expenses by Category")
        ax.set_xlabel("Month")
        ax.set_ylabel("Category")
        plt.xticks(rotation=45)
        plt.tight_layout()
        st.pyplot(fig)
        plt.close(fig)

    st.subheader("Net Monthly Balance by Account")
    account_heatmap = build_net_balance_heatmap(filtered_transactions)
    if account_heatmap.empty:
        st.info("No account balance trend is available for the current filter range.")
    else:
        fig, ax = plt.subplots(figsize=(12, 6))
        sns.heatmap(
            account_heatmap,
            annot=account_heatmap.applymap(lambda value: f"{value:,.0f}"),
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
        plt.close(fig)

    st.subheader("Investment Overview")
    investment_summary = build_investment_summary(filtered_transactions)
    if investment_summary.empty:
        st.info("No investment-category transactions are available in the current filter range.")
    else:
        fig = px.bar(
            investment_summary,
            x="month",
            y=["Inflow", "Outflow", "Net"],
            title="Investment Efficiency Over Time",
            labels={"value": "Amount", "month": "Month"},
            barmode="group",
            color_discrete_map={"Inflow": "green", "Outflow": "red", "Net": "blue"},
        )
        fig.update_traces(texttemplate="%{y:,.0f}", textposition="outside")
        fig.update_layout(xaxis_tickangle=-45, yaxis_title=SUPPORTED_CURRENCY, legend_title="")
        st.plotly_chart(fig, use_container_width=True)

    st.subheader("Expense Pivot")
    expense_pivot = build_expense_pivot(filtered_transactions)
    if expense_pivot.empty:
        st.info("No expense data is available for the pivot table.")
    else:
        st.dataframe(expense_pivot.style.format("{:,.0f}").background_gradient(cmap="YlOrRd", axis=None), use_container_width=True)

    st.subheader("Budget vs Actual")
    budget_vs_actual = build_budget_vs_actual(filtered_transactions, budgets)
    if budget_vs_actual.empty:
        st.info("Need both saved budgets and filtered expense transactions to render this chart.")
    else:
        latest_month = budget_vs_actual["month"].max()
        latest_data = budget_vs_actual[budget_vs_actual["month"] == latest_month]
        fig = px.bar(
            latest_data,
            x="category",
            y=["monthly_limit", "amount"],
            barmode="group",
            title=f"Budget vs Actual Expenses ({latest_month})",
            labels={"value": SUPPORTED_CURRENCY},
            color_discrete_map={"monthly_limit": "gray", "amount": "red"},
        )
        fig.update_traces(texttemplate="%{y:,.0f}", textposition="outside")
        fig.update_layout(xaxis_tickangle=-45, yaxis_title=SUPPORTED_CURRENCY, legend_title="")
        st.plotly_chart(fig, use_container_width=True)


def render_transaction_manager(repo: FinanceRepository, filtered_transactions: pd.DataFrame) -> None:
    st.subheader("Review and Edit Transactions")
    if filtered_transactions.empty:
        st.info("No transactions match the current reporting filters.")
        return

    manager_df = filtered_transactions.copy()
    all_categories = sorted(manager_df["category"].dropna().unique())
    all_debit_accounts = sorted(manager_df["debit_account"].dropna().unique())
    all_credit_accounts = sorted(manager_df["credit_account"].dropna().unique())

    col1, col2 = st.columns(2)
    with col1:
        selected_categories = st.multiselect("Categories", options=all_categories, default=all_categories, key="manager_categories")
    with col2:
        search_text = st.text_input("Description search", key="manager_search")

    col3, col4 = st.columns(2)
    with col3:
        selected_debits = st.multiselect("Debit accounts", options=all_debit_accounts, default=all_debit_accounts, key="manager_debits")
    with col4:
        selected_credits = st.multiselect("Credit accounts", options=all_credit_accounts, default=all_credit_accounts, key="manager_credits")

    if selected_categories:
        manager_df = manager_df[manager_df["category"].isin(selected_categories)]
    else:
        manager_df = manager_df.iloc[0:0].copy()

    if selected_debits:
        manager_df = manager_df[manager_df["debit_account"].isin(selected_debits)]
    else:
        manager_df = manager_df.iloc[0:0].copy()

    if selected_credits:
        manager_df = manager_df[manager_df["credit_account"].isin(selected_credits)]
    else:
        manager_df = manager_df.iloc[0:0].copy()

    if search_text:
        manager_df = manager_df[manager_df["description"].fillna("").str.contains(search_text, case=False, na=False)]

    export_df = prepare_transactions_for_display(manager_df)
    st.download_button(
        "Download filtered CSV",
        data=repo.export_transactions_csv(export_df),
        file_name="transactions_export.csv",
        mime="text/csv",
    )

    st.dataframe(export_df, use_container_width=True)
    if manager_df.empty:
        st.info("No transactions remain after the manager filters.")
        return

    active_accounts = repo.get_accounts(active_only=True)["name"].tolist()
    for _, row in manager_df.iterrows():
        row_date = pd.to_datetime(row["date"], errors="coerce")
        account_options = sorted(set(active_accounts).union({row["debit_account"], row["credit_account"]}))
        category_options = list(CATEGORY_MAP.keys())
        category_value = row["category"] if row["category"] in category_options else category_options[0]
        subcategory_options = CATEGORY_MAP[category_value]
        subcategory_value = row["subcategory"] if row["subcategory"] in subcategory_options else subcategory_options[0]

        with st.expander(f"#{int(row['id'])} | {row_date.date()} | {row['description']} | {row['amount']:,.0f} {SUPPORTED_CURRENCY}"):
            with st.form(f"edit_tx_{int(row['id'])}"):
                left, right = st.columns(2)
                with left:
                    edit_date = st.date_input("Date", value=row_date.date(), key=f"date_{int(row['id'])}")
                    edit_description = st.text_input("Description", value=row["description"], key=f"desc_{int(row['id'])}")
                    edit_category = st.selectbox(
                        "Category",
                        category_options,
                        index=category_options.index(category_value),
                        key=f"category_{int(row['id'])}",
                    )
                with right:
                    edit_subcategory = st.selectbox(
                        "Sub-category",
                        CATEGORY_MAP[edit_category],
                        index=CATEGORY_MAP[edit_category].index(subcategory_value) if subcategory_value in CATEGORY_MAP[edit_category] else 0,
                        key=f"subcategory_{int(row['id'])}",
                    )
                    edit_debit = st.selectbox(
                        "Debit account",
                        account_options,
                        index=_account_index(account_options, row["debit_account"]),
                        key=f"debit_{int(row['id'])}",
                    )
                    edit_credit = st.selectbox(
                        "Credit account",
                        account_options,
                        index=_account_index(account_options, row["credit_account"]),
                        key=f"credit_{int(row['id'])}",
                    )
                    edit_amount = st.number_input(
                        "Amount (VND)",
                        min_value=0.0,
                        value=float(row["amount"]),
                        format="%.0f",
                        key=f"amount_{int(row['id'])}",
                    )

                confirm_delete = st.checkbox("Confirm delete", key=f"confirm_delete_{int(row['id'])}")
                save_col, delete_col = st.columns(2)
                save_pressed = save_col.form_submit_button("Save changes")
                delete_pressed = delete_col.form_submit_button("Delete transaction")

                if save_pressed:
                    try:
                        repo.update_transaction(
                            int(row["id"]),
                            tx_date=edit_date.isoformat(),
                            description=edit_description,
                            category=edit_category,
                            subcategory=edit_subcategory,
                            debit_account=edit_debit,
                            credit_account=edit_credit,
                            amount=edit_amount,
                            source=row.get("source", "manual"),
                        )
                        st.success(f"Transaction {int(row['id'])} updated.")
                        st.rerun()
                    except ValueError as exc:
                        st.error(str(exc))

                if delete_pressed:
                    if not confirm_delete:
                        st.error("Tick confirm delete before removing the transaction.")
                    else:
                        repo.delete_transaction(int(row["id"]))
                        st.warning(f"Transaction {int(row['id'])} deleted.")
                        st.rerun()


def render_budget_manager(repo: FinanceRepository, budgets: pd.DataFrame) -> None:
    st.subheader("Budget Management")
    category_options = sorted(CATEGORY_MAP.keys())
    budget_lookup = dict(zip(budgets["category"], budgets["monthly_limit"])) if not budgets.empty else {}

    with st.form("budget_form"):
        category = st.selectbox("Category", category_options)
        monthly_limit = st.number_input(
            "Monthly limit (VND)",
            min_value=0.0,
            value=float(budget_lookup.get(category, 0.0)),
            format="%.0f",
        )
        save_budget = st.form_submit_button("Save budget")
        if save_budget:
            try:
                repo.upsert_budget(category, monthly_limit)
                st.success(f"Budget saved for {category}.")
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))

    if budgets.empty:
        st.info("No budgets saved yet.")
    else:
        st.dataframe(budgets.style.format({"monthly_limit": "{:,.0f}"}), use_container_width=True)
        delete_category = st.selectbox("Delete a saved budget", budgets["category"].tolist(), key="delete_budget_category")
        confirm_delete = st.checkbox("Confirm budget delete", key="confirm_budget_delete")
        if st.button("Delete budget"):
            if not confirm_delete:
                st.error("Tick confirm budget delete before removing the budget.")
            else:
                repo.delete_budget(delete_category)
                st.warning(f"Deleted budget for {delete_category}.")
                st.rerun()


def render_account_manager(repo: FinanceRepository, accounts: pd.DataFrame) -> None:
    st.subheader("Account Management")

    with st.form("new_account_form", clear_on_submit=True):
        new_name = st.text_input("New account name")
        new_type = st.selectbox("Account type", ACCOUNT_TYPES)
        new_active = st.checkbox("Active", value=True)
        create_account = st.form_submit_button("Create account")
        if create_account:
            try:
                repo.upsert_account(name=new_name, account_type=new_type, is_active=new_active)
                st.success(f"Created account {new_name}.")
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))

    if accounts.empty:
        st.info("No accounts available.")
        return

    display_accounts = accounts[["name", "account_type", "is_active", "usage_count"]].copy()
    st.dataframe(display_accounts, use_container_width=True)

    for _, row in accounts.iterrows():
        account_name = row["name"]
        usage_count = int(row["usage_count"])
        label = f"{account_name} | {row['account_type']} | {'Active' if row['is_active'] else 'Inactive'} | Used {usage_count} times"
        with st.expander(label):
            with st.form(f"account_{account_name}"):
                rename_disabled = usage_count > 0
                edited_name = st.text_input("Account name", value=account_name, disabled=rename_disabled, key=f"name_{account_name}")
                edited_type = st.selectbox(
                    "Account type",
                    ACCOUNT_TYPES,
                    index=ACCOUNT_TYPES.index(row["account_type"]),
                    key=f"type_{account_name}",
                )
                edited_active = st.checkbox("Active", value=bool(row["is_active"]), key=f"active_{account_name}")
                if rename_disabled:
                    st.caption("Accounts already used in transactions can change type or active status, but not their name.")

                confirm_delete = st.checkbox("Confirm delete", value=False, disabled=usage_count > 0, key=f"delete_confirm_{account_name}")
                save_col, delete_col = st.columns(2)
                save_pressed = save_col.form_submit_button("Save account")
                delete_pressed = delete_col.form_submit_button("Delete account", disabled=usage_count > 0)

                if save_pressed:
                    try:
                        repo.upsert_account(
                            name=edited_name,
                            account_type=edited_type,
                            is_active=edited_active,
                            original_name=account_name,
                        )
                        st.success(f"Updated account {account_name}.")
                        st.rerun()
                    except ValueError as exc:
                        st.error(str(exc))

                if delete_pressed:
                    if not confirm_delete:
                        st.error("Tick confirm delete before removing the account.")
                    else:
                        try:
                            repo.delete_account(account_name)
                            st.warning(f"Deleted account {account_name}.")
                            st.rerun()
                        except ValueError as exc:
                            st.error(str(exc))


def render_system_tab(repo: FinanceRepository, transactions: pd.DataFrame, budgets: pd.DataFrame, accounts: pd.DataFrame) -> None:
    st.header("System")
    counts = repo.get_counts()
    col1, col2, col3 = st.columns(3)
    col1.metric("Transactions", f"{counts['transactions']:,}")
    col2.metric("Budgets", f"{counts['budgets']:,}")
    col3.metric("Accounts", f"{counts['accounts']:,}")

    st.markdown("### Storage")
    st.code(str(repo.db_path))
    st.caption(f"Backups directory: {repo.backup_dir}")
    if LEGACY_DB_PATH.exists():
        st.caption(f"Legacy database detected at: {LEGACY_DB_PATH}")

    if repo.bootstrap_note:
        st.info(repo.bootstrap_note)

    if st.button("Create database backup now"):
        backup_path = repo.create_backup("manual")
        st.success(f"Backup created at {backup_path}")

    backups = repo.list_backups(limit=5)
    st.markdown("### Recent Backups")
    if not backups:
        st.info("No backups have been created yet.")
    else:
        for backup_path in backups:
            st.write(str(backup_path))

    st.markdown("### Project Notes")
    st.write("Primary supported entrypoint: streamlit run system_v2.py")
    st.write("Legacy references kept for history: system_v0.py, system_v1.py, and system_v0.ipynb")
    st.write(f"Current in-app data counts: {len(transactions)} transactions, {len(budgets)} budgets, {len(accounts)} accounts.")


def run_app() -> None:
    repo = get_repository()
    transactions = repo.get_transactions()
    budgets = repo.get_budgets()
    accounts = repo.get_accounts(active_only=False)
    shared_filters = render_sidebar_filters(transactions)
    filtered_transactions = apply_filters(transactions, shared_filters)

    st.title("Personal Finance Manager")
    st.caption("Reliable local finance tracking with validated entries, editable accounts, saved budgets, exports, and automatic database backups.")
    render_overview(repo, filtered_transactions)

    tabs = st.tabs(["Transactions", "Insights", "Manage", "System"])

    with tabs[0]:
        render_transactions_tab(repo, transactions, filtered_transactions, accounts)

    with tabs[1]:
        render_insights_tab(filtered_transactions, budgets)

    with tabs[2]:
        manage_tabs = st.tabs(["Transactions", "Budgets", "Accounts"])
        with manage_tabs[0]:
            render_transaction_manager(repo, filtered_transactions)
        with manage_tabs[1]:
            render_budget_manager(repo, budgets)
        with manage_tabs[2]:
            render_account_manager(repo, accounts)

    with tabs[3]:
        render_system_tab(repo, transactions, budgets, accounts)
