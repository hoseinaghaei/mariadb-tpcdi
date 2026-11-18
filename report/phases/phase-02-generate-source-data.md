# Phase 2 — Generate the source data at SF=5

**Status: COMPLETE.** All eight validations pass. 2026-11-18.

Plan step: `../01-steps-and-validation.md` Step 2.

---

## 1. Goal

Produce the TPC-DI staging area at Scale Factor 5 — `Batch1`, `Batch2`, `Batch3`
and `digen_report.txt` — entirely inside Docker, and settle four questions the
plan left open:

1. Does DIGen run on a modern JVM, or is the Java-8 ceiling real? **Settled: the
   ceiling is real.**
2. How large is SF=5, given the schema's scaling constants appeared to contradict
   the third-party "SF 10 ≈ 1 GB"? **Settled: 464 MiB; the third party was right
   and our reading of the constants was wrong.**
3. Do the generated `*_audit.csv` files carry the `DataSet`/`Attribute` pairs the
   130 audit tests read? **Settled: yes, all of them.**
4. Do Batch2/3 carry CDC columns that Batch1 does not? **Settled: yes.**

## 2. Where the inputs come from

| Input | Location | Notes |
|---|---|---|
| `DIGen.jar`, `PDGF/` | `tools/Tools/` | From `TPC-DI_Tools_v1.1.0.zip`, not committed. SHA-256 in `../../tools/MANIFEST.md` |
| Usage | `tools/Tools/README.txt` | `-sf` default 5, range 3–2147483647 |
| Expected file inventory | Spec Clauses 2.2.2.1–2.2.2.20 | `../../spec/tpc-di_v1.1.0.pdf` |
| Expected layout | Spec Clause 2.3 | `Batch1`/`Batch2`/`Batch3` |
| Row counts for the metric | `digen_report.txt` | Clause 6.3 — must not be modified |
| What the audit reads | `../../tools/appendices/AppendixA/tpcdi_audit.sql` | For V7 |

## 3. How it runs — Docker only, named volume

```
docker build -f docker/digen/Dockerfile -t tpcdi-digen:jre8 tools/
docker volume create tpcdi-staging-sf5
( printf '\nYES\n'; sleep 1800 ) | docker run --rm -i \
    -v tpcdi-staging-sf5:/staging tpcdi-digen:jre8 -o /staging/sf5 -sf 5
```

A **named volume**, not a bind mount — the same choice Part I made
(`benchmark.py:106`). It avoids host path translation on Windows and the
root-owned-files problem on Linux, and makes the command identical on all three
operating systems. Every validation below was also run inside a container against
that volume, so no result depends on the host.

Nothing is installed on the host. The professor needs Docker and nothing else.

### Why the invocation looks strange

Three separate obstacles, each of which silently produced no data:

1. **Two prompts, not one.** PDGF first prints `press [ENTER] to show them`, then
   after the licence text asks for `YES`. A single `echo YES` is consumed by the
   first prompt; the second then reads EOF. Hence `printf '\nYES\n'`.
2. **Stdin must stay open.** After acceptance, PDGF enters an interactive command
   shell. On EOF it reads `null` repeatedly until its own flood guard fires —
   `Flooding prevention! to much failed commands in a row...terminating
   application`. Hence holding the pipe open for the duration.
3. **The exit code lies.** See §5.

## 4. Results

| | |
|---|---|
| Generated | 463.9 MiB, 7,871,344 source rows |
| Wall time | 28.8 s (16.1 MiB/s, 273,054 rows/s) |
| DIGen / PDGF | 1.1.0 / v2.5_#1343_b4177 |
| JVM | Temurin 1.8.0_512, arm64 native |

Row counts from `digen_report.txt` — these are `RH`, `RI1`, `RI2` in the metric:

| Batch | Phase | Rows | On disk |
|---|---|---|---|
| Batch1 | Historical Load | 7,804,509 | 454.1 MiB |
| Batch2 | Incremental Update 1 | 33,380 | 5.6 MiB |
| Batch3 | Incremental Update 2 | 33,455 | 5.6 MiB |

**Batch1 is 99.2% of the rows.** The Historical Load is the benchmark; the two
incremental phases are rounding error by comparison. That has a direct bearing on
the metric — see §7.

## 5. Validations

