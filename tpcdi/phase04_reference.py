"""Phase 4 -- load the Audit table and the reference tables.

Plan step 4. Report: report/phases/phase-04-reference-data.md
Load SQL: sql/di/02_load_reference.sql

The Audit table goes in first, deliberately: it is the generator's own record
of what it produced, and nearly every test in tpcdi_audit.sql compares the
warehouse against a row in it. Nothing can be verified until it exists.
"""
import os

from .common import (ROOT, Checks, Fail, container, container_running, docker,
                     in_alpine, mariadb, may_replace, run_appendix,
                     run_sql_text, scalar, staging_dir, staging_volume)

NUMBER = 4
TITLE = "Audit and reference tables"
DESCRIPTION = "load the Audit and reference tables"

LOAD_SQL = os.path.join(ROOT, "sql", "di", "02_load_reference.sql")

# Clause 1.3.7: loaded once in the Historical Load, never modified again.
REFERENCE_TABLES = [("DimDate", "Date.txt"), ("DimTime", "Time.txt"),
                    ("Industry", "Industry.txt"), ("StatusType", "StatusType.txt"),
                    ("TaxRate", "TaxRate.txt"), ("TradeType", "TradeType.txt")]
ALL_TABLES = ["Audit"] + [t for t, _ in REFERENCE_TABLES]


def run(scale, force=False, keep=False, **_opts):
    ctr, stagedir = container(scale), staging_dir(scale)
    if not container_running(ctr):
        raise Fail(f"{ctr} is not running.  Run phase 3 first.")
    if docker("exec", ctr, "test", "-d", f"{stagedir}/Batch1", check=False).returncode:
        raise Fail(f"the server cannot see {stagedir}/Batch1.\n"
                   "  Run phase 2, then phase 3 again so the volume gets mounted.")

    present = []
    for t in ALL_TABLES:
        n = scalar(ctr, f"select count(*) from `{t}`", "0")
        if n.isdigit() and int(n) > 0:
            present.append(f"{t}: {n}")
    if present:
        if not may_replace(f"Phase 4 tables for scale factor {scale}",
                           present, force, keep):
            return
        for t in ALL_TABLES:
            mariadb(ctr, sql=f"delete from `{t}`", check=False)
        print("  emptied")

    _load_audit(ctr, stagedir)
    _load_reference(ctr, stagedir)


def _load_audit(ctr, stagedir):
    print("\nLoading the Audit table ...")
    files = docker("exec", ctr, "sh", "-c",
                   f"find {stagedir} -name '*_audit.csv' | sort").stdout.split()
    if not files:
        raise Fail(f"no *_audit.csv found under {stagedir}")
    # LOAD DATA takes no wildcard, so the files are enumerated.  Clause
    # 7.2.6.2: the first row of each is field names and must NOT be loaded.
    # LINES TERMINATED BY '\r\n' because the generator writes CRLF -- see
    # sql/di/02_load_reference.sql for why that matters.
    run_sql_text(ctr, "\n".join(
        f"LOAD DATA INFILE '{f}' INTO TABLE Audit\n"
        "  FIELDS TERMINATED BY ',' LINES TERMINATED BY '\\r\\n' IGNORE 1 LINES\n"
        "  (DataSet, @BatchID, @Date, Attribute, @Value, @DValue)\n"
        "  SET BatchID = nullif(@BatchID,''), `Date` = nullif(@Date,''),\n"
        "      Value = nullif(@Value,''), DValue = nullif(@DValue,'');"
        for f in files))
    print(f"  {len(files)} files -> {scalar(ctr, 'select count(*) from Audit')} rows")


def _load_reference(ctr, stagedir):
    print(f"\nLoading the reference tables from {stagedir}/Batch1 ...")
    with open(LOAD_SQL, encoding="utf-8") as fh:
        text = fh.read().replace("__STAGING__", f"{stagedir}/Batch1")
    run_sql_text(ctr, text)
    for table, _ in REFERENCE_TABLES:
        print(f"  {table:<12} {scalar(ctr, f'select count(*) from `{table}`')} rows")


# ---------------------------------------------------------------- checks ---

BOOLEAN_COLUMNS = (("DimDate", "HolidayFlag"), ("DimTime", "MarketHoursFlag"),
                   ("DimTime", "OfficeHoursFlag"))

CARRIAGE_RETURN_SQL = r"""select
 (select count(*) from DimDate where DateDesc like '%\r' or FiscalQtrDesc like '%\r')
+(select count(*) from DimTime where SecondDesc like '%\r')
+(select count(*) from Industry where IN_SC_ID like '%\r')
+(select count(*) from StatusType where ST_NAME like '%\r')
+(select count(*) from TradeType where TT_NAME like '%\r')
+(select count(*) from TaxRate where TX_NAME like '%\r')
+(select count(*) from Audit where Attribute like '%\r' or DataSet like '%\r')"""


