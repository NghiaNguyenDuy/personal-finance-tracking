"""Legacy reference module. This file is kept for history only and is not a supported runtime entrypoint."""`r`n`r`nimport sqlite3
import pandas as pd
import streamlit as st
from datetime import date
import datetime
# Plot heatmap
import matplotlib.pyplot as plt
import seaborn as sns
import plotly.express as px


def init_db():
    conn = sqlite3.connect('finance.db')
    cursor = conn.cursor()
    cursor.execute('''
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
    ''')
    conn.commit()
    return conn

def record_transaction(conn, date, desc, category, subcategory, debit, credit, amount):
    conn.execute('''
        INSERT INTO transactions (date, description, category, subcategory, debit_account, credit_account, amount)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (date, desc, category, subcategory, debit, credit, amount))
    conn.commit()

def delete_transaction(conn, tx_id):
    conn.execute("DELETE FROM transactions WHERE id = ?", (tx_id,))
    conn.commit()

# def update_transaction(conn, tx_id, date, desc, category, debit, credit, amount):
#     conn.execute("""
#         UPDATE transactions
#         SET date = ?, description = ?, category = ?, debit_account = ?, credit_account = ?, amount = ?
#         WHERE id = ?
#     """, (date, desc, category, debit, credit, amount, tx_id))
#     conn.commit()

def update_transaction(conn, tx_id, date, desc, category, subcategory, debit, credit, amount):
    conn.execute("""
        UPDATE transactions
        SET date = ?, description = ?, category = ?, subcategory = ?, debit_account = ?, credit_account = ?, amount = ?
        WHERE id = ?
    """, (date, desc, category, subcategory, debit, credit, amount, tx_id))
    conn.commit()

def get_ledger(conn):
    df = pd.read_sql_query("SELECT * FROM transactions ORDER BY date DESC", conn)
    return df

def get_account_balance(conn):
    df = get_ledger(conn)
    debit = df.groupby('debit_account')['amount'].sum()
    credit = df.groupby('credit_account')['amount'].sum()

    account_names = ['Asset:Receivable', 'Asset:Savings', 'Cash', 'Equity:Opening Balance',
       'Expense', 'Income:Salary', 'Liability:Payable']
    for key in account_names:
        if key not in debit.index:
            debit[key] = 0
        if key not in credit.index:
            credit[key] = 0

    debit.sort_index(inplace=True)
    credit.sort_index(inplace=True)
    balance = (debit - credit).fillna(0).to_frame('balance')
    return balance.reset_index().rename(columns={'index': 'account'})


if __name__ == "__main__":
    dict_category = {
                    "Income": ["Salary", "Bonuses", "Outsourcing", "Investment income", "Side hustle revenue"],
                    "Housing": ["Mortgage/rent", "Property taxes", "HOA fees", "Maintenance"],
                    "Food": ["Groceries", "Dining out"],
                    "Growth & Learning": ["Courses", "Books", "Workshops", "Certifications", "Tuition"],
                    "Transportation": ["Car payments", "Gas", "Insurance", "Maintenance", "Public transit fees"],
                    "Utilities": ["Electricity", "Internet", "Phone"],
                    "Savings & Investing" : ["Emergency fund", "Brokerage accounts"],
                    "Debt Payments": ["Credit card debt", "Personal loans", "Bank loans"],
                    "Healthcare": ["Insurance premiums", "Copays", "Prescriptions"],
                    "Personal Care/Lifestyle": ["Clothing", "Grooming", "Entertainment", "Subscriptions"],
                    "Family/Love/Dependents": ["Parental care", "Love", "Childcare", "School fees", "Pet care"],
                    "Protection": ["Life insurance", "Disability insurance", "Estate planning"],
                    "Others": "Other expense"
                    }
    tab1, tab2, tab3 = st.tabs(["ðŸ§¾ Transactions", "ðŸ“Š Insights", "âœï¸ Edit Transactions"])
    with tab1:
        conn = init_db()

        st.title("ðŸ“˜ Personal Finance Manager with T-Accounts")


        # --- Add Transaction ---
        st.header("âž• Add New Transaction")

        category = st.selectbox("Category", list(dict_category.keys()), key="category")
        sub_categories = dict_category.get(category, [])
        sub_category = st.selectbox("Sub-category", dict_category[st.session_state["category"]],)

        with st.form("entry_form"):
            t_date = st.date_input("Date", date.today())
            t_date = t_date.strftime("%Y-%m-%d")
            description = st.text_input("Description")
            
            debit = st.selectbox("Debit Account", ["Cash", "Expense", "Asset:Receivable", "Asset:Savings", "Liability:Payable", "Equity:General"])
            credit = st.selectbox("Credit Account", ["Cash", "Income:Salary", "Liability:Payable", "Equity:Opening Balance", "Equity:General", "Asset:Savings", "Asset:Receivable"])
            amount = st.number_input("Amount", min_value=0.0, format="%.0f")
            submitted = st.form_submit_button("Add Transaction")

            st.text(t_date)
            if submitted:
                record_transaction(conn, t_date, description, category, sub_category, debit, credit, amount)
                st.success("Transaction recorded!")
       

        
        # --- View Ledger ---
        st.header("ðŸ“‘ Ledger")
        st.dataframe(get_ledger(conn)[:20].style.format({
                    'amount': '{:,.0f}'  # Format with commas and no decimal places
                }))
        
        # --- View Balances ---
        st.header("ðŸ“Š Account Balances")
        balance = get_account_balance(conn)
        balance.rename(columns={'debit_account': 'account'}, inplace=True)
        st.dataframe(balance.style.format({
                    'balance': '{:,.0f}'  # Format with commas and no decimal places
                })
            )

        # Classify accounts
        # balance = balance.reset_index().rename(columns={'Debit-Account': 'account'})
        balance['type'] = balance['account'].apply(
            lambda x: 'Asset' if x.startswith('Asset:')
            else 'Liability' if x.startswith('Liability:')
            else 'Equity' if x.startswith('Equity:')
            else 'Income' if x.startswith('Income:')
            else 'Expense'
        )

        # Display Balance Sheet
        st.header("ðŸ“Š Balance Sheet")

        col1, col2 = st.columns(2)

        with col1:
            st.subheader("Assets")
            df_assets = balance[balance['type'] == 'Asset'][['account', 'balance']]
            st.dataframe(df_assets.style.format({
                    'balance': '{:,.0f}'  # Format with commas and no decimal places
                }))

        with col2:
            st.subheader("Liabilities + Equity")
            liab_eq = balance[balance['type'].isin(['Liability', 'Equity'])][['account', 'balance']]
            st.dataframe(liab_eq.style.format({
                    'balance': '{:,.0f}'  # Format with commas and no decimal places
                }))

        # Optional: Totals
        st.markdown("---")
        asset_total = balance[balance['type'] == 'Asset']['balance'].sum()
        liab_eq_total = balance[(balance['type'].isin(['Liability', 'Equity'])) | (balance['type'] == 'Income') | (balance['type'] == 'Expense')]['balance'].sum()

        st.metric("Total Assets", f"{asset_total:,.0f}")
        st.metric("Total Liabilities + Equity", f"{liab_eq_total:,.0f}")

    with tab3:
        st.header("âœï¸ Edit or Delete Transactions")
        df = get_ledger(conn)
        if not df.empty:
            df['date'] = pd.to_datetime(df['date'])
            df['month'] = df['date'].dt.to_period('M').astype(str)

            # --- Filtering (reuse your filters here if needed) ---
            filtered_df = df.copy()  # Optionally apply filters here


            # selected_row = st.selectbox(
            #     "Select transaction to edit/delete",
            #     df.to_dict(orient='records'),
            #     format_func=lambda row: f"{row['id']} | {row['date']} | {row['description']} | {row['amount']:,}"
            # )

            # tx_id = selected_row['id']
            # cat_edit = st.selectbox("Category", list(dict_category.keys()), index=0)
            # subcat_edit = st.selectbox("Subcategory", dict_category[cat_edit])
            # with st.form(f"edit_form_{tx_id}"):
            #     date_edit = st.date_input("Date", pd.to_datetime(selected_row['date']))
            #     desc_edit = st.text_input("Description", selected_row['description'])
                
            #     debit_edit = st.text_input("Debit Account", selected_row['debit_account'])
            #     credit_edit = st.text_input("Credit Account", selected_row['credit_account'])
            #     amount_edit = st.number_input("Amount", value=selected_row['amount'], format="%.2f")

            #     col1, col2 = st.columns(2)
            #     if col1.form_submit_button("âœ… Update Transaction"):
            #         update_transaction(conn, tx_id, date_edit.isoformat(), desc_edit, cat_edit, subcat_edit, debit_edit, credit_edit, amount_edit)
            #         st.success("Transaction updated!")

            #     if col2.form_submit_button("ðŸ—‘ï¸ Delete Transaction"):
            #         delete_transaction(conn, tx_id)
            #         st.warning("Transaction deleted!")

        # Date range filters
        col1, col2 = st.columns(2)
        with col1:
            start_date = st.date_input("Start Date", df['date'].min().date())
        with col2:
            end_date = st.date_input("End Date", df['date'].max().date())

        # --- Month filter ---
        month_options = sorted(df['month'].unique(), reverse=True)
        selected_months = st.multiselect("Filter by Month", options=month_options, default=month_options)

        # --- Category filter with "Select All" ---
        all_categories = sorted(df['category'].dropna().unique())
        select_all_categories = st.checkbox("Select All Categories", value=True)
        if select_all_categories:
            selected_categories = st.multiselect("Category", options=all_categories, default=all_categories)
        else:
            selected_categories = st.multiselect("Category", options=all_categories)

        # Account filters
        debit_filter = st.multiselect("Debit Account", options=sorted(df['debit_account'].dropna().unique()), default=sorted(df['debit_account'].dropna().unique()))
        credit_filter = st.multiselect("Credit Account", options=sorted(df['credit_account'].dropna().unique()), default=sorted(df['credit_account'].dropna().unique()))

        # Description search
        search_text = st.text_input("Search in Description")

        # --- Apply filters ---
        filtered_df = df[
            (df['date'].dt.date >= start_date) &
            (df['date'].dt.date <= end_date) &
            (df['month'].isin(selected_months)) &
            (df['category'].isin(selected_categories)) &
            (df['debit_account'].isin(debit_filter)) &
            (df['credit_account'].isin(credit_filter)) &
            (df['description'].str.contains(search_text, case=False, na=False))
        ]

        # Format amount and date
        filtered_df['date'] = filtered_df['date'].dt.strftime('%Y-%m-%d')
        # print(filtered_df['date'].dtype)
        filtered_df = filtered_df.sort_values(by='id', ascending=False)

        # --- Show transaction rows with edit/delete per row ---
        for idx, row in filtered_df.iterrows():
            with st.expander(f"{row['id']} | {row['date']} | {row['description']} | {row['amount']:,.0f} VND"):
                with st.form(f"edit_form_{row['id']}"):
                    col1, col2 = st.columns(2)
                    with col1:
                        date_edit = st.date_input("Date", row['date'], key=f"date_{row['id']}")
                        description_edit = st.text_input("Description", row['description'], key=f"desc_{row['id']}")
                        category_edit = st.selectbox("Category", list(dict_category.keys()), \
                                                        index=list(dict_category.keys()).index(row['category']) if row['category'] in list(dict_category.keys()) else 12, \
                                                        key=f"cat_{row['id']}")
                    with col2:
                        subcat_edit = st.selectbox("Sub-category", dict_category[category_edit], index=dict_category[category_edit].index(row['subcategory']) if row['subcategory'] in dict_category[category_edit] else 0, key=f"subcat_{row['id']}")
                        debit_edit = st.text_input("Debit Account", row['debit_account'], key=f"debit_{row['id']}")
                        credit_edit = st.text_input("Credit Account", row['credit_account'], key=f"credit_{row['id']}")
                        amount_edit = st.number_input("Amount", min_value=0.0, value=row['amount'], format="%.0f", key=f"amt_{row['id']}")

                    c1, c2 = st.columns(2)
                    if c1.form_submit_button("âœ… Update"):
                        update_transaction(
                            conn,
                            row['id'],
                            date_edit.isoformat(),
                            description_edit,
                            category_edit,
                            subcat_edit,
                            debit_edit,
                            credit_edit,
                            amount_edit
                        )
                        st.success(f"Transaction {row['id']} updated!")

                    if c2.form_submit_button("ðŸ—‘ Delete"):
                        delete_transaction(conn, row['id'])
                        st.warning(f"Transaction {row['id']} deleted!")

        st.subheader(f"ðŸ“„ Filtered Transactions ({len(filtered_df)} rows)")
        st.dataframe(filtered_df.style.format({
                            'amount': '{:,.0f}'  # Format with commas and no decimal places
                        }), use_container_width=True)

        

    with tab2:
        st.header("ðŸ“Š Finance Insights Dashboard")



        df = pd.read_sql_query("SELECT * FROM transactions", conn)
        df['date'] = pd.to_datetime(df['date'], format="%Y-%m-%d")

        # --- Monthly Expense & Income
        # df['month'] = df['date'].dt.to_period('M')
        df['month'] = df['date'].dt.to_period('M').dt.to_timestamp()
        expense_df = df[df['debit_account'].str.startswith("Expense")]
        income_df = df[df['credit_account'].str.startswith("Income")]

        monthly_expense = expense_df.groupby('month')['amount'].sum().reset_index()
        monthly_expense.rename({"amount": "Expense"}, axis=1, inplace=True)
        monthly_income = income_df.groupby('month')['amount'].sum().reset_index()
        monthly_income.rename({"amount": "Income"}, axis=1, inplace=True)
        monthly_summary = pd.concat([monthly_income[["Income"]], monthly_expense], axis=1).fillna(0).reset_index()

        st.subheader("ðŸ—‚ï¸ Set Monthly Budget per Category")

        budget_df = pd.read_sql_query("SELECT * FROM budgets", conn)
        category_input = st.selectbox("Select Category", sorted(budget_df['category'].unique()))
        budget_input = st.number_input("Monthly Budget (VND)", min_value=0.0, format="%.0f")

        if st.button("ðŸ’¾ Save Budget"):
            conn.execute("REPLACE INTO budgets (category, monthly_limit) VALUES (?, ?)", (category_input, budget_input))
            conn.commit()
            st.success(f"Budget saved for {category_input}")


        st.subheader("ðŸ’° Monthly Income vs Expense")
        # st.line_chart(pd.DataFrame({
        #     "Income": monthly_income,
        #     "Expense": monthly_expense
        # }))
        # Melt for Plotly
        plot_df = monthly_summary.melt(id_vars='month', value_vars=['Income', 'Expense'], var_name='Type', value_name='Amount')

        # Plot
        fig = px.line(
            plot_df,
            x='month',
            y='Amount',
            color='Type',
            title='ðŸ’° Monthly Income vs Expense',
            color_discrete_map={'Income': 'green', 'Expense': 'red'},
            markers=True
        )

        fig.update_traces(line=dict(width=2))
        fig.update_layout(xaxis_title="Month", yaxis_title="Amount", legend_title="Type")

        st.plotly_chart(fig, use_container_width=True)

        st.header("ðŸ“Š Monthly Expenses by Category")

        # Load from database
        df = pd.read_sql_query("SELECT * FROM transactions", conn)
        df['date'] = pd.to_datetime(df['date'], format="%Y-%m-%d")
        df['month'] = df['date'].dt.to_period('M').astype(str)

        # Filter only expense transactions
        expense_df = df[df['debit_account'].str.startswith("Expense")]

        # Pivot: category vs. month
        # Get latest 3 months
        latest_months = sorted(expense_df['month'].unique())[-3:]
        recent_expenses = expense_df[expense_df['month'].isin(latest_months)]


        # Pivot: category vs. month
        pivot_df = recent_expenses.pivot_table(
            index='category',
            columns='month',
            values='amount',
            aggfunc='sum',
            fill_value=0
        )

        # Format: add thousands separator with comma
        # def fmt(x): return f"{x:,.0f}"

        fig, ax = plt.subplots(figsize=(12, 6))
        sns.heatmap(
            pivot_df,
            annot=True,
            fmt=",.0f",
            cmap="YlOrRd",
            linewidths=0.5,
            cbar=True,
            ax=ax
        )
        ax.set_title("Monthly Expenses by Category")
        ax.set_xlabel("Month")
        ax.set_ylabel("Category")
        plt.xticks(rotation=45)
        plt.tight_layout()

        # Show in Streamlit
        st.pyplot(fig)

        st.header("ðŸ“Š Net Monthly Balance by Account (All Types)")



        three_ms_df = df[df['month'].isin(latest_months)]
        # Debit: positive
        debit_bal = three_ms_df.groupby(['debit_account', 'month'])['amount'].sum().unstack(fill_value=0)

        # Credit: negative
        credit_bal = three_ms_df.groupby(['credit_account', 'month'])['amount'].sum().unstack(fill_value=0) * -1

        # Combine: Net balance per account per month
        all_balances = debit_bal.add(credit_bal, fill_value=0)

        # Clean index
        all_balances.index.name = 'account'

        fig, ax = plt.subplots(figsize=(12, 6))
        sns.heatmap(
            all_balances,
            annot=all_balances.applymap(lambda x: f"{x:,.0f}"),
            fmt='',
            cmap="RdYlGn",
            linewidths=0.5,
            cbar=True,
            ax=ax
        )
        ax.set_title("Net Balance by Account and Month")
        ax.set_xlabel("Month")
        ax.set_ylabel("Account")
        plt.xticks(rotation=45)
        plt.tight_layout()

        st.pyplot(fig)


        st.header("ðŸ“Š Investment Overview")

        # Filter only investment-related transactions
        inv_df = df[df['category'] == 'investment']

        # Tag direction: outflow (-), inflow (+)
        def tag_investment_direction(row):
            if row['debit_account'].startswith('Cash'):
                return 'Inflow'
            elif row['credit_account'].startswith('Cash'):
                return 'Outflow'
            else:
                return 'Neutral'

        inv_df['direction'] = inv_df.apply(tag_investment_direction, axis=1)
        inv_df['signed_amount'] = inv_df.apply(
            lambda row: row['amount'] if row['direction'] == 'Inflow' else (
                -row['amount'] if row['direction'] == 'Outflow' else 0),
            axis=1
        )

        # Group by month
        monthly_inv = inv_df.groupby(['month', 'direction'])['signed_amount'].sum().reset_index()

        # Pivot for visualization
        monthly_summary = monthly_inv.pivot(index='month', columns='direction', values='signed_amount').fillna(0)
        monthly_summary['Net'] = monthly_summary.get('Inflow', 0) + monthly_summary.get('Outflow', 0)
        monthly_summary = monthly_summary.reset_index()

        # Plot
        fig = px.bar(
            monthly_summary,
            x='month',
            y=['Inflow', 'Outflow', 'Net'],
            title='ðŸ“ˆ Investment Efficiency Over Time',
            labels={'value': 'Amount', 'month': 'Month'},
            barmode='group',
            color_discrete_map={
                'Inflow': 'green',
                'Outflow': 'red',
                'Net': 'blue'
            }
        )

        fig.update_traces(texttemplate='%{y:,.0f}', textposition='outside')
        fig.update_layout(xaxis_tickangle=-45, yaxis_title="VND", legend_title="")

        # Streamlit display
        st.plotly_chart(fig, use_container_width=True)

        st.header("ðŸ“Š Pivot Table: Monthly Expenses by Category and Sub-category")

        # Step 1: Prepare base data
        df = pd.read_sql_query("SELECT * FROM transactions", conn)
        df['date'] = pd.to_datetime(df['date'])
        df['month'] = df['date'].dt.to_period('M').astype(str)

        # Step 2: Filter only Expense transactions
        expense_df = df[df['debit_account'].str.startswith("Expense")]

        # Step 3: Pivot table (multi-index)
        pivot = (
            expense_df
            .groupby(['category', 'subcategory', 'month'])['amount']
            .sum()
            .unstack(fill_value=0)
        )

        # Step 4: Format table with commas + apply color
        styled_table = pivot.style.format('{:,.0f}').background_gradient(
            cmap='YlOrRd',
            axis=None
        )


        st.header("ðŸ“Š Budget vs. Actual Chart")
        # Step 5: Display in Streamlit
        st.dataframe(styled_table, use_container_width=True)


        # Get budget data
        budget_df = pd.read_sql_query("SELECT * FROM budgets", conn)

        # Aggregate actual monthly expenses
        monthly_actual = expense_df.groupby(['month', 'category'])['amount'].sum().reset_index()

        # Merge with budget
        merged_budget = pd.merge(monthly_actual, budget_df, on='category', how='left')
        merged_budget['variance'] = merged_budget['monthly_limit'] - merged_budget['amount']

        # Plot budget vs. actual for latest month
        latest_month = merged_budget['month'].max()
        latest_data = merged_budget[merged_budget['month'] == latest_month]

        fig = px.bar(
            latest_data,
            x='category',
            y=['monthly_limit', 'amount'],
            barmode='group',
            title=f"ðŸ“Š Budget vs Actual Expenses ({latest_month})",
            labels={'value': 'VND'},
            color_discrete_map={
                'monthly_limit': 'gray',
                'amount': 'red'
            }
        )
        fig.update_traces(texttemplate='%{y:,.0f}', textposition='outside')
        fig.update_layout(xaxis_tickangle=-45, yaxis_title="VND", legend_title="")

        st.plotly_chart(fig, use_container_width=True)


