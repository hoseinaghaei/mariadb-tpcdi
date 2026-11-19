-- Phase 7, step b: DimCustomer.
--
-- Clause 4.5.4.  Source: tpcdi_stage.stg_cmg_customer, one row per action in
-- document order.  The rules, with their clause references and the traps, are
-- written up in report/dimcustomer-rules.md; this file is the implementation.
--
-- Runs AFTER 08_prospect.sql, which it reads, and BEFORE 10_dimaccount.sql,
-- which reads it.
--
-- THE ORDER OF OPERATIONS MATTERS
--
-- The forward-fill runs over every action first, and only then are same-day
-- actions collapsed.  The other way round would discard a field supplied by
-- the earlier of two actions on one day.  Clause 4.4.1.5.1 says to
-- "accumulate the changes ... on a daily basis" and 4.4.1.5.2 that "if a
-- field changes more than once per day, the latest value is used":
-- accumulate, then take the last -- not take the last and lose the rest.
--
-- __BATCHDATE__ is substituted by the loader.

INSERT INTO DimCustomer
    (SK_CustomerID, CustomerID, TaxID, Status, LastName, FirstName,
     MiddleInitial, Gender, Tier, DOB, AddressLine1, AddressLine2, PostalCode,
     City, StateProv, Country, Phone1, Phone2, Phone3, Email1, Email2,
     NationalTaxRateDesc, NationalTaxRate, LocalTaxRateDesc, LocalTaxRate,
     AgencyID, CreditRating, NetWorth, MarketingNameplate,
     IsCurrent, BatchID, EffectiveDate, EndDate)
