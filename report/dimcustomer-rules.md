# Everything the DimCustomer query has to do

Clause 4.5.4, with the pieces it depends on gathered in. Written so the query
can be built without reopening the specification.

Source: `tpcdi_stage.stg_cmg_customer`, 24,570 rows, one per action, `seq` in
document order. Target: `DimCustomer`, Clause 3.2.4.

---

## 0. Read this first: a dependency that changes the phase order

**`Prospect` must be loaded before `DimCustomer` runs.** Clause 4.5.4.2 says
outright: *"The TaxRate and Prospect tables will be referenced in the
transformation."* `AgencyID`, `CreditRating`, `NetWorth` and
`MarketingNameplate` all come from it.

And there is a cycle, which the specification resolves by splitting the work:

```
1.  Prospect rows loaded           (demographics, MarketingNameplate, dates)
2.  DimCustomer built              reads Prospect demographics
3.  Prospect.IsCustomer updated    reads DimCustomer
```

Clause 4.5.15 puts step 3 last explicitly — `IsCustomer` is set *"after all
customer records in the batch have been processed"*. So `Prospect` is not a
Phase 8 job as the plan currently has it; its load belongs before this query
and only its `IsCustomer` column belongs after.

`TaxRate`, `StatusType`, `DimDate` and `DimBroker` are already loaded by
Phases 4 and 5.

---

## 1. Which actions produce a row

Only three of the six touch `DimCustomer` (Clause 4.5.4.1):

| ActionType | Rows | Produces a DimCustomer row? |
|---|---:|---|
| `NEW` | 7,800 | yes — a fresh customer |
| `UPDCUST` | 2,150 | yes — a partial update |
| `INACT` | 860 | yes — status change only |
| `ADDACCT` | 7,310 | **no** |
| `UPDACCT` | 4,300 | **no** |
| `CLOSEACCT` | 2,150 | **no** |

So filter to 10,810 of the 24,570 actions. The other three carry only `C_ID`
and must not create a customer version.

## 2. The merge, which is the hard part

`C_ID` is the natural key (Clause 4.5.4.2). Within one customer, ordered by
`seq`:

- **`NEW`** — every field is supplied. Missing or `<Element/>` both mean NULL.
- **`UPDCUST`** — *"only the updated fields are supplied a new value, i.e. all
  missing values retain their current values. Fields with empty values
  (e.g. `<Element />`) should be processed as NULL values."*
- **`INACT`** — supplies nothing but `C_ID`; everything carries forward and
  only `Status` changes.

The staging table encodes the distinction the parser preserved:

```
NULL   the field was absent     -> carry the previous value
''     the field was <Element/> -> the new value is NULL
```

A forward-fill per `C_ID` ordered by `seq` does it, and MariaDB has no
`IGNORE NULLS`, so use the gaps-and-islands form:

```sql
count(col) over (partition by C_ID order by seq)          as grp,
first_value(col) over (partition by C_ID, grp order by seq) as carried,
nullif(carried, '')                                        as final
```

Verified: after an explicit `''`, a later absent field stays NULL rather than
resurrecting the older value.

**Apply this to every field**, including the attributes. At SF=5, `UPDCUST`
has 443 actions where `C_CTRY` is absent and 75 where it is empty.

## 3. Direct field mapping (Clause 4.5.4.3)

| `DimCustomer` | From |
|---|---|
| `CustomerID` | `C_ID` |
| `TaxID` | `C_TAX_ID` |
| `LastName`, `FirstName`, `MiddleInitial` | `C_L_NAME`, `C_F_NAME`, `C_M_NAME` |
| `Tier` | `C_TIER` |
| `DOB` | `C_DOB` |
| `AddressLine1`, `AddressLine2`, `PostalCode`, `City`, `StateProv`, `Country` | `C_ADLINE1`, `C_ADLINE2`, `C_ZIPCODE`, `C_CITY`, `C_STATE_PROV`, `C_CTRY` |
| `Email1`, `Email2` | `C_PRIM_EMAIL`, `C_ALT_EMAIL` |

Note the name change: `C_STATE_PROV` → `StateProv`, `C_CTRY` → `Country`.

## 4. Derived fields

**`Gender`** — uppercase `C_GNDR`; anything that is not `M` or `F` becomes
`'U'`. Not NULL, `'U'`.

**`Status`** — `'ACTIVE'` on `NEW`, `'INACTIVE'` on `INACT`, carried forward on
`UPDCUST`. A literal, not a `StatusType` lookup — unlike `DimCompany`, which
does look up.

