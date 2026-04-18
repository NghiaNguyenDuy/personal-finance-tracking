from __future__ import annotations

from datetime import date

import matplotlib.pyplot as plt
import pandas as pd
import plotly.express as px
import seaborn as sns
import streamlit as st

from .constants import ACCOUNT_OPTIONS, CATEGORY_MAP, DEFAULT_CREDIT_ACCOUNTS, DEFAULT_DEBIT_ACCOUNTS, SOURCE_HSBC, SOURCE_TCB_IMAGE
from .importers import clean_merchant_keyword, dependency_summary, scan_sources
from .repository import FinanceRepository, classify_account_name


@st.cache_resource
def get_repository() -> FinanceRepository:
    return FinanceRepository()


def _account_index(options: list[str], preferred: str) -> int:
    if preferred in options:
        return options.index(preferred)
    return 0


def _chart_key(prefix: str, name: str) -> str:
    return f"{prefix}_{name}"


def render_transactions_tab(repository: FinanceRepository) -> None:
    st.title("Personal Finance Manager with T-Accounts")
    st.caption("Manual ledger entries stay separate from statement imports until imported rows are reviewed and posted.")
    st.header("Add New Transaction")

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
    st.subheader("Ledger")
    if ledger_df.empty:
        st.info("No manual or posted ledger transactions yet.")
    else:
        st.dataframe(ledger_df.head(20).style.format({"amount": "{:,.0f}"}), use_container_width=True)

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
    st.subheader("Monthly Budget per Category")
    category_input = st.selectbox("Select Category", sorted(CATEGORY_MAP.keys()), key="budget_category")
    existing_budget = 0.0
    if not budget_df.empty and category_input in budget_df["category"].tolist():
        existing_budget = float(budget_df.loc[budget_df["category"] == category_input, "monthly_limit"].iloc[0])
    budget_input = st.number_input("Monthly Budget (VND)", min_value=0.0, value=existing_budget, format="%.0f")
    if st.button("Save Budget"):
        repository.save_budget(category_input, budget_input)
        st.success(f"Budget saved for {category_input}.")
        st.rerun()

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

    hsbc_df = statement_df[statement_df["source_type"] == SOURCE_HSBC].copy()
    tcb_df = statement_df[statement_df["source_type"] == SOURCE_TCB_IMAGE].copy()

    top1, top2, top3 = st.columns(3)
    top1.metric("Parsed Statement Rows", f"{len(statement_df):,}")
    top2.metric("Pending Review", f"{int((statement_df['review_status'] == 'pending').sum()):,}")
    top3.metric("Posted To Ledger", f"{int((statement_df['review_status'] == 'posted').sum()):,}")

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

    low_confidence = statement_df[statement_df["confidence"] < 0.8]
    st.subheader("Rows Needing Extra Review")
    if low_confidence.empty:
        st.success("No low-confidence rows are currently flagged.")
    else:
        st.dataframe(
            low_confidence[
                ["source_type", "statement_month", "transaction_date", "description", "amount", "confidence", "parse_notes"]
            ],
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
        filter_col1, filter_col2, filter_col3 = st.columns(3)
        with filter_col1:
            source_filter = st.selectbox("Source", ["", SOURCE_HSBC, SOURCE_TCB_IMAGE], format_func=lambda value: value or "All sources")
        with filter_col2:
            status_filter = st.selectbox("Status", ["", "pending", "posted", "ignored"], format_func=lambda value: value or "All statuses")
        with filter_col3:
            month_options = [""]
            if not files_df.empty:
                month_options.extend(sorted(m for m in files_df["statement_month"].dropna().unique().tolist() if m))
            month_filter = st.selectbox("Statement month", month_options, format_func=lambda value: value or "All months")

        rows_df = repository.get_statement_rows_df(source_filter, status_filter, month_filter)
        if rows_df.empty:
            st.info("No statement rows match the current filters.")
            return

        editor_df = rows_df.copy()
        editor_df.insert(0, "selected", False)
        editor_df["transaction_date"] = pd.to_datetime(editor_df["transaction_date"], errors="coerce")
        edited_df = st.data_editor(
            editor_df,
            use_container_width=True,
            num_rows="fixed",
            hide_index=True,
            column_config={
                "selected": st.column_config.CheckboxColumn("Select"),
                "transaction_date": st.column_config.DateColumn("Transaction Date", format="YYYY-MM-DD"),
                "category": st.column_config.SelectboxColumn("Category", options=list(CATEGORY_MAP.keys())),
                "debit_account": st.column_config.SelectboxColumn("Debit Account", options=ACCOUNT_OPTIONS),
                "credit_account": st.column_config.SelectboxColumn("Credit Account", options=ACCOUNT_OPTIONS),
                "amount": st.column_config.NumberColumn("Amount", format="%.0f"),
                "confidence": st.column_config.NumberColumn("Confidence", format="%.2f", disabled=True),
            },
            disabled=[
                "id",
                "source_file_id",
                "source_type",
                "statement_month",
                "post_date",
                "event_time",
                "merchant",
                "direction",
                "running_balance",
                "account_ref",
                "row_type",
                "review_status",
                "confidence",
                "parse_notes",
                "raw_text",
            ],
        )

        save_col, post_col, ignore_col = st.columns(3)
        if save_col.button("Save Edits"):
            payload = []
            for row in edited_df.to_dict("records"):
                tx_date = row["transaction_date"]
                if pd.notna(tx_date):
                    row["transaction_date"] = pd.Timestamp(tx_date).strftime("%Y-%m-%d")
                else:
                    row["transaction_date"] = ""
                payload.append(row)
            repository.update_statement_row_edits(payload)
            st.success("Saved statement-row edits.")
            st.rerun()

        selected_ids = edited_df.loc[edited_df["selected"], "id"].tolist()
        if post_col.button("Post Selected"):
            payload = []
            for row in edited_df.to_dict("records"):
                tx_date = row["transaction_date"]
                if pd.notna(tx_date):
                    row["transaction_date"] = pd.Timestamp(tx_date).strftime("%Y-%m-%d")
                else:
                    row["transaction_date"] = ""
                payload.append(row)
            repository.update_statement_row_edits(payload)
            posted, messages = repository.post_statement_rows(selected_ids, clean_merchant_keyword)
            if posted:
                st.success(f"Posted {posted} rows into the ledger.")
            for message in messages:
                st.info(message)
            st.rerun()

        if ignore_col.button("Ignore Selected"):
            ignored = repository.set_statement_status(selected_ids, "ignored")
            st.success(f"Ignored {ignored} rows.")
            st.rerun()

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


def render_edit_tab(repository: FinanceRepository) -> None:
    st.header("Edit or Delete Transactions")
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
    tab_transactions, tab_insights, tab_imports, tab_edit = st.tabs(["Transactions", "Insights", "Imports", "Edit Transactions"])
    with tab_transactions:
        render_transactions_tab(repository)
    with tab_insights:
        ledger_tab, statement_tab = st.tabs(["Ledger Insights", "Statement Insights"])
        with ledger_tab:
            render_ledger_insights_tab(repository, key_prefix="main_ledger_insights")
        with statement_tab:
            render_statement_insights_tab(repository, key_prefix="main_statement_insights")
    with tab_imports:
        render_imports_tab(repository)
    with tab_edit:
        render_edit_tab(repository)
