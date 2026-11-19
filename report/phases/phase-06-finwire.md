# Phase 6 — FINWIRE into DimCompany, DimSecurity and Financial

**Status: COMPLETE.** 31 of 31 checks pass, at SF=5 and SF=3. All 32 of the
audit query's tests for these three tables report OK. 2026-11-19.

Plan step: `../01-steps-and-validation.md` Step 6.

---

## 1. Goal

Parse 203 quarterly fixed-width files carrying three record layouts, and build
the first dimensions whose history actually changes.

Phase 5 had the SCD-2 columns but nothing ever expired a row — `HR.csv` is a
snapshot. Here companies and securities change across fifty years of postings,
so rows are superseded for real, and every SCD-2 invariant has to hold.

## 2. Where the inputs come from

| Input | Where | What it gives |
|---|---|---|
| File format and field widths | Clause 2.2.2.8 | Three layouts, the column-16 discriminator, the variable last field |
| `DimCompany` rules | Clause 4.5.3 | CIK as natural key, the coded-field lookups, the S&P rating alert |
| `DimSecurity` rules | Clause 4.5.6 | Symbol as natural key, the company resolution |
| `Financial` rules | Clause 4.5.14 | One row per company per quarter |
| History tracking | Clause 4.4.1 | Effective dating, daily grain, surrogate keys |
| Ground truth | `Audit` `FW_CMP`/`FW_SEC`/`FW_FIN` | 2,500 / 4,000 / 198,061 at SF=5 |

Field layouts with byte offsets: `../file-to-table-mapping.md` §4. They are
transcribed there so nobody needs the PDF, and **checked against the data** —
the widths sum to 543, 160+10|60 and 186+10|60, and the measured line lengths
are 544, 171|221 and 197|247, one more each because the line still carries its
`\r`.

Code: `../../tpcdi/finwire.py` (parsing), `../../sql/di/04_finwire_stage.sql`,
`../../sql/di/05_finwire_transform.sql`.

## 3. How the data is read, and why

**All 203 files in one pass — and the reason is correctness, not size.**

The files total 46.3 MB and 204,561 records, which would fit in memory many
times over. But SCD-2 effective dating depends on posting-timestamp order, and
that order is **global across files**: a company posted in `FINWIRE1990Q1` can
be superseded by a record in `FINWIRE1990Q2`. Parsing quarter by quarter and
loading each separately would produce correct-looking rows with wrong effective
dates wherever a natural key spans a boundary.

Python reads the concatenated stream, classifies every record by characters
16–18, and streams three TSVs into
`LOAD DATA LOCAL INFILE '/dev/stdin'`. Nothing touches disk, and because
`LOCAL` is read by the *client*, it sidesteps both the read-only staging mount
and `secure_file_priv`. The transform is then entirely set-based SQL.

Measured at SF=5: parse and stage 3.7 s, transform 1.7 s.

**Memory does not depend on the scale factor.** Three `LOAD DATA` clients run
concurrently, one per record type, and parsed rows go down the matching pipe
in blocks of 1,000 (`finwire.CHUNK_ROWS`). At SF=5 that is 206 writes rather
than 204,561, and at most 3,000 rows resident at any moment.

| | over a bare interpreter |
|---|---|
| Chunked streaming (what ships) | **+1.0 MiB** |
| Buffering (what it replaced) | +47.9 MiB |

The first version held every parsed row before sending it: 55 MiB at SF=5,
scaling linearly to roughly 1.1 GiB at SF=100 and 5.5 GiB at SF=500, which
would not fit on the machine this is measured on. The ceiling was never going
to bite at the scale factors Phase 12 will pick, but it cost nothing to
remove, and Phase 7's XML replay should use the same shape.

## 4. Results

| | SF=5 | SF=3 |
|---|---|---|
| Records parsed | 204,561 | 102,576 |
| `CMP` / `SEC` / `FIN` | 2,500 / 4,000 / 198,061 | 1,500 / 2,400 / 98,676 |
| Distinct companies | 2,096 | — |
| `DimCompany` rows | 2,500 | 1,500 |
| `DimSecurity` rows | **4,732** | 2,795 |
| `Financial` rows | 198,061 | 98,576 |
| Invalid S&P ratings reported | 265 (10.6%) | 150 (10.0%) |

The alert rate matches `Generator/FWSPRatingErrPct = 0.1` — the generator
injects them deliberately, and Clause 4.5.3.3 requires each to be reported and
the rating nulled.

`DimSecurity` holding **more** rows than there are `SEC` records is not a
mistake. See §6.

## 5. Validations

| # | Check | Result |
|---|---|---|
| F1 | Every record classified; staged counts equal the files | **PASS** |
| F2 | Row counts within the `Audit` bounds, in the right direction | **PASS** |
| F3 | SCD-2 chains: distinct keys, one current row, ends at 9999-12-31, no zero-length rows, no overlaps, no gaps | **PASS** |
| F4 | Daily grain: at most one row per natural key per day | **PASS** |
| F5 | Every `SK_CompanyID` resolved, none dangling, both CoNameOrCIK forms exercised | **PASS** |
| F6 | Invalid S&P ratings nulled and reported, one alert each | **PASS** |
| F7 | `isLowGrade` False for A\*/BBB\*, True otherwise | **PASS** |
| F8 | All 32 audit tests for these three tables report OK | **PASS** |

```
python verify_di.py --scale 5    -> passed 100, failed 0
python verify_di.py --scale 3    -> passed 100, failed 0
```

