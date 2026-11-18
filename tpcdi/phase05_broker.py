"""Phase 5 -- DimBroker from HR.csv.

Plan step 5. Report: report/phases/phase-05-dimbroker.md
SQL: sql/di/03_dimbroker.sql

The smallest real transformation in the benchmark: read a flat file, filter
it, assign surrogate keys, log what happened. It exercises the whole pipeline
shape at a size that can be read by eye, which is why it comes before FINWIRE
and the XML replay.
"""
import os

from .common import (ROOT, Checks, Fail, container, container_running, docker,
                     in_alpine, mariadb, may_replace, run_appendix,
                     run_sql_text, scalar, staging_dir, staging_volume)

NUMBER = 5
TITLE = "broker dimension"
DESCRIPTION = "load DimBroker from HR.csv"

LOAD_SQL = os.path.join(ROOT, "sql", "di", "03_dimbroker.sql")

# Clause 4.5.2.1: everything else in HR.csv is an employee, not a broker.
BROKER_JOB_CODE = 314

# The six DimBroker tests in tpcdi_audit.sql.
AUDIT_TESTS = ["DimBroker row count", "DimBroker distinct keys",
               "DimBroker BatchID", "DimBroker IsCurrent",
               "DimBroker EffectiveDate", "DimBroker EndDate"]


def run(scale, force=False, keep=False):
    ctr, stagedir = container(scale), staging_dir(scale)
    if not container_running(ctr):
        raise Fail(f"{ctr} is not running.  Run phase 3 first.")
    if docker("exec", ctr, "test", "-f", f"{stagedir}/Batch1/HR.csv",
              check=False).returncode:
        raise Fail(f"the server cannot see {stagedir}/Batch1/HR.csv.\n"
                   "  Run phase 2, then phase 3 again so the volume gets mounted.")
    # DimBroker's EffectiveDate is the earliest date in DimDate, so phase 4
    # has to have run.  Checked here rather than left to fail as a NULL.
    if scalar(ctr, "select count(*) from DimDate", "0") == "0":
        raise Fail("DimDate is empty -- run phase 4 before phase 5.")

    existing = scalar(ctr, "select count(*) from DimBroker", "0")
    if existing.isdigit() and int(existing) > 0:
        if not may_replace(f"DimBroker for scale factor {scale}",
                           [f"{existing} rows"], force, keep):
            return
        mariadb(ctr, sql="delete from DimBroker")
        mariadb(ctr, sql="delete from DImessages where MessageSource='DimBroker'",
                check=False)
        print("  emptied")

    print(f"\nLoading DimBroker from {stagedir}/Batch1/HR.csv ...")
    with open(LOAD_SQL, encoding="utf-8") as fh:
        text = fh.read().replace("__STAGING__", f"{stagedir}/Batch1")
    run_sql_text(ctr, text)

    rows = scalar(ctr, "select count(*) from DimBroker")
    expected = scalar(ctr, "select Value from Audit where DataSet='DimBroker' "
                           "and Attribute='HR_BROKERS'", "?")
    print(f"  DimBroker   {rows} rows   (Audit says {expected})")


# ---------------------------------------------------------------- checks ---

