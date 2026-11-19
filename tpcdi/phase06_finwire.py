"""Phase 6 -- FINWIRE into DimCompany, DimSecurity and Financial.

Plan step 6. Report: report/phases/phase-06-finwire.md
Parsing: tpcdi/finwire.py       Layouts: report/file-to-table-mapping.md section 4
SQL: sql/di/04_finwire_stage.sql, sql/di/05_finwire_transform.sql

Why all 203 files are read in one pass: not because 46 MB is large, but
because SCD-2 effective dating depends on posting-timestamp order and that
order is global across files. A company posted in 1990Q1 can be superseded by
a record in 1990Q2, so loading quarter by quarter would give correct-looking
rows with wrong effective dates wherever a natural key spans a boundary.

The parsed rows never touch disk. Python streams TSV into
LOAD DATA LOCAL INFILE '/dev/stdin', which sidesteps both the read-only
staging mount and secure_file_priv.
"""
import os
import subprocess
import time

from . import finwire
from .common import (ALPINE, DB, DB_PASSWORD, ROOT, Checks, Fail, container,
                     container_running, docker, in_alpine, mariadb,
                     may_replace, run_appendix, run_audit_after_validation,
                     run_sql_text, scalar,
                     staging_dir, staging_volume)

NUMBER = 6
TITLE = "FINWIRE dimensions"
DESCRIPTION = "load DimCompany, DimSecurity and Financial from FINWIRE"

STAGE_SQL = os.path.join(ROOT, "sql", "di", "04_finwire_stage.sql")
TRANSFORM_SQL = os.path.join(ROOT, "sql", "di", "05_finwire_transform.sql")

TARGETS = ["DimCompany", "DimSecurity", "Financial"]
STAGE_TABLES = {"CMP": "stg_fw_cmp", "SEC": "stg_fw_sec", "FIN": "stg_fw_fin"}

# Clause 4.5.3.3. The generator injects invalid ratings deliberately --
# Generator/FWSPRatingErrPct is 0.1, so about a tenth of CMP records.
VALID_SP_RATINGS = {
    "AAA", "AA", "A", "BBB", "BB", "B", "CCC", "CC", "C", "D",
    "AA+", "A+", "BBB+", "BB+", "B+", "CCC+",
    "AA-", "A-", "BBB-", "BB-", "B-", "CCC-",
}

AUDIT_TESTS = [
    "DimCompany row count", "DimCompany distinct keys", "DimCompany EndDate",
    "DimCompany Overlap", "DimCompany End of Time", "DimCompany consolidation",
    "DimCompany batches", "DimCompany EffectiveDate", "DimCompany Status",
    "DimCompany distinct names", "DimCompany Industry", "DimCompany SPrating",
    "DimCompany Country",
    "DimSecurity row count", "DimSecurity distinct keys", "DimSecurity EndDate",
    "DimSecurity Overlap", "DimSecurity End of Time", "DimSecurity consolidation",
    "DimSecurity batches", "DimSecurity IsCurrent", "DimSecurity EffectiveDate",
    "DimSecurity Status", "DimSecurity SK_CompanyID", "DimSecurity ExchangeID",
    "DimSecurity Issue",
    "Financial row count", "Financial SK_CompanyID", "Financial FI_YEAR",
    "Financial FI_QTR", "Financial FI_QTR_START_DATE", "Financial EPS",
]


def run(scale, force=False, keep=False, **_opts):
    ctr = container(scale)
    if not container_running(ctr):
        raise Fail(f"{ctr} is not running.  Run phase 3 first.")
    # Both lookups below need these, so fail with a usable message rather than
    # with NULL surrogate keys three steps later.
    for table, phase in (("StatusType", 4), ("Industry", 4), ("DimDate", 4)):
        if scalar(ctr, f"select count(*) from `{table}`", "0") == "0":
            raise Fail(f"{table} is empty -- run phase {phase} before phase 6.")

    present = [f"{t}: {n}" for t in TARGETS
               if (n := scalar(ctr, f"select count(*) from `{t}`", "0")).isdigit()
               and int(n) > 0]
    if present:
        if not may_replace(f"FINWIRE tables for scale factor {scale}",
                           present, force, keep):
            return
        for t in TARGETS:
            mariadb(ctr, sql=f"delete from `{t}`", check=False)
        mariadb(ctr, sql="delete from DImessages where MessageSource in "
                         "('DimCompany','DimSecurity','Financial')", check=False)
        for ix, t in (("ix_dimcompany_cik", "DimCompany"),
                      ("ix_dimcompany_name", "DimCompany")):
            mariadb(ctr, sql=f"drop index {ix} on {t}", check=False)
        print("  emptied")

    rows, counts = _parse(scale)
    _stage(ctr, rows, counts)
    _transform(ctr)

    for t in TARGETS:
        print(f"  {t:<12} {scalar(ctr, f'select count(*) from `{t}`')} rows")
    alerts = scalar(ctr, "select count(*) from DImessages where "
                         "MessageSource='DimCompany' and MessageText='Invalid SPRating'")
    print(f"  {'alerts':<12} {alerts} invalid S&P ratings reported")