### Verifying tests that depend on a later phase

Three audit tests — the row counts — compare a table against a `Row count`
**Validation** message rather than against the table itself, and those messages
come from `tpcdi_validation.sql`, which Phase 10 runs. Rather than defer the
checks, `common.run_audit_after_validation()` runs the validation query inside
a transaction so the audit can see its messages, then rolls the whole thing
back. The warehouse is unchanged — `DImessages` holds 269 rows before and after
a full verification — and the tests become answerable now.

Their batch-2 and batch-3 variants remain red until Phase 11 loads those
batches, which is correct.

### Negative tests

Reading the record type one column early — `line[14:17]` instead of
`line[15:18]` — fails immediately and legibly:

```
FINWIRE parse failed: line 1: unknown record type '5CM'
```

Breaking the `CoNameOrCIK` branch so every value is treated as a name produces
2,360 `DimSecurity` rows instead of 4,732, caught by F2 and by three audit
tests. Restoring returns 31 of 31.

## 6. The finding: a security row is split wherever its company changes

This cost the most time and is the most useful thing in the phase.

**Clause 4.5.6.2 reads like a point lookup.** `SK_CompanyID` is resolved
"where PTS >= EffectiveDate and PTS < EndDate" — the company record current at
the security's own posting date. Implemented that way, everything looked right:
4,000 rows, every key resolved, every SCD-2 invariant intact.

**One audit test disagreed.** `DimSecurity SK_CompanyID` requires the
security's *whole* range to sit inside the company row's range:

```sql
c.EffectiveDate <= a.EffectiveDate and a.EndDate <= c.EndDate
```

673 of 4,000 rows failed it — securities that outlive the company record they
point at. Example: symbol `AAAAAAAAAAAAAAB` runs 1968-03-30 to 9999-12-31,
while company 51's record runs 1967-03-02 to 1969-06-22.

**The apparent contradiction.** Satisfying containment means splitting a
security's interval at company-change boundaries, which adds rows — and the
row-count test would then seem to fail.

**The directions of the tests resolve it.** They are not all the same way
round, and the difference carries the meaning:

| Test | Direction | Why |
|---|---|---|
| `DimCompany row count` | `<=` `FW_CMP` | Clause 4.5.3.1 drops records unchanged from the one before |
| `DimSecurity row count` | **`>=`** `FW_SEC` | described as "matches or **exceeds**" — splitting adds rows |
| `Financial row count` | `<=` `FW_FIN` | a quarter is never restated (4.5.14.1) |

So the audit *expects* more security rows than there are `SEC` records. The
implementation intersects each security interval with every overlapping company
interval and emits one row per intersection, which satisfies containment and
the `>=` simultaneously: 4,732 rows from 4,000 records.

**The lesson, which is general.** A row count that merely looks plausible is
not evidence. Three different tables, three different bounds, in two different
directions — and the one that looked most obviously "wrong" was the one telling
us what the rule actually was. `PHASES.md` now says to read the direction of a
comparison, not just its operands.

## 7. Other things worth recording

**The dedup rule fires, but never here.** Clause 4.5.3.1 says unchanged `CMP`
records are not recorded, and the transform hashes thirteen content fields to
detect them. At SF=5 it drops none: all 404 repeat records for the 2,096
companies genuinely differ. Checked by hand on company `0000000000`, whose two
records differ in `IndustryID` (`DR` → `CV`) and `FoundingDate`. The machinery
is right and the data simply does not exercise it — worth knowing before
someone "fixes" it.

**Daily grain is load-bearing, not tidying.** The audit's `consolidation` test
fails any row whose `EffectiveDate` equals its `EndDate`, which is exactly what
two same-day changes would produce. The `ROW_NUMBER` collapse is what prevents
it, and the split in §6 needs its own `WHERE EffDate < EndDate` for the same
reason.

**Both halves of `CoNameOrCIK` are exercised**: 2,013 `SEC` records reference
their company by CIK and 1,987 by name, matching
`Generator/FWCIKPct = 0.5`. F5 asserts both are non-zero, because a parser that
handled only one form would still resolve every key — by silently matching the
wrong company.

**Two indexes were added to `DimCompany`**, on `(CompanyID, EffectiveDate,
EndDate)` and `(Name, EffectiveDate, EndDate)`. Without them the as-of-date
lookup scans `DimCompany` once per staged row, 200,000 times. Clause 3.1.1
leaves indexing to the implementation and 7.2.3.2 permits auxiliary structures.
They are dropped and recreated when the phase is re-run.

## 8. What this phase settles for later work

- **The FINWIRE parser is reusable and tested.** `tpcdi/finwire.py` has no
  database dependency.
- **`LOAD DATA LOCAL INFILE '/dev/stdin'`** is the route for anything Python
  must parse: no temporary files, no writable mount, no `secure_file_priv`
  change. Phase 7's XML replay should use it.
- **`run_audit_after_validation()`** lets any phase check audit tests that
  depend on validation messages, without waiting for Phase 10.
- **Read the direction of an audit comparison.** See §6.
- **`DimCompany` and `DimSecurity` are complete**, so Phase 8's facts can
  resolve their company and security keys, and Phase 9 has the `FI_BASIC_EPS`
  values it needs for `PERatio`.
- Audit suite: **123 of 167** OK, up from 116.

## 9. Reproducing this

```
python benchmark_di.py --scale 5 --phase 6
python verify_di.py    --scale 5 --phase 6
```
