"""Phase 3 -- create the twenty Data Warehouse tables.

Plan step 3. Report: report/phases/phase-03-schema.md
Schema: sql/di/01_schema.sql, transcribed from Clause 3.2.
"""
import os
import subprocess
import sys
import tempfile

from .common import (APPENDIX_DIR, DB, DB_PASSWORD, MARIADB_IMAGE, ROOT,
                     Checks, Fail,
                     audit_summary, container, container_running, docker,
                     mariadb, may_replace, remove_container, run_appendix, scalar,
                     staging_volume, volume_exists, wait_for_server,
                     warehouse_volume)

NUMBER = 3
TITLE = "warehouse schema"
DESCRIPTION = "create the twenty warehouse tables"

DDL = os.path.join(ROOT, "sql", "di", "01_schema.sql")

# Clause 3.2, sections 3.2.1-3.2.19 plus DimTrade at 3.2.7.1.
SPEC_TABLES = ("Audit DimAccount DimBroker DimCompany DimCustomer DimDate "
               "DImessages DimSecurity DimTime DimTrade FactCashBalances "
               "FactHoldings FactMarketHistory FactWatches Financial Industry "
               "Prospect StatusType TaxRate TradeType").split()

APPENDIX_QUERIES = ("tpcdi_visibility_1", "tpcdi_visibility_2",
                    "tpcdi_validation", "tpcdi_audit", "tpcdi_audit.innerjoin")


def run(scale, force=False, keep=False, port=None, **_opts):
    vol, ctr = warehouse_volume(scale), container(scale)
    stage = staging_volume(scale)
    if not os.path.isfile(DDL):
        raise Fail(f"missing {DDL}")

    if volume_exists(vol):
        detail = [f"volume {vol}", f"container {ctr}"]
        if container_running(ctr):
            n = scalar(ctr, "select count(*) from information_schema.tables "
                            f"where table_schema='{DB}'", "?")
            detail.append(f"{n} tables")
        if not may_replace(f"A warehouse for scale factor {scale}",
                           detail, force, keep):
            return
        remove_container(ctr)
        docker("volume", "rm", "-f", vol)

    remove_container(ctr)
    docker("volume", "create", vol)

    # The staging area is mounted read-only so LOAD DATA INFILE can reach the
    # files.  Read-only because the server has no business writing there, and
    # a mistake would damage data every later measurement is taken against.
    mounts = ["-v", f"{vol}:/var/lib/mysql"]
    if volume_exists(stage):
        mounts += ["-v", f"{stage}:/staging:ro"]
        print(f"  mounting {stage} at /staging (read-only)")
    else:
        print(f"  note: no staging volume '{stage}' yet -- run phase 2 first")

    # Nothing is published by default: the server is reached with docker exec,
    # so it cannot collide with anything already on the host.  --port opens it
    # on 127.0.0.1 only, for a GUI client; never on 0.0.0.0.
    if port:
        mounts += ["-p", f"127.0.0.1:{port}:3306"]
        print(f"  publishing 127.0.0.1:{port} -> 3306")

    print(f"\nStarting {MARIADB_IMAGE} as {ctr} ...")
    docker("run", "-d", "--name", ctr,
           "-e", f"MARIADB_ROOT_PASSWORD={DB_PASSWORD}",
           "-e", f"MARIADB_DATABASE={DB}",
           *mounts, MARIADB_IMAGE, "--secure-file-priv=/staging")
    print(f"  ready after {wait_for_server(ctr)}s")

    print("Applying sql/di/01_schema.sql ...")
    mariadb(ctr, sql_file=DDL)
    n = scalar(ctr, "select count(*) from information_schema.tables "
                    f"where table_schema='{DB}'")
    print(f"\nCreated {n} tables in database '{DB}'.")
    if port:
        print(f"    mariadb://root:{DB_PASSWORD}@127.0.0.1:{port}/{DB}")


# ---------------------------------------------------------------- checks ---

