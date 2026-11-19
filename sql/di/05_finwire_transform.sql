-- Phase 6, part 2: FINWIRE staging -> DimCompany, DimSecurity, Financial.
--
-- Clauses 4.5.3, 4.5.6, 4.5.14.  All set-based: the staged rows never leave
-- the server.
--
-- ORDER OF WORK IS FORCED.  DimSecurity and Financial both resolve
-- SK_CompanyID against the DimCompany row current at their own PTS, so
-- DimCompany must be complete first (Clauses 4.5.6.2 and 4.5.14.2 say so
-- explicitly, and Table 4.3 lists the dependency).
--
-- THE TWO RULES THAT SHAPE EVERY QUERY HERE
--
--   Daily grain (Clause 4.4.1.5): at most one row per natural key per day,
--   even where the source holds several changes that day; the latest wins.
--   This is not optional tidying -- the audit's "consolidation" test fails
--   any row whose EffectiveDate equals its EndDate, which is exactly what two
--   same-day changes would produce.
--
--   Unchanged records are not recorded (Clause 4.5.3.1): "CMP records may
--   have content that is unchanged from prior CMP records, except for the PTS
--   field... unchanged records are not recorded."  Hence the content hash.
--   The audit tests DimCompany's row count with <=, not =, so dropping these
--   is permitted rather than merely tolerated.

-- --------------------------------------------------------------- DimCompany
-- Clause 4.5.3.  CIK is the natural key.
INSERT INTO DimCompany
    (SK_CompanyID, CompanyID, Status, Name, Industry, SPrating, isLowGrade,
     CEO, AddressLine1, AddressLine2, PostalCode, City, StateProv, Country,
     Description, FoundingDate, IsCurrent, BatchID, EffectiveDate, EndDate)
WITH
-- One record per company per day: the last posting of that day wins.
daily AS (
    SELECT c.*,
           ROW_NUMBER() OVER (PARTITION BY c.CIK, DATE(c.PTS)
                              ORDER BY c.PTS DESC) AS rn
    FROM tpcdi_stage.stg_fw_cmp c
),
-- Resolve the coded fields and apply Clause 4.5.3.2/4.5.3.3 before comparing
-- records, so that two postings differing only in an invalid rating that both
-- become NULL count as unchanged.
resolved AS (
    SELECT
        d.CIK,
        DATE(d.PTS) AS EffDate,
        d.CompanyName,
        st.ST_NAME  AS StatusName,
        ind.IN_NAME AS IndustryName,
        -- Clause 4.5.3.3: an invalid S&P rating is nulled and reported.  The
        -- generator injects these deliberately -- Generator/FWSPRatingErrPct.
        CASE WHEN d.SPrating IN ('AAA','AA','A','BBB','BB','B','CCC','CC','C',
                                 'D','AA+','A+','BBB+','BB+','B+','CCC+',
                                 'AA-','A-','BBB-','BB-','B-','CCC-')
             THEN d.SPrating END AS SPrating,
        d.CEOname, d.AddrLine1, d.AddrLine2, d.PostalCode, d.City,
        d.StateProvince, d.Country, d.Description,
        STR_TO_DATE(d.FoundingDate, '%Y%m%d') AS FoundingDate
    FROM daily d
    LEFT JOIN StatusType st  ON st.ST_ID  = d.Status
    LEFT JOIN Industry  ind  ON ind.IN_ID = d.IndustryID
    WHERE d.rn = 1
),
-- Clause 4.5.3.1: drop a record whose content repeats the one before it.
-- CONCAT_WS skips NULLs, which would make ('a',NULL) and (NULL,'a') equal, so
-- each field is coalesced to a token that cannot occur in the data.
hashed AS (
    SELECT r.*, MD5(CONCAT_WS('\037',
        COALESCE(CompanyName,'\036'), COALESCE(StatusName,'\036'),
        COALESCE(IndustryName,'\036'), COALESCE(SPrating,'\036'),
        COALESCE(CEOname,'\036'), COALESCE(AddrLine1,'\036'),
        COALESCE(AddrLine2,'\036'), COALESCE(PostalCode,'\036'),
        COALESCE(City,'\036'), COALESCE(StateProvince,'\036'),
        COALESCE(Country,'\036'), COALESCE(Description,'\036'),
        COALESCE(CAST(FoundingDate AS char),'\036'))) AS h
    FROM resolved r
),
changed AS (
    SELECT * FROM (
        SELECT h.*, LAG(h.h) OVER (PARTITION BY h.CIK ORDER BY h.EffDate) AS prev_h
        FROM hashed h
    ) x
    WHERE x.prev_h IS NULL OR x.h <> x.prev_h
),
-- Clause 4.4.1.2/4.4.1.3: a row runs until the next one for that key begins.
dated AS (
    SELECT c.*,
           LEAD(c.EffDate) OVER (PARTITION BY c.CIK ORDER BY c.EffDate) AS NextDate
    FROM changed c
)
SELECT
    ROW_NUMBER() OVER (ORDER BY CAST(CIK AS UNSIGNED), EffDate),
    CAST(CIK AS UNSIGNED),
    StatusName, CompanyName, IndustryName, SPrating,
    -- Clause 4.5.3.2: False if the rating begins with A or BBB, else True.
    -- NULL where the rating was invalid, per 4.5.3.3.
    CASE WHEN SPrating IS NULL THEN NULL
         WHEN SPrating LIKE 'A%' OR SPrating LIKE 'BBB%' THEN 0
         ELSE 1 END,
    CEOname, AddrLine1, AddrLine2, PostalCode, City, StateProvince, Country,
    Description, FoundingDate,
    CASE WHEN NextDate IS NULL THEN 1 ELSE 0 END,
    1,
    EffDate,
    COALESCE(NextDate, '9999-12-31')
