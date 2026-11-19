# How every source file becomes warehouse rows

**You should not have to open the specification to work on a phase.** Everything
here is transcribed from `../spec/tpc-di_v1.1.0.pdf` with the clause cited, so
the detail lives where the code is.

Two things are covered: **what feeds what** (§1–§4), and **why each phase reads
its data in one pass or in pieces** (§5).

Measured at SF=5. File sizes scale linearly with the scale factor except
`DailyMarket.txt`, which grows slightly faster — see §5.6.

---

## 1. The Batch1 inventory

| File | Bytes | Lines | Feeds | Phase |
|---|---:|---:|---|---|
| `BatchDate.txt` | 12 | 1 | the batch date, used by several transforms | — |
| `Date.txt` | 3,501,598 | 25,933 | `DimDate` | 4 |
| `Time.txt` | 4,782,598 | 86,400 | `DimTime` | 4 |
| `Industry.txt` | 2,680 | 102 | `Industry` | 4 |
| `StatusType.txt` | 89 | 6 | `StatusType` | 4 |
| `TaxRate.txt` | 17,039 | 320 | `TaxRate` | 4 |
| `TradeType.txt` | 99 | 5 | `TradeType` | 4 |
| `HR.csv` | 2,016,550 | 25,000 | `DimBroker` | 5 |
| `FINWIRE*` (203 files) | 46,301,328 | 204,561 | `DimCompany`, `DimSecurity`, `Financial` | 6 |
| `CustomerMgmt.xml` | 15,622,194 | 541,601 | `DimCustomer`, `DimAccount` | 7 |
| `Prospect.csv` | 5,200,493 | 24,970 | `Prospect` | 8 |
| `CashTransaction.txt` | 54,627,929 | 602,115 | `FactCashBalances` | 8 |
| `HoldingHistory.txt` | 12,988,969 | 602,956 | `FactHoldings` | 8 |
| `WatchHistory.txt` | 69,769,750 | 1,500,311 | `FactWatches` | 8 |
| `Trade.txt` | 64,075,858 | 650,412 | `DimTrade` | 8 |
| `TradeHistory.txt` | 53,665,094 | 1,634,784 | `DimTrade` (status history) | 8 |
| `DailyMarket.txt` | 142,245,151 | 2,422,064 | `FactMarketHistory` | 9 |
| `*_audit.csv` (238 files) | — | 1,402 data rows | `Audit` | 4 |

Batch2 and Batch3 carry nine files each — the OLTP CDC extracts plus
`Prospect.csv` and `BatchDate.txt` — about 5.6 MB per batch. Phase 11.

**Every file is CRLF**, because `PDGF/Constants.properties` sets
`DEFAULT_LINESEPARATOR=CRLF`. Always `LINES TERMINATED BY '\r\n'`, and in
Python always strip `\r`. Loaded with the default `'\n'` the last column of
every row silently keeps a carriage return.

---

## 2. The simple ones: one file, one table

Delimiter `|` unless stated. Field order is the target table's column order, so
the `LOAD DATA` column list is positional — see `../sql/di/02_load_reference.sql`.

| File | Table | Fields | Clause |
|---|---|---:|---|
| `Date.txt` | `DimDate` | 18 | 2.2.2.7 / 4.5.5 |
| `Time.txt` | `DimTime` | 10 | 2.2.2.15 / 4.5.7 |
| `Industry.txt` | `Industry` | 3 | 2.2.2.11 / 4.5.13 |
| `StatusType.txt` | `StatusType` | 2 | 2.2.2.13 / 4.5.16 |
| `TaxRate.txt` | `TaxRate` | 3 | 2.2.2.14 / 4.5.17 |
| `TradeType.txt` | `TradeType` | 4 | 2.2.2.18 / 4.5.18 |

**The one trap:** boolean fields arrive as the words `true` and `false`, not
the `"0"`/`"1"` Clause 2.2.1 Table 2.2.1 promises. Affects
`DimDate.HolidayFlag`, `DimTime.MarketHoursFlag`, `DimTime.OfficeHoursFlag`.
Assigned straight across, every flag becomes 0 and nothing complains.

