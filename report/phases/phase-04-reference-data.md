# Phase 4 — The Audit table and the reference tables

**Status: COMPLETE.** 19 of 19 checks pass, at SF=5 and again at SF=3. 2026-11-18.

Plan step: `../01-steps-and-validation.md` Step 4.

---

## 1. Goal

Load the data that everything else will be measured against, before loading
anything that needs measuring.

Two groups:

- **The `Audit` table** — the generator's own record of what it produced. Nearly
  every test in `tpcdi_audit.sql` compares the warehouse against a row in here,
  so until it exists nothing can be verified.
- **The reference tables** — `DimDate`, `DimTime`, `Industry`, `StatusType`,
  `TaxRate`, `TradeType`. Loaded once in the Historical Load and never modified
  again (Clause 1.3.7). `DimDate` and `DimTime` are static dimensions: no
  history tracking, no surrogate key generation, the file already carries the
  key.

This is the simplest real loading work in the benchmark — plain delimited files,
no transformation, no key lookups — which makes it the right place to find out
what the source files are actually like before the hard transformations depend
on it.

## 2. Where the inputs come from

| Input | Where | What it gives |
|---|---|---|
| `*_audit.csv` | 238 files across `Batch1/`, `Batch2/`, `Batch3/` and the staging root | The answer key. Clause 2.2.2.20 defines the format |
| `Date.txt`, `Time.txt`, `Industry.txt`, `StatusType.txt`, `TaxRate.txt`, `TradeType.txt` | `Batch1/` | Clauses 2.2.2.7, .15, .11, .13, .14, .18 |
| Load rules | Clauses 4.5.5, 4.5.7, 4.5.13, 4.5.16, 4.5.17, 4.5.18 | What goes where |
| Target columns | Clause 3.2 | Already built in Phase 3 |
| "Do not load the header" | Clause 7.2.6.2 | Explicit, and checked |

Output: **`sql/di/02_load_reference.sql`** — six `LOAD DATA INFILE` statements
with a `__STAGING__` placeholder so one file serves every scale factor.

## 3. How it runs

```
bash scripts/load-reference.sh                 scale factor 5
bash scripts/load-reference.sh --scale 3       any scale factor
bash scripts/load-reference.sh --force         reload without asking
bash scripts/load-reference.sh --keep          leave existing rows alone
```

Same contract as the other scripts: with rows already present it prints the
counts and asks, `--force` reloads, `--keep` leaves it, and a non-interactive
shell with neither flag is an error rather than a guess.

`create-schema.sh` was changed in this phase to mount the staging volume into
the MariaDB container at `/staging`, **read-only**, with
`--secure-file-priv=/staging`. Read-only because the server has no business
writing there, and because a mistake would damage data every later measurement
is taken against. The server is still not published on any port.

The 238 audit files are enumerated and loaded one at a time — `LOAD DATA` takes
no wildcard.

## 4. Results

| Table | Rows (SF=5) | Rows (SF=3) | Source |
|---|---|---|---|
| `Audit` | 1,402 | 1,402 | 238 CSVs, headers excluded |
| `DimDate` | 25,933 | 25,933 | `Date.txt` |
| `DimTime` | 86,400 | 86,400 | `Time.txt` |
| `Industry` | 102 | 102 | `Industry.txt` |
| `StatusType` | 6 | 6 | `StatusType.txt` |
| `TaxRate` | 320 | 320 | `TaxRate.txt` |
| `TradeType` | 5 | 5 | `TradeType.txt` |

**Every one of these is scale-independent**, which is worth knowing before
sizing work begins. `DimDate` spans a fixed 1950–2021, `DimTime` is the 86,400
seconds in a day, and the reference tables are code lists. The *values* in the
`Audit` table differ by scale factor; the number of facts does not.

## 5. Validations

| # | Check | Result |
|---|---|---|
| P1 | `Audit` row count = CSV data rows; no header rows loaded | **PASS** |
| P2 | 3 batches, 14 data sets, `FactCashBalances` absent | **PASS** |
| P3 | Each reference table's row count = its file's line count | **PASS** |
| P4 | No value ends in a carriage return | **PASS** |
| P5 | `true`/`false` mapped to 0/1, both present | **PASS** |
| P6 | `HR_BROKERS` and `Generator SF` match the source files | **PASS** |
| P7 | The audit query's own `Audit` table tests report OK | **PASS** |

