# ============================================================
# HISAABAI - AI-Powered Expense Tracker
# app.py - Single file, complete implementation
# ============================================================

import streamlit as st
import mysql.connector
import pandas as pd
import datetime
import hashlib
import os
import pickle
import plotly.express as px
import plotly.graph_objects as go
import numpy as np
from sklearn.linear_model import LinearRegression
from sklearn.metrics import mean_absolute_error
from sklearn.preprocessing import LabelEncoder
import certifi


# ============================================================
# PAGE CONFIG (must be the first Streamlit command in the script)
# ============================================================
st.set_page_config(
    page_title="HisaabAI",
    page_icon="💸",
    layout="wide"
)


# ============================================================
# SECTION 1: DATABASE CONNECTION
# ============================================================

def get_db_connection():
    """
    Opens a connection to TiDB Cloud (MySQL-compatible).
    Reads credentials from .streamlit/secrets.toml.
    Returns the connection object or None if it fails.
    """
    try:
        conn = mysql.connector.connect(
            host                = st.secrets["mysql"]["host"],
            port                = int(st.secrets["mysql"]["port"]),
            user                = st.secrets["mysql"]["user"],
            password            = st.secrets["mysql"]["password"],
            database            = st.secrets["mysql"]["database"],
            ssl_ca              = certifi.where(),
            connection_timeout  = 10
        )
        return conn
    except mysql.connector.Error as e:
        st.error(f"⚠️ Database connection failed: {e}")
        return None


# ============================================================
# SECTION 2: AUTHENTICATION HELPERS
# ============================================================

def hash_password(password: str) -> str:
    """SHA-256 hash of a password string."""
    return hashlib.sha256(password.encode()).hexdigest()


def verify_login(username: str, password: str):
    """
    Checks username + password against the users table.
    Returns (user_id, username) on success, or None on failure.
    """
    conn = get_db_connection()
    if not conn:
        return None
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT user_id, username FROM users WHERE username=%s AND pass_hash=%s",
            (username, hash_password(password))
        )
        result = cursor.fetchone()
        return result  # (user_id, username) or None
    except mysql.connector.Error as e:
        st.error(f"Login error: {e}")
        return None
    finally:
        conn.close()


def register_user(username: str, password: str) -> bool:
    """
    Inserts a new user into the users table.
    Returns True on success, False if username already exists.
    """
    conn = get_db_connection()
    if not conn:
        return False
    try:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO users (username, pass_hash) VALUES (%s, %s)",
            (username, hash_password(password))
        )
        conn.commit()
        return True
    except mysql.connector.IntegrityError:
        # UNIQUE constraint on username fired — username already taken
        return False
    except mysql.connector.Error as e:
        st.error(f"Registration error: {e}")
        return False
    finally:
        conn.close()


# ============================================================
# SECTION 3: AUDIT LOG
# ============================================================