---

## 3. `HR.csv` → `DimBroker` (Phase 5)

Comma-delimited, **no header**, nine fields, 25,000 rows. Clause 2.2.2.10.

| # | Source field | → `DimBroker` |
|---:|---|---|
| 1 | `EmployeeID` | `BrokerID` |
| 2 | `ManagerID` | `ManagerID` |
| 3 | `EmployeeFirstName` | `FirstName` |
| 4 | `EmployeeLastName` | `LastName` |
| 5 | `EmployeeMI` | `MiddleInitial` |
| 6 | `EmployeeJobCode` | **the filter — not stored** |
| 7 | `EmployeeBranch` | `Branch` |
| 8 | `EmployeeOffice` | `Office` |
| 9 | `EmployeePhone` | `Phone` |

**Filter (Clause 4.5.2.1):** keep only `EmployeeJobCode = 314`. Everything else
is an employee, not a broker, and is ignored — 7,144 of 25,000 at SF=5.

**Derived columns:** `SK_BrokerID` a new unique surrogate key (4.4.1.3);
`IsCurrent = 1`; `EndDate = 9999-12-31` (4.4.1.2); `BatchID = 1` (4.4.2.1);
`EffectiveDate` = **the earliest date in `DimDate`**, which is why Phase 4 must
run first.

---

## 4. `FINWIRE*` → three tables (Phase 6)

203 files, one per quarter, `FINWIRE1967Q1` to `FINWIRE2017Q3`. Clause 2.2.2.8.

### 4.1 Telling the three record types apart

**Characters 16–18 of the line.** Nothing else distinguishes them.

```
19670101-173145CMPkkhSvCsfgbDUvsiZWtPQwjkY...
19670425-114814SECAAAAAAAAAAAAAAACOMMONACTV...
19670401-065923FIN196721967040119670401...
│              │││
1            15 │└ 18
                16
```

Positions 1–15 are always `PTS`, the posting timestamp, `YYYYMMDD-HHMMSS`. In
Python, 0-indexed: `kind = line[15:18]`.

At SF=5: **2,500 `CMP`**, **4,000 `SEC`**, **198,061 `FIN`**. A fourth value
would mean a record we cannot classify, which is why the Phase 2 check asserts
exactly three.

### 4.2 Fixed-width, with one variable field

No separators. Text is padded right with spaces, numbers padded left. An empty
field is all spaces. **Every field must be stripped after slicing.**

The last field of `SEC` and `FIN` is `CoNameOrCIK`, and it is **either** a
10-digit CIK **or** a 60-character company name. Clause 2.2.2.8:

> *"in some cases the last field may contain a company name (60 characters) or
> a CIK number (10 digits)"*

Decide by record length, or by whether the trailing text is all digits. At SF=5
the split is close to even — `SEC` 2,013 short / 1,987 long; `FIN` 99,353 /
98,708. **Get this wrong and half the records attach to the wrong company,
silently, while row counts still look right.**

Measured lengths confirm the field tables below (each is one more than the sum
of the widths, because the line still carries its `\r`):

| Type | Widths sum | Measured line length |
|---|---:|---|
| `CMP` | 543 | 544 |
| `SEC` | 160 + (10 or 60) | 171 or 221 |
| `FIN` | 186 + (10 or 60) | 197 or 247 |

### 4.3 `CMP` → `DimCompany` (Clause 4.5.3)

Offsets are 0-indexed Python slices, computed from the widths in Table 2.2.8.

