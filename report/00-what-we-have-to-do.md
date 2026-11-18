# TPC-DI: what Part II actually requires

Written 2026-11-18, from the TPC-DI Standard Specification v1.1.0 (November 2014,
117 pages) read in full, plus the course brief. Clause numbers below refer to the
spec so anything here can be checked against it.

Deadline: **24 December 2026**. Five weeks and change from today.

---

## 0. What the course asks for

Same shape as Part I, and the same group:

- A **PDF report** explaining the essential aspects of the implementation.
- A **ZIP of the code**, with instructions good enough that the lecturer can
  replicate it on standard computing infrastructure.
- **Four scale factors.** The rule is ours to set: estimate the biggest that fits
  on our own machine, call that 1.0, then run three smaller ones (the brief
  suggests 0.1, 0.2, 0.5) so the performance curve is visible.
- Tool choice is free. The brief names SSIS, Pentaho Data Integration, Talend,
  **or SQL scripts**, loading into any DBMS.

So the last clause matters: a hand-written ETL in SQL plus a driver program is an
explicitly sanctioned implementation, not a shortcut.

---

## 1. What TPC-DI is

TPC-DI is the TPC's ETL benchmark. Where TPC-DS measures *querying* a warehouse,
TPC-DI measures *building* one. The business model is a retail brokerage: an OLTP
trading system, an HR system, a customer-management system, a financial newswire
and a bought-in prospect list, all feeding one dimensional warehouse.

The benchmark deliberately does **not** test extraction from live source systems.
Everything arrives as flat files in a **Staging Area**, and the System Under Test
is measured from "read the staging files" to "data is in the warehouse" (Clause
1.2.1, Clause 5).

The headline number is a throughput: **source rows processed per second**
(Clause 0.1.1).

TPC lists TPC-DI under *obsolete workloads*, but v1.1.0 is still the current and
only specification and the tools are still downloadable. For a course project
that is irrelevant — it is the benchmark we were assigned.

---

## 2. The source data

DIGen writes three directories into the staging area (Clause 2.3):

| Directory | Used by | Contents |
|---|---|---|
| `Batch1` | Historical Load | Full extracts, multi-year history |
| `Batch2` | Incremental Update 1 | One day of CDC changes |
| `Batch3` | Incremental Update 2 | One more day of CDC changes |

**Five source "systems", five different formats** — this variety is the whole
point of the benchmark:

1. **OLTP extracts** — pipe-delimited text. `Account.txt`, `Customer.txt`,
   `Trade.txt`, `TradeHistory.txt`, `CashTransaction.txt`, `HoldingHistory.txt`,
   `DailyMarket.txt`, `WatchHistory.txt`. In Batch1 these are full extracts; in
   Batch2/3 they are **CDC extracts** carrying two extra leading columns,
   `CDC_FLAG` (`I`/`U`/`D`) and `CDC_DSN` (a monotonically increasing sequence
   number). An update row does not say *what* changed. A row may change several
   times in one day. Unchanged rows simply do not appear. (Clause 1.3.2)
2. **HR** — `HR.csv`, a full table extract, historical load only.
3. **Prospect** — `Prospect.csv`, a full daily extract from an external data
   provider with **no change indicators at all**. Working out what changed is our
   job. (Clause 1.3.4)
4. **FINWIRE** — one fixed-width file per quarter (`FINWIRE1967Q1`, …), with
   **three different record layouts interleaved in the same file**, discriminated
   by three characters starting at column 16: `CMP` (company), `SEC` (security),
   `FIN` (quarterly financials). No field separators. Text padded right with
   spaces, numerics padded left. Historical load only. (Clause 2.2.2.8)
5. **CustomerMgmt** — `CustomerMgmt.xml`, hierarchical, transactional: a stream of
   `Action` elements (new customer, add account, update account, update customer,
   close account, inactivate) that must be replayed in order to derive the
   historical state of `DimAccount` and `DimCustomer`. (Clause 1.3.6)

Plus reference files loaded once and never touched again: `Date.txt`, `Time.txt`,
`Industry.txt`, `StatusType.txt`, `TaxRate.txt`, `TradeType.txt`, and
`BatchDate.txt` (a single date, the extraction date for that batch).