**`Phone1`, `Phone2`, `Phone3`** — assembled from four parts each, in this
order of precedence:

```
ctry and area and local present   ->  '+' + ctry + ' (' + area + ') ' + local
ctry null, area and local present ->  '(' + area + ') ' + local
area null, local present          ->  local
otherwise                         ->  NULL
then, if a rule applied and ext is not null:  phone = phone + ext
```

Note the third case ignores `ctry` entirely, and the extension is appended
with no separator.

**`NationalTaxRateDesc` / `NationalTaxRate`** — `TX_NAME` / `TX_RATE` from
`TaxRate` matched on `C_NAT_TX_ID = TX_ID`. The match is guaranteed.
**`LocalTaxRateDesc` / `LocalTaxRate`** — the same, via `C_LCL_TX_ID`.

**`AgencyID`, `CreditRating`, `NetWorth`, `MarketingNameplate`** — see §5.

## 5. The Prospect match, which only applies to the latest record

A `Prospect` row matches a `DimCustomer` row when **`LastName`, `FirstName`,
`AddressLine1`, `AddressLine2` and `PostalCode` all match upper-cased**
(Clause 4.5.4.3). Note `AddressLine2` is in the key and is often NULL — decide
deliberately how NULL compares, because `upper(NULL) = upper(NULL)` is not
true in SQL.

It applies **only where there is no newer `UPDCUST` or `INACT` for that
customer** — in practice, only the row with `IsCurrent = 1`. The rationale in
the clause: demographics describe today and must not be applied retroactively.

On `NEW` and `UPDCUST`: if no match, or the record is not the latest, the four
fields are **NULL** on `NEW` and **unchanged** on `UPDCUST`.
On `INACT`: if the prospect exists the values are taken; if not they are
**unchanged** — note there is no "newer record" condition here.

`MarketingNameplate` is rebuilt, not copied — the same rule as Clause 4.5.15.
Tags joined with `+`, **in this order**, NULL if none apply:

| Tag | Condition |
|---|---|
| `HighValue` | `NetWorth > 1000000` or `Income > 200000` |
| `Expenses` | `NumberChildren > 3` or `NumberCreditCards > 5` |
| `Boomer` | `Age > 45` |
| `MoneyAlert` | `Income < 50000` or `CreditRating < 600` or `NetWorth < 100000` |
| `Spender` | `NumberCars > 3` or `NumberCreditCards > 7` |
| `Inherited` | `Age < 25` and `NetWorth > 1000000` |

## 6. History tracking (Clause 4.4.1)

- `SK_CustomerID` — a new unique surrogate key per version.
- `EffectiveDate` — **the date part of `ActionTS`**, not the batch date.
- `EndDate` — the next version's `EffectiveDate`, or `9999-12-31`.
- `IsCurrent` — 1 only on the last version of each `C_ID`.
- `BatchID` — 1.
- **Daily grain** (4.4.1.5): at most one row per `C_ID` per day. Where a
  customer has several actions on one day, keep the last by `seq` — the
  audit's `consolidation` test fails any row where
  `EffectiveDate = EndDate`.

## 7. The two alerts

Both write to `DImessages` with `MessageType = 'Alert'` and
`MessageSource = 'DimCustomer'`.

**Invalid tier** (4.5.4.4) — `C_TIER` not in (1, 2, 3).

```
MessageText  'Invalid customer tier'
MessageData  'C_ID = ' + C_ID + ', C_TIER = ' + C_TIER
```

Expected: `Audit.C_TIER_INV` = **380** at SF=5.

**DOB out of range** (4.5.4.5) — `DOB < BatchDate - 100 years` or
`DOB > BatchDate`. The batch date is in `Batch1/BatchDate.txt`: **2017-07-07**.

```
MessageText  'DOB out of range'
MessageData  'C_ID = ' + C_ID + ', C_DOB = ' + C_DOB
```

Expected: `Audit.C_DOB_TO` = **10** (too old) plus `C_DOB_TY` = **16** (too
young) = 26 at SF=5.

The record is still loaded — these are alerts, not rejections.

## 8. What will catch you out

1. **Absent is not empty.** The single biggest one. 443 vs 75 on `C_CTRY`
   alone.
2. **`INACT` supplies nothing**, so every field must survive the carry; only
   `Status` changes.