WITH
-- Only three of the six action types touch this table (Clause 4.5.4.1).
-- Status rides the same forward-fill as everything else: NULL on UPDCUST
-- means "carry", which is exactly the rule.
src AS (
    SELECT seq, C_ID, DATE(ActionTS) AS EffDate,
           CASE ActionType WHEN 'NEW'   THEN 'ACTIVE'
                           WHEN 'INACT' THEN 'INACTIVE' END AS Status,
           C_TAX_ID, C_GNDR, C_TIER, C_DOB, C_L_NAME, C_F_NAME, C_M_NAME,
           C_ADLINE1, C_ADLINE2, C_ZIPCODE, C_CITY, C_STATE_PROV, C_CTRY,
           C_PRIM_EMAIL, C_ALT_EMAIL, C_LCL_TX_ID, C_NAT_TX_ID,
           C_PHONE_1_C_CTRY_CODE AS P1c, C_PHONE_1_C_AREA_CODE AS P1a,
           C_PHONE_1_C_LOCAL     AS P1l, C_PHONE_1_C_EXT       AS P1e,
           C_PHONE_2_C_CTRY_CODE AS P2c, C_PHONE_2_C_AREA_CODE AS P2a,
           C_PHONE_2_C_LOCAL     AS P2l, C_PHONE_2_C_EXT       AS P2e,
           C_PHONE_3_C_CTRY_CODE AS P3c, C_PHONE_3_C_AREA_CODE AS P3a,
           C_PHONE_3_C_LOCAL     AS P3l, C_PHONE_3_C_EXT       AS P3e
    FROM tpcdi_stage.stg_cmg_customer
    WHERE ActionType IN ('NEW', 'UPDCUST', 'INACT')
),
-- Gaps and islands, step 1.  A running count of non-NULL values numbers the
-- islands; NULL means the field was absent from that action.  MariaDB has no
-- IGNORE NULLS, so this is how a forward-fill is spelled.
grp AS (
    SELECT s.*,
      COUNT(Status)  OVER w AS g_Status, COUNT(C_TAX_ID)     OVER w AS g_TaxID,
      COUNT(C_GNDR)  OVER w AS g_Gndr,   COUNT(C_TIER)       OVER w AS g_Tier,
      COUNT(C_DOB)   OVER w AS g_Dob,    COUNT(C_L_NAME)     OVER w AS g_LName,
      COUNT(C_F_NAME)OVER w AS g_FName,  COUNT(C_M_NAME)     OVER w AS g_MName,
      COUNT(C_ADLINE1)   OVER w AS g_Ad1, COUNT(C_ADLINE2)   OVER w AS g_Ad2,
      COUNT(C_ZIPCODE)   OVER w AS g_Zip, COUNT(C_CITY)      OVER w AS g_City,
      COUNT(C_STATE_PROV)OVER w AS g_St,  COUNT(C_CTRY)      OVER w AS g_Ctry,
      COUNT(C_PRIM_EMAIL)OVER w AS g_Em1, COUNT(C_ALT_EMAIL) OVER w AS g_Em2,
      COUNT(C_LCL_TX_ID) OVER w AS g_Lcl, COUNT(C_NAT_TX_ID) OVER w AS g_Nat,
      COUNT(P1c) OVER w AS g_P1c, COUNT(P1a) OVER w AS g_P1a,
      COUNT(P1l) OVER w AS g_P1l, COUNT(P1e) OVER w AS g_P1e,
      COUNT(P2c) OVER w AS g_P2c, COUNT(P2a) OVER w AS g_P2a,
      COUNT(P2l) OVER w AS g_P2l, COUNT(P2e) OVER w AS g_P2e,
      COUNT(P3c) OVER w AS g_P3c, COUNT(P3a) OVER w AS g_P3a,
      COUNT(P3l) OVER w AS g_P3l, COUNT(P3e) OVER w AS g_P3e
    FROM src s
    WINDOW w AS (PARTITION BY C_ID ORDER BY seq)
),
-- Step 2.  Inside an island there is exactly one non-NULL, so MAX() finds it.
-- NULLIF(...,'') then turns a supplied-but-empty <Element/> into SQL NULL --
-- the other half of Clause 4.5.4.2.
fill AS (
    SELECT seq, C_ID, EffDate,
      MAX(Status) OVER (PARTITION BY C_ID, g_Status) AS Status,
      NULLIF(MAX(C_TAX_ID)    OVER (PARTITION BY C_ID, g_TaxID),'') AS TaxID,
      NULLIF(MAX(C_GNDR)      OVER (PARTITION BY C_ID, g_Gndr), '') AS Gndr,
      NULLIF(MAX(C_TIER)      OVER (PARTITION BY C_ID, g_Tier), '') AS Tier,
      NULLIF(MAX(C_DOB)       OVER (PARTITION BY C_ID, g_Dob),  '') AS DOB,
      NULLIF(MAX(C_L_NAME)    OVER (PARTITION BY C_ID, g_LName),'') AS LastName,
      NULLIF(MAX(C_F_NAME)    OVER (PARTITION BY C_ID, g_FName),'') AS FirstName,
      NULLIF(MAX(C_M_NAME)    OVER (PARTITION BY C_ID, g_MName),'') AS MiddleInitial,
      NULLIF(MAX(C_ADLINE1)   OVER (PARTITION BY C_ID, g_Ad1),  '') AS AddressLine1,
      NULLIF(MAX(C_ADLINE2)   OVER (PARTITION BY C_ID, g_Ad2),  '') AS AddressLine2,
      NULLIF(MAX(C_ZIPCODE)   OVER (PARTITION BY C_ID, g_Zip),  '') AS PostalCode,
      NULLIF(MAX(C_CITY)      OVER (PARTITION BY C_ID, g_City), '') AS City,
      NULLIF(MAX(C_STATE_PROV)OVER (PARTITION BY C_ID, g_St),   '') AS StateProv,
      NULLIF(MAX(C_CTRY)      OVER (PARTITION BY C_ID, g_Ctry), '') AS Country,
      NULLIF(MAX(C_PRIM_EMAIL)OVER (PARTITION BY C_ID, g_Em1),  '') AS Email1,
      NULLIF(MAX(C_ALT_EMAIL) OVER (PARTITION BY C_ID, g_Em2),  '') AS Email2,
      NULLIF(MAX(C_LCL_TX_ID) OVER (PARTITION BY C_ID, g_Lcl),  '') AS LclTx,
      NULLIF(MAX(C_NAT_TX_ID) OVER (PARTITION BY C_ID, g_Nat),  '') AS NatTx,
      NULLIF(MAX(P1c) OVER (PARTITION BY C_ID, g_P1c),'') AS P1c,
      NULLIF(MAX(P1a) OVER (PARTITION BY C_ID, g_P1a),'') AS P1a,
      NULLIF(MAX(P1l) OVER (PARTITION BY C_ID, g_P1l),'') AS P1l,
      NULLIF(MAX(P1e) OVER (PARTITION BY C_ID, g_P1e),'') AS P1e,
      NULLIF(MAX(P2c) OVER (PARTITION BY C_ID, g_P2c),'') AS P2c,
      NULLIF(MAX(P2a) OVER (PARTITION BY C_ID, g_P2a),'') AS P2a,
      NULLIF(MAX(P2l) OVER (PARTITION BY C_ID, g_P2l),'') AS P2l,
      NULLIF(MAX(P2e) OVER (PARTITION BY C_ID, g_P2e),'') AS P2e,
      NULLIF(MAX(P3c) OVER (PARTITION BY C_ID, g_P3c),'') AS P3c,
      NULLIF(MAX(P3a) OVER (PARTITION BY C_ID, g_P3a),'') AS P3a,
      NULLIF(MAX(P3l) OVER (PARTITION BY C_ID, g_P3l),'') AS P3l,
      NULLIF(MAX(P3e) OVER (PARTITION BY C_ID, g_P3e),'') AS P3e
    FROM grp
),
-- Daily grain, Clause 4.4.1.5: one row per customer per day, the last by seq.
-- After the fill, so a field supplied by the earlier of two same-day actions
-- is not lost.
daily AS (
    SELECT f.*, ROW_NUMBER() OVER (PARTITION BY C_ID, EffDate
                                   ORDER BY seq DESC) AS rn
    FROM fill f
),
-- Clause 4.4.1.2: a version runs until the next one for that key begins.
dated AS (
    SELECT d.*,
           LEAD(d.EffDate) OVER (PARTITION BY d.C_ID ORDER BY d.EffDate) AS NextDate
    FROM daily d WHERE d.rn = 1
)
SELECT
    ROW_NUMBER() OVER (ORDER BY CAST(d.C_ID AS UNSIGNED), d.EffDate),
    CAST(d.C_ID AS UNSIGNED),
    d.TaxID,
    d.Status,
    d.LastName, d.FirstName, d.MiddleInitial,
    -- Clause 4.5.4.3: uppercased, and anything but M or F becomes 'U'.
    -- 'U', not NULL.
    CASE WHEN UPPER(d.Gndr) IN ('M','F') THEN UPPER(d.Gndr) ELSE 'U' END,
    CAST(d.Tier AS UNSIGNED),
    STR_TO_DATE(d.DOB, '%Y-%m-%d'),
    d.AddressLine1, d.AddressLine2, d.PostalCode, d.City, d.StateProv, d.Country,
    -- Clause 4.5.4.3, four cases in precedence order.  The third ignores the
    -- country code entirely; the extension is appended with no separator.
    CASE WHEN d.P1c IS NOT NULL AND d.P1a IS NOT NULL AND d.P1l IS NOT NULL
              THEN CONCAT('+', d.P1c, ' (', d.P1a, ') ', d.P1l, COALESCE(d.P1e,''))
         WHEN d.P1a IS NOT NULL AND d.P1l IS NOT NULL
              THEN CONCAT('(', d.P1a, ') ', d.P1l, COALESCE(d.P1e,''))
         WHEN d.P1l IS NOT NULL
              THEN CONCAT(d.P1l, COALESCE(d.P1e,''))
    END,
    CASE WHEN d.P2c IS NOT NULL AND d.P2a IS NOT NULL AND d.P2l IS NOT NULL
              THEN CONCAT('+', d.P2c, ' (', d.P2a, ') ', d.P2l, COALESCE(d.P2e,''))
         WHEN d.P2a IS NOT NULL AND d.P2l IS NOT NULL
              THEN CONCAT('(', d.P2a, ') ', d.P2l, COALESCE(d.P2e,''))
         WHEN d.P2l IS NOT NULL
              THEN CONCAT(d.P2l, COALESCE(d.P2e,''))
    END,
    CASE WHEN d.P3c IS NOT NULL AND d.P3a IS NOT NULL AND d.P3l IS NOT NULL
              THEN CONCAT('+', d.P3c, ' (', d.P3a, ') ', d.P3l, COALESCE(d.P3e,''))
         WHEN d.P3a IS NOT NULL AND d.P3l IS NOT NULL
              THEN CONCAT('(', d.P3a, ') ', d.P3l, COALESCE(d.P3e,''))
         WHEN d.P3l IS NOT NULL
              THEN CONCAT(d.P3l, COALESCE(d.P3e,''))
    END,
    d.Email1, d.Email2,
    nat.TX_NAME, nat.TX_RATE,     -- Clause 4.5.4.3, the match is guaranteed
    lcl.TX_NAME, lcl.TX_RATE,
    -- Clause 4.5.4.3.  Demographics attach only where there is no newer
    -- UPDCUST or INACT for this customer -- which, since those are two of
    -- the three actions that produce a row here, is exactly the current one.
    -- Older versions get NULL, and so does a current version with no
    -- matching prospect: the "leave unchanged" case on UPDCUST and INACT
    -- would be carrying a NULL forward anyway.
    CASE WHEN d.NextDate IS NULL THEN pr.AgencyID END,
    CASE WHEN d.NextDate IS NULL THEN pr.CreditRating END,
    CASE WHEN d.NextDate IS NULL THEN pr.NetWorth END,
    CASE WHEN d.NextDate IS NULL THEN pr.MarketingNameplate END,
    CASE WHEN d.NextDate IS NULL THEN 1 ELSE 0 END,
    1,
    d.EffDate,
    COALESCE(d.NextDate, '9999-12-31')