Every check ran inside a container against the volume.

| # | Check | Result | Evidence |
|---|---|---|---|
| V1 | Generator completes | **PASS** | `digen_report.txt` written with an End Time and per-batch counts; log ends `DIGen completed successfully` |
| V2 | Batch directories | **PASS** | `Batch1`, `Batch2`, `Batch3` all present |
| V3 | Report written | **PASS** | 570 bytes, names a row count for each of the three batches |
| V4 | File inventory | **PASS** | Every file in Clauses 2.2.2.1–2.2.2.20 present in the batch the spec assigns it. 203 FINWIRE files, `FINWIRE1967Q1` to `FINWIRE2017Q3` |
| V5 | CDC columns | **PASS** | Batch2/3 OLTP files begin `I\|2468179`, `U\|2475485` — flag and DSN. The Batch1 equivalents begin with data (`2\|2012-07-11 08:10:15`), no CDC prefix, exactly as Clause 1.3.2 says |
| V6 | FINWIRE layout | **PASS** | Across all 203 files, every record's columns 16–18 are one of `CMP` (2,500), `SEC` (4,000), `FIN` (198,061). **Zero unclassified** |
| V7 | Audit coverage | **PASS** | See below |
| V8 | Size recorded | **PASS** | Table in §4 |

### V7 in detail — the one that mattered

The audit query compares against the `Audit` table using literal pairs such as
`DataSet = 'DimBroker' and Attribute = 'HR_BROKERS'`. If the generator never
emits a pair that a test reads, that test can never pass, and it would look like
an ETL bug in Phase 6.

- The audit query reads **22** distinct `DataSet`/`Attribute` pairs.
- The generator emits **132** pairs across **14** data sets: `Batch`,
  `DimAccount`, `DimBroker`, `DimCompany`, `DimCustomer`, `DimSecurity`,
  `DimTrade`, `DimTradeHistory`, `FactHoldings`, `FactMarketHistory`,
  `FactWatches`, `Financial`, `Generator`, `Prospect`.
- **Pairs read but not generated: 0. Data sets read but not generated: 0.**

Note `FactCashBalances` is absent from the generated data — and the audit query
anticipates this, carrying the comment *"currently we are not generating any
audit data for this table"* and excluding it from its own data-set list. The
remaining 13 in that list are exactly what we have, so the `Audit table sources`
test expecting 13 will pass.

A first attempt at V7 found "0 missing" using a regex that only matched
`DataSet = 'X' and Attribute = 'Y'` adjacently. The audit query also writes
`DataSet = 'X' and BatchID = ... and Attribute = 'Y'`, which that pattern missed.
Re-run with a clause-scoped extraction over all 86 `from Audit` references. Same
answer, now for the right reason.

## 6. Three defects in the tooling, and how they were handled

### 6.1 DIGen does not run on Linux as shipped

```
java.io.IOException: Cannot run program "/opt/java/openjdk/bin/java"
  (in directory "/opt/tpcdi/pdgf"): error=2, No such file or directory
	at org.tpc.di.digen.DIGen.executePDGF(DIGen.java:189)
```

DIGen spawns its generation JVM with the working directory hard-coded as `pdgf`
in lower case; the package ships `PDGF`. macOS and Windows are case-insensitive
by default so it never surfaces there. Any container runs on Linux, where the
child process cannot start.

Fixed with `RUN ln -s PDGF /opt/tpcdi/pdgf`. A symlink and not a rename, so a
reference to the upper-case name still resolves. Clause 6.2.2 forbids modifying
DIGen; a symlink outside the jar is not a modification of it.

### 6.2 The exit code is not a success signal

When PDGF aborted, DIGen printed `DIGen failed with non-zero return code from
PDGF`, `Data generation aborted` — and **exited 0**. An empty staging directory
with a zero exit code is the worst possible failure mode for an automated
pipeline.

**Consequence for the driver:** success must be decided by inspecting the output
— `digen_report.txt` exists, contains an End Time, and reports a row count for
all three batches — never by `$?`. Written down here so it ends up in
`benchmark_di.py` rather than being rediscovered.

### 6.3 The Java-8 ceiling is real, and the README is wrong

`Tools/README.txt` says "Requires Java SE 7 **or above**". `DIGen.asp` says
"Requires Java 1.7". Both are wrong about the upper bound. Built the identical
image on `eclipse-temurin:21-jre`:

