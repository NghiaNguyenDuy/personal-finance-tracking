# # Personal Finance Manager using Streamlit, SQLAlchemy, and PDF parsing

# import streamlit as st
# from sqlalchemy import create_engine, Column, Integer, String, Float, Date, Text
# from sqlalchemy.ext.declarative import declarative_base
# from sqlalchemy.orm import sessionmaker
# import pandas as pd
# from datetime import datetime
# # import fitz  # PyMuPDF
# import pdfplumber
# import io



# # --- Database Setup ---
# Base = declarative_base()

# class Spending(Base):
#     __tablename__ = 'spendings'
#     id = Column(Integer, primary_key=True)
#     date = Column(Date)
#     amount = Column(Float)
#     category = Column(String(50))
#     description = Column(Text)

# engine = create_engine('sqlite:///finance.db')
# Base.metadata.create_all(engine)
# Session = sessionmaker(bind=engine)
# session = Session()

# # --- Helper Functions ---
# def add_spending(date, amount, category, description):
#     spending = Spending(date=date, amount=amount, category=category, description=description)
#     session.add(spending)
#     session.commit()

# def get_all_spendings():
#     return session.query(Spending).all()

# def parse_pdf(file):
#     lines = []
#     with pdfplumber.open(file) as pdf:
#         for page in pdf.pages:
#             text = page.extract_text()
#             if text:
#                 lines += text.split("\n")
#     return lines

# def auto_categorize(desc):
#     if "coffee" in desc.lower():
#         return "Food & Beverage"
#     elif "uber" in desc.lower():
#         return "Transport"
#     elif "shopee" in desc.lower():
#         return "Shopping"
#     else:
#         return "Others"

# if __name__ == "__main__":
#     # --- Streamlit App ---
#     st.title("💼 Personal Finance Management")

#     st.header("Add Daily Spending")
#     date = st.date_input("Date", datetime.today())
#     amount = st.number_input("Amount", min_value=0.0, step=0.01)
#     description = st.text_input("Description")
#     category = st.text_input("Category (optional)")

#     if st.button("Add Spending"):
#         final_cat = category if category else auto_categorize(description)
#         add_spending(date, amount, final_cat, description)
#         st.success("Spending added successfully!")

#     st.header("Upload PDF Statement")
#     pdf_file = st.file_uploader("Upload your credit card statement (PDF)", type="pdf")
#     if pdf_file:
#         lines = parse_pdf(pdf_file)
#         for line in lines:
#             parts = line.strip().split()
#             try:
#                 # Simple rule: date amount description
#                 if len(parts) >= 3 and parts[0].count("/") == 2:
#                     parsed_date = datetime.strptime(parts[0], "%d/%m/%Y").date()
#                     parsed_amount = float(parts[1].replace(',', ''))
#                     parsed_desc = " ".join(parts[2:])
#                     parsed_cat = auto_categorize(parsed_desc)
#                     add_spending(parsed_date, parsed_amount, parsed_cat, parsed_desc)
#             except:
#                 continue
#         st.success("PDF processed successfully!")

#     st.header("Spending Summary")
#     spendings = get_all_spendings()
#     df = pd.DataFrame([(s.date, s.amount, s.category, s.description) for s in spendings],
#                     columns=["Date", "Amount", "Category", "Description"])
#     st.dataframe(df.sort_values(by="Date", ascending=False))

#     st.subheader("Category Summary")
#     if not df.empty:
#         summary = df.groupby("Category")["Amount"].sum().sort_values(ascending=False)
#         st.bar_chart(summary)




import sqlite3
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
        debit_account TEXT,
        credit_account TEXT,
        amount REAL
    )
    ''')
    conn.commit()
    return conn

def record_transaction(conn, date, desc, category, debit, credit, amount):
    conn.execute('''
        INSERT INTO transactions (date, description, category, debit_account, credit_account, amount)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (date, desc, category, debit, credit, amount))
    conn.commit()

def delete_transaction(conn, tx_id):
    conn.execute("DELETE FROM transactions WHERE id = ?", (tx_id,))
    conn.commit()

def update_transaction(conn, tx_id, date, desc, category, debit, credit, amount):
    conn.execute("""
        UPDATE transactions
        SET date = ?, description = ?, category = ?, debit_account = ?, credit_account = ?, amount = ?
        WHERE id = ?
    """, (date, desc, category, debit, credit, amount, tx_id))
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


# def is_valid_mmddyyyy(date_str):
#     try:
#         return datetime.datetime.strptime(date_str, "%m/%d/%Y")
#     except ValueError:
#         return None


