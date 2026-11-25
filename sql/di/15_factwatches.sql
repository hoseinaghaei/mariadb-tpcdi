USE tpcdi;

-- Phase 8: Populate FactWatches

-- 1. Collect cancellation dates by customer and security.
DROP TEMPORARY TABLE IF EXISTS tmp_watch_cancellations;

CREATE TEMPORARY TABLE tmp_watch_cancellations
ENGINE=InnoDB
AS
SELECT
    CAST(W_C_ID AS UNSIGNED) AS CustomerID,
    W_S_SYMB AS Symbol,
    DATE(STR_TO_DATE(W_DTS, '%Y-%m-%d %H:%i:%s')) AS CancelDate
FROM tpcdi_stage.stg_watch_history
WHERE W_ACTION = 'CNCL';

ALTER TABLE tmp_watch_cancellations
    ADD INDEX idx_watch_cancel_pair (CustomerID, Symbol);

-- 2. Resolve customer and security keys at activation time.
DROP TEMPORARY TABLE IF EXISTS tmp_watch_activations;

CREATE TEMPORARY TABLE tmp_watch_activations
ENGINE=InnoDB
AS
SELECT
    CustomerID,
    Symbol,
    SK_CustomerID,
    SK_SecurityID,
    SK_DateID_DatePlaced
FROM (
    SELECT
        CAST(w.W_C_ID AS UNSIGNED) AS CustomerID,
        w.W_S_SYMB AS Symbol,
        c.SK_CustomerID,
        s.SK_SecurityID,
        d.SK_DateID AS SK_DateID_DatePlaced,
        ROW_NUMBER() OVER (
            PARTITION BY w.W_SEQ
            ORDER BY
                c.EffectiveDate DESC,
                c.SK_CustomerID DESC,
                s.EffectiveDate DESC,
                s.SK_SecurityID DESC
        ) AS rn
    FROM tpcdi_stage.stg_watch_history w
    JOIN tpcdi.DimCustomer c
        ON c.CustomerID = CAST(w.W_C_ID AS UNSIGNED)
       AND DATE(STR_TO_DATE(w.W_DTS, '%Y-%m-%d %H:%i:%s'))
           BETWEEN c.EffectiveDate AND c.EndDate
    JOIN tpcdi.DimSecurity s
        ON s.Symbol = w.W_S_SYMB
       AND DATE(STR_TO_DATE(w.W_DTS, '%Y-%m-%d %H:%i:%s'))
           BETWEEN s.EffectiveDate AND s.EndDate
    JOIN tpcdi.DimDate d
        ON d.DateValue =
           DATE(STR_TO_DATE(w.W_DTS, '%Y-%m-%d %H:%i:%s'))
    WHERE w.W_ACTION = 'ACTV'
) ranked
WHERE rn = 1;

-- 3. Insert one fact row per activation.
-- Cancellations update the removal date without changing original keys.
INSERT INTO tpcdi.FactWatches (
    SK_CustomerID,
    SK_SecurityID,
    SK_DateID_DatePlaced,
    SK_DateID_DateRemoved,
    BatchID
)
SELECT
    a.SK_CustomerID,
    a.SK_SecurityID,
    a.SK_DateID_DatePlaced,
    d.SK_DateID,
    1
FROM tmp_watch_activations a
LEFT JOIN tmp_watch_cancellations c
    ON c.CustomerID = a.CustomerID
   AND c.Symbol = a.Symbol
LEFT JOIN tpcdi.DimDate d
    ON d.DateValue = c.CancelDate;

-- 4. Report results.
SELECT
    COUNT(*) AS FactWatchesRows,
    SUM(CASE WHEN SK_DateID_DateRemoved IS NULL THEN 1 ELSE 0 END)
        AS ActiveWatches,
    SUM(CASE WHEN SK_DateID_DateRemoved IS NOT NULL THEN 1 ELSE 0 END)
        AS CancelledWatches
FROM tpcdi.FactWatches;