def log_audit(user_id, action, expense_id,
              old_amount=None, new_amount=None,
              old_description=None, new_description=None,
              note=None):
    """
    Writes a record to audit_log every time a CRUD operation happens.
    This gives a complete history of every change ever made,
    including amount and description changes.
    """
    conn = get_db_connection()
    if not conn:
        return
    try:
        cursor = conn.cursor()
        cursor.execute(
            """INSERT INTO audit_log
               (user_id, action, expense_id,
                old_amount, new_amount,
                old_description, new_description,
                note)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
            (user_id, action, expense_id,
             old_amount, new_amount,
             old_description, new_description,
             note)
        )
        conn.commit()
    except mysql.connector.Error as e:
        # Audit failures are non-critical — just print, don't crash
        print(f"Audit log failed: {e}")
    finally:
        conn.close()


# ============================================================
# SECTION 4: BUDGET ALERT CHECKER
# ============================================================

def check_and_create_alert(user_id, category_id, exp_date):
    """
    After every INSERT, checks if the user has exceeded their
    category budget for the month. If yes, writes to budget_alerts.
    This replicates trigger logic at the application layer
    (TiDB Serverless free tier has limited trigger support).
    """
    conn = get_db_connection()
    if not conn:
        return
    try:
        cursor = conn.cursor()

        # Get total spent in this category this month
        cursor.execute(
            """SELECT SUM(amount)
               FROM expenses
               WHERE user_id = %s
                 AND category_id = %s
                 AND MONTH(exp_date) = MONTH(%s)
                 AND YEAR(exp_date)  = YEAR(%s)""",
            (user_id, category_id, exp_date, exp_date)
        )
        total = cursor.fetchone()[0] or 0

        # Get budget limit for this category
        cursor.execute(
            "SELECT budget_limit, category_name FROM categories WHERE category_id = %s",
            (category_id,)
        )
        row = cursor.fetchone()
        if not row:
            return
        budget_limit, cat_name = row

        # If over budget, insert an alert (only if one doesn't exist for this month)
        if total > budget_limit:
            cursor.execute(
                """SELECT COUNT(*) FROM budget_alerts
                   WHERE user_id = %s
                     AND category_id = %s
                     AND MONTH(created_at) = MONTH(%s)
                     AND YEAR(created_at)  = YEAR(%s)""",
                (user_id, category_id, exp_date, exp_date)
            )
            already_alerted = cursor.fetchone()[0]

            if not already_alerted:
                msg = (f"Budget exceeded for {cat_name}! "
                       f"Spent ₹{total:,.2f} of ₹{budget_limit:,.2f} limit.")
                cursor.execute(
                    """INSERT INTO budget_alerts
                       (user_id, category_id, alert_msg)
                       VALUES (%s, %s, %s)""",
                    (user_id, category_id, msg)
                )
                conn.commit()
    except mysql.connector.Error as e:
        print(f"Alert check failed: {e}")
    finally:
        conn.close()


def fetch_unread_alerts(user_id):
    """Fetches all unread budget alerts for the user."""
    conn = get_db_connection()
    if not conn:
        return pd.DataFrame()
    try:
        query = """
            SELECT a.alert_id, a.alert_msg, a.created_at, c.category_name
            FROM budget_alerts a
            JOIN categories c ON a.category_id = c.category_id
            WHERE a.user_id = %s AND a.is_read = FALSE
            ORDER BY a.created_at DESC
        """
        return pd.read_sql(query, conn, params=(user_id,))
    finally:
        conn.close()


def mark_alerts_read(user_id):
    """Marks all alerts as read after displaying them."""
    conn = get_db_connection()
    if not conn:
        return
    try:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE budget_alerts SET is_read = TRUE WHERE user_id = %s",
            (user_id,)
        )
        conn.commit()
    finally:
        conn.close()


# ============================================================
# SECTION 5: CRUD OPERATIONS
# ============================================================

def fetch_categories():
    """Returns all categories as a list of (id, name, budget_limit)."""
    conn = get_db_connection()
    if not conn:
        return []
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT category_id, category_name, budget_limit FROM categories"
        )
        return cursor.fetchall()
    finally:
        conn.close()


def add_category(category_name, budget_limit):
    """Inserts a new category into the categories table."""
    conn = get_db_connection()
    if not conn:
        return False
    try:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO categories (category_name, budget_limit) VALUES (%s, %s)",
            (category_name, budget_limit)
        )
        conn.commit()
        return True
    except mysql.connector.Error as e:
        st.error(f"Failed to add category: {e}")
        return False
    finally:
        conn.close()


def update_category_budget(category_id, new_limit):
    """Updates the budget limit for an existing category."""
    conn = get_db_connection()
    if not conn:
        return False
    try:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE categories SET budget_limit = %s WHERE category_id = %s",
            (new_limit, category_id)
        )
        conn.commit()
        return True
    except mysql.connector.Error as e:
        st.error(f"Failed to update budget limit: {e}")
        return False
    finally:
        conn.close()


def add_expense(user_id, category_id, exp_date, amount, description):
    """
    INSERT operation.
    After inserting, also logs to audit and checks budget.
    """
    conn = get_db_connection()
    if not conn:
        return False
    try:
        cursor = conn.cursor()
        cursor.execute(
            """INSERT INTO expenses
               (user_id, category_id, exp_date, amount, description)
               VALUES (%s, %s, %s, %s, %s)""",
            (user_id, category_id, exp_date, amount, description)
        )
        conn.commit()
        new_id = cursor.lastrowid  # Get the auto-generated ID

        # Audit log — record this INSERT (with description)
        log_audit(user_id, 'INSERT', new_id,
                  new_amount=amount,
                  new_description=description,
                  note=f"Category ID {category_id}")

        # Budget alert check
        check_and_create_alert(user_id, category_id, exp_date)

        return True
    except mysql.connector.Error as e:
        st.error(f"Failed to add expense: {e}")
        return False
    finally:
        conn.close()


def fetch_expenses(user_id):
    """
    SELECT with JOIN — fetches all expenses for a user,
    joining with categories to get human-readable names and limits.
    Queries the indexed columns for performance.
    """
    conn = get_db_connection()
    if not conn:
        return pd.DataFrame()
    try:
        query = """
            SELECT
                e.id,
                e.exp_date,
                c.category_name,
                e.amount,
                e.description,
                c.budget_limit,
                e.created_at
            FROM expenses e
            JOIN categories c ON e.category_id = c.category_id
            WHERE e.user_id = %s
            ORDER BY e.exp_date DESC
        """
        df = pd.read_sql(query, conn, params=(user_id,))
        return df
    finally:
        conn.close()


def fetch_expense_by_id(expense_id, user_id):
    """Fetches a single expense row for the edit form pre-fill."""
    conn = get_db_connection()
    if not conn:
        return None
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            """SELECT e.*, c.category_name
               FROM expenses e
               JOIN categories c ON e.category_id = c.category_id
               WHERE e.id = %s AND e.user_id = %s""",
            (expense_id, user_id)
        )
        return cursor.fetchone()
    finally:
        conn.close()


def update_expense(expense_id, user_id, category_id,
                   exp_date, new_amount, new_description):
    """
    UPDATE operation.
    Captures old amount AND old description before updating
    so the audit log records both kinds of changes.
    """
    conn = get_db_connection()
    if not conn:
        return False
    try:
        # Get old amount AND old description for audit log
        cursor = conn.cursor()
        cursor.execute(
            "SELECT amount, description FROM expenses WHERE id = %s AND user_id = %s",
            (expense_id, user_id)
        )
        row = cursor.fetchone()
        old_amount      = row[0] if row else None
        old_description = row[1] if row else None

        # Perform the update
        cursor.execute(
            """UPDATE expenses
               SET category_id  = %s,
                   exp_date      = %s,
                   amount        = %s,
                   description   = %s
               WHERE id = %s AND user_id = %s""",
            (category_id, exp_date, new_amount,
             new_description, expense_id, user_id)
        )
        conn.commit()

        # Audit log — record old vs new amount AND description
        log_audit(user_id, 'UPDATE', expense_id,
                  old_amount=old_amount,
                  new_amount=new_amount,
                  old_description=old_description,
                  new_description=new_description)
        return True
    except mysql.connector.Error as e:
        st.error(f"Update failed: {e}")
        return False
    finally:
        conn.close()


def delete_expense(expense_id, user_id):
    """
    DELETE operation.
    Records the deleted amount AND description in audit log
    before removing the row.
    """
    conn = get_db_connection()
    if not conn:
        return False
    try:
        cursor = conn.cursor()

        # Get amount AND description before deleting for audit
        cursor.execute(
            "SELECT amount, description FROM expenses WHERE id = %s AND user_id = %s",
            (expense_id, user_id)
        )
        row = cursor.fetchone()
        old_amount      = row[0] if row else None
        old_description = row[1] if row else None

        cursor.execute(
            "DELETE FROM expenses WHERE id = %s AND user_id = %s",
            (expense_id, user_id)
        )
        conn.commit()

        log_audit(user_id, 'DELETE', expense_id,
                  old_amount=old_amount,
                  old_description=old_description,
                  note="Record deleted by user")
        return True
    except mysql.connector.Error as e:
        st.error(f"Delete failed: {e}")
        return False
    finally:
        conn.close()


def fetch_monthly_summary(user_id):
    """
    Queries the monthly_summary VIEW.
    This is a named saved query stored in the DB — demonstrates DDL.
    """
    conn = get_db_connection()
    if not conn:
        return pd.DataFrame()
    try:
        return pd.read_sql(
            "SELECT * FROM monthly_summary WHERE user_id = %s ORDER BY month",
            conn, params=(user_id,)
        )
    finally:
        conn.close()


def fetch_audit_log(user_id):
    """Retrieves full audit history for the user."""
    conn = get_db_connection()
    if not conn:
        return pd.DataFrame()
    try:
        query = """
            SELECT
                log_id,
                action,
                expense_id,
                old_amount,
                new_amount,
                old_description,
                new_description,
                note,
                changed_at
            FROM audit_log
            WHERE user_id = %s
            ORDER BY changed_at DESC
            LIMIT 50
        """
        return pd.read_sql(query, conn, params=(user_id,))
    finally:
        conn.close()


# ============================================================
# SECTION 6: ANALYTICS HELPERS
# ============================================================

def detect_recurring_expenses(df):
    """
    Analyzes spending patterns to flag recurring vs occasional expenses.
    A category is 'recurring' if it appears in 60%+ of the months
    in your data.
    """
    if df.empty:
        return pd.DataFrame()

    df = df.copy()
    df['exp_date'] = pd.to_datetime(df['exp_date'])
    df['month']    = df['exp_date'].dt.to_period('M')

    total_months = df['month'].nunique()
    if total_months == 0:
        return pd.DataFrame()

    summary = df.groupby('category_name').agg(
        months_active = ('month', 'nunique'),
        total_spent   = ('amount', 'sum'),
        avg_per_month = ('amount', 'mean'),
        num_entries   = ('amount', 'count')
    ).reset_index()

    summary['is_recurring'] = (
        summary['months_active'] >= max(1, total_months * 0.6)
    )
    summary['type'] = summary['is_recurring'].map(
        {True: '🔁 Recurring', False: '📌 Occasional'}
    )
    summary['total_spent']   = summary['total_spent'].round(2)
    summary['avg_per_month'] = summary['avg_per_month'].round(2)

    return summary.sort_values('months_active', ascending=False)


def get_budget_vs_actual(df, categories_data):
    """
    Compares current month spending against budget limits per category.
    Uses the budget_limit from the categories table (via the JOIN in fetch_expenses).
    """
    if df.empty:
        return pd.DataFrame()

    df = df.copy()
    df['exp_date'] = pd.to_datetime(df['exp_date'])

    # Filter to current month only
    now = datetime.date.today()
    current = df[
        (df['exp_date'].dt.month == now.month) &
        (df['exp_date'].dt.year  == now.year)
    ]

    if current.empty:
        return pd.DataFrame()

    spent = current.groupby('category_name').agg(
        spent        = ('amount', 'sum'),
        budget_limit = ('budget_limit', 'first')
    ).reset_index()

    spent['remaining'] = spent['budget_limit'] - spent['spent']
    spent['pct_used']  = (spent['spent'] / spent['budget_limit'] * 100).round(1)
    spent['status']    = spent['pct_used'].apply(
        lambda p: '🔴 Over Budget' if p > 100
             else '🟡 Near Limit'  if p > 80
             else '🟢 On Track'
    )
    return spent


# ============================================================
# SECTION 7: MACHINE LEARNING
# ============================================================

def train_model(df, model_type="Linear Regression"):
    """
    Trains a predictive model on monthly spending totals.
    Supports:
        - Linear Regression (OLS trend line)
        - Moving Average (3-month rolling mean)
        - Hybrid Ensemble (50/50 combination)
    """
    if df.empty or len(df) < 3:
        return None, "Need at least 3 expense entries.", None

    df = df.copy()
    df['exp_date'] = pd.to_datetime(df['exp_date'])

    # Aggregate to monthly totals
    monthly = (
        df.groupby(df['exp_date'].dt.to_period('M'))
        .agg(total=('amount', 'sum'))
        .reset_index()
    )
    monthly.columns = ['month', 'total']

    if len(monthly) < 2:
        return None, "Need data across at least 2 different months.", None

    # Convert month period to integer index for the model
    monthly['month_num'] = range(len(monthly))

    X = monthly[['month_num']].values  # 2D array — required by sklearn
    y = monthly['total'].values        # 1D array

    # Train linear regression
    lr_model = LinearRegression()
    lr_model.fit(X, y)
    lr_pred = lr_model.predict([[len(monthly)]])[0]

    # Calculate 3-Month Moving Average
    k = min(3, len(monthly))
    ma_pred = monthly['total'].iloc[-k:].mean()

    # Calculate prediction and y_pred based on chosen model
    if model_type == "Linear Regression":
        prediction = lr_pred
        y_pred = lr_model.predict(X)
    elif model_type == "Moving Average (3-Month)":
        prediction = ma_pred
        y_pred = monthly['total'].rolling(window=k, min_periods=1).mean().values
    else:  # Hybrid Ensemble
        prediction = 0.5 * lr_pred + 0.5 * ma_pred
        y_pred_lr = lr_model.predict(X)
        y_pred_ma = monthly['total'].rolling(window=k, min_periods=1).mean().values
        y_pred = 0.5 * y_pred_lr + 0.5 * y_pred_ma

    # Mean Absolute Error — how accurate the model is on training data
    mae = mean_absolute_error(y, y_pred)

    return round(prediction, 2), round(mae, 2), monthly


def get_regression_line(monthly_df, model_coef, model_intercept):
    """
    Returns x,y arrays for plotting the regression trend line
    alongside the actual monthly data points.
    """
    x_vals = monthly_df['month_num'].values
    y_vals = model_intercept + model_coef * x_vals
    return x_vals, y_vals


# ============================================================
# SECTION 8: UI COMPONENTS
# ============================================================

def inject_premium_styles():
    """Injects high-end CSS for a custom modern look and feel."""
    st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700&display=swap');
        
        /* Apply Outfit Font globally */
        html, body, [data-testid="stAppViewContainer"], .main {
            font-family: 'Outfit', sans-serif !important;
        }
        
        /* Metric container styling - Glassmorphism card */
        div[data-testid="metric-container"] {
            background: rgba(255, 255, 255, 0.45) !important;
            backdrop-filter: blur(8px) !important;
            -webkit-backdrop-filter: blur(8px) !important;
            border: 1px solid rgba(255, 255, 255, 0.25) !important;
            border-radius: 16px !important;
            box-shadow: 0 8px 32px 0 rgba(31, 38, 135, 0.05) !important;
            padding: 20px !important;
            transition: all 0.3s cubic-bezier(0.25, 0.8, 0.25, 1) !important;
        }
        
        div[data-testid="metric-container"]:hover {
            transform: translateY(-4px) !important;
            box-shadow: 0 12px 40px 0 rgba(31, 38, 135, 0.08) !important;
            border: 1px solid rgba(79, 70, 229, 0.3) !important;
        }

        /* Metric text formatting */
        div[data-testid="stMetricValue"] {
            color: #4f46e5 !important;
            font-weight: 700 !important;
            font-size: 2.1rem !important;
        }

        div[data-testid="stMetricLabel"] {
            font-weight: 500 !important;
            text-transform: uppercase !important;
            letter-spacing: 0.05em !important;
            opacity: 0.8 !important;
        }
        
        /* Sidebar styling */
        section[data-testid="stSidebar"] {
            background-color: #eef2ff !important;
            border-right: 1px solid #c7d2fe !important;
        }

        /* Sidebar text colors */
        section[data-testid="stSidebar"] h1,
        section[data-testid="stSidebar"] h2,
        section[data-testid="stSidebar"] h3,
        section[data-testid="stSidebar"] p,
        section[data-testid="stSidebar"] label,
        section[data-testid="stSidebar"] span {
            color: #1e293b !important;
        }

        section[data-testid="stSidebar"] label {
            font-weight: 600 !important;
            font-size: 0.92rem !important;
        }

        /* Sidebar input fields - make them visible */
        section[data-testid="stSidebar"] input,
        section[data-testid="stSidebar"] textarea {
            background-color: #ffffff !important;
            color: #0f172a !important;
            border: 1px solid #94a3b8 !important;
            border-radius: 8px !important;
        }

        /* Sidebar selectbox & date input */
        section[data-testid="stSidebar"] [data-baseweb="select"] > div,
        section[data-testid="stSidebar"] [data-baseweb="input"] > div {
            background-color: #ffffff !important;
            border: 1px solid #94a3b8 !important;
            border-radius: 8px !important;
        }

        section[data-testid="stSidebar"] [data-baseweb="select"] *,
        section[data-testid="stSidebar"] [data-baseweb="input"] * {
            color: #0f172a !important;
        }

        /* Sidebar headers */
        section[data-testid="stSidebar"] h2 {
            color: #4f46e5 !important;
            font-weight: 700 !important;
        }
        
        /* Global button modifications */
        div.stButton > button:first-child {
            background: linear-gradient(135deg, #4f46e5 0%, #6366f1 100%) !important;
            color: white !important;
            border-radius: 12px !important;
            border: none !important;
            padding: 12px 28px !important;
            font-weight: 600 !important;
            box-shadow: 0 4px 12px rgba(79, 70, 229, 0.2) !important;
            transition: all 0.2s ease !important;
        }
        
        div.stButton > button:first-child:hover {
            transform: translateY(-1px) !important;
            box-shadow: 0 6px 20px rgba(79, 70, 229, 0.3) !important;
        }
        </style>
        """,
        unsafe_allow_html=True
    )


