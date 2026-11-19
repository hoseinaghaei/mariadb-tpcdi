# Phase 3 — The Data Warehouse schema

**Status: COMPLETE.** 15 of 15 checks pass. 2026-11-18.

Plan step: `../01-steps-and-validation.md` Step 3.

---

## 1. Goal

Create the twenty Data Warehouse tables in MariaDB, and establish — before a
single row is loaded — that our column names, types and nullability match what
the benchmark expects.

## 2. Where the schema comes from

| Input | Where | What it gives |
|---|---|---|
| **Clause 3.2** | `../../spec/tpc-di_v1.1.0.pdf`, 3.2.1–3.2.19 and 3.2.7.1 | The twenty tables, field by field: name, type, nullability. Transcribed by hand. |
| **Table 3.2.1** | Clause 3.2 preamble | Base types: BOOLEAN, CHAR(n), DATE, DATETIME, NUM(m,n), SNUM(m,n), TIME |
| **Table 3.2.2** | Clause 3.2 preamble | Meta-types: SK_T, IDENT_T, TRADE_T, S_COUNT_T, S_PRICE_T, VALUE_T, BALANCE_T, FIN_AGG_T |
| **Table 4.3** | Clause 4.3.3 | Key relationships, for Phase 5 onwards |
| `reference/qasim-mysql/preparation.sql` | third party | A cross-check only, never a source |

The spec is the authority. The third-party MySQL schema was read *after* ours
was written, as a second opinion, never as a starting point — their own README
says their implementation does not pass the audit.

Output: **`sql/di/01_schema.sql`**, 20 `CREATE TABLE` statements in the spec's
order, with the type mapping documented in the file header.

## 3. The type mapping, and why

Clause 3.2 says storage "must accommodate exact values for any number in the
input range" and forbids approximate representations unless every valid value
round-trips exactly. So no FLOAT or DOUBLE anywhere, which S2 checks.

| Spec | MariaDB | Reasoning |
|---|---|---|
| `BOOLEAN` | `TINYINT(1)` | Clause 8.2.1.2 allows any two distinct values |
| `CHAR(n)` | `VARCHAR(n)` | The spec defines CHAR(n) as "up to n characters" — that is VARCHAR's semantics, not SQL CHAR's fixed width. Avoids padding `Description VARCHAR(150)` to 150 bytes per row. |
| `DATE` | `DATE` | Spec needs 1800-01-01 to 9999-12-31; MariaDB covers 1000-01-01 upward |
| `DATETIME` | `DATETIME(3)` | Spec requires 1 ms precision |
| `TIME` | `TIME(3)` | Spec requires 1 ms precision |
| `NUM(m,n)`, `SNUM(m,n)` with n>0 | `DECIMAL(m,n)` | Exact |
| `NUM(m)`, `SNUM(m)` with n=0 | smallest exact integer type holding m digits | per column |

Two places where the obvious choice is wrong and the spec's digit count decides:

- **`BatchID` is `NUM(5)`**, so the range is 0–99999. `SMALLINT UNSIGNED` stops
  at 65535 and cannot hold it. Used `INT UNSIGNED`, even though the benchmark
  only ever writes 0, 1, 2 and 3. The third-party schema uses `SMALLINT
  UNSIGNED` here — correct in practice, wrong against the spec.
- **`Prospect.Age` is `NUM(3)`**, maximum 999. `TINYINT UNSIGNED` stops at 255.
  Used `SMALLINT UNSIGNED`.

`SK_T` and `IDENT_T` are `NUM(11)`, whose maximum exceeds `INT UNSIGNED`'s
4.29 billion, so every surrogate key is `BIGINT`.

No indexes beyond primary keys. Clause 3.1.1 says the specification does not
require any, and Clause 7.2.3.2 permits auxiliary structures — which is where
they belong, as something measured rather than assumed.

## 4. How it is created — Docker only, no published port

```
python benchmark_di.py --phase 3                 scale factor 5
python benchmark_di.py --scale 3 --phase 3       any scale factor
python benchmark_di.py --phase 3 --force         replace without asking
python benchmark_di.py --phase 3 --keep          leave an existing one alone
```

`mariadb:11.4`, data in a named volume `tpcdi-dw-sf<N>`, container
`tpcdi-mariadb-sf<N>`. **No port is published** — the server is reached with
`docker exec`, so nothing collides with Part I's server and there is no
listening socket on the host.

Like the generator script, it refuses to destroy a warehouse silently: with one
already present it prints what is there and asks, `--force` replaces without
asking, `--keep` leaves it, and a non-interactive shell with neither flag is an
error rather than a guess.

### How the data is read, and why

**No data is read in this phase.** It creates empty tables. The question of one
pass versus chunking first arises in Phase 4.

Worth recording for the phases that follow: no indexes are created beyond
primary keys, so the bulk loads in Phases 4 to 9 are not paying to maintain
secondary indexes while they run. Clause 7.2.3.2 permits auxiliary structures,
and where one is needed — `FactMarketHistory`'s window is the likely case — it
should be created deliberately and measured, not assumed.

## 5. The validation — and why it is not circular

Checking our DDL against our own reading of the spec proves nothing. The real
check is to run **TPC's own SQL against our tables**: the audit, validation and
visibility queries between them reference seventeen tables and most of their
columns by exact spelling. If they execute, our names agree with TPC's.

That is what S5 and S6 do. The audit query returns **126 tests** against the
empty warehouse. 101 report OK, 22 report failures — correctly, since there is
no data. What matters at this stage is that it *runs*: no "unknown column", no
"no such table".

