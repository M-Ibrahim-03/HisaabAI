-- =====================================================================
-- HISAABAI DATABASE — COMPLETE SCHEMA (TiDB / MySQL compatible)
-- =====================================================================
-- This file creates the entire database from scratch.
-- Tables are in 3rd Normal Form. See docs/normalization.md for proof.
-- =====================================================================

USE hisaabai_db;

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
-- 3. CATEGORIES — Spending categories with monthly budget limits
-- ---------------------------------------------------------------------
CREATE TABLE categories (
    category_id   INT AUTO_INCREMENT PRIMARY KEY,
    category_name VARCHAR(50) NOT NULL,
    budget_limit  DECIMAL(10,2) DEFAULT 5000.00
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
-- 8a. STORED PROCEDURE — GetMonthlyTotal
--     Returns total spending for a given user in a given month.
--     Demonstrates parameterized server-side logic.
--     Usage: CALL GetMonthlyTotal(1, 2026, 4);
-- ---------------------------------------------------------------------
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

-- ---------------------------------------------------------------------
-- 8b. STORED PROCEDURE — GetCategoryBudgetStatus
--     Returns budget status for every category for a user in a month.
--     Shows: spent, budget, remaining, percent used.
--     Usage: CALL GetCategoryBudgetStatus(1, 2026, 4);
-- ---------------------------------------------------------------------
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
        IFNULL(SUM(e.amount), 0)                            AS spent,
        c.budget_limit - IFNULL(SUM(e.amount), 0)           AS remaining,
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

-- ---------------------------------------------------------------------
-- 8c. TRIGGER — Auto-log every expense deletion
--     Database-level safety net: even if a future client forgets to
--     write to audit_log, this trigger guarantees the action is recorded.
--     Demonstrates DDL trigger syntax with NEW/OLD row references.
-- ---------------------------------------------------------------------
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

-- ---------------------------------------------------------------------
-- 9. SEED — Default categories
-- ---------------------------------------------------------------------
INSERT INTO categories (category_name, budget_limit) VALUES
('Food',          6000.00),
('Transport',     3000.00),
('Subscriptions', 2000.00),
('Shopping',      5000.00),
('Medical',       4000.00),
('Other',         5000.00);

-- ---------------------------------------------------------------------
-- 10. SEED — Demo user (username: demo, password: demo123)
--     Password below is the SHA-256 hex digest of "demo123"
-- ---------------------------------------------------------------------
INSERT INTO users (username, pass_hash) VALUES
('demo', 'd3ad9315b7be5dd53b31a273b3b3aba5defe700808305aa16a3062b76658a791');

-- ---------------------------------------------------------------------
-- 11. SEED — Sample expenses across 8 months
--     Designed to showcase the ML forecast and pattern detection:
--       • Recurring monthly: Subscriptions (₹299), Electricity bill
--       • Weekly groceries (Food)
--       • Steady-but-rising Transport
--       • Occasional Medical and Shopping spikes
-- ---------------------------------------------------------------------
INSERT INTO expenses (user_id, category_id, exp_date, amount, description) VALUES
-- ===== September 2025 =====
(1, 1, '2025-09-02',  480.00, 'Groceries'),
(1, 2, '2025-09-04',  170.00, 'Fuel'),
(1, 3, '2025-09-09',  299.00, 'Netflix + Spotify'),
(1, 1, '2025-09-12',  520.00, 'Groceries'),
(1, 6, '2025-09-15',  950.00, 'Electricity bill'),
(1, 2, '2025-09-18',  140.00, 'Bus pass'),
(1, 4, '2025-09-22',  600.00, 'T-shirts'),
(1, 1, '2025-09-26',  430.00, 'Groceries'),
(1, 5, '2025-09-29',  350.00, 'Pharmacy'),

-- ===== October 2025 =====
(1, 1, '2025-10-03',  500.00, 'Groceries'),
(1, 2, '2025-10-06',  180.00, 'Fuel'),
(1, 3, '2025-10-09',  299.00, 'Netflix + Spotify'),
(1, 6, '2025-10-13', 1000.00, 'Electricity bill'),
(1, 1, '2025-10-15',  470.00, 'Groceries'),
(1, 4, '2025-10-18',  900.00, 'Diwali shopping'),
(1, 2, '2025-10-21',  150.00, 'Bus pass'),
(1, 1, '2025-10-25',  550.00, 'Groceries'),
(1, 4, '2025-10-28', 1500.00, 'Festive clothes'),

-- ===== November 2025 =====
(1, 1, '2025-11-02',  490.00, 'Groceries'),
(1, 2, '2025-11-05',  195.00, 'Fuel'),
(1, 3, '2025-11-09',  299.00, 'Subscriptions'),
(1, 6, '2025-11-14', 1050.00, 'Electricity bill'),
(1, 1, '2025-11-16',  500.00, 'Groceries'),
(1, 2, '2025-11-20',  160.00, 'Bus pass'),
(1, 5, '2025-11-23',  700.00, 'Doctor visit + meds'),
(1, 1, '2025-11-27',  460.00, 'Groceries'),

-- ===== December 2025 =====
(1, 1, '2025-12-02',  540.00, 'Groceries'),
(1, 2, '2025-12-05',  200.00, 'Fuel'),
(1, 3, '2025-12-09',  299.00, 'Subscriptions'),
(1, 6, '2025-12-13', 1100.00, 'Electricity bill'),
(1, 1, '2025-12-15',  510.00, 'Groceries'),
(1, 4, '2025-12-19', 1800.00, 'Christmas gifts'),
(1, 2, '2025-12-22',  170.00, 'Cab'),
(1, 1, '2025-12-27',  480.00, 'Groceries'),
(1, 6, '2025-12-30',  450.00, 'Internet recharge'),

-- ===== January 2026 =====
(1, 1, '2026-01-03',  500.00, 'Groceries'),
(1, 2, '2026-01-06',  190.00, 'Fuel'),
(1, 3, '2026-01-09',  299.00, 'Subscriptions'),
(1, 6, '2026-01-13', 1080.00, 'Electricity bill'),
(1, 1, '2026-01-15',  470.00, 'Groceries'),
(1, 5, '2026-01-18',  400.00, 'Pharmacy'),
(1, 2, '2026-01-22',  155.00, 'Bus pass'),
(1, 1, '2026-01-26',  520.00, 'Groceries'),
(1, 4, '2026-01-29',  650.00, 'Shoes'),

-- ===== February 2026 =====
(1, 1, '2026-02-03',  520.00, 'Weekly groceries'),
(1, 2, '2026-02-07',  200.00, 'Fuel'),
(1, 3, '2026-02-10',  299.00, 'Netflix + Spotify'),
(1, 1, '2026-02-14',  450.00, 'Groceries'),
(1, 6, '2026-02-18', 1200.00, 'Electricity bill'),
(1, 2, '2026-02-22',  150.00, 'Bus pass'),
(1, 4, '2026-02-26',  800.00, 'Clothes'),

-- ===== March 2026 =====
(1, 1, '2026-03-02',  600.00, 'Groceries'),
(1, 2, '2026-03-06',  210.00, 'Fuel'),
(1, 3, '2026-03-10',  299.00, 'Subscriptions'),
(1, 5, '2026-03-14',  500.00, 'Doctor visit'),
(1, 6, '2026-03-18', 1100.00, 'Electricity bill'),
(1, 1, '2026-03-22',  480.00, 'Groceries'),
(1, 4, '2026-03-28', 1200.00, 'Shoes + Bag'),

-- ===== April 2026 =====
(1, 1, '2026-04-01',  550.00, 'Groceries'),
(1, 2, '2026-04-05',  190.00, 'Fuel'),
(1, 3, '2026-04-09',  299.00, 'Subscriptions'),
(1, 6, '2026-04-13', 1300.00, 'Electricity bill'),
(1, 1, '2026-04-17',  420.00, 'Groceries'),
(1, 5, '2026-04-21',  800.00, 'Medical checkup'),
(1, 4, '2026-04-25',  650.00, 'Shopping');
