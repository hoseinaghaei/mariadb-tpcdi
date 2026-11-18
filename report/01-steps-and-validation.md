# TPC-DI: the steps, and how each one is validated

Written 2026-11-18. Companion to `00-what-we-have-to-do.md`. Clause numbers refer
to TPC-DI Standard Specification v1.1.0 (`../spec/tpc-di_v1.1.0.pdf`).

The short answer to "is there a source we can validate against at each step": yes,
and unusually so. TPC-DI ships **five independent oracles**, layered from weakest
to strongest. Part I had none of this — we had to invent our own correctness
checks by diffing query output. Here correctness is handed to us, which is why
this plan is organised around the oracles rather than around the code.

---

## Correction, 2026-11-18 (Phase 1)

**This section was written before the tools package was in hand. Two of the five
oracles below do not exist.** `TPC-DI_Tools_v1.1.0.zip` contains DIGen and PDGF
and no SQL whatsoever — no audit query, no validation query, no visibility
queries, no comparison tool, no expected result data, no DDL. The specification
says the appendices come from TPC's download section; that archive is not
published, and the TPC-DI entry links only the spec PDF and the tools zip
(verified against the page HTML). See `../tools/MANIFEST.md`.

What survives is oracles 0, 1 and 2. They are enough to build a rigorous
validation, but we now have to *write* the audit rather than *run* it, and the
report must say so as a limitation rather than imply we passed TPC's own tests.

## The oracles

| # | Oracle | Status | What question it answers |
|---|---|---|---|
| 0 | **The specification** | `../spec/tpc-di_v1.1.0.pdf`, Clauses 2.2, 3.2, 4.5, 4.6 | **Available.** What is correct. Definitional, not executable. |
| 1 | **`digen_report.txt`** | Written by DIGen, must not be edited (Clause 6.3) | **Available.** Did we read every source row? Gives `RH`, `RI1`, `RI2` for the metric. |
| 2 | **`Audit` table** | `*_audit.csv` in every batch directory (Clause 2.2.2.20) | **Available, and now our primary oracle.** Does what we loaded match what the generator says it generated? Counts, sums and dates per attribute per batch. The PDGF config carries 15 audit definitions, so this data really is produced. |
| 3 | `tpcdi_validation.sql` | Appendix B | **Not published.** We write our own phase-boundary capture, including the PCR rows the metric is timed from. |
| 4 | `tpcdi_audit.sql` | Appendix A | **Not published.** We write our own audit suite from Clause 4 and from structural invariants. |
| 5 | Comparison tool + expected results | Clause 8.2.1.1 | **Not published.** No row-level diff against TPC's output is possible. |

### What replaces oracles 3–5

Three things, in descending order of strength:

1. **The `Audit` table (oracle 2).** The generator's own ground truth: row counts
   and sums per attribute per batch, for Account, Customer, CustomerMgmt,
   DailyMarket, Date, FINWIRE, HR, Industry, Prospect, StatusType, TaxRate, Time,
   TradeType, WatchHistory and a General set. Every load step gets checked
   against it. This is genuinely strong — it is not our own arithmetic restated.

2. **Structural invariants derived from Clause 4.** These hold regardless of
   data and are cheap to assert continuously:
   - SCD-2 chain integrity: for each natural key exactly one `IsCurrent = TRUE`;
     each row's `EndDate` equals its successor's `EffectiveDate`; no gaps, no
     overlaps; current rows end `9999-12-31` (Clause 4.4.1.2).
   - Daily grain: at most one row per natural key per day (Clause 4.4.1.5).
   - Every surrogate key in Table 4.3 resolves, and resolves to the row *current
     at the referencing row's date*, not the latest row.
   - The fifteen Clause 8.2.2.2 extract queries all parse and run — a free check
     that our column names match the spec's spelling exactly.
   - `DImessages` alert text matches the exact strings Clause 4 prescribes.

3. **Independent reimplementations, as corroboration only.** Several public
   projects reconstructed the schema and audit logic from the same spec
   (`shannon-barrow/databricks-tpc-di`, `stewartbryson/dbt-tpcdi`,
   `haydarai/tpcdi-kit`). Agreeing row counts with an independent implementation
   at the same scale factor is real evidence. Disagreeing is a prompt to re-read
   the clause, not proof either side is wrong. Never cite these as authority.

**SF = 5 is still the correctness scale factor.** Clause 8.1.3 fixes
qualification data at SF=5 and the DIGen default is also 5, so it stays the size
we iterate on — now because it is small and fast rather than because expected
results exist for it.

