# Phase 5 — DimBroker from HR.csv

**Status: COMPLETE.** 18 of 18 checks pass, at SF=5 and SF=3. 2026-11-19.

Plan step: `../01-steps-and-validation.md` Step 5.

---

## 1. Goal

The first real transformation. Everything before this loaded files as they
arrived; this one filters, derives surrogate keys and writes to the log.

It is deliberately first. `HR.csv` is a flat comma-delimited file with nine
fields and no header, so there is no XML to replay, no fixed-width parsing and
no date-sensitive key lookup — just the pipeline shape, at a size that can be
read by eye. If the shape is wrong, it is cheaper to find out here than in
FINWIRE.

## 2. Where the inputs come from

| Input | Where | What it gives |
|---|---|---|
| Transformation rules | Clause 4.5.2 | The job-code filter, the column mapping, the history fields |
| Source file format | Clause 2.2.2.10 | `HR.csv`: EmployeeID, ManagerID, EmployeeFirstName, EmployeeLastName, EmployeeMI, EmployeeJobCode, EmployeeBranch, EmployeeOffice, EmployeePhone |
| Target columns | Clause 3.2.2 | `DimBroker` |
| History-tracking rules | Clause 4.4.1.2, 4.4.1.3 | `IsCurrent`, `EffectiveDate`, `EndDate`, surrogate keys |
| BatchID | Clause 4.4.2.1 | 1 for the Historical Load |
| Ground truth | `Audit.HR_BROKERS` | 7,144 at SF=5; 4,293 at SF=3 |

Output: **`sql/di/03_dimbroker.sql`** and **`tpcdi/phase05_broker.py`**.

## 3. The rules, and what they mean here

**Only `EmployeeJobCode = 314` is a broker** (4.5.2.1). `HR.csv` holds every
employee — 25,000 at SF=5 — of whom 7,144 are brokers. The remaining 17,856 are
ignored.

**It is a history-tracking dimension that never tracks any history.** Clause
4.4.1.1 lists `DimBroker` among the SCD-2 tables, but 4.5.2.1 notes the source
"is simply a snapshot of the current state of the HR data" — there is no CDC
and no change history in the file. So every row is inserted current and nothing
is ever expired. The machinery exists; in this phase it never fires.

**`EffectiveDate` is the earliest date in `DimDate`**, which is why Phase 4 had
to come first. The audit query spells this as the literal `1950-01-01`; the
implementation reads `min(DateValue)` from the table instead, so it follows the
rule rather than the constant, and the check does the same.

**Surrogate keys** are `ROW_NUMBER() OVER (ORDER BY EmployeeID)`. Clause 8.2.1.2
allows surrogate key *values* to differ between implementations provided the
key/foreign-key references are internally correct, so any unique assignment is
compliant. A deterministic one was chosen so two runs produce identical keys and
remain comparable.

## 4. How it runs

```
python benchmark_di.py --scale 5 --phase 5
python verify_di.py    --scale 5 --phase 5
```

`LOAD DATA` has no `WHERE` clause, so the filter cannot be applied while
reading. The file lands in a staging table and the filter runs as a set-based
`INSERT ... SELECT`.

**The staging table lives in a separate database, `tpcdi_stage`.** Clause 4.3.7
permits auxiliary data structures, but putting one in the `tpcdi` schema would
mean it no longer holds exactly the twenty tables of Clause 3.2 — which the
Phase 3 checks assert. Keeping them apart means the warehouse stays exactly what
the specification describes, and the Phase 3 checks keep their meaning.

## 5. Results

| | SF=5 | SF=3 |
|---|---|---|
| Employees in `HR.csv` | 25,000 | 15,000 |
| Brokers loaded | **7,144** | **4,293** |
| Excluded | 17,856 | 10,707 |
| `Audit.HR_BROKERS` | 7,144 | 4,293 |

## 6. Validations

| # | Check | Result |
|---|---|---|
| B1 | Row count = `HR.csv` rows with job code 314, and = `Audit.HR_BROKERS`; non-brokers accounted for | **PASS** |
| B2 | All `SK_BrokerID` distinct, none NULL, one row per natural key | **PASS** |
| B3 | `IsCurrent`, `EndDate`, `BatchID`, `EffectiveDate` all as Clause 4.5.2.1 requires | **PASS** |
| B4 | A sampled row matches `HR.csv` field for field | **PASS** |
| B5 | A `DImessages` Status row records the insert count | **PASS** |
| B6 | All six `DimBroker` tests in `tpcdi_audit.sql` report OK | **PASS** |

```
python verify_di.py --scale 5 --phase 5    -> passed 18, failed 0
python verify_di.py --scale 3 --phase 5    -> passed 18, failed 0
python verify_di.py --scale 5              -> passed 69, failed 0
```

### Two checks that earn their place

**B1's third assertion** is that `brokers + non-brokers = all employees`. A
count check alone would pass if the filter were inverted at the right scale
factor by coincidence; this one makes the exclusion explicit.