3. **Gender's invalid value is `'U'`, not NULL.**
4. **`Status` is a literal here**, not a `StatusType` lookup.
5. **`AddressLine2` is part of the Prospect match key** and is frequently
   NULL.
6. **Demographics only on the current row**, and the "no newer record" rule
   differs between `UPDCUST` and `INACT`.
7. **`MarketingNameplate` tag order is fixed** — `Boomer+Spender`, never
   `Spender+Boomer`.
8. **`EffectiveDate` is the action timestamp's date**, not the batch date —
   unlike `DimBroker`, where it is the earliest date in `DimDate`.
9. **Daily grain** — collapse same-day actions or the audit's
   `consolidation` test fails.
10. **Alerts are raised from the staged records**, not from `DimCustomer`:
    an invalid tier that a later action overwrites still happened.

## 9. The audit tests this has to satisfy

Twenty-three of them, the most of any table:

```
DimCustomer row count          DimCustomer EndDate
DimCustomer distinct keys      DimCustomer Overlap
DimCustomer End of Time        DimCustomer consolidation
DimCustomer batches            DimCustomer IsCurrent
DimCustomer EffectiveDate      DimCustomer Status
DimCustomer Gender             DimCustomer Tier
DimCustomer TaxID              DimCustomer phones
DimCustomer email              DimCustomer LocalTaxRate
DimCustomer NationalTaxRate    DimCustomer demographic fields
DimCustomer inactive customers DimCustomer AgencyID
DimCustomer Credit Rating      DimCustomer NetWorth
DimCustomer MarketingNameplate
```

Run them with `python verify_di.py --scale 5 --phase 7` once the query is in
place, or read the exact SQL in
`tools/appendices/AppendixA/tpcdi_audit.sql` — it is the oracle, and where it
disagrees with a reading of the clause, it wins.

---

## 10. The query

Not yet committed to `sql/di/`, and the four Prospect-derived columns
(`AgencyID`, `CreditRating`, `NetWorth`, `MarketingNameplate`) are left NULL —
they need the `Prospect` table loaded first, per §0.

**Note the order of operations.** The forward-fill runs over *every* action
first, and only then are same-day actions collapsed. Doing it the other way
round would discard a field supplied by the earlier of two actions on one day.
Clause 4.4.1.5.1 says to "accumulate the changes… on a daily basis", and
4.4.1.5.2 that "if a field changes more than once per day, the latest value is
used" — accumulate, then take the last, not take the last and lose the rest.

```sql
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
    NULL, NULL, NULL, NULL,       -- AgencyID, CreditRating, NetWorth,
                                  -- MarketingNameplate: need Prospect, see 0
    CASE WHEN d.NextDate IS NULL THEN 1 ELSE 0 END,
    1,
    d.EffDate,
    COALESCE(d.NextDate, '9999-12-31')
FROM dated d
LEFT JOIN TaxRate nat ON nat.TX_ID = d.NatTx
LEFT JOIN TaxRate lcl ON lcl.TX_ID = d.LclTx;
```

### The two alerts

Raised from the staged actions, not from `DimCustomer`: an invalid tier that a
later action overwrites still happened, and the offending value is what did
not survive into the warehouse.

```sql
-- Clause 4.5.4.4.  Expect Audit.C_TIER_INV = 380 at SF=5.
INSERT INTO DImessages
    (MessageDateAndTime, BatchID, MessageSource, MessageText, MessageType,
     MessageData)
SELECT CURRENT_TIMESTAMP(3), 1, 'DimCustomer', 'Invalid customer tier', 'Alert',
       CONCAT('C_ID = ', C_ID, ', C_TIER = ', COALESCE(C_TIER, ''))
FROM tpcdi_stage.stg_cmg_customer
WHERE ActionType IN ('NEW', 'UPDCUST')
  AND C_TIER IS NOT NULL
  AND C_TIER NOT IN ('1', '2', '3');

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
  AND (STR_TO_DATE(C_DOB, '%Y-%m-%d') < DATE_SUB('2017-07-07', INTERVAL 100 YEAR)
    OR STR_TO_DATE(C_DOB, '%Y-%m-%d') > '2017-07-07');
```

The batch date is hard-coded above for readability; in the real file it should
be read from `Batch1/BatchDate.txt` so it follows the scale factor's data.

### Untested

None of this has been run. The counts to check it against are
`Audit.C_TIER_INV` = 380, `C_DOB_TO` = 10, `C_DOB_TY` = 16, and the
twenty-three `DimCustomer` tests in the audit query.
