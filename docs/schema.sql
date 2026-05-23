-- =====================================================================
-- HISAABAI DATABASE — COMPLETE SCHEMA (TiDB / MySQL compatible)
-- =====================================================================
-- This file creates the entire database from scratch.
-- Tables are in 3rd Normal Form. See docs/normalization.md for proof.
-- =====================================================================

USE spendwise_db;

-- ---------------------------------------------------------------------
-- 1. DROP existing objects (child tables first due to foreign keys)
-- ---------------------------------------------------------------------
DROP VIEW  IF EXISTS monthly_summary;
DROP TABLE IF EXISTS audit_log;
DROP TABLE IF EXISTS budget_alerts;
DROP TABLE IF EXISTS expenses;
DROP TABLE IF EXISTS categories;
DROP TABLE IF EXISTS users;

-- ---------------------------------------------------------------------
-- 2. USERS — Account credentials
-- ---------------------------------------------------------------------
CREATE TABLE users (
    user_id    INT AUTO_INCREMENT PRIMARY KEY,
    username   VARCHAR(50) UNIQUE NOT NULL,
    pass_hash  VARCHAR(64) NOT NULL,           -- SHA-256 hex digest
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) AUTO_ID_CACHE 1;

-- ---------------------------------------------------------------------
-- 3. CATEGORIES — Per-user spending categories with monthly budget limits
--    Each user owns their own list, so different users can have
--    different categories and different limits independently.
-- ---------------------------------------------------------------------
CREATE TABLE categories (
    category_id   INT AUTO_INCREMENT PRIMARY KEY,
    user_id       INT NOT NULL,
    category_name VARCHAR(50) NOT NULL,
    budget_limit  DECIMAL(10,2) DEFAULT 5000.00,
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
    UNIQUE KEY uniq_user_cat (user_id, category_name)
) AUTO_ID_CACHE 1;

-- ---------------------------------------------------------------------
-- 4. EXPENSES — Individual transactions
--     Foreign keys to users + categories enforce referential integrity
-- ---------------------------------------------------------------------
CREATE TABLE expenses (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    user_id     INT NOT NULL,
    category_id INT NOT NULL,
    exp_date    DATE NOT NULL,
    amount      DECIMAL(10,2) NOT NULL,
    description VARCHAR(255),
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id)     REFERENCES users(user_id)     ON DELETE CASCADE,
    FOREIGN KEY (category_id) REFERENCES categories(category_id)
) AUTO_ID_CACHE 1;

-- ---------------------------------------------------------------------
-- 5. BUDGET_ALERTS — Auto-generated when category budget is exceeded
-- ---------------------------------------------------------------------
CREATE TABLE budget_alerts (
    alert_id    INT AUTO_INCREMENT PRIMARY KEY,
    user_id     INT NOT NULL,
    category_id INT NOT NULL,
    alert_msg   VARCHAR(255),
    is_read     BOOLEAN DEFAULT FALSE,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id)     REFERENCES users(user_id)     ON DELETE CASCADE,
    FOREIGN KEY (category_id) REFERENCES categories(category_id)
) AUTO_ID_CACHE 1;

-- ---------------------------------------------------------------------
-- 6. AUDIT_LOG — Append-only history of every CRUD operation
--    Records both old and new amount AND description for full traceability
-- ---------------------------------------------------------------------
CREATE TABLE audit_log (
    log_id           INT AUTO_INCREMENT PRIMARY KEY,
    user_id          INT,
    action           ENUM('INSERT','UPDATE','DELETE') NOT NULL,
    expense_id       INT,
    old_amount       DECIMAL(10,2),
    new_amount       DECIMAL(10,2),
    old_description  VARCHAR(255),
    new_description  VARCHAR(255),
    note             VARCHAR(255),
    changed_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) AUTO_ID_CACHE 1;

-- ---------------------------------------------------------------------
-- 7. INDEXES — Optimize the most frequent query paths
-- ---------------------------------------------------------------------
CREATE INDEX idx_exp_date      ON expenses(exp_date);
CREATE INDEX idx_user_id       ON expenses(user_id);
CREATE INDEX idx_user_category ON expenses(user_id, category_id);   -- Composite
CREATE INDEX idx_alerts_user   ON budget_alerts(user_id, is_read);

-- ---------------------------------------------------------------------
-- 8. VIEW — Pre-aggregated monthly summary per user
--    Demonstrates DDL: a saved query treated as a virtual table
-- ---------------------------------------------------------------------
CREATE VIEW monthly_summary AS
SELECT
    user_id,
    DATE_FORMAT(exp_date, '%Y-%m') AS month,
    SUM(amount)                    AS total_spent,
    COUNT(*)                       AS num_transactions,
    AVG(amount)                    AS avg_transaction,
    MAX(amount)                    AS largest_expense
