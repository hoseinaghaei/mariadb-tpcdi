USE tpcdi;

-- Phase 8: Populate FactCashBalances

-- 1. Aggregate cash transactions by natural account and date.
DROP TEMPORARY TABLE IF EXISTS tmp_cash_daily;

CREATE TEMPORARY TABLE tmp_cash_daily
ENGINE=InnoDB
AS
SELECT
    CAST(CT_CA_ID AS UNSIGNED) AS AccountID,
    DATE(STR_TO_DATE(CT_DTS, '%Y-%m-%d %H:%i:%s')) AS CashDate,
    SUM(CAST(CT_AMT AS DECIMAL(15,2))) AS DailyAmount
FROM tpcdi_stage.stg_cash_transaction
GROUP BY
    CAST(CT_CA_ID AS UNSIGNED),
    DATE(STR_TO_DATE(CT_DTS, '%Y-%m-%d %H:%i:%s'));

ALTER TABLE tmp_cash_daily
    ADD PRIMARY KEY (AccountID, CashDate);

-- 2. Calculate the running balance across all account versions.
DROP TEMPORARY TABLE IF EXISTS tmp_cash_running;

CREATE TEMPORARY TABLE tmp_cash_running
ENGINE=InnoDB
AS
SELECT
    AccountID,
    CashDate,
    SUM(DailyAmount) OVER (
        PARTITION BY AccountID
        ORDER BY CashDate
        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
    ) AS RunningCash
FROM tmp_cash_daily;

ALTER TABLE tmp_cash_running
    ADD PRIMARY KEY (AccountID, CashDate);

-- 3. Resolve the correct account and customer surrogate keys.
DROP TEMPORARY TABLE IF EXISTS tmp_cash_resolved;

CREATE TEMPORARY TABLE tmp_cash_resolved
ENGINE=InnoDB
AS
SELECT
    AccountID,
    SK_CustomerID,
    SK_AccountID,
    SK_DateID,
    RunningCash
FROM (
    SELECT
        r.AccountID,
        da.SK_CustomerID,
        da.SK_AccountID,
        d.SK_DateID,
        r.RunningCash,
        ROW_NUMBER() OVER (
            PARTITION BY r.AccountID, r.CashDate
            ORDER BY da.EffectiveDate DESC, da.SK_AccountID DESC
        ) AS rn
    FROM tmp_cash_running r
    JOIN tpcdi.DimAccount da
        ON da.AccountID = r.AccountID
       AND r.CashDate >= da.EffectiveDate
       AND r.CashDate <= da.EndDate
    JOIN tpcdi.DimDate d
        ON d.DateValue = r.CashDate
) ranked
WHERE rn = 1;

-- 4. Insert daily running balances.
INSERT INTO tpcdi.FactCashBalances (
    SK_CustomerID,
    SK_AccountID,
    SK_DateID,
    Cash,
    BatchID
)
SELECT
    SK_CustomerID,
    SK_AccountID,
    SK_DateID,
    RunningCash,
    1
FROM tmp_cash_resolved;

-- 5. Report results.
SELECT
    COUNT(*) AS FactCashBalancesRows,
    COUNT(DISTINCT SK_AccountID) AS DistinctAccountVersions,
    SUM(Cash) AS SumOfRunningBalances
FROM tpcdi.FactCashBalances;