| # | Check | Result |
|---|---|---|
| S1 | 20 tables, all named in Clause 3.2, none extra | **PASS** |
| S2 | No `float`/`double`/`real` columns | **PASS** |
| S3 | `DATETIME(3)` and `TIME(3)` | **PASS** |
| S4 | Adapted appendix SQL reproducible from the pristine files | **PASS** |
| S5 | All five appendix queries run | **PASS** |
| S6 | 126 tests returned; both audit variants agree | **PASS** |

```
python verify_di.py --phase 3          -> passed 14, failed 0
```

**S4 had to be fixed before it was worth anything.** As first written it
regenerated `sql/di/adapted` in place and then diffed — which overwrote any
tampering before the diff could see it. A deliberately corrupted
`tpcdi_audit.sql`, with `select no_such_column from DimBroker` appended, passed
14 of 14. It now regenerates into a scratch directory and compares, and the same
corruption fails both S4 (drift) and S5 (`Unknown column 'no_such_column'`).

Negative tests run: tampered file → 2 failures, exit 1; no container for the
requested scale factor → exit 1; restored file → exit 0.

**The verifier was also writing to the database it verified.** Three of the four
appendix queries — `tpcdi_validation.sql` and both visibility queries — are
`INSERT`s into `DImessages`; only the audit query reads. Running them bare left
130 rows behind, and once `Audit` was populated in Phase 4 those rows broke the
audit query outright with `ERROR 1242 Subquery returns more than 1 row`. It
surfaced as a Phase 3 regression after a Phase 4 load, which is a confusing way
to find out.

They are now wrapped in `START TRANSACTION` / `ROLLBACK`, and a fifteenth check
asserts `DImessages` is unchanged across the run. Verified by running
phase 3 and phase 4 verification alternately twice: 15/15 and 19/19 each time,
with `DImessages` at 0 throughout.

## 6. Adapting TPC's SQL to MariaDB

The appendix files are written for a generic SQL dialect. Their header names two
substitutions a sponsor is expected to make; MariaDB forces three more. Clause
3.4.11 covers this: where a system cannot run the query as supplied, the sponsor
follows the SQL compliance rules.

`scripts/adapt-appendix.py` performs them all, deterministically, from the
pristine files into `sql/di/adapted/`. The pristine copies stay read-only so the
diff is always available — S4 regenerates and checks nothing has drifted.

| # | Substitution | Occurrences | Why |
|---|---|---|---|
| 1 | `FROM DUMMY TABLE` removed | 206 | Prescribed by the TPC header. MariaDB allows `SELECT` with no `FROM`. |
| 2 | `cast(x as bigint)` → `cast(x as signed)` | 10 | MariaDB's cast targets are `SIGNED`/`UNSIGNED`; `BIGINT` is not one. |
| 3 | derived table given an alias | 1 | MariaDB requires every derived table to have one. |
| 4 | `FULL JOIN` emulated | 2 | **MariaDB has no FULL OUTER JOIN.** See below. |

The `||` case needed no edit: running with `sql_mode='PIPES_AS_CONCAT'` makes it
concatenation, which is the same session setting four TPC-DS queries needed in
Part I.

### 6.1 The full join, and why it is not a quiet weakening

Both occurrences have the same shape:

```sql
from Audit a
full join DImessages m on m.BatchID = 0 and m.MessageText = 'Row count' ...
join DImessages m1 on m1.BatchID = a.BatchID   and m1.MessageSource = m.MessageSource ...
join DImessages m2 on m2.BatchID = a.BatchID-1 and m2.MessageSource = m.MessageSource ...
```

The `ON` clause references only `m`, never `a`. And the two joins that follow
are **inner**, requiring both `a.BatchID` and `m.MessageSource` to be present
and matching — which discards every NULL-extended row the full join would
produce. So a plain `INNER JOIN` gives the same answer here.

That is an argument, not a measurement. Softening an audit to make it pass is
worth nothing, so the default adaptation does **not** take the shortcut: it
emulates the full join faithfully,

```sql
A FULL JOIN B ON c  ==  (A LEFT JOIN B ON c) UNION (A RIGHT JOIN B ON c)
```

folded into a derived table so the downstream joins still see both sides.
`--inner` writes the simplified variant beside it as
`tpcdi_audit.innerjoin.sql`.

Both run, and on the empty warehouse both return byte-identical output. That is
a weak check — there is no data to distinguish them — so S6 compares them every
time, and **Phase 6 repeats the comparison on a loaded warehouse**. If they
agree there, the argument becomes evidence. If they disagree, the faithful one
is the oracle and the finding is worth reporting.

## 7. What this phase settles for later work

- **The column names are right.** Any "unknown column" from here on is a bug in
  new SQL, not in the schema.
- **`PIPES_AS_CONCAT` is mandatory** for every session that runs TPC's SQL. It
  belongs in the server configuration, not in each caller.
- **`DImessages` has no primary key**, deliberately — the spec defines none and
  the same message legitimately repeats. Clause 7.5.1 reads the phase timings
  out of its `PCR` rows, so it is load-bearing, not a log.
- **`Audit.Date` is backticked** in the DDL; `DATE` is reserved in MariaDB.
- The 22 failing tests on an empty warehouse are the **baseline**. Phase 6 drives
  that number to zero.

## 8. Reproducing this

```
python benchmark_di.py --scale 5 --phase 3
python verify_di.py --scale 5 --phase 3
docker exec -it tpcdi-mariadb-sf5 mariadb -uroot -ptpcdi tpcdi
```