FROM dated d
LEFT JOIN TaxRate nat ON nat.TX_ID = d.NatTx
LEFT JOIN TaxRate lcl ON lcl.TX_ID = d.LclTx
-- The match is on five fields, compared case-insensitively (Clause 4.5.4.3).
--
-- Written with <=> rather than UPPER(COALESCE(...)) for two reasons.  The
-- columns collate as utf8mb4_uca1400_ai_ci, so = is already case- and
-- accent-insensitive and UPPER() buys nothing.  And both UPPER() and
-- COALESCE() make the predicate non-sargable: with them the optimiser chose
-- a block-nested loop over 10,388 x 24,160 rows and this step took 129
-- seconds.  <=> is NULL-safe, which AddressLine2 needs on both sides, and
-- indexable -- it becomes a ref lookup on ix_prospect_match, 0.14 seconds.
LEFT JOIN Prospect pr
       ON pr.LastName     <=> d.LastName
      AND pr.FirstName    <=> d.FirstName
      AND pr.AddressLine1 <=> d.AddressLine1
      AND pr.AddressLine2 <=> d.AddressLine2
      AND pr.PostalCode   <=> d.PostalCode
      AND d.NextDate IS NULL;

-- The two alerts, Clause 4.5.4.4 and 4.5.4.5.  Raised from the staged actions
-- rather than from DimCustomer: an invalid tier that a later action
-- overwrites still happened, and the offending value is exactly what did not
-- survive into the warehouse.

