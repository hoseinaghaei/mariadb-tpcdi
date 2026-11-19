"""Phase 7 -- CustomerMgmt.xml into DimCustomer and DimAccount.

Plan step 7. Report: report/phases/phase-07-customer.md
Parsing: tpcdi/customermgmt.py
SQL: sql/di/06_customer_stage.sql, sql/di/07_customer_events.sql

STAGE 1 ONLY SO FAR. This parses the XML, loads the two tables the file can
fill, and derives the account event stream. The transform into DimCustomer
and DimAccount is the next piece of work; until it exists, verify() checks
the staging and says so rather than implying the phase is finished.
"""
import os
import subprocess
import time

from . import customermgmt as cmg
from .common import (ALPINE, DB, DB_PASSWORD, ROOT, Checks, Fail, container,
                     container_running, docker, in_alpine, mariadb,
                     may_replace, run_sql_text, scalar, staging_dir,
                     staging_volume)

NUMBER = 7
TITLE = "customer and account (staging)"
DESCRIPTION = "parse CustomerMgmt.xml into the staging tables"

STAGE_SQL = os.path.join(ROOT, "sql", "di", "06_customer_stage.sql")
EVENTS_SQL = os.path.join(ROOT, "sql", "di", "07_customer_events.sql")

STAGE_TABLES = ["stg_cmg_customer", "stg_cmg_account", "stg_cmg_account_event"]

# Clause 4.5.4.1. The Audit table carries a count for each.
ACTION_AUDIT = {"NEW": "C_NEW", "UPDCUST": "C_UPDCUST", "INACT": "C_INACT",
                "ADDACCT": "CA_ADDACCT", "UPDACCT": "CA_UPDACCT",
                "CLOSEACCT": "CA_CLOSEACCT"}


def run(scale, force=False, keep=False, **_opts):
    ctr = container(scale)
    if not container_running(ctr):
        raise Fail(f"{ctr} is not running.  Run phase 3 first.")
    xml = f"{staging_dir(scale)}/Batch1/CustomerMgmt.xml"
    if docker("exec", ctr, "test", "-f", xml, check=False).returncode:
        raise Fail(f"the server cannot see {xml}.\n"
                   "  Run phase 2, then phase 3 again so the volume gets mounted.")

    present = [f"{t}: {n}" for t in STAGE_TABLES
               if (n := scalar(ctr, f"select count(*) from tpcdi_stage.{t}", "0"))
               .isdigit() and int(n) > 0]
    if present and not may_replace(f"Phase 7 staging for scale factor {scale}",
                                   present, force, keep):
        return

    _parse_and_stage(ctr, scale)
    _derive_events(ctr)