| Field | Width | Slice | → `DimCompany` |
|---|---:|---|---|
| `PTS` | 15 | `[0:15]` | drives `EffectiveDate` |
| `RecType` | 3 | `[15:18]` | the discriminator |
| `CompanyName` | 60 | `[18:78]` | `Name` |
| `CIK` | 10 | `[78:88]` | `CompanyID` |
| `Status` | 4 | `[88:92]` | `Status` (via `StatusType`) |
| `IndustryID` | 2 | `[92:94]` | `Industry` (via `Industry.IN_ID`) |
| `SPrating` | 4 | `[94:98]` | `SPrating`, and `isLowGrade` |
| `FoundingDate` | 8 | `[98:106]` | `FoundingDate` |
| `AddrLine1` | 80 | `[106:186]` | `AddressLine1` |
| `AddrLine2` | 80 | `[186:266]` | `AddressLine2` |
| `PostalCode` | 12 | `[266:278]` | `PostalCode` |
| `City` | 25 | `[278:303]` | `City` |
| `StateProvince` | 20 | `[303:323]` | `StateProv` |
| `Country` | 24 | `[323:347]` | `Country` |
| `CEOname` | 46 | `[347:393]` | `CEO` |
| `Description` | 150 | `[393:543]` | `Description` |

### 4.4 `SEC` → `DimSecurity` (Clause 4.5.6)

| Field | Width | Slice | → `DimSecurity` |
|---|---:|---|---|
| `PTS` | 15 | `[0:15]` | drives `EffectiveDate` |
| `RecType` | 3 | `[15:18]` | the discriminator |
| `Symbol` | 15 | `[18:33]` | `Symbol` |
| `IssueType` | 6 | `[33:39]` | `Issue` |
| `Status` | 4 | `[39:43]` | `Status` (via `StatusType`) |
| `Name` | 70 | `[43:113]` | `Name` |
| `ExID` | 6 | `[113:119]` | `ExchangeID` |
| `ShOut` | 13 | `[119:132]` | `SharesOutstanding` |
| `FirstTradeDate` | 8 | `[132:140]` | `FirstTrade` |
| `FirstTradeExchg` | 8 | `[140:148]` | `FirstTradeOnExchange` |
| `Dividend` | 12 | `[148:160]` | `Dividend` |
| `CoNameOrCIK` | 10 or 60 | `[160:]` | resolves `SK_CompanyID` |

`SK_CompanyID` is resolved from `CoNameOrCIK` against `DimCompany` — by
`CompanyID` when it is all digits, by `Name` when it is not — **and against the
row current at this record's date**, not the latest row (Table 4.3).

### 4.5 `FIN` → `Financial` (Clause 4.5.14)

| Field | Width | Slice | → `Financial` |
|---|---:|---|---|
| `PTS` | 15 | `[0:15]` | — |
| `RecType` | 3 | `[15:18]` | the discriminator |
| `Year` | 4 | `[18:22]` | `FI_YEAR` |
| `Quarter` | 1 | `[22:23]` | `FI_QTR` |
| `QtrStartDate` | 8 | `[23:31]` | `FI_QTR_START_DATE` |
| `PostingDate` | 8 | `[31:39]` | — |
| `Revenue` | 17 | `[39:56]` | `FI_REVENUE` |
| `Earnings` | 17 | `[56:73]` | `FI_NET_EARN` |
| `EPS` | 12 | `[73:85]` | `FI_BASIC_EPS` |
| `DilutedEPS` | 12 | `[85:97]` | `FI_DILUT_EPS` |
| `Margin` | 12 | `[97:109]` | `FI_MARGIN` |
| `Inventory` | 17 | `[109:126]` | `FI_INVENTORY` |
| `Assets` | 17 | `[126:143]` | `FI_ASSETS` |
| `Liabilities` | 17 | `[143:160]` | `FI_LIABILITY` |
| `ShOut` | 13 | `[160:173]` | `FI_OUT_BASIC` |
| `DilutedShOut` | 13 | `[173:186]` | `FI_OUT_DILUT` |
| `CoNameOrCIK` | 10 or 60 | `[186:]` | resolves `SK_CompanyID` |

`FI_BASIC_EPS` is what Phase 9 sums over the trailing four quarters to compute
`FactMarketHistory.PERatio`.

---

## 5. Why each phase reads its data the way it does

The question for every phase is the same: **one pass over everything, or in
pieces?** The answers differ, and each has a reason.

### 5.1 The general rule

Read the whole of a file in one `LOAD DATA`, then transform set-based in SQL.
Chunk only when something forces it. Rows never travel one at a time between
Python and the server — that is the slowest possible shape and there has been
no reason to reach for it.

