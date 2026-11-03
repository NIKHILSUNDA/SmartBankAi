CREATE DATABASE IF NOT EXISTS smartbank_analytics CHARACTER SET utf8mb4;
USE smartbank_analytics;

CREATE TABLE IF NOT EXISTS customers (
  customer_id VARCHAR(16) PRIMARY KEY,               -- anonymized id, no PII stored
  customer_segment ENUM('RETAIL','PREMIUM','BUSINESS') NOT NULL,
  home_country CHAR(2) NOT NULL,
  account_created_at DATETIME NOT NULL
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS accounts (
  account_id VARCHAR(16) PRIMARY KEY,
  customer_id VARCHAR(16) NOT NULL,
  account_type ENUM('SAVINGS','CURRENT','CREDIT') NOT NULL,
  account_status ENUM('ACTIVE','FROZEN','CLOSED') NOT NULL DEFAULT 'ACTIVE',
  created_at DATETIME NOT NULL DEFAULT (UTC_TIMESTAMP()),
  FOREIGN KEY (customer_id) REFERENCES customers(customer_id),
  INDEX idx_acc_cust (customer_id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS transactions (
  transaction_id BIGINT AUTO_INCREMENT PRIMARY KEY,
  account_id VARCHAR(16) NOT NULL,
  transaction_timestamp DATETIME NOT NULL,            -- stored in UTC
  amount DECIMAL(14,2) NOT NULL CHECK (amount > 0),
  currency CHAR(3) NOT NULL DEFAULT 'USD',
  transaction_type ENUM('PAYMENT','TRANSFER','CASH_OUT','CASH_IN','DEBIT') NOT NULL,
  merchant_category VARCHAR(40) NULL,
  origin_country CHAR(2) NULL,
  destination_country CHAR(2) NULL,
  origin_latitude DECIMAL(8,5) NULL CHECK (origin_latitude BETWEEN -90 AND 90),
  origin_longitude DECIMAL(8,5) NULL CHECK (origin_longitude BETWEEN -180 AND 180),
  destination_latitude DECIMAL(8,5) NULL CHECK (destination_latitude BETWEEN -90 AND 90),
  destination_longitude DECIMAL(8,5) NULL CHECK (destination_longitude BETWEEN -180 AND 180),
  is_synthetic TINYINT(1) NOT NULL DEFAULT 1,
  FOREIGN KEY (account_id) REFERENCES accounts(account_id),
  INDEX idx_tx_ts (transaction_timestamp),
  INDEX idx_tx_acct_ts (account_id, transaction_timestamp),
  INDEX idx_tx_type (transaction_type)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS transaction_predictions (
  prediction_id BIGINT AUTO_INCREMENT PRIMARY KEY,
  transaction_id BIGINT NOT NULL UNIQUE,              -- latest prediction per transaction
  risk_score DECIMAL(5,2) NOT NULL CHECK (risk_score BETWEEN 0 AND 100),
  risk_category ENUM('LOW','MEDIUM','HIGH','CRITICAL') NOT NULL,
  model_prediction TINYINT(1) NOT NULL,               -- 1 = flagged for review (NOT confirmed fraud)
  model_version VARCHAR(40) NOT NULL,
  explanation JSON NULL,
  predicted_at DATETIME NOT NULL,
  FOREIGN KEY (transaction_id) REFERENCES transactions(transaction_id) ON DELETE CASCADE,
  INDEX idx_pred_cat (risk_category),
  INDEX idx_pred_score (risk_score)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS investigation_cases (
  case_id BIGINT AUTO_INCREMENT PRIMARY KEY,
  transaction_id BIGINT NOT NULL UNIQUE,
  case_status ENUM('NEW','UNDER_REVIEW','ESCALATED','RESOLVED','FALSE_POSITIVE') NOT NULL DEFAULT 'NEW',
  assigned_analyst VARCHAR(60) NULL,
  investigation_notes TEXT NULL,
  created_at DATETIME NOT NULL DEFAULT (UTC_TIMESTAMP()),
  updated_at DATETIME NOT NULL DEFAULT (UTC_TIMESTAMP()),
  FOREIGN KEY (transaction_id) REFERENCES transactions(transaction_id) ON DELETE CASCADE,
  INDEX idx_case_status (case_status)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS audit_logs (
  audit_id BIGINT AUTO_INCREMENT PRIMARY KEY,
  case_id BIGINT NOT NULL,
  action VARCHAR(30) NOT NULL,
  previous_value TEXT NULL,
  new_value TEXT NULL,
  changed_by VARCHAR(60) NOT NULL,
  changed_at DATETIME NOT NULL DEFAULT (UTC_TIMESTAMP()),
  FOREIGN KEY (case_id) REFERENCES investigation_cases(case_id) ON DELETE CASCADE,
  INDEX idx_audit_case (case_id)
) ENGINE=InnoDB;

CREATE OR REPLACE VIEW v_daily_summary AS
SELECT DATE(t.transaction_timestamp) AS day, COUNT(*) AS tx_count, SUM(t.amount) AS total_amount,
       SUM(p.model_prediction = 1) AS flagged, SUM(p.risk_category = 'CRITICAL') AS critical
FROM transactions t LEFT JOIN transaction_predictions p ON p.transaction_id = t.transaction_id
GROUP BY DATE(t.transaction_timestamp);
