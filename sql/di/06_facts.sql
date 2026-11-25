
-- ---------------------------------------------------------------------------
-- Phase 8: source staging for Trade, TradeHistory, CashTransaction,
-- HoldingHistory and WatchHistory.
--
-- Preserve source values as text. Type conversion and warehouse mapping
-- happen during the transformation phase.
--
-- Historical Batch1 files do not contain CDC_FLAG or CDC_DSN.
--
-- These tables belong in tpcdi_stage, not the 20-table tpcdi warehouse.
-- ---------------------------------------------------------------------------

CREATE DATABASE IF NOT EXISTS tpcdi_stage;

-- Trade.txt: one source record per trade.
DROP TABLE IF EXISTS tpcdi_stage.stg_trade;
CREATE TABLE tpcdi_stage.stg_trade (
    T_ID          VARCHAR(20),
    T_DTS         VARCHAR(30),
    T_ST_ID       VARCHAR(10),
    T_TT_ID       VARCHAR(10),
    T_IS_CASH     VARCHAR(10),
    T_S_SYMB      VARCHAR(30),
    T_QTY         VARCHAR(20),
    T_BID_PRICE   VARCHAR(30),
    T_CA_ID       VARCHAR(20),
    T_EXEC_NAME   VARCHAR(100),
    T_TRADE_PRICE VARCHAR(30),
    T_CHRG        VARCHAR(30),
    T_COMM        VARCHAR(30),
    T_TAX         VARCHAR(30),
    KEY ix_trade_id (T_ID),
    KEY ix_trade_account (T_CA_ID),
    KEY ix_trade_symbol (T_S_SYMB)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- TradeHistory.txt: preserve every status event in source order.
DROP TABLE IF EXISTS tpcdi_stage.stg_trade_history;
CREATE TABLE tpcdi_stage.stg_trade_history (
    TH_SEQ   BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    TH_T_ID  VARCHAR(20),
    TH_DTS   VARCHAR(30),
    TH_ST_ID VARCHAR(10),
    PRIMARY KEY (TH_SEQ),
    KEY ix_trade_history_id_time (TH_T_ID, TH_DTS)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- CashTransaction.txt: preserve the complete transaction description.
DROP TABLE IF EXISTS tpcdi_stage.stg_cash_transaction;
CREATE TABLE tpcdi_stage.stg_cash_transaction (
    CT_CA_ID VARCHAR(20),
    CT_DTS   VARCHAR(30),
    CT_AMT   VARCHAR(30),
    CT_NAME  VARCHAR(255),
    KEY ix_cash_account_time (CT_CA_ID, CT_DTS)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- HoldingHistory.txt: preserve before and after quantities.
DROP TABLE IF EXISTS tpcdi_stage.stg_holding_history;
CREATE TABLE tpcdi_stage.stg_holding_history (
    HH_H_T_ID     VARCHAR(20),
    HH_T_ID       VARCHAR(20),
    HH_BEFORE_QTY VARCHAR(20),
    HH_AFTER_QTY  VARCHAR(20),
    KEY ix_holding_current_trade (HH_T_ID),
    KEY ix_holding_original_trade (HH_H_T_ID)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- WatchHistory.txt: preserve every watch event in source order.
DROP TABLE IF EXISTS tpcdi_stage.stg_watch_history;
CREATE TABLE tpcdi_stage.stg_watch_history (
    W_SEQ    BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    W_C_ID   VARCHAR(20),
    W_S_SYMB VARCHAR(30),
    W_DTS    VARCHAR(30),
    W_ACTION VARCHAR(10),
    PRIMARY KEY (W_SEQ),
    KEY ix_watch_customer_symbol_time (W_C_ID, W_S_SYMB, W_DTS),
    KEY ix_watch_time (W_DTS)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