def _parse(scale):
    """Read all 203 files in one pass and classify every record."""
    d = f"{staging_dir(scale)}/Batch1".replace("/staging", "/s")
    print(f"Parsing FINWIRE from {staging_volume(scale)} ...")
    started = time.time()
    proc = subprocess.Popen(
        ["docker", "run", "--rm", "-v", f"{staging_volume(scale)}:/s:ro", ALPINE,
         "sh", "-c", f"cat {d}/FINWIRE????Q?"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, errors="replace")
    try:
        rows, counts = finwire.parse_stream(proc.stdout)
    except finwire.ParseError as exc:
        proc.kill()
        raise Fail(f"FINWIRE parse failed: {exc}") from None
    proc.wait()
    if proc.returncode != 0:
        raise Fail(f"could not read FINWIRE files: {proc.stderr.read()[:200]}")
    total = sum(counts.values())
    if total == 0:
        raise Fail("no FINWIRE records found -- has phase 2 run?")
    print(f"  {total} records in {time.time() - started:.1f}s: "
          + ", ".join(f"{k} {v}" for k, v in counts.items()))
    return rows, counts


def _stage(ctr, rows, counts):
    """Load the parsed rows, streaming each set through the client's stdin.

    LOAD DATA LOCAL INFILE reads from the *client*, so '/dev/stdin' means the
    pipe we are writing to. No temporary file, and no need to make the
    read-only staging mount writable or to widen secure_file_priv.
    """
    print("Staging ...")
    mariadb(ctr, sql_file=STAGE_SQL)
    for kind, table in STAGE_TABLES.items():
        payload = "\n".join(rows[kind]) + ("\n" if rows[kind] else "")
        proc = subprocess.Popen(
            ["docker", "exec", "-i", ctr, "mariadb", "-uroot", f"-p{DB_PASSWORD}",
             "--local-infile=1", DB, "-e",
             f"LOAD DATA LOCAL INFILE '/dev/stdin' INTO TABLE tpcdi_stage.{table} "
             f"({', '.join('`%s`' % c for c in finwire.COLUMNS[kind])})"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True)
        _, err = proc.communicate(payload)
        if proc.returncode != 0:
            line = next((l for l in (err or "").splitlines() if l.startswith("ERROR")),
                        (err or "")[:200])
            raise Fail(f"staging {table} failed: {line}")
        got = scalar(ctr, f"select count(*) from tpcdi_stage.{table}", "0")
        if got != str(counts[kind]):
            raise Fail(f"{table}: parsed {counts[kind]} rows but staged {got}")
        print(f"  {table:<12} {got} rows")


def _transform(ctr):
    print("Transforming ...")
    started = time.time()
    with open(TRANSFORM_SQL, encoding="utf-8") as fh:
        run_sql_text(ctr, fh.read())
    print(f"  done in {time.time() - started:.1f}s")


# ---------------------------------------------------------------- checks ---

def verify(scale, c: Checks):
    ctr, vol = container(scale), staging_volume(scale)
    d = f"/s/sf{scale}"
    if not container_running(ctr):
        c.bad(f"{ctr} is not running -- run phase 6 first")
        return

    print("F1     every record was classified and staged")
    # Counted from the files, so this still means something at a scale factor
    # nobody has run.
    for kind, table in STAGE_TABLES.items():
        want = in_alpine(vol, f"cat {d}/Batch1/FINWIRE????Q? | "
                              f"grep -c '^.\\{{15\\}}{kind}'")
        got = scalar(ctr, f"select count(*) from tpcdi_stage.{table}", "0")
        c.want(got == want, f"{kind}: {got} staged = {want} in the files",
               f" -- files hold {want}")
    audit_cmp = scalar(ctr, "select sum(Value) from Audit where "
                            "DataSet='DimCompany' and Attribute='FW_CMP'")
    staged_cmp = scalar(ctr, "select count(*) from tpcdi_stage.stg_fw_cmp", "0")
    c.want(staged_cmp == audit_cmp,
           f"CMP staged count matches Audit.FW_CMP = {audit_cmp}")

    print("\nF2     row counts against the Audit table")
    # The audit tests these in different directions, and the directions carry
    # the meaning.  DimCompany is <= because Clause 4.5.3.1 drops unchanged
    # records.  DimSecurity is >= -- described as "matches or exceeds" --
    # because a security row is split wherever its company changes.  Financial
    # is <= because a quarter is never restated (Clause 4.5.14.1).
    for table, attribute, direction in (("DimCompany", "FW_CMP", "<="),
                                        ("DimSecurity", "FW_SEC", ">="),
                                        ("Financial", "FW_FIN", "<=")):
        rows = int(scalar(ctr, f"select count(*) from `{table}`", "0"))
        bound = int(scalar(ctr, "select sum(Value) from Audit where "
                                f"Attribute='{attribute}'", "0"))
        ok = rows <= bound if direction == "<=" else rows >= bound
        c.want(ok and rows > 0,
               f"{table} {rows} rows {direction} Audit.{attribute} = {bound}",
               f" -- {rows} fails {direction} {bound}")

    print("\nF3     SCD-2 chain integrity (Clause 4.4.1.2)")
    for table, key, sk in (("DimCompany", "CompanyID", "SK_CompanyID"),
                           ("DimSecurity", "Symbol", "SK_SecurityID")):
        distinct = scalar(ctr, f"select count(distinct {sk}) from `{table}`")
        total = scalar(ctr, f"select count(*) from `{table}`")
        c.want(distinct == total, f"{table}: all {total} surrogate keys distinct")
        # Exactly one current row per natural key.
        bad = scalar(ctr, f"select count(*) from (select {key} from `{table}` "
                          f"where IsCurrent=1 group by {key} having count(*)<>1) x", "?")
        c.want(bad == "0", f"{table}: exactly one IsCurrent row per {key}",
               f" -- {bad} keys differ")
        # Every chain ends at the end of time.
        ends = scalar(ctr, f"select count(*) from `{table}` "
                           "where IsCurrent=1 and EndDate <> '9999-12-31'", "?")
        c.want(ends == "0", f"{table}: current rows end 9999-12-31",
               f" -- {ends} do not")
        # No zero-length rows -- what the audit calls "consolidation", and the
        # reason the daily-grain collapse is mandatory rather than tidy.
        zero = scalar(ctr, f"select count(*) from `{table}` "
                           "where EffectiveDate = EndDate", "?")
        c.want(zero == "0", f"{table}: no row begins and ends the same day",
               f" -- {zero} do")
        # No overlapping ranges for one key.
        overlap = scalar(ctr, f"select count(*) from `{table}` a join `{table}` b "
                              f"on a.{key}=b.{key} and a.{sk}<>b.{sk} "
                              "and a.EffectiveDate >= b.EffectiveDate "
                              "and a.EffectiveDate < b.EndDate", "?")
        c.want(overlap == "0", f"{table}: no overlapping date ranges",
               f" -- {overlap} overlap")
        # No gaps: each row's EndDate is the next row's EffectiveDate.
        gaps = scalar(ctr, f"""select count(*) from (
            select EndDate, lead(EffectiveDate) over (partition by {key}
                              order by EffectiveDate) nxt
            from `{table}`) x where nxt is not null and nxt <> EndDate""", "?")
        c.want(gaps == "0", f"{table}: no gaps between successive rows",
               f" -- {gaps} gaps")

    print("\nF4     daily grain (Clause 4.4.1.5)")
    for table, key in (("DimCompany", "CompanyID"), ("DimSecurity", "Symbol")):
        dup = scalar(ctr, f"select count(*) from (select {key}, EffectiveDate "
                          f"from `{table}` group by 1,2 having count(*)>1) x", "?")
        c.want(dup == "0", f"{table}: at most one row per {key} per day",
               f" -- {dup} keys have two")

    print("\nF5     the company lookup resolved (Clause 4.5.6.2, 4.5.14.2)")
    # The spec says these matches are guaranteed to succeed, so a NULL here is
    # our bug -- most likely the CoNameOrCIK digits/name branch.
    for table in ("DimSecurity", "Financial"):
        nulls = scalar(ctr, f"select count(*) from `{table}` "
                            "where SK_CompanyID is null", "?")
        c.want(nulls == "0", f"{table}: every SK_CompanyID resolved",
               f" -- {nulls} are NULL")
    orphans = scalar(ctr, "select count(*) from DimSecurity s "
                          "left join DimCompany c on c.SK_CompanyID = s.SK_CompanyID "
                          "where c.SK_CompanyID is null", "?")
    c.want(orphans == "0", "DimSecurity.SK_CompanyID all exist in DimCompany",
           f" -- {orphans} dangle")
    # Both halves of the CoNameOrCIK branch must have been exercised:
    # FWCIKPct is 0.5, so roughly half the records go each way.  If only one
    # branch worked, the other half would have failed the NULL check above --
    # unless it silently matched the wrong company, which this catches.
    by_cik = scalar(ctr, "select count(*) from tpcdi_stage.stg_fw_sec "
                         "where CoNameOrCIK regexp '^[0-9]+$'", "0")
    by_name = scalar(ctr, "select count(*) from tpcdi_stage.stg_fw_sec "
                          "where CoNameOrCIK not regexp '^[0-9]+$'", "0")
    c.want(int(by_cik) > 0 and int(by_name) > 0,
           f"SEC records use both forms: {by_cik} by CIK, {by_name} by name")

    print("\nF6     invalid S&P ratings (Clause 4.5.3.3)")
    bad_kept = scalar(ctr, "select count(*) from DimCompany where SPrating is not null "
                           "and SPrating not in ('AAA','AA','A','BBB','BB','B','CCC',"
                           "'CC','C','D','AA+','A+','BBB+','BB+','B+','CCC+','AA-',"
                           "'A-','BBB-','BB-','B-','CCC-')", "?")
    c.want(bad_kept == "0", "no invalid SPrating survived into DimCompany",
           f" -- {bad_kept} did")
    alerts = int(scalar(ctr, "select count(*) from DImessages where MessageSource="
                             "'DimCompany' and MessageText='Invalid SPRating'", "0"))
    staged_bad = int(scalar(ctr, "select count(*) from tpcdi_stage.stg_fw_cmp "
                                 "where SPrating is null or SPrating not in ('AAA',"
                                 "'AA','A','BBB','BB','B','CCC','CC','C','D','AA+',"
                                 "'A+','BBB+','BB+','B+','CCC+','AA-','A-','BBB-',"
                                 "'BB-','B-','CCC-')", "0"))
    c.want(alerts == staged_bad,
           f"{alerts} alerts raised, one per invalid staged record")
    # Generator/FWSPRatingErrPct says about a tenth should be invalid.  The
    # band is wide on purpose: this is a sanity check that the detection is
    # working at all, not an assertion about the generator's exact mix.
    total_cmp = int(scalar(ctr, "select count(*) from tpcdi_stage.stg_fw_cmp", "1"))
    pct = 100.0 * staged_bad / max(total_cmp, 1)
    c.want(2.0 < pct < 25.0,
           f"{pct:.1f}% of CMP records carry an invalid rating "
           "(generator says ~10%)", f" -- {pct:.1f}% is implausible")
    nulled = scalar(ctr, "select count(*) from DimCompany "
                         "where SPrating is null and isLowGrade is not null", "?")
    c.want(nulled == "0", "isLowGrade is NULL wherever SPrating is",
           f" -- {nulled} rows disagree")

    print("\nF7     isLowGrade (Clause 4.5.3.2)")
    wrong = scalar(ctr, """select count(*) from DimCompany where SPrating is not null
        and isLowGrade <> (case when SPrating like 'A%' or SPrating like 'BBB%'
                                then 0 else 1 end)""", "?")
    c.want(wrong == "0", "False for A* and BBB*, True otherwise",
           f" -- {wrong} rows wrong")

    print("\nF8     the audit query's tests for these three tables")
    # Three of these compare against a 'Row count' Validation message rather
    # than against the table, so the validation query is run first inside a
    # transaction that is rolled back -- see common.run_audit_after_validation.
    # Their batch-2 and batch-3 variants stay red until Phase 11 loads those
    # batches, which is why only batch 1 and the unbatched tests are required.
    rows, err = run_audit_after_validation(ctr)
    if err:
        c.bad(f"the audit query failed: {err[:100]}")
        return
    results = {}
    for r in rows:
        if len(r) >= 3 and (r[0] not in results or r[1] in ("1", "NULL")):
            results[r[0]] = r[2]
    failed = [t for t in AUDIT_TESTS if results.get(t) != "OK"]
    # "for batch 1" is not a detail.  The row-count tests also run for batches
    # 2 and 3, where they fail because those batches are not loaded until
    # Phase 11, and only the batch-1 row is taken above.  Saying "all 32 pass"
    # without that qualification would be claiming more than was checked.
    c.want(not failed, f"all {len(AUDIT_TESTS)} tests report OK for batch 1",
           f" -- failing: {failed}")
    ok_n = sum(1 for r in rows if len(r) >= 3 and r[2] == "OK")
    print(f"        whole suite: {ok_n} of {len(rows)} OK")