### 5.2 Phase 2, generation — one invocation, not our choice

DIGen writes all three batches itself from a single command and offers no way
to split the work. 465 MB in 29 s at SF=5. Generation is untimed under Clause
1.2.2.1.1, so there would be nothing to gain anyway.

### 5.3 Phase 4, the `Audit` table — 238 statements, forced

`LOAD DATA` takes no wildcard, so 238 files mean 238 statements. They are
concatenated into one SQL block and sent in a single connection, so it is 238
statements but one round trip. 1,402 rows total — the count is irrelevant to
performance; the file count is simply a constraint.

The six reference files are one `LOAD DATA` each, whole file at a time. The
largest, `Time.txt`, is 4.8 MB.

### 5.4 Phase 5, `DimBroker` — whole file, one pass

2.0 MB, 25,000 rows, **3.7 s** end to end at SF=5 (1.7 s at SF=3), most of it
container round-trips rather than data. Scaling is linear at roughly 400 KB per
unit of scale factor, so SF=100 would be 40 MB and 500,000 rows — still one
pass, still seconds. No reason to chunk, now or later.

### 5.5 Phase 6, FINWIRE — all 203 files in one pass, deliberately

The files are small: 46.3 MB and 204,561 records in total, which fits in memory
with room to spare even at a much larger scale factor.

**But the reason for one pass is correctness, not size.** SCD-2 effective
dating depends on processing records in posting-timestamp order, and that order
is global across all 203 files — a company posted in `FINWIRE1990Q1` can be
superseded by a record in `FINWIRE1990Q2`. Parsing file by file and loading
each separately would produce correct-looking rows with the wrong effective
dates wherever a natural key spans a quarter boundary.

So: Python reads all 203 files, classifies each record by characters 16–18,
writes three TSVs, and three `LOAD DATA` statements stage them. The SCD-2
transform then runs as set-based SQL over the staged rows in `PTS` order.

### 5.6 Phase 8 and 9, the facts — one `LOAD DATA` per file

These are the big ones. `DailyMarket.txt` is 142 MB and 2.4 M rows at SF=5, and
it is the one file that grows **faster than linear**: `DM_RECORDS` went
1,282,768 → 2,422,064 between SF=3 and SF=5, a ratio of 1.89 where everything
else was 1.66. Extrapolating it from SF=5 will understate it.

Still one `LOAD DATA` each. MariaDB streams the file; it does not materialise
it in memory, and a single bulk load is far cheaper than several. The thing to
watch is InnoDB buffer pool and redo log size, not the statement count.

**Phase 9 is where chunking may genuinely be needed**, and if so it will be for
the transform rather than the load. `FactMarketHistory` needs, for each of 2.4 M
rows, a 52-week lookback for high, low and the earliest date each occurred,
plus a trailing-four-quarters EPS join. A window function over the whole table
is the right shape; if it will not fit, the natural split is **by security**,
because the window never crosses securities — not by date, which would cut the
lookback in half at every boundary. That decision belongs in Phase 9 with a
measurement behind it, not here.

### 5.7 Phase 11, the incremental updates — one pass per batch

Batch2 and Batch3 are 5.6 MB each. The phases are separate because the
specification requires it (Clause 7.2.5.1), not for any size reason.

---

## 6. What is still to be transcribed

This document covers the files Phases 4 to 6 need. Before starting Phase 7 or
8, extend it with:

- `CustomerMgmt.xml` — the action types and their element structure, Clause
  2.2.2.5, and the transforms in 4.5.1 and 4.5.4
- `Prospect.csv`, `CashTransaction.txt`, `HoldingHistory.txt`,
  `WatchHistory.txt`, `Trade.txt`, `TradeHistory.txt` — Clauses 2.2.2.12, .3,
  .9, .19, .17, .16
- `DailyMarket.txt` — Clause 2.2.2.6
- the Batch2/3 CDC variants, which add `CDC_FLAG` and `CDC_DSN` as two leading
  columns

Keep the same shape: the field table, the filter, the derived columns, and the
trap that would otherwise cost someone an afternoon.