FROM expenses
GROUP BY user_id, DATE_FORMAT(exp_date, '%Y-%m');

-- ---------------------------------------------------------------------
-- 8a. STORED PROCEDURES & TRIGGER — DOCUMENTATION ONLY
--
--   ⚠️  TiDB Serverless (free tier) does NOT support CREATE PROCEDURE
--       or CREATE TRIGGER. Running them returns:
--           "Unsupported type *resolve.NodeW"
--
--   The application replicates this functionality in Python:
--     • check_and_create_alert()   replaces an AFTER INSERT trigger
--     • log_audit()                replaces an AFTER DELETE trigger
--     • fetch_monthly_summary()    replaces a stored procedure call
--
--   The blocks below are kept as DOCUMENTATION for the project report
--   and would run unchanged on standard MySQL 8 / MariaDB. To enable
--   them on a self-hosted MySQL, uncomment everything between the
--   /* and */ markers.
-- ---------------------------------------------------------------------
/*

-- STORED PROCEDURE: GetMonthlyTotal(user, year, month)
DROP PROCEDURE IF EXISTS GetMonthlyTotal;
DELIMITER $$
CREATE PROCEDURE GetMonthlyTotal(
    IN  p_user_id INT,
    IN  p_year    INT,
    IN  p_month   INT
)
BEGIN
    SELECT
        c.category_name,
        COUNT(*)        AS transactions,
        SUM(e.amount)   AS total_spent,
        AVG(e.amount)   AS avg_amount
    FROM expenses e
    JOIN categories c ON e.category_id = c.category_id
    WHERE e.user_id          = p_user_id
      AND YEAR(e.exp_date)   = p_year
      AND MONTH(e.exp_date)  = p_month
    GROUP BY c.category_name
    ORDER BY total_spent DESC;
END$$
DELIMITER ;


-- STORED PROCEDURE: GetCategoryBudgetStatus(user, year, month)
DROP PROCEDURE IF EXISTS GetCategoryBudgetStatus;
DELIMITER $$
CREATE PROCEDURE GetCategoryBudgetStatus(
    IN  p_user_id INT,
    IN  p_year    INT,
    IN  p_month   INT
)
BEGIN
    SELECT
        c.category_name,
        c.budget_limit,
        IFNULL(SUM(e.amount), 0)                                  AS spent,
        c.budget_limit - IFNULL(SUM(e.amount), 0)                 AS remaining,
        ROUND(IFNULL(SUM(e.amount), 0) / c.budget_limit * 100, 1) AS pct_used
    FROM categories c
    LEFT JOIN expenses e
        ON e.category_id = c.category_id
       AND e.user_id     = p_user_id
       AND YEAR(e.exp_date)  = p_year
       AND MONTH(e.exp_date) = p_month
    GROUP BY c.category_id, c.category_name, c.budget_limit
    ORDER BY pct_used DESC;
END$$
DELIMITER ;


-- TRIGGER: auto-log every expense deletion
DROP TRIGGER IF EXISTS trg_audit_expense_delete;
DELIMITER $$
CREATE TRIGGER trg_audit_expense_delete
AFTER DELETE ON expenses
FOR EACH ROW
BEGIN
    INSERT INTO audit_log
        (user_id, action, expense_id, old_amount, old_description, note)
    VALUES
        (OLD.user_id, 'DELETE', OLD.id, OLD.amount, OLD.description,
         'Auto-logged by trg_audit_expense_delete');
END$$
DELIMITER ;

*/

-- ---------------------------------------------------------------------
-- 9. SEED — Demo user (username: demo, password: demo123)
--    Password is the SHA-256 hex digest of "demo123".
-- ---------------------------------------------------------------------
INSERT INTO users (username, pass_hash) VALUES
('demo', 'd3ad9315b7be5dd53b31a273b3b3aba5defe700808305aa16a3062b76658a791');