FROM dated;

-- Clause 4.5.3.3: report every company record whose S&P rating was invalid.
-- Raised from the staged records rather than from DimCompany, because the
-- offending value is exactly what did not survive into the warehouse.
INSERT INTO DImessages
    (MessageDateAndTime, BatchID, MessageSource, MessageText, MessageType,
     MessageData)
SELECT CURRENT_TIMESTAMP(3), 1, 'DimCompany', 'Invalid SPRating', 'Alert',
       CONCAT('CO_ID = ', CAST(c.CIK AS UNSIGNED),
              ', CO_SP_RATE = ', COALESCE(c.SPrating, ''))
FROM tpcdi_stage.stg_fw_cmp c
WHERE c.SPrating IS NULL
   OR c.SPrating NOT IN ('AAA','AA','A','BBB','BB','B','CCC','CC','C','D',
                         'AA+','A+','BBB+','BB+','B+','CCC+',
                         'AA-','A-','BBB-','BB-','B-','CCC-');

-- Indexes for the as-of-date company lookup that DimSecurity and Financial
-- both need.  Clause 3.1.1 leaves indexing to the implementation and Clause
-- 7.2.3.2 permits auxiliary structures; without these the lookup is a scan of
-- DimCompany for each of ~200,000 staged rows.
CREATE INDEX ix_dimcompany_cik  ON DimCompany (CompanyID, EffectiveDate, EndDate);
CREATE INDEX ix_dimcompany_name ON DimCompany (Name, EffectiveDate, EndDate);

-- -------------------------------------------------------------- DimSecurity
-- Clause 4.5.6.  Symbol is the natural key.
--
-- A SECURITY ROW IS SPLIT WHEREVER ITS COMPANY CHANGES.
--
-- Clause 4.5.6.2 says only to resolve SK_CompanyID "where PTS >=
-- EffectiveDate and PTS < EndDate", which reads as a point lookup at the
-- security's own posting date.  Done that way, a security that outlives its
-- company's record keeps pointing at the stale one: at SF=5 that was 673 of
-- 4,000 rows.
--
-- The audit settles it.  "DimSecurity SK_CompanyID" requires the security's
-- WHOLE range to sit inside the company row's range:
--     c.EffectiveDate <= s.EffectiveDate and s.EndDate <= c.EndDate
-- and "DimSecurity row count" tests count >= FW_SEC, described as "matches or
-- exceeds".  A point lookup can satisfy neither; splitting satisfies both,
-- and the >= is there precisely because splitting adds rows.  DimCompany's
-- equivalent test is <=, for the opposite reason -- unchanged records are
-- dropped there.
--
-- So each security interval is intersected with the company intervals that
-- overlap it, and each intersection becomes a row.
INSERT INTO DimSecurity
    (SK_SecurityID, Symbol, Issue, Status, Name, ExchangeID, SK_CompanyID,
     SharesOutstanding, FirstTrade, FirstTradeOnExchange, Dividend,
     IsCurrent, BatchID, EffectiveDate, EndDate)