def verify(scale, c: Checks):
    ctr, vol = container(scale), staging_volume(scale)
    d = f"/s/sf{scale}"
    if not container_running(ctr):
        c.bad(f"{ctr} is not running -- run phase 5 first")
        return

    print("B1     the broker filter (Clause 4.5.2.1)")
    rows = scalar(ctr, "select count(*) from DimBroker", "0")
    # Counted from the file, not from a literal, so this still means something
    # at a scale factor nobody has run.
    from_file = in_alpine(
        vol, "awk -F, '$6==%d' %s/Batch1/HR.csv | wc -l" % (BROKER_JOB_CODE, d))
    employees = in_alpine(vol, f"wc -l < {d}/Batch1/HR.csv")
    c.want(rows == from_file,
           f"{rows} rows = HR.csv rows with EmployeeJobCode {BROKER_JOB_CODE} "
           f"(of {employees} employees)", f" -- file gives {from_file}")
    audit = scalar(ctr, "select Value from Audit where DataSet='DimBroker' "
                        "and Attribute='HR_BROKERS'", "?")
    c.want(rows == audit, f"row count matches Audit.HR_BROKERS = {audit}")
    # The filter has to exclude, not just include: if every employee were
    # loaded this would still pass the count check at the wrong number, but a
    # job code other than 314 reaching DimBroker is a different kind of wrong.
    others = in_alpine(
        vol, "awk -F, '$6!=%d' %s/Batch1/HR.csv | wc -l" % (BROKER_JOB_CODE, d))
    c.want(int(employees) == int(from_file) + int(others),
           f"{others} non-broker employees were excluded")

    print("\nB2     surrogate keys (Clause 4.4.1.3)")
    distinct = scalar(ctr, "select count(distinct SK_BrokerID) from DimBroker", "0")
    c.want(distinct == rows, f"all {distinct} SK_BrokerID values are distinct",
           f" -- {rows} rows but {distinct} keys")
    nulls = scalar(ctr, "select count(*) from DimBroker where SK_BrokerID is null", "0")
    c.want(nulls == "0", "no NULL surrogate keys")
    natural = scalar(ctr, "select count(distinct BrokerID) from DimBroker", "0")
    c.want(natural == rows, "one row per natural key (no duplicate BrokerID)",
           f" -- {natural} distinct BrokerID for {rows} rows")

    print("\nB3     history-tracking fields (Clause 4.4.1.2, 4.5.2.1)")
    for column, value, why in (
            ("IsCurrent", "1", "every row is current -- the source is a snapshot"),
            ("EndDate", "9999-12-31", "every row ends at the end of time"),
            ("BatchID", "1", "every row is marked Historical Load")):
        wrong = scalar(ctr, f"select count(*) from DimBroker "
                            f"where `{column}` <> '{value}'", "?")
        c.want(wrong == "0", f"{column} = {value}: {why}", f" -- {wrong} rows differ")
    # Clause 4.5.2.1 says the earliest date in DimDate; the audit query spells
    # that as the literal 1950-01-01.  Compared against DimDate so the check
    # follows the rule rather than the literal.
    earliest = scalar(ctr, "select min(DateValue) from DimDate")
    wrong = scalar(ctr, "select count(*) from DimBroker where EffectiveDate <> "
                        "(select min(DateValue) from DimDate)", "?")
    c.want(wrong == "0", f"EffectiveDate = earliest date in DimDate ({earliest})",
           f" -- {wrong} rows differ")

    print("\nB4     the columns carry the source values (Clause 4.5.2.1)")
    # One row, checked end to end against the file.  A column mapping that is
    # off by one passes every count check above and fails here.
    sample = in_alpine(
        vol, "awk -F, '$6==%d' %s/Batch1/HR.csv | head -1" % (BROKER_JOB_CODE, d))
    fields = sample.split(",")
    if len(fields) != 9:
        c.bad(f"could not read a sample broker row from HR.csv (got {len(fields)} fields)")
    else:
        emp, mgr, first, last, _mi, _job, branch, office, phone = fields
        got = mariadb(ctr, tabs=True, sql=(
            "select BrokerID, ManagerID, FirstName, LastName, Branch, Office, Phone "
            f"from DimBroker where BrokerID = {emp}")).strip().split("\t")
        want = [emp, mgr, first, last, branch, office, phone]
        if len(got) != len(want):
            c.bad(f"BrokerID {emp} not found in DimBroker")
        else:
            mismatch = [f"{w}!={g}" for w, g in zip(want, got) if w.strip() != g.strip()]
            c.want(not mismatch,
                   f"BrokerID {emp} matches HR.csv field for field",
                   f" -- {mismatch}")

    print("\nB5     DImessages (Clause 3.2.8)")
    logged = scalar(ctr, "select MessageData from DImessages "
                         "where MessageSource='DimBroker' "
                         "and MessageText='Inserted rows'", "")
    c.want(logged == rows, f"a Status row records {rows} inserted",
           f" -- logged '{logged}'")

    print("\nB6     the audit query's DimBroker tests")
    audit_rows, err = run_appendix(ctr, "tpcdi_audit")
    if err:
        c.bad(f"the audit query failed: {err[:100]}")
        return
    results = {r[0]: r[2] for r in audit_rows if len(r) >= 3}
    for name in AUDIT_TESTS:
        c.want(results.get(name) == "OK", f'"{name}" -> OK',
               f" -- got {results.get(name)}")
    ok_n = sum(1 for r in audit_rows if len(r) >= 3 and r[2] == "OK")
    print(f"        whole suite: {ok_n} of {len(audit_rows)} OK")