def show_login_page():
    """
    Renders the login/register UI.
    Called at the top of the app if the user is not in session.
    """
    inject_premium_styles()
    st.title("💸 HisaabAI")
    st.caption("AI-Powered Personal Expense Tracker")
    st.divider()

    tab_login, tab_register = st.tabs(["Login", "Create Account"])

    with tab_login:
        with st.form("login_form"):
            username = st.text_input("Username")
            password = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Login", use_container_width=True)

        if submitted:
            if not username or not password:
                st.error("Please fill in both fields.")
            else:
                result = verify_login(username, password)
                if result:
                    st.session_state.logged_in  = True
                    st.session_state.user_id    = result[0]
                    st.session_state.username   = result[1]
                    st.session_state.data_version = 0
                    st.rerun()
                else:
                    st.error("Invalid username or password.")

    with tab_register:
        with st.form("register_form"):
            new_user = st.text_input("Choose a username")
            new_pass = st.text_input("Choose a password", type="password")
            confirm  = st.text_input("Confirm password", type="password")
            reg_btn  = st.form_submit_button(
                "Create Account", use_container_width=True
            )

        if reg_btn:
            if not new_user or not new_pass:
                st.error("All fields are required.")
            elif new_pass != confirm:
                st.error("Passwords do not match.")
            elif len(new_pass) < 6:
                st.error("Password must be at least 6 characters.")
            else:
                success = register_user(new_user, new_pass)
                if success:
                    st.success("Account created! Go to Login tab.")
                else:
                    st.error("Username already taken.")


