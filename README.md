# 💸 HisaabAI — AI-Powered Personal Expense Tracker

A full-stack DBMS mini project that combines a **cloud relational database** (TiDB Cloud / MySQL), a **Python backend**, **machine learning forecasting**, and an **interactive web UI** built with Streamlit.

> *Hisaab* (हिसाब / حساب) means "accounts" in Hindi/Urdu — combined with AI-driven forecasting and budget intelligence, HisaabAI helps you track expenses, monitor budgets, detect spending patterns, and forecast next month's spend. All backed by a normalized relational schema and a full audit trail.

---

## ✨ Features

- 🔐 **Multi-user authentication** with SHA-256 password hashing
- 📋 **Full CRUD** on expenses (add / edit / delete) with filters and search
- 🗂️ **Category-based budgeting** with adjustable monthly limits
- 📊 **Live analytics dashboard** — pie chart, bar chart, budget-vs-actual visualizations
- 🤖 **Forecasting engine** — Linear Regression, 3-month Moving Average, and a Hybrid Ensemble
- 🔔 **Automatic budget alerts** when monthly category limits are exceeded
- 🔁 **Recurring vs occasional expense detection**
- 🗃️ **Complete audit log** capturing every INSERT, UPDATE, and DELETE with old/new values
- ⚙️ **Settings panel** to manage categories and budget limits

---

## 🛠️ Tech Stack

| Layer | Technology |
|---|---|
| Frontend | Streamlit |
| Backend | Python 3.10+ |
| Database | TiDB Cloud (MySQL-compatible, distributed) |
| ML | scikit-learn (Linear Regression) |
| Charts | Plotly |
| Data | Pandas, NumPy |
| Security | hashlib (SHA-256), TLS via certifi |

---

## 🗂️ Project Structure

```
hisaabai/
├── app.py                  # Single-file Streamlit application
├── requirements.txt        # Python dependencies
├── README.md               # This file
├── .gitignore              # Keeps secrets out of git
├── docs/
│   └── schema.sql          # Complete database schema
└── .streamlit/
    └── secrets.toml        # DB credentials (NOT committed)
```

---

## 🗄️ Database Schema

Five tables, all in **3rd Normal Form**:

| Table | Purpose |
|---|---|
| `users` | Account credentials (hashed passwords) |
| `categories` | Spending categories with monthly budget limits |
| `expenses` | Individual transactions, foreign-keyed to user + category |
| `budget_alerts` | Generated when a user exceeds a category budget |
| `audit_log` | Append-only history of every CRUD action with old + new values |

Plus:
- 1 SQL `VIEW` (`monthly_summary`) for pre-aggregated monthly totals
- 4 indexes (including a composite `(user_id, category_id)`)
- Foreign keys with `ON DELETE CASCADE` for referential integrity

The full schema is in [`docs/schema.sql`](docs/schema.sql). Normalization analysis and ER diagram are in the project report.

---

## 🚀 Local Setup

### 1. Clone the repository
```bash
git clone https://github.com/<your-username>/hisaabai.git
cd hisaabai
```

### 2. Install dependencies
```bash
pip install -r requirements.txt
```

### 3. Set up the database
- Create a free [TiDB Cloud Serverless cluster](https://tidbcloud.com) (or use a local MySQL instance).
- Open `docs/schema.sql` and run it in your TiDB SQL Editor.

### 4. Add your credentials
Create a file at `.streamlit/secrets.toml`:

```toml
[mysql]
host     = "gateway01.aws.dev.tidbcloud.com"
port     = "4000"
user     = "your_user.root"
password = "your_password"
database = "hisaabai_db"
```

> ⚠️ Never commit this file. It is already listed in `.gitignore`.

### 5. Run the app
```bash
streamlit run app.py
```

Open `http://localhost:8501` in your browser.

---

## 🧮 Forecasting Methodology

The Forecast tab offers three models, each fed by aggregated monthly totals:

- **Trend Based** — Linear Regression over month index. Equation: `ŷ = β₀ + β₁ x`
- **Recent Average** — 3-month rolling mean. Smooths out short-term spikes.
- **Smart Mix** — 50/50 ensemble of the two. Robust when neither pattern dominates.

Each model is evaluated using **Mean Absolute Error (MAE)** computed on training data. The UI displays a "typical estimate error" so the user understands the prediction's uncertainty.

---

## 🛡️ Security Notes

- Passwords are hashed with SHA-256 before storage. (Production: should use bcrypt + per-user salt.)
- All SQL queries use parameterized statements (`%s` placeholders) — no string interpolation, no SQL injection.
- Database credentials live in `secrets.toml`, never in source.
- TiDB Cloud connections use TLS verified via `certifi`.

---

## 📦 Deployment

This app is deployable to [Streamlit Community Cloud](https://streamlit.io/cloud) with one click. See the deployment section below for step-by-step instructions, or follow these high-level steps:

1. Push to GitHub
2. Connect your repo in Streamlit Cloud
3. Paste your `secrets.toml` contents into the Cloud secrets panel
4. Deploy

---

## 📚 Academic Context

Built as a DBMS mini project to demonstrate:

- Relational schema design and normalization (1NF → 3NF)
- Primary keys, foreign keys, referential actions, constraints
- Complex SELECT with JOIN, GROUP BY, aggregate functions
- DDL features: `VIEW`, `INDEX` (including composite)
- Application-layer trigger logic (budget alerts)
- Audit logging for data integrity tracking
- Python/SQL integration with parameterized queries
- Bonus: machine learning over time-series data

---

## 📝 License

MIT — free to use, modify, and learn from.