And **audit files**, `<name>_audit.csv`, generated into every batch directory.
These are not warehouse data — they are the generator's own record of what it
produced, loaded at the end into the `Audit` table so the audit query can check
our work against ground truth (Clause 2.2.2.20).

---

## 3. The destination warehouse

Twenty tables (Clause 3.2). A Kimball-style dimensional model:

**Dimensions** — `DimAccount`, `DimBroker`, `DimCompany`, `DimCustomer`,
`DimDate`, `DimSecurity`, `DimTime`, `DimTrade`.
**Facts** — `FactCashBalances`, `FactHoldings`, `FactMarketHistory`,
`FactWatches`, and `DimTrade` again.
**Reference** — `Industry`, `Financial`, `Prospect`, `StatusType`, `TaxRate`,
`TradeType`.
**Operational** — `DImessages` (the DI log: status, alerts, validation output and
the phase-completion records), `Audit`.

`DimTrade` is both dimension and fact depending on use, and unlike the other
dimensions it is *not* kept as a slowly-changing dimension — it is updated in
place (Clause 3.2.7.1).

`DImessages` is not optional bookkeeping. The phase timings are read out of it
(see §5), and the audit query reads the messages we wrote to decide whether our
transformations were correct.

---

## 4. The run: five phases, in order (Clause 7.2.2)

1. **Initialization** — create the warehouse, run the batch validation query,
   write a phase-completion record (PCR) to `DImessages`. *Not timed.* Touching
   the staging data here is forbidden (7.2.3.4) — no pre-reading, no warm caches.
2. **Historical Load** — the Clause 4.5 transformations over `Batch1`, then the
   batch validation query, then a PCR. **Timed.** May take as long as it takes.
3. **Incremental Update 1** — the Clause 4.6 transformations over `Batch2`, then
   validation, then PCR. **Timed**, and must finish within 3600 s.
4. **Incremental Update 2** — same, over `Batch3`. **Timed**, same limit.
5. **Automated Audit** — load the `Audit` table, run `tpcdi_audit.sql`. *Not
   timed*, but **every test must report OK** or the run is invalid (7.2.6.7).

Once started, the run must complete all phases without manual intervention
(7.2.2.2) — which is exactly the one-command-does-everything shape we already
built for Part I.

One extra requirement that is easy to miss: during both Incremental Updates, the
**data visibility queries** must run from a *separate database session*, at
intervals of no more than five minutes (Clause 7.3). At our scale an update phase
will likely last less than five minutes, so this reduces to one visibility-1 query
near the start and one near the end — but it still means a second connection
running concurrently with the load.

---

## 5. The metric (Clause 7.5, 7.6)

Timings are *not* wall-clock from our driver. They are differences between
`MessageDateAndTime` values of the `PCR` rows in `DImessages`:

```
EH  = CT1 - CT0            Historical Load elapsed
EI1 = CT2 - CT1            Incremental Update 1 elapsed
EI2 = CT3 - CT2            Incremental Update 2 elapsed
```

Row counts `RH`, `RI1`, `RI2` come from `digen_report.txt`, which DIGen writes and
which must not be edited (Clause 6.3).

```
TH  = RH  / EH
TI1 = RI1 / Max(EI1, 1800)
TI2 = RI2 / Max(EI2, 1800)

TPC_DI_RPS = Trunc( GeoMean( TH, Min(TI1, TI2) ) )
```

**The 1800-second floor is going to dominate our numbers and we should say so in
the report.** The TPC put it there to stop sponsors running trivially small
updates; at the scale factors that fit a laptop, our update phases will finish in
seconds, so `Max(EI, 1800)` will substitute 1800 every time and the incremental
half of the metric becomes "rows in the batch ÷ 1800" — a measure of the *data*,
not of our *system*. We should report the official `TPC_DI_RPS` for compliance
and, alongside it, the raw elapsed times and uncapped throughputs, which are the
numbers that actually respond to our engineering. That distinction is the kind of
measurement observation Part I was built on.

There are two result classes, ACID and OPEN (Clause 1.6). OPEN is the minimum.
MariaDB with InnoDB would qualify for ACID claims, but the full ACID criteria
(Clause 8.4, durability tests) are beyond what the course asks.

---

## 6. The transformations that will cost us

Clause 4 is 28 pages. The parts that are genuinely hard:

- **Slowly Changing Dimensions, Type 2** on `DimCustomer`, `DimAccount`,
  `DimSecurity` and `DimCompany`. Every change opens a new row with
  `EffectiveDate`/`EndDate`/`IsCurrent` and a fresh surrogate key, and every later
  lookup must resolve to the row current *as of the relevant date*, not the latest
  one.
- **`FactMarketHistory`** — the expensive one (Clause 4.5.11). For every security
  on every day it needs the 52-week high and low *and the earliest date on which
  each occurred*, a P/E ratio from the trailing four quarters of EPS out of
  FINWIRE `FIN` records, and a yield from the security's dividend. The spec warns
  twice that a security's surrogate key may have changed inside the window, so
  the lookback cannot simply group by `SK_SecurityID`. In SQL this is a window
  function over a ~2.5M-row-per-SF table; done naively it is a self-join that will
  not finish.
- **CDC replay** in the updates. `U` rows give no hint as to which columns moved,
  so every update is a full comparison against the current row to decide whether
  it is an SCD-2 change, an in-place correction, or a no-op.
- **Prospect change detection** with no CDC flags, including the rule that a row
  whose `IsCustomer` flag changed but whose values did not must *not* count as an
  update (Clause 4.6.14).
- **Error checking and alerts.** Business-rule violations must be written to
  `DImessages` as `Alert` rows with exact `MessageSource`/`MessageText`/
  `MessageData` strings. The audit query checks these. "It loaded" is not the pass
  condition; "it loaded and complained about exactly the right rows" is.
- **The XML replay** for the historical customer/account state.
- **Fixed-width parsing with three record types per file** for FINWIRE.

This is a different kind of work from Part I. Part I was *measurement* — the
schema was given and the difficulty was telling signal from noise. Part II is
*construction* — the difficulty is correctness, and the audit query is a
pass/fail oracle we cannot argue with.

---

## 7. What we have to download — and the constraint that bites

Two artifacts from tpc.org:

1. **The specification PDF** (we have it):
   `https://www.tpc.org/tpc_documents_current_versions/pdf/tpc-di_v1.1.0.pdf`
2. **`TPC-DI_Tools_v1.1.0.zip`** — not a direct download. It goes through a
   request form; TPC emails a time-limited link:
   `https://www.tpc.org/tpc_documents_current_versions/tools-download-request5.asp?bm_type=TPC-DI&bm_vers=1.1.0&mode=CURRENT-ONLY`
   (form confirmed reachable, 2026-11-18)

