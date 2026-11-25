USE tpcdi;

-- Phase 8: Populate FactHoldings

-- 1. Insert holding history records using their corresponding trades.
INSERT INTO tpcdi.FactHoldings (
    TradeID,
    CurrentTradeID,
    SK_CustomerID,
    SK_AccountID,
    SK_SecurityID,
    SK_CompanyID,
    SK_DateID,
    SK_TimeID,
    CurrentPrice,
    CurrentHolding,
    BatchID
)
SELECT
    CAST(h.HH_H_T_ID AS UNSIGNED),
    t.TradeID,
    t.SK_CustomerID,
    t.SK_AccountID,
    t.SK_SecurityID,
    t.SK_CompanyID,
    t.SK_CloseDateID,
    t.SK_CloseTimeID,
    t.TradePrice,
    CAST(h.HH_AFTER_QTY AS SIGNED),
    1
FROM tpcdi_stage.stg_holding_history h
JOIN tpcdi.DimTrade t
    ON t.TradeID = CAST(h.HH_T_ID AS UNSIGNED);

-- 2. Report inserted rows.
SELECT
    COUNT(*) AS FactHoldingsRows,
    COUNT(DISTINCT CurrentTradeID) AS DistinctCurrentTrades,
    COUNT(DISTINCT TradeID) AS DistinctHoldingTrades
FROM tpcdi.FactHoldings;