```
Exception in thread "main" java.lang.NoClassDefFoundError:
    org/apache/commons/cli/ParseException
	at org.eclipse.jdt.internal.jarinjarloader.JarRsrcLoader.main(JarRsrcLoader.java:56)
Caused by: java.lang.ClassNotFoundException: org.apache.commons.cli.ParseException
```

It fails before reaching the licence prompt, and generated zero files. The cause
is identifiable: `DIGen.jar` is an Eclipse "jar-in-jar", loaded by
`JarRsrcLoader`, whose nested-jar classloading does not survive the Java 9 module
system. Nothing to do with PDGF or with the benchmark logic.

**So the community claim was right and the vendor documentation was wrong**, and
pinning `eclipse-temurin:8-jre` is required, not merely cautious. Probe image and
volume were deleted after the test.

## 7. What this changes elsewhere

**The sizing contradiction is resolved, and our reading was the wrong one.**
Phase 1 flagged that the schema's scaling constants implied far fewer rows than
the third-party "SF 10 ≈ 1 GB". Measured: SF=5 is 464 MiB, so SF=10 would be
around 0.9 GiB. The third party was right. The constants are expressed against an
effective `${SF}` of **`-sf` × 1000** — 2,500 `CMP` records is `FWCMPScaling`
(0.5) × 5000, and 4,000 `SEC` is `FWSECScaling` (0.8) × 5000, with `-sf 5`. That
multiplier is an inference from two data points and should be confirmed by
generating at a second scale factor in Step 12, but it explains the discrepancy
exactly.

**The 1800-second floor will dominate even harder than expected.** Batch2 and
Batch3 are 33k rows each against Batch1's 7.8M. Even if an incremental update
takes two seconds, `Max(E, 1800)` substitutes 1800, so `TI` ≈ 33,380 / 1800 ≈ 19
rows/s while `TH` will be in the thousands. Since the metric is
`GeoMean(TH, Min(TI1, TI2))`, the official figure will be driven almost entirely
by a constant we cannot influence. Reporting raw elapsed times beside it is not a
stylistic choice; without them the measurements say nothing about our system.

**Scale-factor planning.** At 93 MiB of source data per unit of `-sf`, disk is
not going to be the constraint the way it was for TPC-DS — SF=100 would be about
9 GiB of staging. The Historical Load's CPU cost will decide the reference scale
factor, which is what Step 12 measures.

**CRLF line endings.** `PDGF/Constants.properties` sets
`DEFAULT_LINESEPARATOR=CRLF`, so the generated files are CRLF on every platform.
Consistent across operating systems, but every parser must strip `\r` or the last
field of each row silently carries one. Carried into Phase 3 as a check.

## 7a. Determinism, verified

Phase 2 was re-run end to end into a separate volume on 2026-11-18 and the two
generations compared. All 475 data files (everything but `digen_report.txt`,
which carries timestamps) are **byte-for-byte identical**, and the row counts
match exactly: 7,804,509 / 33,380 / 33,455.

DIGen has no `-RNGSEED` equivalent — unlike `dsdgen` in Part I, where the seed
was part of the dataset's identity — so this had to be established empirically
rather than assumed. It means a scale factor alone identifies a dataset, and
anyone regenerating at SF=5 gets the bytes we measured against.

The second generation reported a different throughput (938,853 rows/s against
273,054) purely because the first ran against a cold page cache. Generation is
untimed under Clause 1.2.2.1.1, so this affects nothing.

## 8. Reproducing this

```
docker build -f docker/digen/Dockerfile -t tpcdi-digen:jre8 tools/
docker volume create tpcdi-staging-sf5
( printf '\nYES\n'; sleep 1800 ) | docker run --rm -i \
    -v tpcdi-staging-sf5:/staging tpcdi-digen:jre8 -o /staging/sf5 -sf 5
docker run --rm -v tpcdi-staging-sf5:/staging alpine:3 cat /staging/sf5/digen_report.txt
```

Answering `YES` accepts the bankmark PDGF licence. §1.4 permits academic and
research use; §1.5 forbids sublicensing, which is why the tools package is not
committed and each group member downloads their own. Full text:
`tools/Tools/PDGF/LICENSE.txt`.
