USE tpcdi;

-- Phase 8: Populate DimTrade

-- Ensure the target is empty before loading.
-- The Python phase runner will handle replacement checks.

-- 1. Find the relevant creation event.
DROP TEMPORARY TABLE IF EXISTS tmp_trade_create;

CREATE TEMPORARY TABLE tmp_trade_create
ENGINE=InnoDB
AS
SELECT
    h.TH_T_ID,
    STR_TO_DATE(h.TH_DTS, '%Y-%m-%d %H:%i:%s') AS CreateDTS
FROM tpcdi_stage.stg_trade_history h
JOIN tpcdi_stage.stg_trade t
    ON t.T_ID = h.TH_T_ID
WHERE
    (h.TH_ST_ID = 'SBMT' AND t.T_TT_ID IN ('TMB', 'TMS'))
    OR h.TH_ST_ID = 'PNDG';

ALTER TABLE tmp_trade_create
    ADD PRIMARY KEY (TH_T_ID);

-- 2. Find the relevant closing event.
DROP TEMPORARY TABLE IF EXISTS tmp_trade_close;

CREATE TEMPORARY TABLE tmp_trade_close
ENGINE=InnoDB
AS
SELECT
    TH_T_ID,
    STR_TO_DATE(TH_DTS, '%Y-%m-%d %H:%i:%s') AS CloseDTS
FROM tpcdi_stage.stg_trade_history
WHERE TH_ST_ID IN ('CMPT', 'CNCL');

ALTER TABLE tmp_trade_close
    ADD PRIMARY KEY (TH_T_ID);

-- 3. Populate DimTrade.
INSERT INTO tpcdi.DimTrade (
    TradeID,
    SK_BrokerID,
    SK_CreateDateID,
    SK_CreateTimeID,
    SK_CloseDateID,
    SK_CloseTimeID,
    Status,
    Type,
    CashFlag,
    SK_SecurityID,
    SK_CompanyID,
    Quantity,
    BidPrice,
    SK_CustomerID,
    SK_AccountID,
    ExecutedBy,
    TradePrice,
    Fee,
    Commission,
    Tax,
    BatchID
)
SELECT
    CAST(t.T_ID AS UNSIGNED),
    da.SK_BrokerID,
    dc.SK_DateID,
    tc_time.SK_TimeID,
    dclose.SK_DateID,
    close_time.SK_TimeID,
    st.ST_NAME,
    tt.TT_NAME,
    CAST(t.T_IS_CASH AS UNSIGNED),
    ds.SK_SecurityID,
    ds.SK_CompanyID,
    CAST(t.T_QTY AS UNSIGNED),
    CAST(t.T_BID_PRICE AS DECIMAL(8,2)),
    da.SK_CustomerID,
    da.SK_AccountID,
    t.T_EXEC_NAME,
    CAST(NULLIF(t.T_TRADE_PRICE, '') AS DECIMAL(8,2)),
    CAST(NULLIF(t.T_CHRG, '') AS DECIMAL(10,2)),
    CAST(NULLIF(t.T_COMM, '') AS DECIMAL(10,2)),
    CAST(NULLIF(t.T_TAX, '') AS DECIMAL(10,2)),
    1
FROM tpcdi_stage.stg_trade t
JOIN tmp_trade_create tc
    ON tc.TH_T_ID = t.T_ID
JOIN tpcdi.StatusType st
    ON st.ST_ID = t.T_ST_ID
JOIN tpcdi.TradeType tt
    ON tt.TT_ID = t.T_TT_ID
JOIN tpcdi.DimAccount da
    ON da.AccountID = CAST(t.T_CA_ID AS UNSIGNED)
   AND DATE(tc.CreateDTS) >= da.EffectiveDate
   AND DATE(tc.CreateDTS) < da.EndDate
JOIN tpcdi.DimSecurity ds
    ON ds.Symbol = t.T_S_SYMB
   AND DATE(tc.CreateDTS) >= ds.EffectiveDate
   AND DATE(tc.CreateDTS) < ds.EndDate
JOIN tpcdi.DimDate dc
    ON dc.DateValue = DATE(tc.CreateDTS)
JOIN tpcdi.DimTime tc_time
    ON tc_time.TimeValue = TIME(tc.CreateDTS)
LEFT JOIN tmp_trade_close tclose
    ON tclose.TH_T_ID = t.T_ID
LEFT JOIN tpcdi.DimDate dclose
    ON dclose.DateValue = DATE(tclose.CloseDTS)
LEFT JOIN tpcdi.DimTime close_time
    ON close_time.TimeValue = TIME(tclose.CloseDTS);

-- 4. Report inserted row count.
SELECT COUNT(*) AS DimTradeRows
FROM tpcdi.DimTrade;