## The steps

### Step 1 — Obtain the tools, and inventory them — **DONE 2026-11-18**

Zip obtained, hashed, unpacked, inventoried in `../tools/MANIFEST.md`.

Answers to the three questions it had to settle:

1. **`DIGen.jar` is present**, v1.1.0, with PDGF R1343 beside it. Its README says
   "Java SE 7 **or above**", contradicting the third-party claim of a Java-8
   ceiling. Unresolved; Step 2 settles it by running it.
2. **The four appendix SQL files are absent.** So is the comparison tool, the
   expected result data, and any DDL. The package is the generator and nothing
   else.
3. **Confirmed against the page HTML** that TPC publishes only two TPC-DI
   artifacts. The appendices archive the spec refers to is not public.

Also recovered: the authoritative DIGen usage (`-sf` default 5, range 3 to
2147483647) and the generation schema's scaling constants.

**Consequence:** the plan's oracle structure changed. See the correction above.
Steps 3, 4 and 10 are affected.

### Step 2 — Generate at SF=5 inside a container

**Do.** Build an `eclipse-temurin:8-jre` stage; run
`java -jar DIGen.jar -o /staging/sf5 -sf 5`. Generation is untimed (Clause
1.2.2.1.1), so it can be cached per scale factor exactly as `dsdgen` output was.

**Validate against.** Clause 2.3 (three directories) and Clauses 2.2.2.1–2.2.2.20
(the file inventory).

**Check.**
- `Batch1/`, `Batch2/`, `Batch3/` all exist.
- `digen_report.txt` exists, is non-empty, and reports per-batch row counts.
- Every file the spec names is present where the spec says it should be, and the
  Batch2/3 OLTP files have the two extra leading CDC columns while the Batch1 ones
  do not.
- Record staging size on disk — this is the first real input to the scale-factor
  sizing in Step 12.

**This is also where the Java-8 claim gets settled.** If DIGen runs on a modern
JVM after all, say so in the report and drop the extra container stage.

---

### Step 3 — DDL for the twenty tables

**Do.** Write `sql/di/01_schema.sql` for `DimAccount`, `DimBroker`, `DimCompany`,
`DimCustomer`, `DimDate`, `DimSecurity`, `DimTime`, `DimTrade`, `DImessages`,
`FactCashBalances`, `FactHoldings`, `FactMarketHistory`, `FactWatches`,
`Industry`, `Financial`, `Prospect`, `StatusType`, `TaxRate`, `TradeType`,
`Audit`.

**Validate against.** Clause 3.2, field by field: name, type, nullability. Column
*order* is free (Clause 3.4.5) but every listed column must exist.

**Check — and this one is free and strong.** The fifteen extract queries in Clause
8.2.2.2 name, by exact spelling, most of the columns in the warehouse. Run all
fifteen against the empty schema. If every one parses and returns zero rows, our
column names and the spec's agree. A typo in a column name is otherwise the kind
of error that surfaces three steps later.

Also assert the dependency list in Table 4.3 (Clause 4.3.3) as real foreign keys,
or at minimum as a set of checking queries.

---

### Step 4 — Load the `Audit` table and the reference tables

**Do.** Load every `*_audit.csv` into `Audit` (skipping the header row — Clause
7.2.6.2 is explicit that the header must not be loaded), and load `Industry.txt`,
`StatusType.txt`, `TaxRate.txt`, `TradeType.txt`, `Date.txt`, `Time.txt`.

**Validate against.** Oracle 2, trivially: the CSVs themselves.

**Check.** `SELECT COUNT(*) FROM Audit` equals total data lines across all
`*_audit.csv` minus one header per file. `DimDate` and `DimTime` row counts equal
the line counts of `Date.txt` and `Time.txt`.

Doing this *first* is deliberate: the `Audit` table is the ground truth every
later step is measured against, so it should exist before there is anything to
measure.

---

### Step 5 — Historical Load: `DimBroker` from `HR.csv`

**Do.** Clause 4.5.2. The smallest real transformation — a handful of columns, a
manager hierarchy, SCD-2 scaffolding that never actually fires in the historical
load.

**Validate against.** Oracle 2 (`Audit` rows with `DataSet = 'HR'`), and Oracle 5
(`DimBroker.txt` must match completely — it is one of the tables Clause 8.4.1.2.1
requires to match in full even under failure testing, which tells us it is fully
determined).

**Check.** `DimBroker` row count against the `Audit` attribute for HR. Every row
`IsCurrent = TRUE`, `EndDate = 9999-12-31` (Clause 4.4.1.2).