WITH
daily AS (
    SELECT s.*,
           ROW_NUMBER() OVER (PARTITION BY s.Symbol, DATE(s.PTS)
                              ORDER BY s.PTS DESC) AS rn
    FROM tpcdi_stage.stg_fw_sec s
),
resolved AS (
    SELECT
        d.Symbol, DATE(d.PTS) AS EffDate, d.IssueType, st.ST_NAME AS StatusName,
        d.Name, d.ExID, CAST(d.ShOut AS UNSIGNED) AS ShOut,
        STR_TO_DATE(d.FirstTradeDate, '%Y%m%d')  AS FirstTrade,
        STR_TO_DATE(d.FirstTradeExchg, '%Y%m%d') AS FirstTradeOnExchange,
        CAST(d.Dividend AS DECIMAL(10,2)) AS Dividend,
        -- The company's natural key, not a surrogate: the surrogate depends
        -- on the date, and the date range is what we are about to split.
        -- CoNameOrCIK is a CIK when it is all digits, a name otherwise
        -- (Clause 2.2.2.8); names are unique per company, which the audit's
        -- "DimCompany distinct names" test guarantees.
        COALESCE(
            CASE WHEN d.CoNameOrCIK REGEXP '^[0-9]+$'
                 THEN CAST(d.CoNameOrCIK AS UNSIGNED) END,
            (SELECT MIN(dc.CompanyID) FROM DimCompany dc WHERE dc.Name = d.CoNameOrCIK)
        ) AS CompanyID
    FROM daily d
    LEFT JOIN StatusType st ON st.ST_ID = d.Status
    WHERE d.rn = 1
),
dated AS (
    SELECT r.*,
           COALESCE(LEAD(r.EffDate) OVER (PARTITION BY r.Symbol ORDER BY r.EffDate),
                    '9999-12-31') AS NextDate
    FROM resolved r
),
-- Intersect each security interval with every overlapping company interval.
split AS (
    SELECT d.Symbol, d.IssueType, d.StatusName, d.Name, d.ExID, d.ShOut,
           d.FirstTrade, d.FirstTradeOnExchange, d.Dividend,
           c.SK_CompanyID,
           GREATEST(d.EffDate, c.EffectiveDate) AS EffDate,
           LEAST(d.NextDate, c.EndDate)         AS EndDate
    FROM dated d
    JOIN DimCompany c
      ON c.CompanyID = d.CompanyID
     AND c.EffectiveDate < d.NextDate
     AND c.EndDate      > d.EffDate
)
SELECT
    ROW_NUMBER() OVER (ORDER BY Symbol, EffDate),
    Symbol, IssueType, StatusName, Name, ExID, SK_CompanyID,
    ShOut, FirstTrade, FirstTradeOnExchange, Dividend,
    CASE WHEN EndDate = '9999-12-31' THEN 1 ELSE 0 END,
    1,
    EffDate,
    EndDate
FROM split
-- An intersection can be empty where a company record begins on the same day
-- the security's next record does; the audit's "consolidation" test rejects a
-- row that begins and ends on one day.
WHERE EffDate < EndDate;

-- ---------------------------------------------------------------- Financial
-- Clause 4.5.14.  Not a dimension: one row per company per quarter, no
-- history tracking.  4.5.14.1 notes the data never restates a quarter.
INSERT INTO Financial
    (SK_CompanyID, FI_YEAR, FI_QTR, FI_QTR_START_DATE, FI_REVENUE,
     FI_NET_EARN, FI_BASIC_EPS, FI_DILUT_EPS, FI_MARGIN, FI_INVENTORY,
     FI_ASSETS, FI_LIABILITY, FI_OUT_BASIC, FI_OUT_DILUT)
SELECT
    COALESCE(
        (SELECT dc.SK_CompanyID FROM DimCompany dc
         WHERE f.CoNameOrCIK REGEXP '^[0-9]+$'
           AND dc.CompanyID = CAST(f.CoNameOrCIK AS UNSIGNED)
           AND DATE(f.PTS) >= dc.EffectiveDate
           AND DATE(f.PTS) <  dc.EndDate LIMIT 1),
        (SELECT dc.SK_CompanyID FROM DimCompany dc
         WHERE dc.Name = f.CoNameOrCIK
           AND DATE(f.PTS) >= dc.EffectiveDate
           AND DATE(f.PTS) <  dc.EndDate LIMIT 1)
    ),
    CAST(f.`Year` AS UNSIGNED),
    CAST(f.Quarter AS UNSIGNED),
    STR_TO_DATE(f.QtrStartDate, '%Y%m%d'),
    CAST(f.Revenue      AS DECIMAL(15,2)),
    CAST(f.Earnings     AS DECIMAL(15,2)),
    CAST(f.EPS          AS DECIMAL(10,2)),
    CAST(f.DilutedEPS   AS DECIMAL(10,2)),
    CAST(f.Margin       AS DECIMAL(10,2)),
    CAST(f.Inventory    AS DECIMAL(15,2)),
    CAST(f.Assets       AS DECIMAL(15,2)),
    CAST(f.Liabilities  AS DECIMAL(15,2)),
    CAST(f.ShOut        AS UNSIGNED),
    CAST(f.DilutedShOut AS UNSIGNED)
FROM tpcdi_stage.stg_fw_fin f;

-- Status rows, Clause 3.2.8.
INSERT INTO DImessages
    (MessageDateAndTime, BatchID, MessageSource, MessageText, MessageType,
     MessageData)
SELECT CURRENT_TIMESTAMP(3), 1, 'DimCompany', 'Inserted rows', 'Status',
       CAST(count(*) AS char) FROM DimCompany
UNION ALL
SELECT CURRENT_TIMESTAMP(3), 1, 'DimSecurity', 'Inserted rows', 'Status',
       CAST(count(*) AS char) FROM DimSecurity
UNION ALL
SELECT CURRENT_TIMESTAMP(3), 1, 'Financial', 'Inserted rows', 'Status',
       CAST(count(*) AS char) FROM Financial;
