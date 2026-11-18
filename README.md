# mariadb-tpcdi

TPC-DI (Data Integration) implemented as Python + SQL against MariaDB, in Docker.

Part II of the ULB INFO-H419 Data Warehouses project. Part I — TPC-DS on the same
engine — lives in a separate repository; this one is self-contained.

Due **24 December 2026**.

## Where things are

| Path | What |
|---|---|
| `report/00-what-we-have-to-do.md` | What TPC-DI requires, read out of the specification with clause references |
| `report/01-steps-and-validation.md` | The ordered plan, and the oracle that validates each step |
| `spec/tpc-di_v1.1.0.pdf` | TPC-DI Standard Specification v1.1.0 (November 2014) |
| `tools/` | `TPC-DI_Tools_v1.1.0.zip` and its contents — not committed, see `tools/MANIFEST.md` |
| `sql/` | Schema DDL and the transformation SQL |
| `scripts/` | Loader and driver code |
| `docker/` | MariaDB image, and the Java 8 layer DIGen needs |

## Status

Phase 1: obtaining and inventorying the TPC tools. Nothing is implemented yet.

## The one thing to know before reading anything else

TPC-DI ships an audit query that is a pass/fail oracle. "It loaded" is not the
success condition — the run is invalid unless every audit test reports OK
(Clause 7.2.6.7). Correctness comes first here; performance is what we measure
once the audit is green. `report/01-steps-and-validation.md` is organised around
that.