Pick this first because it exercises the whole pipeline shape — read, transform,
surrogate key, insert, log to `DImessages` — at a size small enough to eyeball.

---

### Step 6 — Historical Load: FINWIRE → `DimCompany`, `DimSecurity`, `Financial`

**Do.** Clauses 4.5.3, 4.5.6, 4.5.14. Parse fixed-width records, three layouts
(`CMP`, `SEC`, `FIN`) discriminated by three characters at column 16, interleaved
in one file per quarter and ordered by posting timestamp.

**Validate against.** Clause 2.2.2.8 for the field widths; Oracle 2 for counts;
Oracle 5 for content — `DimCompany`, `DimSecurity` and `Financial` are all in the
must-match-completely list.

**Check.**
- Every input record is classified; no record falls through the three types.
- Records processed in PTS order, since SCD-2 effective dating depends on it.
- The daily-grain invariant (Clause 4.4.1.5): **at most one row per natural key
  per day**. This is a one-line query and it catches a whole class of SCD bugs:
  `select CompanyID, EffectiveDate, count(*) from DimCompany group by 1,2 having count(*) > 1`
  must return nothing.
- SCD-2 chain integrity: for each natural key, exactly one `IsCurrent = TRUE`,
  and the `EndDate` of each row equals the `EffectiveDate` of its successor with
  no gaps and no overlaps.

---

### Step 7 — Historical Load: `CustomerMgmt.xml` → `DimCustomer`, `DimAccount`

**Do.** Clause 4.5.1 and 4.5.4. Replay the action stream in document order: new
customer, add account, update account, update customer, close account, inactivate.

**Validate against.** Oracle 2 (`Audit` entries for the customer management data
set) and Oracle 5.

**Check.** The same SCD-2 invariants as Step 6, applied to `DimCustomer` and
`DimAccount`. Plus the dependency in Table 4.3: every `DimAccount.SK_CustomerID`
and `SK_BrokerID` must resolve, and must resolve to the row *current at the
account's effective date* — not to the latest row. A join that silently picks the
current row is the most likely bug in this step and the FK constraint will not
catch it, so it needs its own query.

---

### Step 8 — Historical Load: the facts

**Do.** Clauses 4.5.9–4.5.12 and `DimTrade` — `FactCashBalances`, `FactHoldings`,
`FactWatches`, `DimTrade` — in dependency order per Table 4.3.

**Validate against.** Oracle 2 for counts and sums; Oracle 5 for content.

**Check.** Every surrogate key in Table 4.3 resolves (as FK constraints, or as a
set of anti-join queries returning zero rows). Row counts against the `Audit`
attributes. `FactCashBalances` in particular has running-total semantics that the
`Audit` table carries sums for — compare the sums, not just the counts.

---

### Step 9 — Historical Load: `FactMarketHistory`

**Do.** Clause 4.5.11. Last, alone, because it is the expensive one: 52-week high
and low plus the *earliest date* each occurred, P/E from the trailing four
quarters of FINWIRE EPS, and yield from the security's dividend.

**Validate against.** Oracle 2 and Oracle 5.

**Check.**
- The spec warns twice that a security's surrogate key may change inside the
  lookback window, so a correctness check is that the result is *unchanged* when
  computed by natural key (`Symbol`) versus by `SK_SecurityID`. If those differ,
  the window is grouped on the wrong key.
- Where a company has no earnings, `PERatio` is NULL **and** a `DImessages` row
  exists with `MessageSource = 'FactMarketHistory'`, `MessageType = 'Alert'`,
  `MessageText = 'No earnings for company'` and `MessageData = 'DM_S_SYMB = …'`.
  The audit query checks these strings. Getting the data right and the message
  wrong still fails.
- Time it separately from the start. If this one step dominates the Historical
  Load, that is the report's performance story.

---

### Step 10 — Build and pass our own audit at SF=5

**Revised.** The original plan was "run `tpcdi_audit.sql`". That file does not
exist, so this step is now authorship as well as verification.

**Do.** Write `sql/di/audit.sql` — our reconstruction of the Clause 7.2.6 audit —
as a set of named tests each returning OK or FAIL, covering:
- every row count and sum in the `Audit` table versus the warehouse;
- the structural invariants listed under "What replaces oracles 3–5";
- the `DImessages` alert rows Clause 4 prescribes, by exact text;
- referential integrity across all of Table 4.3.

Also write `sql/di/validation.sql`, our phase-boundary capture, which must write
the PCR row because Clause 7.5.1 times the run from it.