**B4** takes one broker from `HR.csv` and compares all seven mapped columns
against the loaded row. Every other check in this phase is a count or a
constant — a column mapping that is off by one passes all of them. `Branch`
and `Office` are adjacent and both free text, which is exactly where an
off-by-one would hide.

### Negative test

Changing the filter to `EmployeeJobCode IN (314, 647)` and reloading gives 8,990
rows. Four checks fail: B1's file comparison, B1's `Audit` comparison, and the
audit query's own `DimBroker row count` and `DimBroker distinct keys`. Restoring
the filter returns 18 of 18.

## 7. The audit suite gained a test, and it is red

Before this phase: **114 of 166** OK. After: **116 of 167**.

Six `DimBroker` tests now report OK, but only two of them changed — the other
four were already passing trivially against an empty table, since "count rows
where `IsCurrent <> 1`" is zero when there are no rows. That is worth noticing
generally: **an empty table passes most of this audit**, so a rising OK count
is not by itself evidence of progress.

The total went from 166 to 167 because writing a `DImessages` row for batch 1
created a new per-batch test, `DImessages validation reports` for batch 1, which
reports *"Validation checks not fully reported"*. That is correct. The audit
requires each batch that appears in `DImessages` to carry its full set of 24
validation messages, and those come from `tpcdi_validation.sql`, which Phase 10
runs.

So this phase added a failing test. The alternative — not writing the Status
row, since Clause 4.5.2 does not require one — would have kept the number
tidier while hiding a real requirement. Making the audit look better by writing
less is the same mistake as softening it.

## 7a. Two discrepancies worth recording

### The audit query contradicts itself about EffectiveDate

Clause 4.5.2.1 says:

> "EffectiveDate is set to the **earliest date in the DimDate table** and
> EndDate is set to 9999-12-31."

TPC's audit query tests that correctly, against a literal:

```sql
select 'DimBroker EffectiveDate', NULL, case when
    (select count(*) from DimBroker where EffectiveDate <> '1950-01-01') = 0
then 'OK' else 'Wrong date' end, 'All rows have Batch1 BatchDate as EffectiveDate'
```

`1950-01-01` is indeed the earliest date in `DimDate`, so the test agrees with
the clause. **Its description string does not.** "Batch1 BatchDate as
EffectiveDate" names a different value: Batch1's BatchDate is `2017-07-07`,
sixty-seven years later. Anyone implementing from the description rather than
the clause would fail the test.

The implementation follows the clause, reading `min(DateValue)` from `DimDate`
rather than hard-coding a date, and check B3 compares against the table the
same way. If the generator's date range ever changed, our code would follow the
rule and TPC's literal would be the thing that broke.

### A warehouse lost its Phase 5 data, and I cannot account for it

Between a passing `69 of 69` run and the next look at the database, `DimBroker`
and `DImessages` in the SF=5 warehouse were both empty, while everything Phase
4 had loaded survived and the SF=3 warehouse was untouched.

What was ruled out: the Docker VM had not restarted (5.2 hours' uptime), the
SF=3 container had been running since 20:37 with its 4,293 rows intact, and the
image's durability settings are the safe ones --
`innodb_flush_log_at_trx_commit=1`, `innodb_doublewrite=ON`,
`innodb_flush_method=O_DIRECT` -- so a committed row should survive even an
abrupt kill. No command run in between touches the database.

It did not recur: reloading, then stopping and starting the container cleanly,
leaves all 7,144 rows in place.

So this is recorded as unexplained rather than explained away. Two things
changed as a result, both of which are worth having regardless:

- `common.remove_container()` now stops a container with a 30-second timeout
  before removing it. `docker rm -f` sends SIGKILL and leaves InnoDB to do
  crash recovery on the next start; a clean shutdown costs two seconds, and a
  warehouse that has to be reloaded is a measurement lost.
- Before Phase 13 records any timing, the run must end with a clean shutdown
  and a re-verification, so that a number is never reported from a warehouse
  whose state was not confirmed afterwards.

## 8. What this phase settles for later work

- **The pipeline shape works**: staging table in `tpcdi_stage`, set-based
  `INSERT ... SELECT`, surrogate keys from `ROW_NUMBER`, a `DImessages` row.
  Phases 6 to 9 follow it.
- **`tpcdi_stage` is where auxiliary structures go**, so the warehouse keeps
  exactly its twenty tables.
- **Phase 4 is a hard prerequisite**, not just a convenience: `EffectiveDate`
  reads from `DimDate`. The loader checks and fails with a usable message
  rather than writing NULLs.
- **A rising audit OK count means little on its own.** Prefer the per-table
  tests, which is what B6 does.
- **Any batch written to `DImessages` owes 24 validation messages.** Phase 10
  settles this for batches 1, 2 and 3.
- **Read rules from the clause, not from the audit query's prose.** The
  `EffectiveDate` description is wrong where its own test is right.
- **Containers are stopped, not killed.** See 7a.

## 9. Reproducing this

```
python benchmark_di.py --scale 5 --phase 2
python benchmark_di.py --scale 5 --phase 3
python benchmark_di.py --scale 5 --phase 4
python benchmark_di.py --scale 5 --phase 5
python verify_di.py    --scale 5
```
