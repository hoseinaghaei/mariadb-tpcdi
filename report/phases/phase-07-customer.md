# Phase 7 — Prospect, DimCustomer and DimAccount

**Status: COMPLETE.** 40 of 40 checks pass, at SF=5 and SF=3. All 39 audit
tests for these three tables report OK; `batches` is deferred to Phase 11.
2026-11-19.

Plan step: `../01-steps-and-validation.md` Step 7.

---

## 1. Goal

Replay a stream of 24,570 hierarchical actions in document order to derive the
historical state of two slowly-changing dimensions — and load the `Prospect`
table they both depend on.

This is the hardest phase so far, for three reasons that do not appear
elsewhere: the source is XML rather than delimited text, an update supplies
only the fields that changed, and a change to a customer cascades into every
account that customer holds.

## 2. Where the inputs come from

| Input | Where | What it gives |
|---|---|---|
| `CustomerMgmt.xml` | `Batch1/`, 15.6 MB | 24,570 actions, six types |
| `Prospect.csv` | `Batch1/`, 5.2 MB | 24,970 prospects, 22 fields |
| Action semantics | Clauses 4.5.1.1, 4.5.4.1 | which action touches which table |
| `DimCustomer` rules | Clause 4.5.4 | the merge, phones, tax, demographics, alerts |
| `DimAccount` rules | Clause 4.5.1 | status, the cascade, the key lookups |
| `Prospect` rules | Clause 4.5.15 | `MarketingNameplate`, `IsCustomer` |
| Cascade rule | Clause 4.4.1.6.1 | why an account follows its customer |
| Ground truth | `Audit` | `C_NEW` 7800, `C_TIER_INV` 380, `C_DOB_TO` 10, `C_DOB_TY` 16 |

The full `DimCustomer` logic, with the traps, is written up separately in
`../dimcustomer-rules.md`.

Code: `../../tpcdi/customermgmt.py`, `../../tpcdi/phase07_customer.py`,
`../../sql/di/06` through `11`.

## 3. Four steps, because of a cycle

```
a  Prospect              rows, demographics, MarketingNameplate   0.6 s
b  DimCustomer           reads Prospect                          11.1 s
c  DimAccount            reads DimCustomer                       22.4 s
d  Prospect.IsCustomer   reads DimCustomer                        0.2 s
```

`Prospect` and `DimCustomer` are circular: Clause 4.5.4.2 says *"the TaxRate
and Prospect tables will be referenced in the transformation"*, while Clause
4.5.15.1 sets `IsCustomer` from `DimCustomer` *"after all customer records in
the batch have been processed"*.

The specification resolves it by splitting the work, and the whole cycle is
kept inside one phase. Split across a boundary it would leave a half-built
`Prospect` that no check could validate, and "Phase 7 is done" would stop
meaning anything.

**This moved `Prospect` out of Phase 8**, where the plan had it with the fact
tables.

## 4. How the data is read, and why

**One pass over the XML, nothing accumulated.** `iterparse` with `clear()`,
rows streamed into two `LOAD DATA LOCAL INFILE '/dev/stdin'` clients in blocks
of 1,000. Measured +2.5 MiB over a bare interpreter for the 15.6 MB file;
memory does not depend on the scale factor.

Python records only what SQL cannot recover later: **document order** as a
`seq` column, and **absent versus empty**. It does no lookups, no merging, and
issues no per-row statements. Everything after that is set-based SQL.

**Order matters absolutely, but order-dependent is not the same as
row-by-row.** Once `seq` is a column, "apply in order" is an `ORDER BY` inside
a window, which is a sort rather than a loop.

## 5. The distinction the whole phase turns on

Clause 4.5.4.2:

> *"only the updated fields are supplied a new value, i.e. all missing values
> retain their current values. Fields with empty values (e.g. `<Element />`)
> should be processed as NULL values."*

Within a single `UPDCUST`, three lines apart:

```xml
<Address>
  <C_ADLINE1>3713 Jarvis Avenue</C_ADLINE1>   a new value
  <C_ZIPCODE>E7O 1P6</C_ZIPCODE>              a new value
</Address>                      C_ADLINE2, C_STATE_PROV, C_CTRY absent -> KEEP
<C_PHONE_1>
  <C_CTRY_CODE/>                              present, empty -> NULL
</C_PHONE_1>
```

Written the obvious way — `elem.text or None` — both collapse to NULL and 2,150
customers silently lose their state and country on every update, with row
counts still perfect.

They are handed to SQL as `\N` for absent and `''` for empty, and a
gaps-and-islands forward-fill does the rest:

```sql
count(col) over (partition by C_ID order by seq)   -- islands
max(col)   over (partition by C_ID, grp)           -- the island's one value
nullif(..., '')                                    -- empty becomes NULL
```

At SF=5, `UPDCUST` has **443** actions where `C_CTRY` is absent and **75**
where it is empty. The parser's self-test asserts both cases, and reverting it
to `elem.text or None` fails exactly the three checks that cover them.

## 6. Results

| | SF=5 | SF=3 |
|---|---|---|
| Actions parsed | 24,570 | 14,670 |
| `Prospect` rows | 24,970 | 14,980 |
| `Prospect.IsCustomer` | 5,442 | 3,279 |
| `DimCustomer` rows | 10,810 | 6,450 |
| distinct customers | 7,800 | 4,650 |
| customers matched to a prospect | 6,133 | 3,679 |
| `DimAccount` rows | 27,338 | 16,320 |
| distinct accounts | 15,110 | 9,010 |
| Invalid tier alerts | **380** | 230 |
| DOB alerts | **26** | 14 |

Both alert counts match the `Audit` table exactly.

## 7. Validations

| # | Check | Result |
|---|---|---|
| X1–X2 | Every action staged; counts match `Audit` for all six types | **PASS** |
| X3 | Absent and empty kept apart | **PASS** |
| X4 | Account event stream: explicit ∪ cascade, ordering total | **PASS** |
| X5 | `Prospect` rows = file lines; every nameplate matches its tags, in order | **PASS** |
| X6–X7 | `DimCustomer` counts and SCD-2 chain integrity | **PASS** |
| X8 | Demographics only on the current row | **PASS** |
| X9 | Both alert counts match `Audit` | **PASS** |
| X10 | `DimAccount` keys, chains, and containment in its customer's range | **PASS** |
| X11 | All 39 audit tests for these tables | **PASS** |

```
python verify_di.py --scale 5    -> passed 140, failed 0   (131 of 167 audit)
python verify_di.py --scale 3    -> passed 140, failed 0   (130 of 167 audit)
```

## 8. Three things the audit corrected

### 8.1 The tier alert was counting the wrong thing

First version raised 89 alerts where `Audit.C_TIER_INV` says **380**. It only
counted actions that *supplied* an invalid tier.

The missing 291 are `NEW` actions with **no tier at all**. Clause 4.5.4.2: a
field missing from a new customer "should be processed as a NULL value", and
NULL is not one of the valid values (1, 2, 3), so it alerts. The arithmetic is
exact — 377 absent plus 3 bad values is 380. `UPDCUST` does not alert, because
an absent tier there means unchanged.

### 8.2 The cascade must reach accounts that have already closed

Clause 4.5.4.3 says `UPDCUST` updates *"all current accounts"*, so the cascade
was bounded at the account's `CLOSEACCT`. Everything looked right — until
`DimAccount SK_CustomerID` reported `Bad join`.

That test requires an account's **whole range** to sit inside its customer
row's range, and 603 closed accounts were still running to `9999-12-31` while
their customer changed underneath them. Clause 4.5.1.3 uses different wording
— *"each account **held by** the customer"* — and the audit sides with it.