if __name__ == "__main__":
    tab1, tab2, tab3 = st.tabs(["🧾 Transactions", "📊 Insights", "✏️ Edit Transactions"])
    with tab1:
        conn = init_db()

        st.title("📘 Personal Finance Manager with T-Accounts")

        # --- Add Transaction ---
        st.header("➕ Add New Transaction")
        with st.form("entry_form"):
            t_date = st.date_input("Date", date.today())
            t_date = t_date.strftime("%Y-%m-%d")
            # t_date = datetime.date(t_date.year, t_date.month, t_date.day)
            # t_date = st.date_input("Date (MM/dd/YYYY)", placeholder="e.g. 09/20/2025")
            description = st.text_input("Description")
            category = st.selectbox("Category", [
                "necessity",
                "food&beverage",
                "health",
                "lend",
                "friend",
                "outsource",
                "salary",
                "saving",
                "credit_payment",
                "investment",
                "charity",
                "parents",
                "hobby",
                "learning",
                "tech",
                "love",
                "shopping",
                "transport",
                "travel",
                "others"
            ])
            debit = st.selectbox("Debit Account", ["Cash", "Expense", "Asset:Receivable", "Asset:Savings", "Liability:Payable", "Equity:General"])
            credit = st.selectbox("Credit Account", ["Cash", "Income:Salary", "Liability:Payable", "Equity:Opening Balance", "Equity:General", "Asset:Savings", "Asset:Receivable"])
            amount = st.number_input("Amount", min_value=0.0, format="%.0f")
            submitted = st.form_submit_button("Add Transaction")

            st.text(t_date)
            if submitted:
                record_transaction(conn, t_date, description, category, debit, credit, amount)
                st.success("Transaction recorded!")

        



        # Reload updated data
        # df = pd.read_sql_query("SELECT * FROM transactions ORDER BY date DESC", conn)
        # st.dataframe(df.style.format({
        #                     'amount': '{:,.0f}'  # Format with commas and no decimal places
        #                 }))
        
        # --- View Ledger ---
        st.header("📑 Ledger")
        st.dataframe(get_ledger(conn)[:20].style.format({
                    'amount': '{:,.0f}'  # Format with commas and no decimal places
                }))
        
        # --- View Balances ---
        st.header("📊 Account Balances")
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
        st.header("📊 Balance Sheet")

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
        st.header("✏️ Edit or Delete Transactions")
        df = get_ledger(conn)
        if not df.empty:
            df['date'] = pd.to_datetime(df['date'], format="%Y-%m-%d")
            df['month'] = df['date'].dt.to_period('M').astype(str)
            selected_row = st.selectbox(
                "Select transaction to edit/delete",
                df.to_dict(orient='records'),
                format_func=lambda row: f"{row['id']} | {row['date']} | {row['description']} | {row['amount']:,}"
            )

            tx_id = selected_row['id']
            with st.form(f"edit_form_{tx_id}"):
                date_edit = st.date_input("Date", pd.to_datetime(selected_row['date']))
                desc_edit = st.text_input("Description", selected_row['description'])
                cat_edit = st.selectbox("Category", [
                "necessity",
                "food&beverage",
                "health",
                "lend",
                "friend",
                "outsource",
                "salary",
                "saving",
                "credit_payment",
                "investment",
                "charity",
                "parents",
                "hobby",
                "learning",
                "tech",
                "love",
                "shopping",
                "transport",
                "others"
            ], index=0 if not selected_row['category'] else [
                "necessity",
                "food&beverage",
                "health",
                "lend",
                "friend",
                "outsource",
                "salary",
                "saving",
                "credit_payment",
                "investment",
                "charity",
                "parents",
                "hobby",
                "learning",
                "tech",
                "love",
                "shopping",
                "transport",
                "others"
            ].index(selected_row['category']))
                debit_edit = st.text_input("Debit Account", selected_row['debit_account'])
                credit_edit = st.text_input("Credit Account", selected_row['credit_account'])
                amount_edit = st.number_input("Amount", value=selected_row['amount'], format="%.2f")

                col1, col2 = st.columns(2)
                if col1.form_submit_button("✅ Update Transaction"):
                    update_transaction(conn, tx_id, date_edit.isoformat(), desc_edit, cat_edit, debit_edit, credit_edit, amount_edit)
                    st.success("Transaction updated!")

                if col2.form_submit_button("🗑️ Delete Transaction"):
                    delete_transaction(conn, tx_id)
                    st.warning("Transaction deleted!")

        # Date range filters
        col1, col2 = st.columns(2)
        with col1:
            start_date = st.date_input("Start Date", df['date'].min().date())
        with col2:
            end_date = st.date_input("End Date", df['date'].max().date())

        # --- Month filter ---
        month_options = sorted(df['month'].unique())
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

        st.subheader(f"📄 Filtered Transactions ({len(filtered_df)} rows)")
        st.dataframe(filtered_df.style.format({
                            'amount': '{:,.0f}'  # Format with commas and no decimal places
                        }), use_container_width=True)

        

    with tab2:
        st.header("📊 Finance Insights Dashboard")

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
        st.subheader("💰 Monthly Income vs Expense")
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
            title='💰 Monthly Income vs Expense',
            color_discrete_map={'Income': 'green', 'Expense': 'red'},
            markers=True
        )

        fig.update_traces(line=dict(width=2))
        fig.update_layout(xaxis_title="Month", yaxis_title="Amount", legend_title="Type")

        st.plotly_chart(fig, use_container_width=True)

        st.header("📊 Monthly Expenses by Category")

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

        st.header("📊 Net Monthly Balance by Account (All Types)")



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


        st.header("📊 Investment Overview")

        # Prepare data
        # df['date'] = pd.to_datetime(df['date'])
        # df['month'] = df['date'].dt.to_period('M').astype(str)

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
            title='📈 Investment Efficiency Over Time',
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

        # # --- Expense Pie Chart
        # st.subheader("🧁 Expense Distribution")
        # st.pyplot(top_categories.plot.pie(autopct='%1.1f%%', figsize=(5, 5)).get_figure())

        # # --- Account Balance
        # debit = df.groupby('debit_account')['amount'].sum()
        # credit = df.groupby('credit_account')['amount'].sum()
        # balances = (debit - credit).fillna(0)

        # st.subheader("🏦 Account Balances")
        # st.bar_chart(balances)