```
bash scripts/verify-phase-04.sh 5     -> passed 19, failed 0
bash scripts/verify-phase-04.sh 3     -> passed 19, failed 0
```

Counts are compared against the source files themselves rather than against
literals written down here, so the checks still mean something at a scale factor
nobody has run before. Negative test: against an SF=3 warehouse with the schema
created but nothing loaded, 13 checks fail.

`tpcdi_audit.sql` now reports **114 of 166 OK**. Both of its `Audit` table tests
have flipped to OK. The remaining failures need the data Phases 5 onward load.

## 6. Three defects in the source data, and one in my own check

### 6.1 Every line ends CRLF

`PDGF/Constants.properties` sets `DEFAULT_LINESEPARATOR=CRLF`, so every
generated file is CRLF on every platform. Loaded with the default `'\n'`
terminator, the last column of every row silently gains a trailing carriage
return: `TX_RATE` becomes `'0.15000\r'`, which MariaDB either rejects or
truncates depending on `sql_mode`.

Handled with `LINES TERMINATED BY '\r\n'`. It is invisible in ordinary output,
so P4 asserts it rather than leaving it to be eyeballed.

### 6.2 Booleans arrive as words, contradicting the specification

Clause 2.2.1, Table 2.2.1 states BOOLEAN is `"0"` for False and `"1"` for True in
the source files. The generator writes `true` and `false`:

```
Time.txt:  000000|00:00:00|00|00|00|00:00|00|00:00:00|false|false
Date.txt:  ...|false
```

Assigned straight across into a `TINYINT(1)`, every flag becomes 0 and nothing
complains. The three flag columns are therefore mapped explicitly, and P5
requires both values to be present in each — a check that would catch the silent
all-zero outcome.

### 6.3 The generator's audit data does not fit the type the spec gives it

Clause 3.2.19 types `Audit.DValue` as `SNUM(15,5)` — ten digits before the
decimal point. `Generator_audit.csv` contains:

```
Generator,1,,ONE_YEAR_IN_MILLISECONDS,,31557600000
```

Eleven digits. The load fails with `ERROR 1264 Out of range value for column
'DValue'`.

Clause 7.2.6.2 requires every field of the audit data to be loaded into its
column, so the column was widened to `DECIMAL(20,5)`. That holds exactly every
value `SNUM(15,5)` would and more; the audit query never reads `DValue` (zero
references in 1,317 lines), so no test is affected. Measured at SF=3 and SF=5:
the widest value is eleven integer digits and does not grow with scale factor.

The deviation is recorded in `sql/di/01_schema.sql` beside the column.

### 6.4 My own check was wrong, and the verifier caught it

P6 first asserted `HR_BROKERS` equalled the number of data rows in `HR.csv`. It
failed: 7,144 against 24,999.

The data was right and the check was wrong. `HR.csv` holds *all* employees;
Clause 4.5.2.1 says only those with `EmployeeJobCode` 314 are brokers and the
rest are ignored. Counting rows with job code 314 gives exactly 7,144 at SF=5 and
4,293 at SF=3, matching the audit files in both cases.

Fixing the check turned it into something better than a row count: it now
validates Clause 4.5.2.1's filter rule against the generator **before Phase 5
writes a line of `DimBroker`**. If our reading of the broker filter were wrong,
this check would have said so a phase early.

## 7. What this phase settles for later work

- **The `Audit` table is populated**, so every later phase can be checked against
  the generator rather than against our own arithmetic.
- **`LINES TERMINATED BY '\r\n'` is mandatory** for every load in the project.
- **Boolean columns always need explicit mapping.** Any source file with a flag
  needs the same treatment.
- **`DimDate` and `DimTime` are complete**, which matters because Table 4.3 makes
  almost every fact table depend on `SK_DateID` resolving.
- **The broker filter is confirmed as job code 314.**
- Phase 5's baseline is **114 of 166** audit tests OK.

## 8. Reproducing this

```
bash scripts/generate-data.sh  --scale 5
bash scripts/create-schema.sh  --scale 5
bash scripts/load-reference.sh --scale 5
bash scripts/verify-phase-04.sh 5
```
