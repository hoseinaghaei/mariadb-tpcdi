# mariadb-tpcdi

TPC-DI (Data Integration) implemented as Python + SQL against MariaDB, in Docker.

Part II of the ULB INFO-H419 Data Warehouses project. Part I — TPC-DS on the same
engine — lives in a separate repository; this one is self-contained.

Due **24 December 2026**.

## Where things are

| Path | What |
|---|---|
| `PHASES.md` | **Start here if you are joining.** What each phase is, what finishes it, and the conventions |
| `report/00-what-we-have-to-do.md` | What TPC-DI requires, read out of the specification with clause references |
| `report/file-to-table-mapping.md` | Which source file becomes which table: field layouts, byte offsets, filters, and why each phase reads its data in one pass or in pieces |
| `report/01-steps-and-validation.md` | The ordered plan, and the oracle that validates each step |
| `spec/tpc-di_v1.1.0.pdf` | TPC-DI Standard Specification v1.1.0 (November 2014) |
| `tools/` | `TPC-DI_Tools_v1.1.0.zip` and its contents — not committed, see `tools/MANIFEST.md` |
| `benchmark_di.py` | The driver: one command, any OS |
| `verify_di.py` | Runs the checks |
| `tpcdi/phaseNN_*.py` | One module per phase, holding both its loading and its checks |
| `tpcdi/common.py` | Docker, MariaDB and prompting plumbing shared by every phase |
| `sql/di/` | Schema DDL, load SQL, and TPC's appendix queries adapted to MariaDB |
| `scripts/adapt-appendix.py` | Generates `sql/di/adapted/` from the pristine appendix files |
| `docker/` | MariaDB image, and the Java 8 layer DIGen needs |

## Running it

Docker is the only prerequisite. MariaDB, DIGen, Java 8 and everything else
lives in containers.

```
python benchmark_di.py --scale 5          generate, create, load
python verify_di.py    --scale 5          check every phase
```

Both are standard-library Python and work on Windows, macOS and Linux. Add
`--phase N` to run or verify one phase, `--force` to replace existing data
without being asked, `--keep` never to replace anything.

Each phase lives in its own module under `tpcdi/`, holding both how it loads
and how it is checked, so everything about a phase is in one place. A new phase
is a new `tpcdi/phaseNN_name.py` exporting `NUMBER`, `TITLE`, `DESCRIPTION`,
`run()` and `verify()`, listed in `tpcdi/__init__.py`.

The TPC tools are not redistributed: download `TPC-DI_Tools_v1.1.0.zip` from
tpc.org and unpack it into `tools/` so that `tools/Tools/DIGen.jar` exists.
`tools/MANIFEST.md` records the SHA-256.

## Status

| Phase | What | State |
|---|---|---|
| 1 | Obtain and inventory the TPC tools | done |
| 2 | Generate the source data | done |
| 3 | Create the warehouse schema | done |
| 4 | Load the Audit and reference tables | done |
| 5 | DimBroker from HR.csv | done |
| 6+ | FINWIRE, the XML replay, the facts | next |

69 verification checks pass at SF=3 and SF=5. TPC's audit query reports
116 of 167 tests OK; the rest need the data later phases load.

## The one thing to know before reading anything else

TPC-DI ships an audit query that is a pass/fail oracle. "It loaded" is not the
success condition — the run is invalid unless every audit test reports OK
(Clause 7.2.6.7). Correctness comes first here; performance is what we measure
once the audit is green. `report/01-steps-and-validation.md` is organised around
that.