-- Clause 4.5.4.4.  Expect Audit.C_TIER_INV = 380 at SF=5.
INSERT INTO DImessages
    (MessageDateAndTime, BatchID, MessageSource, MessageText, MessageType,
     MessageData)
SELECT CURRENT_TIMESTAMP(3), 1, 'DimCustomer', 'Invalid customer tier', 'Alert',
       CONCAT('C_ID = ', C_ID, ', C_TIER = ', COALESCE(C_TIER, ''))
FROM tpcdi_stage.stg_cmg_customer
-- NEW only, and an absent tier counts.  Clause 4.5.4.2: on a new customer a
-- missing or empty field "should be processed as a NULL value", and NULL is
-- not one of the valid values, so it alerts.  On UPDCUST an absent tier means
-- unchanged, which is not an invalid tier and does not.
--
-- The arithmetic settles it: at SF=5 that is 377 absent plus 3 bad values =
-- 380, which is exactly Audit.C_TIER_INV.  Including UPDCUST gave 89.
WHERE ActionType = 'NEW'
  AND (C_TIER IS NULL OR C_TIER NOT IN ('1', '2', '3'));

-- Clause 4.5.4.5.  DOB < BatchDate - 100 years, or DOB > BatchDate.
-- Batch1/BatchDate.txt is 2017-07-07.  Expect C_DOB_TO = 10 and
-- C_DOB_TY = 16, so 26 rows at SF=5.
INSERT INTO DImessages
    (MessageDateAndTime, BatchID, MessageSource, MessageText, MessageType,
     MessageData)
SELECT CURRENT_TIMESTAMP(3), 1, 'DimCustomer', 'DOB out of range', 'Alert',
       CONCAT('C_ID = ', C_ID, ', C_DOB = ', C_DOB)
FROM tpcdi_stage.stg_cmg_customer
WHERE ActionType IN ('NEW', 'UPDCUST')
  AND C_DOB IS NOT NULL AND C_DOB <> ''
  AND (STR_TO_DATE(C_DOB, '%Y-%m-%d') < DATE_SUB('__BATCHDATE__', INTERVAL 100 YEAR)
    OR STR_TO_DATE(C_DOB, '%Y-%m-%d') > '__BATCHDATE__');

-- DimAccount resolves SK_CustomerID as of each event's date, 26,570 times.
-- Without this that is a scan of DimCustomer per event.
CREATE INDEX IF NOT EXISTS ix_dimcustomer_asof
    ON DimCustomer (CustomerID, EffectiveDate, EndDate);

-- END OF PIPELINE SQL
-- Nothing below this line is executed.  Ad-hoc queries are welcome here;
-- remember to qualify names with tpcdi_stage. or tpcdi. as appropriate.