-- ---------------------------------------------------------------------
-- 10. SEED — Default categories for the demo user
--     Uses INSERT...SELECT with a derived table so we don't depend on
--     hardcoded IDs (TiDB's AUTO_INCREMENT cache may assign any number).
--
--     Every NEW user who signs up via the app gets these same defaults
--     automatically — see register_user() in app.py.
-- ---------------------------------------------------------------------
INSERT INTO categories (user_id, category_name, budget_limit)
SELECT u.user_id, defaults.category_name, defaults.budget_limit
FROM users u
JOIN (
    SELECT 'Food'          AS category_name, 6000.00 AS budget_limit UNION ALL
    SELECT 'Transport',     3000.00 UNION ALL
    SELECT 'Subscriptions', 2000.00 UNION ALL
    SELECT 'Shopping',      5000.00 UNION ALL
    SELECT 'Medical',       4000.00 UNION ALL
    SELECT 'Other',         5000.00
) AS defaults
WHERE u.username = 'demo';

-- ---------------------------------------------------------------------
-- 11. SEED — Sample expenses for the demo user across 8 months
--     Designed to showcase the ML forecast and pattern detection:
--       • Recurring monthly: Subscriptions (₹299), Electricity bill
--       • Weekly groceries (Food)
--       • Steady-but-rising Transport
--       • Occasional Medical and Shopping spikes
--
--     Joins against the demo user + their categories by NAME so we
--     never reference an ID directly. Reads like plain English:
--     "for user 'demo', in their 'Food' category, on Sep 2 2025, ₹480"
-- ---------------------------------------------------------------------
INSERT INTO expenses (user_id, category_id, exp_date, amount, description)
SELECT u.user_id, c.category_id, seed.exp_date, seed.amount, seed.description
FROM users u
JOIN categories c ON c.user_id = u.user_id
JOIN (
    -- ===== September 2025 =====
    SELECT 'Food'          AS cat, DATE '2025-09-02' AS exp_date,  480.00 AS amount, 'Groceries'           AS description UNION ALL
    SELECT 'Transport',     DATE '2025-09-04',  170.00, 'Fuel'               UNION ALL
    SELECT 'Subscriptions', DATE '2025-09-09',  299.00, 'Netflix + Spotify'  UNION ALL
    SELECT 'Food',          DATE '2025-09-12',  520.00, 'Groceries'          UNION ALL
    SELECT 'Other',         DATE '2025-09-15',  950.00, 'Electricity bill'   UNION ALL
    SELECT 'Transport',     DATE '2025-09-18',  140.00, 'Bus pass'           UNION ALL
    SELECT 'Shopping',      DATE '2025-09-22',  600.00, 'T-shirts'           UNION ALL
    SELECT 'Food',          DATE '2025-09-26',  430.00, 'Groceries'          UNION ALL
    SELECT 'Medical',       DATE '2025-09-29',  350.00, 'Pharmacy'           UNION ALL

    -- ===== October 2025 =====
    SELECT 'Food',          DATE '2025-10-03',  500.00, 'Groceries'          UNION ALL
    SELECT 'Transport',     DATE '2025-10-06',  180.00, 'Fuel'               UNION ALL
    SELECT 'Subscriptions', DATE '2025-10-09',  299.00, 'Netflix + Spotify'  UNION ALL
    SELECT 'Other',         DATE '2025-10-13', 1000.00, 'Electricity bill'   UNION ALL
    SELECT 'Food',          DATE '2025-10-15',  470.00, 'Groceries'          UNION ALL
    SELECT 'Shopping',      DATE '2025-10-18',  900.00, 'Diwali shopping'    UNION ALL
    SELECT 'Transport',     DATE '2025-10-21',  150.00, 'Bus pass'           UNION ALL
    SELECT 'Food',          DATE '2025-10-25',  550.00, 'Groceries'          UNION ALL
    SELECT 'Shopping',      DATE '2025-10-28', 1500.00, 'Festive clothes'    UNION ALL

    -- ===== November 2025 =====
    SELECT 'Food',          DATE '2025-11-02',  490.00, 'Groceries'          UNION ALL
    SELECT 'Transport',     DATE '2025-11-05',  195.00, 'Fuel'               UNION ALL
    SELECT 'Subscriptions', DATE '2025-11-09',  299.00, 'Subscriptions'      UNION ALL
    SELECT 'Other',         DATE '2025-11-14', 1050.00, 'Electricity bill'   UNION ALL
    SELECT 'Food',          DATE '2025-11-16',  500.00, 'Groceries'          UNION ALL
    SELECT 'Transport',     DATE '2025-11-20',  160.00, 'Bus pass'           UNION ALL
    SELECT 'Medical',       DATE '2025-11-23',  700.00, 'Doctor visit + meds' UNION ALL
    SELECT 'Food',          DATE '2025-11-27',  460.00, 'Groceries'          UNION ALL

    -- ===== December 2025 =====
    SELECT 'Food',          DATE '2025-12-02',  540.00, 'Groceries'          UNION ALL
    SELECT 'Transport',     DATE '2025-12-05',  200.00, 'Fuel'               UNION ALL
    SELECT 'Subscriptions', DATE '2025-12-09',  299.00, 'Subscriptions'      UNION ALL
    SELECT 'Other',         DATE '2025-12-13', 1100.00, 'Electricity bill'   UNION ALL
    SELECT 'Food',          DATE '2025-12-15',  510.00, 'Groceries'          UNION ALL
    SELECT 'Shopping',      DATE '2025-12-19', 1800.00, 'Christmas gifts'    UNION ALL
    SELECT 'Transport',     DATE '2025-12-22',  170.00, 'Cab'                UNION ALL
    SELECT 'Food',          DATE '2025-12-27',  480.00, 'Groceries'          UNION ALL
    SELECT 'Other',         DATE '2025-12-30',  450.00, 'Internet recharge'  UNION ALL

    -- ===== January 2026 =====
    SELECT 'Food',          DATE '2026-01-03',  500.00, 'Groceries'          UNION ALL
    SELECT 'Transport',     DATE '2026-01-06',  190.00, 'Fuel'               UNION ALL
    SELECT 'Subscriptions', DATE '2026-01-09',  299.00, 'Subscriptions'      UNION ALL
    SELECT 'Other',         DATE '2026-01-13', 1080.00, 'Electricity bill'   UNION ALL
    SELECT 'Food',          DATE '2026-01-15',  470.00, 'Groceries'          UNION ALL
    SELECT 'Medical',       DATE '2026-01-18',  400.00, 'Pharmacy'           UNION ALL
    SELECT 'Transport',     DATE '2026-01-22',  155.00, 'Bus pass'           UNION ALL
    SELECT 'Food',          DATE '2026-01-26',  520.00, 'Groceries'          UNION ALL
    SELECT 'Shopping',      DATE '2026-01-29',  650.00, 'Shoes'              UNION ALL

    -- ===== February 2026 =====
    SELECT 'Food',          DATE '2026-02-03',  520.00, 'Weekly groceries'   UNION ALL
    SELECT 'Transport',     DATE '2026-02-07',  200.00, 'Fuel'               UNION ALL
    SELECT 'Subscriptions', DATE '2026-02-10',  299.00, 'Netflix + Spotify'  UNION ALL
    SELECT 'Food',          DATE '2026-02-14',  450.00, 'Groceries'          UNION ALL
    SELECT 'Other',         DATE '2026-02-18', 1200.00, 'Electricity bill'   UNION ALL
    SELECT 'Transport',     DATE '2026-02-22',  150.00, 'Bus pass'           UNION ALL
    SELECT 'Shopping',      DATE '2026-02-26',  800.00, 'Clothes'            UNION ALL

    -- ===== March 2026 =====
    SELECT 'Food',          DATE '2026-03-02',  600.00, 'Groceries'          UNION ALL
    SELECT 'Transport',     DATE '2026-03-06',  210.00, 'Fuel'               UNION ALL
    SELECT 'Subscriptions', DATE '2026-03-10',  299.00, 'Subscriptions'      UNION ALL
    SELECT 'Medical',       DATE '2026-03-14',  500.00, 'Doctor visit'       UNION ALL
    SELECT 'Other',         DATE '2026-03-18', 1100.00, 'Electricity bill'   UNION ALL
    SELECT 'Food',          DATE '2026-03-22',  480.00, 'Groceries'          UNION ALL
    SELECT 'Shopping',      DATE '2026-03-28', 1200.00, 'Shoes + Bag'        UNION ALL

    -- ===== April 2026 =====
    SELECT 'Food',          DATE '2026-04-01',  550.00, 'Groceries'          UNION ALL
    SELECT 'Transport',     DATE '2026-04-05',  190.00, 'Fuel'               UNION ALL
    SELECT 'Subscriptions', DATE '2026-04-09',  299.00, 'Subscriptions'      UNION ALL
    SELECT 'Other',         DATE '2026-04-13', 1300.00, 'Electricity bill'   UNION ALL
    SELECT 'Food',          DATE '2026-04-17',  420.00, 'Groceries'          UNION ALL
    SELECT 'Medical',       DATE '2026-04-21',  800.00, 'Medical checkup'    UNION ALL
    SELECT 'Shopping',      DATE '2026-04-25',  650.00, 'Shopping'
) AS seed ON c.category_name = seed.cat
WHERE u.username = 'demo';