The tools zip contains `DIGen.jar` (built on bankmark's PDGF) and the four
appendix SQL files the spec refers to but does not print:

- `tpcdi_audit.sql` — Appendix A, the pass/fail oracle
- `tpcdi_validation.sql` — Appendix B, run at the end of every phase
- `tpcdi_visibility_1.sql`, `tpcdi_visibility_2.sql` — Appendix C

**The constraint: DIGen is a Java 7/8 program and multiple independent reports say
it will not run on a JVM newer than 1.8.** Our Macs ship nothing like that. This
is not a problem for us — it is an argument that generation belongs in a
container, the same way `dsdgen` did in Part I. An `eclipse-temurin:8-jre` stage
keeps the host clean, keeps the "everything runs in Docker" rule, and keeps the
lecturer's machine from needing a legacy JDK.

Usage appears to be `java -jar DIGen.jar -o <output-dir> -sf <N>`, with `-sf`
defaulting to 5. **Treat the Java-8 limit, the default and the minimum scale
factor as unverified until we have run it ourselves** — they come from third-party
repositories, not from TPC.

Modifying DIGen is forbidden (Clause 6.2.2) and the same scale factor must be used
for all three batches of a run (7.2.1.1.3).

---

## 8. Scale factors

DIGen's `-sf` is not a multiplier of a 1 GB unit the way TPC-DS's is. Secondary
sources put SF 10 at roughly 1 GB of raw staging data, scaling linearly, with the
minimum SF around 3. **We will measure this ourselves on the first generation run
rather than trust it.**

The course wants a reference size and three smaller ones. Translating:

- Generate at a candidate SF, record staging size on disk, warehouse size after
  load, and historical-load wall time.
- Push up until the Historical Load becomes the limiting factor on the machine we
  used for Part I (16 GiB host, Docker VM at 12 GiB configured / ~11.7 effective,
  220 GiB VM disk). That is our 1.0.
- Then take roughly 0.1 / 0.2 / 0.5 of it, snapped to integers DIGen accepts.

Note that TPC's own sizing rule is a *time* rule, not a byte rule: an SF is legal
if each Incremental Update completes in ≤ 3600 s (7.2.1.1.1). Our binding
constraint will instead be the Historical Load and the disk, because the update
batches are a single day of changes and are tiny next to Batch1.

The data is also far smaller than Part I's. TPC-DS SF=8 cost us ~69 GB; a TPC-DI
reference scale factor that takes an hour to load is likely only a few GB of
source files. The expense here is CPU in the transformations, not I/O volume.

---

## 9. Proposed architecture

Reuse what Part I proved, because most of it transfers:

```
benchmark_di.py --scale N          one command, any OS, Docker only
  └── docker/di/Dockerfile         MariaDB + Python + a Java 8 layer for DIGen
       ├── generate   DIGen -sf N  → Batch1/2/3   (untimed, cached per SF)
       ├── init       DDL + validation query + PCR   (untimed)
       ├── historical Clause 4.5   + validation + PCR   (TIMED)
       ├── update1    Clause 4.6 on Batch2 + validation + PCR   (TIMED)
       ├── update2    Clause 4.6 on Batch3 + validation + PCR   (TIMED)
       └── audit      load Audit table + tpcdi_audit.sql        (untimed)
  └── REPORT.md + report.csv       per-phase elapsed, row counts, throughputs,
                                   TPC_DI_RPS, audit pass/fail, rows per table
```

What carries over directly: the Docker-only prerequisite and preflight checks, the
`--scale` interface, generating the report *inside* the container so there is one
implementation rather than one per OS, the per-scale results bundle, the evidence
ledger, and the habit of asserting the configuration matches before comparing two
numbers.

What is new: the ETL itself, the XML and fixed-width parsers, the SCD-2 logic, a
second concurrent session for the visibility queries, and a correctness oracle we
did not have in Part I.

Sequencing I would suggest:

1. Get the tools zip; generate at the smallest SF; read `digen_report.txt` and the
   four appendix SQL files. Everything else is guesswork until this exists.
2. Write the DDL for the 20 tables and get the audit query to *run* (it will fail
   nearly every test — that is the baseline).
3. Historical Load, table by table, easiest first: reference tables → `DimDate`/
   `DimTime` → `DimBroker` from HR → FINWIRE → the XML replay → facts →
   `FactMarketHistory` last.
4. Get the audit to pass at the smallest SF. **This is the real milestone.** Do
   not optimise anything before it.
5. Incremental updates, then audit passing again.
6. Only then: scale out to four scale factors, measure, and tune.

The risk to manage is that step 4 is where projects stall. Working up from the
smallest SF with the audit query as a continuous check is what keeps it honest.

---

## 10. Decisions

**ETL implementation: Python + SQL driving MariaDB.** Decided 2026-11-18. The
course brief names SQL scripts as a permitted implementation alongside SSIS,
Pentaho and Talend. The deciding arguments: the whole Part I stack transfers
(Docker-only prerequisite, one `--scale N` entry point, report generated inside
the container), SQL and Python diff and review properly where a `.ktr` job file
does not, and the lecturer replicates it on Windows or Linux with Docker and
nothing else. The cost we accept is that the report has no screenshots of a
visual ETL tool; we compensate with the transformation SQL itself and the
measurement discipline from Part I.

**Target DBMS: MariaDB.** Same engine as Part I, which lets the final report
compare the two benchmarks on one system and reuses the image, the configuration
file and the load tooling.

**Still open: division of labour across the group.** Worth fixing early — the
transformations split cleanly by target table, and there are five weeks.

## 11. What in this document is not yet verified

Stated plainly so nothing here gets quoted as measured:

- DIGen's Java-8-only limitation, its `-sf` default of 5 and minimum of 3, and the
  "SF 10 ≈ 1 GB" sizing all come from third-party repositories, not from TPC.
- The exact contents of `TPC-DI_Tools_v1.1.0.zip` are inferred from the spec's
  appendix references.
- Every row count, file size and timing in §8 is an estimate until we generate.

Everything attributed to a clause number is from the specification itself.