# ============================================================
# SECTION 9: MAIN APP
# ============================================================

def main():
    inject_premium_styles()


    # ── Top bar ──────────────────────────────────────────
    col_title, col_user, col_logout = st.columns([5, 2, 1])
    with col_title:
        st.title("💸 HisaabAI")
    with col_user:
        st.markdown(
            f"<div style='padding-top:18px; text-align:right; opacity:0.7'>"
            f"👤 {st.session_state.username}</div>",
            unsafe_allow_html=True
        )
    with col_logout:
        st.markdown("<div style='padding-top:12px'>", unsafe_allow_html=True)
        if st.button("Logout"):
            for key in list(st.session_state.keys()):
                del st.session_state[key]
            st.rerun()

    USER_ID = st.session_state.user_id

    # ── Load categories from DB ───────────────────────────
    categories_raw = fetch_categories()
    # categories_raw = [(id, name, budget), ...]
    cat_id_map   = {name: cid  for cid, name, _ in categories_raw}
    cat_name_map = {cid:  name for cid, name, _ in categories_raw}
    cat_names    = list(cat_id_map.keys())

    # ── Budget Alerts Banner ──────────────────────────────
    alerts = fetch_unread_alerts(USER_ID)
    if not alerts.empty:
        for _, row in alerts.iterrows():
            st.warning(f"⚠️ **Budget Alert:** {row['alert_msg']}")
        mark_alerts_read(USER_ID)

    # ── Sidebar: Add Expense Form ─────────────────────────
    st.sidebar.header("➕ Add New Expense")
    with st.sidebar.form("add_form", clear_on_submit=True):
        date_in  = st.date_input("Date", datetime.date.today())
        cat_in   = st.selectbox("Category", cat_names)
        amt_in   = st.number_input("Amount (₹)", min_value=0.01, step=10.0)
        desc_in  = st.text_input("Description (optional)")
        save_btn = st.form_submit_button("💾 Save Expense",
                                          use_container_width=True)

    if save_btn:
        if amt_in <= 0:
            st.sidebar.error("Amount must be greater than 0.")
        else:
            success = add_expense(
                USER_ID, cat_id_map[cat_in],
                date_in, amt_in, desc_in
            )
            if success:
                st.session_state.data_version += 1
                st.sidebar.success("✅ Expense saved!")
                st.rerun()

    # ── Load data (fresh on every data_version change) ────
    @st.cache_data(ttl=0, show_spinner=False)
    def load_data(uid, version):
        return fetch_expenses(uid)

    @st.cache_data(ttl=0, show_spinner=False)
    def load_monthly(uid, version):
        return fetch_monthly_summary(uid)

    data    = load_data(USER_ID, st.session_state.data_version)
    monthly = load_monthly(USER_ID, st.session_state.data_version)

    # ── Quick KPIs ────────────────────────────────────────
    if not data.empty:
        data['exp_date'] = pd.to_datetime(data['exp_date'])
        now   = datetime.date.today()
        this_month_data = data[
            (data['exp_date'].dt.month == now.month) &
            (data['exp_date'].dt.year  == now.year)
        ]
        total_all_time  = data['amount'].sum()
        total_this_month = this_month_data['amount'].sum()
        avg_transaction  = data['amount'].mean()
        num_categories   = data['category_name'].nunique()

        k1, k2, k3, k4 = st.columns(4)
        k1.metric("This Month",    f"₹{total_this_month:,.0f}")
        k2.metric("All Time Total",f"₹{total_all_time:,.0f}")
        k3.metric("Avg Transaction",f"₹{avg_transaction:,.0f}")
        k4.metric("Active Categories", num_categories)

    st.divider()


    # ── Tabs ──────────────────────────────────────────────
    tab1, tab2, tab3, tab4, tab5, tab6, tab7 = st.tabs([
        "📋 Transactions",
        "📊 Analytics",
        "🤖 Forecast",
        "🔁 Patterns",
        "✏️ Edit / Delete",
        "🗂️ History",
        "⚙️ Settings"
    ])

    # ════════════════════════════════════════════════════════
    # TAB 1: TRANSACTION HISTORY
    # ════════════════════════════════════════════════════════
    with tab1:
        st.subheader("All Transactions")
        st.caption("Every expense you've logged, newest first.")

        if data.empty:
            st.info("No expenses yet. Add one using the sidebar!")
        else:
            # ── Filtering Controls ─────────────────────────
            c_filt1, c_filt2, c_filt3 = st.columns([1, 1, 2])
            with c_filt1:
                selected_cat = st.selectbox("Filter Category", ["All"] + cat_names)
            with c_filt2:
                min_date = data['exp_date'].min().date()
                max_date = data['exp_date'].max().date()
                date_range = st.date_input("Filter Date Range", value=(min_date, max_date))
            with c_filt3:
                search_query = st.text_input("Search Description", placeholder="Type keywords...")

            # Apply filters
            filtered_data = data.copy()
            if selected_cat != "All":
                filtered_data = filtered_data[filtered_data['category_name'] == selected_cat]

            if len(date_range) == 2:
                start_d, end_d = date_range
                filtered_data = filtered_data[
                    (filtered_data['exp_date'].dt.date >= start_d) &
                    (filtered_data['exp_date'].dt.date <= end_d)
                ]

            if search_query:
                filtered_data = filtered_data[
                    filtered_data['description'].str.contains(search_query, case=False, na=False)
                ]

            # Display columns without internal IDs
            display_cols = ['id', 'exp_date', 'category_name',
                            'amount', 'description']
            st.dataframe(
                filtered_data[display_cols],
                use_container_width=True,
                hide_index=True
            )

            c1, c2, c3 = st.columns(3)
            c1.metric("Total Records",   len(filtered_data))
            c2.metric("Total Spent",     f"₹{filtered_data['amount'].sum():,.2f}")
            c3.metric("Largest Expense", f"₹{filtered_data['amount'].max():,.2f}" if not filtered_data.empty else "₹0.00")

    # ════════════════════════════════════════════════════════
    # TAB 2: ANALYTICS
    # ════════════════════════════════════════════════════════
    with tab2:
        st.subheader("Spending Analytics")

        if data.empty:
            st.info("Add some expenses to see analytics.")
        else:
            # ── Row 1: Pie chart + Bar chart ──────────────
            c1, c2 = st.columns(2)

            with c1:
                st.markdown("**Spend by Category (All Time)**")
                cat_totals = (
                    data.groupby('category_name')['amount']
                    .sum().reset_index()
                )
                fig_pie = px.pie(
                    cat_totals,
                    names  = 'category_name',
                    values = 'amount',
                    hole   = 0.4,
                    color_discrete_sequence = px.colors.qualitative.Pastel
                )
                fig_pie.update_layout(margin=dict(t=20, b=20))
                st.plotly_chart(fig_pie, use_container_width=True)

            with c2:
                st.markdown("**Monthly Totals**")
                if not monthly.empty:
                    fig_bar = px.bar(
                        monthly,
                        x     = 'month',
                        y     = 'total_spent',
                        color = 'total_spent',
                        color_continuous_scale = 'Blues',
                        labels = {'total_spent': 'Total (₹)', 'month': 'Month'}
                    )
                    fig_bar.update_layout(
                        showlegend  = False,
                        margin      = dict(t=20, b=20),
                        coloraxis_showscale = False
                    )
                    st.plotly_chart(fig_bar, use_container_width=True)

            # ── Row 2: Budget vs Actual ───────────────────
            st.markdown("---")
            st.markdown("**This Month: Budget vs Actual**")
            budget_data = get_budget_vs_actual(data, categories_raw)

            if budget_data.empty:
                st.info("No spending recorded this month yet.")
            else:
                # Horizontal bar chart
                fig_budget = go.Figure()
                fig_budget.add_trace(go.Bar(
                    name = 'Budget Limit',
                    x    = budget_data['budget_limit'],
                    y    = budget_data['category_name'],
                    orientation = 'h',
                    marker_color = 'lightgrey'
                ))
                fig_budget.add_trace(go.Bar(
                    name = 'Spent',
                    x    = budget_data['spent'],
                    y    = budget_data['category_name'],
                    orientation = 'h',
                    marker_color = [
                        '#ef4444' if p > 100
                        else '#f59e0b' if p > 80
                        else '#22c55e'
                        for p in budget_data['pct_used']
                    ]
                ))
                fig_budget.update_layout(
                    barmode      = 'overlay',
                    xaxis_title  = 'Amount (₹)',
                    margin       = dict(t=10, b=10),
                    height       = 300
                )
                st.plotly_chart(fig_budget, use_container_width=True)

                # Status table
                status_display = budget_data[[
                    'category_name', 'spent', 'budget_limit',
                    'remaining', 'pct_used', 'status'
                ]].rename(columns={
                    'category_name': 'Category',
                    'spent':         'Spent (₹)',
                    'budget_limit':  'Budget (₹)',
                    'remaining':     'Remaining (₹)',
                    'pct_used':      '% Used',
                    'status':        'Status'
                })
                st.dataframe(
                    status_display,
                    use_container_width=True,
                    hide_index=True
                )

    # ════════════════════════════════════════════════════════
    # TAB 3: AI FORECAST
    # ════════════════════════════════════════════════════════
    with tab3:
        st.subheader("🤖 Next Month Forecast")
        st.info(
            "Uses your past spending to estimate what you'll likely spend "
            "next month. The estimate updates every time you add an expense."
        )

        model_selection = st.selectbox(
            "How should we estimate next month?",
            ["Trend Based", "Recent Average", "Smart Mix"],
            help=(
                "Trend Based: looks at how your spending is changing over time.\n"
                "Recent Average: averages your last 3 months.\n"
                "Smart Mix: combines both for a balanced estimate."
            )
        )

        # Friendly explanation for each option
        if model_selection == "Trend Based":
            st.caption(
                "📈 Looks at whether your spending is generally rising or falling "
                "month over month, then projects that trend forward."
            )
        elif model_selection == "Recent Average":
            st.caption(
                "📊 Averages the totals from your last three months. "
                "Great when your spending is fairly stable."
            )
        else:
            st.caption(
                "⚖️ Blends the trend and the recent average together. "
                "Good when neither pattern alone tells the whole story."
            )

        # Map friendly names to internal model identifiers
        _model_map = {
            "Trend Based":     "Linear Regression",
            "Recent Average":  "Moving Average (3-Month)",
            "Smart Mix":       "Hybrid Ensemble (50/50)"
        }
        internal_model = _model_map[model_selection]

        pred, mae_or_err, monthly_ml = train_model(data, internal_model)

        if pred is None:
            st.warning(mae_or_err)
        else:
            c1, c2, c3 = st.columns(3)
            c1.metric("Estimated Next Month", f"₹{pred:,.2f}")
            c2.metric(
                "Typical Estimate Error",
                f"± ₹{mae_or_err:,.2f}",
                help="On average, our estimate has been off by about this much."
            )
            if not monthly_ml.empty:
                last_actual = monthly_ml['total'].iloc[-1]
                delta = pred - last_actual
                c3.metric(
                    "vs Last Month",
                    f"₹{pred:,.2f}",
                    delta = f"₹{delta:,.2f}"
                )

            # Trend chart with model line
            if not monthly_ml.empty:
                # Re-fit linear regression specifically to draw the trend curve
                X_plot = monthly_ml[['month_num']].values
                y_plot = monthly_ml['total'].values
                m_lr   = LinearRegression().fit(X_plot, y_plot)

                # Extend x-axis one step for the prediction point
                x_line = list(monthly_ml['month_num']) + [len(monthly_ml)]

                # Compute line values based on selected model
                if internal_model == "Linear Regression":
                    y_line = [m_lr.intercept_ + m_lr.coef_[0] * x for x in x_line]
                elif internal_model == "Moving Average (3-Month)":
                    k = min(3, len(monthly_ml))
                    historical_ma = list(monthly_ml['total'].rolling(window=k, min_periods=1).mean().values)
                    y_line = historical_ma + [pred]
                else:  # Hybrid Ensemble
                    k = min(3, len(monthly_ml))
                    historical_lr = [m_lr.intercept_ + m_lr.coef_[0] * x for x in monthly_ml['month_num']]
                    historical_ma = list(monthly_ml['total'].rolling(window=k, min_periods=1).mean().values)
                    historical_hybrid = [0.5 * lr + 0.5 * ma for lr, ma in zip(historical_lr, historical_ma)]
                    y_line = historical_hybrid + [pred]

                months_labels = (
                    [str(p) for p in monthly_ml['month']] + ['Next Month']
                )

                fig_ml = go.Figure()
                # Actual data points
                fig_ml.add_trace(go.Scatter(
                    x    = [str(p) for p in monthly_ml['month']],
                    y    = monthly_ml['total'],
                    mode = 'markers+lines',
                    name = 'What you actually spent',
                    marker = dict(size=10, color='#6366f1')
                ))
                # Trend line including prediction
                fig_ml.add_trace(go.Scatter(
                    x          = months_labels,
                    y          = y_line,
                    mode       = 'lines',
                    name       = 'Estimate curve',
                    line       = dict(dash='dash', color='#f59e0b'),
                    opacity    = 0.8
                ))
                # Prediction point
                fig_ml.add_trace(go.Scatter(
                    x      = ['Next Month'],
                    y      = [pred],
                    mode   = 'markers',
                    name   = 'Next month estimate',
                    marker = dict(size=14, color='#ef4444',
                                  symbol='star')
                ))
                fig_ml.update_layout(
                    xaxis_title = 'Month',
                    yaxis_title = 'Total Spend (₹)',
                    height      = 400,
                    margin      = dict(t=20, b=20)
                )
                st.plotly_chart(fig_ml, use_container_width=True)

                # Plain-English summary
                with st.expander("ℹ️ How was this estimate made?"):
                    slope = m_lr.coef_[0]
                    direction = "rising" if slope > 0 else "falling" if slope < 0 else "steady"
                    st.write(
                        f"**Method used:** {model_selection}"
                    )
                    st.write(
                        f"**Typical error:** about ₹{mae_or_err:,.2f} off "
                        f"from what actually happened."
                    )
                    st.write(
                        f"**Overall trend:** your spending has been {direction} "
                        f"by roughly ₹{abs(slope):,.0f} per month."
                    )
                    st.dataframe(
                        monthly_ml.rename(columns={
                            'month':     'Month',
                            'total':     'Total Spent (₹)',
                            'month_num': 'Month #'
                        }),
                        hide_index=True
                    )

    # ════════════════════════════════════════════════════════
    # TAB 4: SPENDING PATTERNS
    # ════════════════════════════════════════════════════════
    with tab4:
        st.subheader("🔁 Recurring vs Occasional Expenses")
        st.caption(
            "Automatically detects which categories appear "
            "consistently across months."
        )

        patterns = detect_recurring_expenses(data)

        if patterns.empty:
            st.info("Need a few months of data to detect patterns.")
        else:
            c1, c2 = st.columns(2)
            with c1:
                recurring_count   = patterns['is_recurring'].sum()
                occasional_count  = len(patterns) - recurring_count
                fig_rec = px.pie(
                    values = [recurring_count, occasional_count],
                    names  = ['Recurring', 'Occasional'],
                    color_discrete_sequence = ['#6366f1', '#e2e8f0']
                )
                fig_rec.update_layout(margin=dict(t=20, b=20))
                st.plotly_chart(fig_rec, use_container_width=True)

            with c2:
                display_patterns = patterns[[
                    'category_name', 'type', 'months_active',
                    'total_spent', 'avg_per_month', 'num_entries'
                ]].rename(columns={
                    'category_name': 'Category',
                    'type':          'Type',
                    'months_active': 'Months Active',
                    'total_spent':   'Total Spent (₹)',
                    'avg_per_month': 'Avg/Month (₹)',
                    'num_entries':   'Entries'
                })
                st.dataframe(
                    display_patterns,
                    use_container_width=True,
                    hide_index=True
                )

    # ════════════════════════════════════════════════════════
    # TAB 5: EDIT / DELETE
    # ════════════════════════════════════════════════════════
    with tab5:
        st.subheader("✏️ Edit an Expense")

        edit_id = st.number_input(
            "Enter Expense ID to edit",
            min_value=1, step=1, key="edit_id"
        )

        if st.button("🔍 Load Record"):
            record = fetch_expense_by_id(edit_id, USER_ID)
            if record:
                st.session_state.edit_record = record
            else:
                st.error(f"No expense found with ID {edit_id} "
                         f"for your account.")

        if 'edit_record' in st.session_state:
            rec = st.session_state.edit_record
            st.success(f"Editing expense #{rec['id']}")

            with st.form("edit_form"):
                e_date = st.date_input(
                    "Date",
                    value=rec['exp_date']
                )
                e_cat  = st.selectbox(
                    "Category",
                    cat_names,
                    index=cat_names.index(rec['category_name'])
                    if rec['category_name'] in cat_names else 0
                )
                e_amt  = st.number_input(
                    "Amount (₹)",
                    value=float(rec['amount']),
                    min_value=0.01,
                    step=10.0
                )
                e_desc = st.text_input(
                    "Description",
                    value=rec['description'] or ''
                )
                update_btn = st.form_submit_button(
                    "✅ Update Expense",
                    use_container_width=True
                )

            if update_btn:
                success = update_expense(
                    rec['id'], USER_ID, cat_id_map[e_cat],
                    e_date, e_amt, e_desc
                )
                if success:
                    st.success("Expense updated successfully!")
                    del st.session_state.edit_record
                    st.session_state.data_version += 1
                    st.rerun()

        st.divider()
        st.subheader("🗑️ Delete an Expense")

        del_id = st.number_input(
            "Enter Expense ID to delete",
            min_value=1, step=1, key="del_id"
        )
        st.warning(
            "⚠️ This permanently removes the record. "
            "It will still appear in the History tab so you can track changes."
        )
        if st.button("🗑️ Delete Record", type="primary"):
            success = delete_expense(del_id, USER_ID)
            if success:
                st.success(f"Record #{del_id} deleted.")
                st.session_state.data_version += 1
                st.rerun()
            else:
                st.error(
                    f"Could not delete #{del_id}. "
                    f"Check the ID belongs to your account."
                )

    # ════════════════════════════════════════════════════════
    # TAB 6: AUDIT LOG
    # ════════════════════════════════════════════════════════
    with tab6:
        st.subheader("🗂️ Activity History")
        st.caption(
            "A complete record of every expense you've added, edited, or removed."
        )

        audit_data = fetch_audit_log(USER_ID)

        if audit_data.empty:
            st.info("No activity yet.")
        else:
            # Friendly labels for the action column
            action_labels = {
                'INSERT': '➕ Added',
                'UPDATE': '✏️ Edited',
                'DELETE': '🗑️ Removed'
            }
            audit_display = audit_data.copy()
            audit_display['action'] = audit_display['action'].map(action_labels)
            audit_display = audit_display.rename(columns={
                'log_id':           'Entry #',
                'action':           'What happened',
                'expense_id':       'Expense #',
                'old_amount':       'Old Amount (₹)',
                'new_amount':       'New Amount (₹)',
                'old_description':  'Old Description',
                'new_description':  'New Description',
                'note':             'Note',
                'changed_at':       'When'
            })

            # Color-code each row based on the action
            def color_action(val):
                if not isinstance(val, str):
                    return ''
                if 'Added' in val:
                    return 'background-color: #dcfce7'
                if 'Edited' in val:
                    return 'background-color: #fef9c3'
                if 'Removed' in val:
                    return 'background-color: #fee2e2'
                return ''

            # Pandas 2.1+ renamed Styler.applymap to Styler.map.
            # Use whichever exists so the app works on both old and new pandas.
            _style_method = getattr(
                audit_display.style, "map", audit_display.style.applymap
            )
            styled = _style_method(
                color_action, subset=['What happened']
            )
            st.dataframe(styled, use_container_width=True, hide_index=True)

    # ════════════════════════════════════════════════════════
    # TAB 7: SETTINGS & CATEGORY MANAGER
    # ════════════════════════════════════════════════════════
    with tab7:
        st.subheader("⚙️ Settings")
        st.caption("Manage your spending categories and monthly budgets.")

        col_c1, col_c2 = st.columns(2)

        with col_c1:
            st.markdown("### 🎛️ Adjust Category Budget")
            with st.form("budget_update_form"):
                cat_to_update = st.selectbox("Select Category", cat_names)
                current_limit = next((limit for cid, name, limit in categories_raw if name == cat_to_update), 5000.0)
                new_budget = st.number_input("New Budget Limit (₹)", min_value=0.0, value=float(current_limit), step=100.0)
                update_b = st.form_submit_button("Update Budget Limit", use_container_width=True)

            if update_b:
                target_cid = cat_id_map[cat_to_update]
                if update_category_budget(target_cid, new_budget):
                    st.success(f"Budget limit for {cat_to_update} updated to ₹{new_budget:,.2f}!")
                    st.session_state.data_version += 1
                    st.rerun()

        with col_c2:
            st.markdown("### ➕ Create New Category")
            with st.form("new_category_form"):
                new_cat_name = st.text_input("Category Name", placeholder="e.g. Healthcare, Education")
                initial_budget = st.number_input("Initial Budget Limit (₹)", min_value=0.0, value=5000.0, step=100.0)
                create_b = st.form_submit_button("Create Category", use_container_width=True)

            if create_b:
                if not new_cat_name:
                    st.error("Please enter a category name.")
                elif new_cat_name in cat_names:
                    st.error(f"Category '{new_cat_name}' already exists.")
                else:
                    if add_category(new_cat_name, initial_budget):
                        st.success(f"New category '{new_cat_name}' created successfully!")
                        st.session_state.data_version += 1
                        st.rerun()

        st.markdown("---")
        st.markdown("### 📊 Active Categories Overview")
        # Format and show current categories in a clean table
        cat_df = pd.DataFrame(categories_raw, columns=['ID', 'Category Name', 'Budget Limit (₹)'])
        st.dataframe(cat_df, use_container_width=True, hide_index=True)


# ============================================================
# SECTION 10: APP ENTRY POINT
# ============================================================

# Initialize session state on first load
if 'logged_in' not in st.session_state:
    st.session_state.logged_in = False

if 'data_version' not in st.session_state:
    st.session_state.data_version = 0

# Route to login or main app
if not st.session_state.logged_in:
    show_login_page()
else:
    main()