def _parse_and_stage(ctr, scale):
    """Read the XML once, writing rows into the server as they are parsed.

    Two LOAD DATA clients run at the same time, one per staging table, and
    rows go out in blocks of customermgmt.CHUNK_ROWS. Nothing accumulates, so
    peak memory does not depend on the scale factor -- measured at +2.5 MiB
    over a bare interpreter for the 15.6 MB file at SF=5.
    """
    print(f"Parsing CustomerMgmt.xml from {staging_volume(scale)} ...")
    mariadb(ctr, sql_file=STAGE_SQL)

    started = time.time()
    reader = subprocess.Popen(
        ["docker", "run", "--rm", "-v", f"{staging_volume(scale)}:/s:ro", ALPINE,
         "cat", f"/s/sf{scale}/Batch1/CustomerMgmt.xml"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    loaders = {}
    for table, columns in (("stg_cmg_customer", cmg.CUSTOMER_COLUMNS),
                           ("stg_cmg_account", cmg.ACCOUNT_COLUMNS)):
        cols = ", ".join(f"`{c}`" for c in columns)
        loaders[table] = subprocess.Popen(
            ["docker", "exec", "-i", ctr, "mariadb", "-uroot", f"-p{DB_PASSWORD}",
             "--local-infile=1", DB, "-e",
             f"LOAD DATA LOCAL INFILE '/dev/stdin' INTO TABLE tpcdi_stage.{table} ({cols})"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True)

    try:
        counts, n_cust, n_acct = cmg.parse_to(
            reader.stdout,
            loaders["stg_cmg_customer"].stdin,
            loaders["stg_cmg_account"].stdin)
    except cmg.ParseError as exc:
        reader.kill()
        for p in loaders.values():
            p.kill()
        raise Fail(f"CustomerMgmt parse failed: {exc}") from None

    reader.wait()
    if reader.returncode != 0:
        raise Fail(f"could not read the XML: {reader.stderr.read()[:200]!r}")
    for table, proc in loaders.items():
        _, err = proc.communicate()
        if proc.returncode != 0:
            line = next((l for l in (err or "").splitlines() if l.startswith("ERROR")),
                        (err or "")[:200])
            raise Fail(f"staging {table} failed: {line}")

    print(f"  {n_cust} actions, {n_acct} accounts in {time.time() - started:.1f}s")
    print("  " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    for table, want in (("stg_cmg_customer", n_cust), ("stg_cmg_account", n_acct)):
        got = scalar(ctr, f"select count(*) from tpcdi_stage.{table}", "0")
        if got != str(want):
            raise Fail(f"{table}: parsed {want} rows but staged {got}")
        print(f"  {table:<22} {got} rows")


def _derive_events(ctr):
    """Union the explicit account events with the UPDCUST/INACT cascade."""
    print("Deriving the account event stream ...")
    started = time.time()
    spilled_before = _tmp_disk_tables(ctr)
    with open(EVENTS_SQL, encoding="utf-8") as fh:
        run_sql_text(ctr, fh.read())
    spilled = _tmp_disk_tables(ctr) - spilled_before
    total = scalar(ctr, "select count(*) from tpcdi_stage.stg_cmg_account_event")
    derived = scalar(ctr, "select count(*) from tpcdi_stage.stg_cmg_account_event "
                          "where explicit = 0")
    print(f"  {total} events ({derived} derived) in {time.time() - started:.1f}s")
    # While this is zero the statement fits in memory.  The moment it is not,
    # the single-statement approach needs revisiting -- see
    # report/file-to-table-mapping.md section 5.
    print(f"  internal temp tables spilled to disk: {spilled}")


def _tmp_disk_tables(ctr):
    return int(scalar(ctr, "select variable_value from information_schema."
                           "global_status where variable_name="
                           "'Created_tmp_disk_tables'", "0"))


# ---------------------------------------------------------------- checks ---

def verify(scale, c: Checks):
    ctr = container(scale)
    if not container_running(ctr):
        c.bad(f"{ctr} is not running -- run phase 7 first")
        return

    print("X1     every action was parsed and staged")
    actions = scalar(ctr, "select count(*) from tpcdi_stage.stg_cmg_customer", "0")
    in_file = in_alpine(staging_volume(scale),
                        f"grep -c 'TPCDI:Action ActionType' /s/sf{scale}/Batch1/CustomerMgmt.xml")
    c.want(actions == in_file, f"{actions} actions staged = {in_file} in the file",
           f" -- file holds {in_file}")

    print("\nX2     action counts against the Audit table")
    for action, attribute in sorted(ACTION_AUDIT.items()):
        staged = scalar(ctr, "select count(*) from tpcdi_stage.stg_cmg_customer "
                             f"where ActionType='{action}'", "0")
        audit = scalar(ctr, "select Value from Audit where BatchID=1 and "
                            f"Attribute='{attribute}'", "?")
        c.want(staged == audit, f"{action:<10} {staged} = Audit.{attribute}",
               f" -- Audit says {audit}")

    print("\nX3     absent and empty were kept apart (Clause 4.5.4.2)")
    # The distinction this phase turns on.  If a parser collapsed them, one
    # of these two counts would be zero and the other would absorb it.
    absent = int(scalar(ctr, "select count(*) from tpcdi_stage.stg_cmg_customer "
                             "where ActionType='UPDCUST' and C_CTRY is null", "0"))
    empty = int(scalar(ctr, "select count(*) from tpcdi_stage.stg_cmg_customer "
                            "where ActionType='UPDCUST' and C_CTRY = ''", "0"))
    c.want(absent > 0 and empty > 0,
           f"UPDCUST C_CTRY: {absent} absent (carry) and {empty} empty (NULL)",
           f" -- absent={absent} empty={empty}, one of them collapsed")
    ph_empty = scalar(ctr, "select count(*) from tpcdi_stage.stg_cmg_customer "
                           "where C_PHONE_1_C_CTRY_CODE = ''", "0")
    c.want(int(ph_empty) > 0,
           f"{ph_empty} rows have an empty <C_CTRY_CODE/>, not absent")

    print("\nX4     the account event stream (Clause 4.5.4.3)")
    explicit = scalar(ctr, "select count(*) from tpcdi_stage.stg_cmg_account_event "
                           "where explicit = 1", "0")
    staged_acct = scalar(ctr, "select count(*) from tpcdi_stage.stg_cmg_account", "0")
    c.want(explicit == staged_acct,
           f"{explicit} explicit events = the {staged_acct} <Account> elements")
    for action in ("UPDCUST", "INACT"):
        n = int(scalar(ctr, "select count(*) from tpcdi_stage.stg_cmg_account_event "
                            f"where explicit=0 and ActionType='{action}'", "0"))
        src = int(scalar(ctr, "select count(*) from tpcdi_stage.stg_cmg_customer "
                              f"where ActionType='{action}'", "0"))
        c.want(n >= src, f"{action}: {n} cascade events from {src} actions",
               f" -- {n} is fewer than the {src} actions that caused them")
    # A cascade must name an account that exists and was open at the time.
    orphan = scalar(ctr, """select count(*) from tpcdi_stage.stg_cmg_account_event e
        where e.explicit = 0 and not exists (
            select 1 from tpcdi_stage.stg_cmg_account a
            where a.CA_ID = e.CA_ID and a.C_ID = e.C_ID and a.seq < e.seq)""", "?")
    c.want(orphan == "0", "every cascade event follows an account the customer owns",
           f" -- {orphan} do not")
    # seq is per ACTION, not per account, so it is deliberately not unique:
    # Clause 4.5.4.1 allows NEW and ADDACCT to carry several <Account>
    # elements.  That is harmless, because the forward-fill partitions by
    # CA_ID and orders by seq WITHIN the partition -- two different accounts
    # sharing a seq are in different partitions and never compete.
    #
    # What would break it is the same CA_ID appearing twice in one action.
    # Then the ordering inside that partition is ambiguous and the carry
    # becomes non-deterministic, silently, with row counts still correct.
    # At SF=5 every action happens to carry exactly one account, so this
    # never fires -- which is precisely why it is asserted rather than
    # assumed.
    dup = scalar(ctr, """select count(*) from (
        select CA_ID, seq from tpcdi_stage.stg_cmg_account_event
        group by 1, 2 having count(*) > 1) x""", "?")
    c.want(dup == "0", "(CA_ID, seq) is unique, so the per-account order is total",
           f" -- {dup} pairs repeat; the forward-fill would be ambiguous")

    closed = scalar(ctr, """select count(*) from tpcdi_stage.stg_cmg_account_event e
        join (select CA_ID, min(case when ActionType='CLOSEACCT' then seq end) closed
              from tpcdi_stage.stg_cmg_account group by CA_ID) x on x.CA_ID = e.CA_ID
        where e.explicit = 0 and x.closed is not null and e.seq >= x.closed""", "?")
    c.want(closed == "0", "no cascade event lands on an already-closed account",
           f" -- {closed} do")

    print("\n        stage 1 only: DimCustomer and DimAccount are not loaded yet.")