Removing the upper bound took violations to zero: 5,010 cascade events became
5,781, and `DimAccount` 26,567 rows became 27,338.

**This is the second time a `<=`/`>=`-style detail in the audit has settled a
reading the prose left ambiguous**, after Phase 6's `DimSecurity row count`.
Clause 4.4.1.6.1 puts both pairs — `DimAccount`/`DimCustomer` and
`DimSecurity`/`DimCompany` — under one rule, and the audit tests them
identically.

### 8.3 A check of mine was asserting the opposite

`no cascade event lands on an already-closed account` had been passing, and was
wrong. It now asserts that **771 cascade events do** reach closed accounts,
because that is what keeps them inside their customer's range.

A check that passes is not the same as a check that is right.

## 9. Two performance findings

Steps b and d first took **129 s** and **157 s**. Both had the same cause and
neither was about data volume.

**`UPPER()` was pointless and harmful.** The columns collate
`utf8mb4_uca1400_ai_ci`, which is already case- and accent-insensitive, so
`UPPER(a) = UPPER(b)` buys nothing — and both it and `COALESCE` make the
predicate non-sargable. The optimiser chose a block-nested loop over
10,388 × 24,160 rows. Replacing them with the NULL-safe `<=>`, which handles
the frequently-NULL `AddressLine2` and *is* indexable, turned the join into a
ref lookup: 129 s to 0.14 s for the same 6,133 matches.

**A correlated `EXISTS` was driving from the wrong side.** Step d ran
`SET IsCustomer = EXISTS (SELECT ... FROM DimCustomer ...)`, which scans
`DimCustomer` once per prospect. Driving from `DimCustomer` instead — 7,800
current active rows rather than 24,970 prospects — lets each find its prospect
through the index. 157 s to 0.2 s.

Two indexes were added: `ix_prospect_match` on the five match fields, and
`ix_dimcustomer_asof` for the as-of lookup `DimAccount` does 27,000 times.
Clause 3.1.1 leaves indexing to the implementation.

## 10. A tooling fix this phase forced

These `.sql` files get opened in an IDE and used as scratchpads, which is the
natural thing to do with a `.sql` file. It broke the phase **three times**: a
trailing `select * from stg_cmg_account_event;` has an unqualified name that
resolves against `tpcdi` rather than `tpcdi_stage`, fails, and aborts the whole
script — tables created, nothing loaded, and an error pointing at the wrong
thing.

A position-based sentinel did not survive, because the next query was pasted
above it. `common.read_sql()` now filters by **statement type**: a pipeline
file only ever contains `CREATE`, `DROP`, `TRUNCATE`, `INSERT`, `UPDATE`,
`DELETE`, `LOAD`, `ALTER` or `SET`. A bare `SELECT` is always someone thinking
out loud, wherever it sits, and is dropped.

That needed a comment-aware statement splitter: these files carry prose like
*"see report/dimcustomer-rules.md; this file is the implementation"*, and
treating that semicolon as a boundary shreds the query after it.

Use `scratch/`, which is gitignored and which nothing executes.

## 11. What this settles for later work

- **`DimCustomer` and `DimAccount` are complete**, so Phase 8's facts can
  resolve customer and account keys.
- **`Prospect` is loaded** and its `IsCustomer` is correct for batch 1.
  Clause 4.6.14's change-detection rules are Phase 11's problem.
- **Collation is case-insensitive.** Do not write `UPPER()` in a join
  predicate again.
- **Prefer `<=>` to `COALESCE(...) = COALESCE(...)`** wherever NULLs must
  match: same semantics, and indexable.
- **The forward-fill pattern works** and is reusable: `count()` for islands,
  `max()` for the value, `nullif(...,'')` for the empty case.
- Audit suite: **131 of 167** OK, up from 123.

## 12. Reproducing this

```
python benchmark_di.py --scale 5 --phase 7
python verify_di.py    --scale 5 --phase 7
python -m tpcdi.customermgmt --self-test
```