**Validate against.** Oracles 0, 1 and 2.

**Check.** Every test reports OK at SF=5. Then cross-check the headline row
counts against an independent public implementation at the same scale factor —
corroboration, not authority.

**This is still the milestone.** Everything before it is construction; everything
after it is measurement. Nothing gets optimised before it passes. And the report
must be explicit that this audit is ours, reconstructed from the specification,
not TPC's supplied one — the distinction matters to anyone reading our numbers.

### Step 11 — Incremental Updates

**Do.** Clause 4.6 over `Batch2` then `Batch3`. CDC replay with `I`/`U`/`D` flags
in `CDC_DSN` order; `Prospect.csv` change detection with no flags at all; a second
database session running the visibility queries (Clause 7.3) concurrently.

**Validate against.** Oracles 2, 3, 4, 5 — all of them again, now with
`BatchID` 2 and 3.

**Check.**
- The audit passes again after each update. It is not enough that the historical
  load was right.
- The SCD-2 invariants hold *across* batches, not just within one.
- Clause 4.6.14's trap: a `Prospect` row whose `IsCustomer` flag changed but whose
  values did not must **not** be counted as an update in the `DImessages` count.
  Easy to get wrong, specifically checked.
- Visibility queries ran from a different session than the loader (Clause 7.3.7),
  at intervals under five minutes.
- `DImessages` has exactly four PCR rows when the run ends (BatchID 0, 1, 2, 3).

---

### Step 12 — Size the four scale factors

**Do.** Generate at candidate scale factors, load each, record staging size,
warehouse size and Historical Load wall time. Push up until the machine is the
limit — the Part I box: 16 GiB host, Docker VM 12 GiB configured / ~11.7
effective, 220 GiB VM disk. That becomes 1.0; then roughly 0.1 / 0.2 / 0.5 of it,
snapped to integers DIGen accepts.

**Validate against.** Measured numbers only, no extrapolation. Our current sizing
belief — "SF 10 ≈ 1 GB raw, minimum SF 3" — is third-party and unverified; Step 2
replaces it with a measurement.

**Check.** Each chosen scale factor completes a full run with the audit passing.
A scale factor whose audit fails is not a data point.

---

### Step 13 — Measure

**Do.** Full runs at all four scale factors, twice each, the way Part I's final
campaign was run.

**Validate against.** Oracle 3 — the phase timings are differences between
`MessageDateAndTime` on the `PCR` rows in `DImessages` (Clause 7.5.1), not our
driver's stopwatch. Row counts come from `digen_report.txt` (Oracle 1), not from
our own counting.

**Check.**
- `TPC_DI_RPS = Trunc(GeoMean(TH, Min(TI1, TI2)))` with
  `TH = RH/EH` and `TIx = RIx / Max(EIx, 1800)`.
- Report the raw elapsed times and uncapped throughputs **beside** the official
  metric, and say why: at our sizes the 1800-second floor substitutes for the real
  elapsed time every time, so the incremental half of the official metric measures
  the data rather than the system.
- The audit passed on every run that contributes a number. Same discipline as
  Part I: assert the configuration matches before comparing two figures.

---

### Step 14 — Report and package

**Do.** PDF report plus a reproducible ZIP, one command, OS-independent, in the
shape of Part I's `benchmark.py`.

**Validate against.** The course brief: replicable by the lecturer on standard
infrastructure.

**Check.** Unpack the ZIP into a clean directory on a machine that has never run
it, run the one command, and confirm it generates, loads, runs and reports. Part
I's lesson applies — test the ZIP from a fresh directory before submission, and
let the tests gate the push rather than decorate it.

---

## What this plan assumes, and should be revisited if wrong

Updated after Phase 1. Resolved:

- ~~That the tools zip contains the four appendix SQL files.~~ **It does not.**
- ~~That the comparison tool and expected results are obtainable.~~ **They are
  not published.** Oracle 5 is gone; Step 10 rests on our own audit suite.

Still open:

- That DIGen needs Java ≤ 1.8. The vendor README says 7 or above; third parties
  say otherwise. Settled in Step 2 by running it.
- That SF=5 data is small enough to iterate on quickly. The scaling constants
  suggest it is very small, but they also contradict the "SF 10 ≈ 1 GB" figure
  from third-party repositories, so neither is trusted until `digen_report.txt`
  exists.
- That the `*_audit.csv` files carry enough coverage to serve as the primary
  oracle. The generator ships 15 audit definitions, which looks sufficient, but
  what they actually contain is unseen until Step 2.