def verify(scale, c: Checks):
    ctr = container(scale)
    if not container_running(ctr):
        c.bad(f"{ctr} is not running -- run phase 3 first")
        return

    print("S1     the twenty tables of Clause 3.2")
    got = mariadb(ctr, tabs=True,
                  sql="select table_name from information_schema.tables "
                      f"where table_schema='{DB}' order by table_name").split()
    lower = {t.lower() for t in got}
    c.want(len(got) == 20, "20 tables present", f" -- got {len(got)}")
    missing = [t for t in SPEC_TABLES if t.lower() not in lower]
    c.want(not missing, "every table named in Clause 3.2 exists",
           f" -- missing {missing}")
    want_lower = {t.lower() for t in SPEC_TABLES}
    extra = [t for t in got if t.lower() not in want_lower]
    c.want(not extra, "no unexpected tables", f" -- {extra}")

    print("\nS2     exact numeric storage (Clause 3.2 forbids approximate types)")
    approx = mariadb(ctr, tabs=True, sql=(
        "select concat(table_name,'.',column_name,' ',data_type) "
        f"from information_schema.columns where table_schema='{DB}' "
        "and data_type in ('float','double','real')")).split()
    c.want(not approx, "no float/double/real columns", f" -- {approx}")

    print("\nS3     millisecond precision (Clause 3.2 base types)")
    for table, col, kind in (("DImessages", "MessageDateAndTime", "DATETIME"),
                             ("DimTime", "TimeValue", "TIME")):
        p = scalar(ctr, "select datetime_precision from information_schema.columns "
                        f"where table_schema='{DB}' and table_name='{table}' "
                        f"and column_name='{col}'")
        c.want(p == "3", f"{table}.{col} is {kind}(3)", f" -- precision {p}")

    print("\nS4     adapted appendix SQL differs only by the documented substitutions")
    _check_adapted(c)

    print("\nS5     TPC's appendix queries run against the schema")
    print("        (this is the real column-name check: their SQL, our tables)")
    before = scalar(ctr, "select count(*) from DImessages", "0")
    for name in APPENDIX_QUERIES:
        _, err = run_appendix(ctr, name)
        c.want(err is None, f"{name} runs", f" -- {(err or '')[:90]}")
    after = scalar(ctr, "select count(*) from DImessages", "0")
    c.want(before == after, f"DImessages unchanged by this check ({after} rows)",
           f" -- was {before}, now {after}")

    print("\nS6     the audit query returns its full set of tests")
    full, _ = run_appendix(ctr, "tpcdi_audit")
    inner, _ = run_appendix(ctr, "tpcdi_audit.innerjoin")
    c.want(len(full) >= 120, f"{len(full)} tests returned")
    # The two differ only in how MariaDB's missing FULL OUTER JOIN is handled.
    # Agreement on a loaded warehouse is what turns the equivalence argument
    # in report/phases/phase-03-schema.md section 6.1 into evidence.
    c.want(full == inner,
           "full-join emulation and inner-join simplification agree")
    audit_summary(full)


def _check_adapted(c: Checks):
    """Regenerate into a scratch directory and compare.

    Regenerating in place would overwrite any tampering before the diff could
    see it -- which is exactly the bug this check had on first writing: a
    deliberately corrupted tpcdi_audit.sql passed every check.
    """
    with tempfile.TemporaryDirectory() as tmp:
        r = subprocess.run(
            [sys.executable, os.path.join(ROOT, "scripts", "adapt-appendix.py"),
             "--inner", "--quiet", "--out", tmp],
            capture_output=True, text=True)
        if r.returncode != 0:
            c.bad(f"adapt-appendix.py failed: {r.stderr.strip()[:120]}")
            return
        drift = []
        for name in sorted(os.listdir(tmp)):
            a = os.path.join(tmp, name)
            b = os.path.join(APPENDIX_DIR, name)
            if not os.path.isfile(b):
                drift.append(f"{name} (missing)")
            elif open(a, "rb").read() != open(b, "rb").read():
                drift.append(name)
        c.want(not drift,
               "sql/di/adapted is exactly what adapt-appendix.py produces",
               f" -- drifted: {drift}")
