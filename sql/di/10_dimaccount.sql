-- Phase 7, step c: DimAccount.
--
-- Clause 4.5.1.  Source: tpcdi_stage.stg_cmg_account_event, which is the
-- explicit <Account> events unioned with the UPDCUST and INACT cascade --
-- see 07_customer_events.sql for why that union is built before any dating.
--
-- Runs AFTER 09_dimcustomer.sql: Clause 4.5.1.3 says that on UPDCUST,
-- SK_CustomerID is set to "the associated customer's DimCustomer current
-- record after it has been updated."
--
-- WHY THERE IS NO INTERVAL SPLIT HERE, UNLIKE DimSecurity
--
-- Clause 4.4.1.6.1 puts DimAccount/DimCustomer and DimSecurity/DimCompany
-- under the same rule, and the audit tests both the same way: the account's
-- whole range must sit inside its customer row's range.
--
-- In Phase 6 that needed each security interval intersected with the company
-- intervals, because nothing in the SEC records marked a company change.
-- Here the cascade has already done it: every DimCustomer version boundary
-- comes from a NEW, UPDCUST or INACT action, and all three produce an
-- account event -- NEW explicitly, the other two through the cascade.  So
-- the boundaries are in the event stream and the dating picks them up.

INSERT INTO DimAccount
    (SK_AccountID, AccountID, SK_BrokerID, SK_CustomerID, Status, AccountDesc,
     TaxStatus, IsCurrent, BatchID, EffectiveDate, EndDate)
WITH
-- Status is folded in as a pseudo-field so it rides the same forward-fill:
-- NULL on UPDACCT and UPDCUST means "carry", which is the rule.
-- Clause 4.5.1.3: ACTIVE on NEW and ADDACCT, INACTIVE on CLOSEACCT and INACT.
src AS (
    SELECT seq, CA_ID, C_ID, DATE(ActionTS) AS EffDate,
           CASE ActionType WHEN 'NEW'       THEN 'ACTIVE'
                           WHEN 'ADDACCT'   THEN 'ACTIVE'
                           WHEN 'CLOSEACCT' THEN 'INACTIVE'
                           WHEN 'INACT'     THEN 'INACTIVE' END AS Status,
           CA_TAX_ST, CA_B_ID, CA_NAME
    FROM tpcdi_stage.stg_cmg_account_event
),
-- Gaps and islands, as in 09_dimcustomer.sql.  A cascade row carries NULL
-- for all three account fields, because the action said nothing about them;
-- that is the same "absent means unchanged" rule one level down.
grp AS (
    SELECT s.*,
      COUNT(Status)    OVER w AS g_Status,
      COUNT(CA_TAX_ST) OVER w AS g_Tax,
      COUNT(CA_B_ID)   OVER w AS g_Broker,
      COUNT(CA_NAME)   OVER w AS g_Name
    FROM src s
    WINDOW w AS (PARTITION BY CA_ID ORDER BY seq)
),
fill AS (
    SELECT seq, CA_ID, C_ID, EffDate,
      MAX(Status) OVER (PARTITION BY CA_ID, g_Status) AS Status,
      NULLIF(MAX(CA_TAX_ST) OVER (PARTITION BY CA_ID, g_Tax),    '') AS TaxStatus,
      NULLIF(MAX(CA_B_ID)   OVER (PARTITION BY CA_ID, g_Broker), '') AS BrokerID,
      NULLIF(MAX(CA_NAME)   OVER (PARTITION BY CA_ID, g_Name),   '') AS AccountDesc
    FROM grp
),
-- Daily grain, Clause 4.4.1.5: one row per account per day, the last by seq.
-- After the fill, so a field supplied by the earlier of two same-day events
-- is not lost.
daily AS (
    SELECT f.*, ROW_NUMBER() OVER (PARTITION BY CA_ID, EffDate
                                   ORDER BY seq DESC) AS rn
    FROM fill f
),
dated AS (
    SELECT d.*,
           LEAD(d.EffDate) OVER (PARTITION BY d.CA_ID ORDER BY d.EffDate) AS NextDate
    FROM daily d WHERE d.rn = 1
)
SELECT
    ROW_NUMBER() OVER (ORDER BY CAST(d.CA_ID AS UNSIGNED), d.EffDate),
    CAST(d.CA_ID AS UNSIGNED),
    -- Clause 4.5.1.3: both matches are guaranteed to succeed.  DimBroker is
    -- not history-tracked in practice (Clause 4.4.1.6.1 note), so a plain
    -- natural-key lookup is enough there.
    br.SK_BrokerID,
    -- The customer version current on the event's date.  Half-open, matching
    -- every other range in this schema; Clause 4.5.1.3 writes it with <= on
    -- EndDate, which would match two rows on a changeover day.
    cu.SK_CustomerID,
    d.Status,
    d.AccountDesc,
    CAST(d.TaxStatus AS UNSIGNED),
    CASE WHEN d.NextDate IS NULL THEN 1 ELSE 0 END,
    1,
    d.EffDate,
    COALESCE(d.NextDate, '9999-12-31')
FROM dated d
LEFT JOIN DimBroker br
       ON br.BrokerID = CAST(d.BrokerID AS UNSIGNED)
LEFT JOIN DimCustomer cu
       ON cu.CustomerID    = CAST(d.C_ID AS UNSIGNED)
      AND cu.EffectiveDate <= d.EffDate
      AND cu.EndDate       >  d.EffDate;

-- END OF PIPELINE SQL
-- Nothing below this line is executed.  Ad-hoc queries are welcome here.