def verify(scale, c: Checks):
    ctr, vol = container(scale), staging_volume(scale)
    d = f"/s/sf{scale}"
    if not container_running(ctr):
        c.bad(f"{ctr} is not running -- run phase 4 first")
        return

    print("P1     the Audit table matches the generated CSVs")
    want = in_alpine(vol, f"T=0; for f in $(find {d} -name '*_audit.csv'); do "
                          "T=$((T + $(wc -l < $f) - 1)); done; echo $T")
    got = scalar(ctr, "select count(*) from Audit", "0")
    c.want(got == want, f"{got} rows = CSV data rows (headers excluded)",
           f" -- CSVs hold {want}")
    hdr = scalar(ctr, "select count(*) from Audit where DataSet='DataSet' "
                      "or Attribute='Attribute'", "0")
    c.want(hdr == "0", "no header rows loaded (Clause 7.2.6.2)", f" -- {hdr} leaked")

    print("\nP2     the Audit table has the shape tpcdi_audit.sql expects")
    batches = scalar(ctr, "select count(distinct BatchID) from Audit")
    highest = scalar(ctr, "select max(BatchID) from Audit")
    c.want(batches == "3" and highest == "3", "3 distinct batches, max 3",
           f" -- batches={batches} max={highest}")
    sets = scalar(ctr, "select count(distinct DataSet) from Audit")
    c.want(sets == "14", "14 data sets", f" -- got {sets}")
    fcb = scalar(ctr, "select count(*) from Audit where DataSet='FactCashBalances'")
    c.want(fcb == "0", "FactCashBalances absent, as the audit query expects")

    print("\nP3     reference tables match their source files line for line")
    for table, fname in REFERENCE_TABLES:
        want = in_alpine(vol, f"wc -l < {d}/Batch1/{fname}")
        got = scalar(ctr, f"select count(*) from `{table}`", "0")
        c.want(got == want, f"{table} {got} rows = {fname}",
               f" -- file has {want} lines")

    print("\nP4     no carriage return survived the load")
    # Loaded with the default '\n' the last column of every row keeps its \r:
    # TX_RATE becomes '0.15000\r'.  Invisible in most output, so asserted.
    cr = scalar(ctr, CARRIAGE_RETURN_SQL, "?")
    c.want(cr == "0", "0 columns end in a carriage return", f" -- {cr} do")

    print("\nP5     true/false mapped to 0/1")
    # Clause 2.2.1 Table 2.2.1 says BOOLEAN arrives as "0"/"1"; the generator
    # writes "true"/"false".  Assigned straight across every flag becomes 0
    # without complaint, which is why both values must be present.
    for table, col in BOOLEAN_COLUMNS:
        distinct = scalar(ctr, f"select count(distinct `{col}`) from `{table}`")
        outside = scalar(ctr, f"select count(*) from `{table}` "
                              f"where `{col}` not in (0,1)")
        true_n = scalar(ctr, f"select count(*) from `{table}` where `{col}`=1")
        c.want(distinct == "2" and outside == "0",
               f"{table}.{col} holds both 0 and 1 ({true_n} true)",
               f" -- {distinct} distinct, {outside} outside (0,1)")

    print("\nP6     spot checks against the generator's own numbers")
    # HR_BROKERS is not the employee count -- HR.csv holds all of them, and
    # Clause 4.5.2.1 keeps only EmployeeJobCode 314.  Checking against that
    # filter validates Phase 5's DimBroker rule before it is written.
    hrb = scalar(ctr, "select Value from Audit where DataSet='DimBroker' "
                      "and Attribute='HR_BROKERS'")
    brokers = in_alpine(vol, f"awk -F, '$6==314' {d}/Batch1/HR.csv | wc -l")
    employees = in_alpine(vol, f"wc -l < {d}/Batch1/HR.csv")
    c.want(hrb == brokers,
           f"HR_BROKERS = {hrb} = HR.csv rows with EmployeeJobCode 314 "
           f"(of {employees} employees)", f" -- job-code rows = {brokers}")
    sf = scalar(ctr, "select DValue from Audit where DataSet='Generator' "
                     "and Attribute='SF'")
    c.want(sf.startswith(f"{scale}000"),
           f"Generator SF = {sf}, confirming -sf x 1000")

    print("\nP7     tpcdi_audit.sql's checks on the Audit table now pass")
    rows, err = run_appendix(ctr, "tpcdi_audit")
    if err:
        c.bad(f"the audit query failed: {err[:100]}")
        return
    results = {r[0]: r[2] for r in rows if len(r) >= 3}
    for name in ("Audit table batches", "Audit table sources"):
        c.want(results.get(name) == "OK", f'"{name}" -> OK',
               f" -- got {results.get(name)}")
    ok_n = sum(1 for r in rows if len(r) >= 3 and r[2] == "OK")
    print(f"        whole suite: {ok_n} of {len(rows)} OK "
          "(the rest need the data later phases load)")
