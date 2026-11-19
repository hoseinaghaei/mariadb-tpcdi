"""Phase 7 -- CustomerMgmt.xml into DimCustomer and DimAccount.

Plan step 7. Report: report/phases/phase-07-customer.md
Parsing: tpcdi/customermgmt.py
SQL: sql/di/06_customer_stage.sql, sql/di/07_customer_events.sql

Four steps, because Prospect and DimCustomer are circular -- DimCustomer
reads Prospect's demographics, and Prospect.IsCustomer reads DimCustomer.
Clause 4.5.15.1 resolves it by putting IsCustomer last. The whole cycle lives
in this phase so that "Phase 7 is done" means something.

    a  Prospect              rows, demographics, MarketingNameplate
    b  DimCustomer           reads Prospect
    c  DimAccount            reads DimCustomer
    d  Prospect.IsCustomer   reads DimCustomer
"""
import os
import subprocess
import time

from . import customermgmt as cmg
from .common import (ALPINE, DB, DB_PASSWORD, ROOT, Checks, Fail, container,
                     container_running, docker, in_alpine, mariadb,
                     may_replace, read_sql, run_audit_after_validation,
                     run_sql_text, scalar, staging_dir,
                     staging_volume)

NUMBER = 7
TITLE = "Prospect, DimCustomer and DimAccount"
DESCRIPTION = "load Prospect, DimCustomer and DimAccount from CustomerMgmt.xml"

STAGE_SQL = os.path.join(ROOT, "sql", "di", "06_customer_stage.sql")
EVENTS_SQL = os.path.join(ROOT, "sql", "di", "07_customer_events.sql")
PROSPECT_SQL = os.path.join(ROOT, "sql", "di", "08_prospect.sql")
DIMCUSTOMER_SQL = os.path.join(ROOT, "sql", "di", "09_dimcustomer.sql")
DIMACCOUNT_SQL = os.path.join(ROOT, "sql", "di", "10_dimaccount.sql")
ISCUSTOMER_SQL = os.path.join(ROOT, "sql", "di", "11_prospect_iscustomer.sql")

# Prospect and DimCustomer are circular -- DimCustomer reads Prospect's
# demographics, and Prospect.IsCustomer reads DimCustomer.  Clause 4.5.15.1
# resolves it by putting IsCustomer last, "after all customer records in the
# batch have been processed".  The whole cycle lives in this phase so that
# "Phase 7 is done" means something; split across a boundary it would leave a
# half-built Prospect that no check could validate.
WAREHOUSE_TABLES = ["Prospect", "DimCustomer", "DimAccount"]

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
    present += [f"{t}: {n}" for t in WAREHOUSE_TABLES
                if (n := scalar(ctr, f"select count(*) from `{t}`", "0"))
                .isdigit() and int(n) > 0]
    if present:
        if not may_replace(f"Phase 7 data for scale factor {scale}",
                           present, force, keep):
            return
        for t in WAREHOUSE_TABLES:
            mariadb(ctr, sql=f"delete from `{t}`", check=False)
        mariadb(ctr, sql="delete from DImessages where MessageSource in "
                         "('DimCustomer','Prospect')", check=False)
        print("  emptied")

    _parse_and_stage(ctr, scale)
    _derive_events(ctr)

    batch_date = _batch_date(ctr, scale)
    print(f"\nBatch date (Batch1/BatchDate.txt): {batch_date}")
    _step(ctr, "a", "Prospect", PROSPECT_SQL, scale, batch_date)
    _step(ctr, "b", "DimCustomer", DIMCUSTOMER_SQL, scale, batch_date)
    _step(ctr, "c", "DimAccount", DIMACCOUNT_SQL, scale, batch_date)
    _step(ctr, "d", "Prospect.IsCustomer", ISCUSTOMER_SQL, scale, batch_date)
    n = scalar(ctr, "select count(*) from Prospect where IsCustomer = 1")
    print(f"  {n} prospects are current active customers")


def _batch_date(ctr, scale):
    """Clause 2.2.2.2: a single date, the extraction date for the batch."""
    out = docker("exec", ctr, "cat",
                 f"{staging_dir(scale)}/Batch1/BatchDate.txt").stdout.strip()
    if len(out) != 10:
        raise Fail(f"BatchDate.txt reads {out!r}, expected a YYYY-MM-DD date")
    return out


def _step(ctr, letter, label, path, scale, batch_date):
    print(f"\nstep {letter}: {label} ...")
    started = time.time()
    run_sql_text(ctr, read_sql(path, staging=f"{staging_dir(scale)}/Batch1",
                               batchdate=batch_date))
    print(f"  done in {time.time() - started:.1f}s")


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
    run_sql_text(ctr, read_sql(EVENTS_SQL))
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

    # The cascade join uses a.seq > life.opened, which reads as though it
    # would skip the account a NEW action creates in that same action -- the
    # customer row and the account row do share a seq there.  It cannot:
    # ActionType is single-valued, so an account is opened by a NEW/ADDACCT
    # action while a cascade comes from an UPDCUST/INACT one, and those are
    # different actions with different seq values.  Measured: > and >= give
    # the same 5,010 rows.  Asserted because the equivalence depends on that
    # disjointness holding, not on anything in the join itself.
    collide = scalar(ctr, """select count(*) from tpcdi_stage.stg_cmg_customer c
        join tpcdi_stage.stg_cmg_account a on a.seq = c.seq
        where c.ActionType in ('UPDCUST','INACT')""", "?")
    c.want(collide == "0",
           "no UPDCUST/INACT shares a seq with an account event, so the "
           "cascade's strict > skips nothing",
           f" -- {collide} do, and the bound would exclude them")

    # This check used to assert the opposite -- that no cascade reached a
    # closed account -- because Clause 4.5.4.3's "all current accounts" reads
    # that way.  It is wrong.  Clause 4.5.1.3 says UPDCUST updates "each
    # account HELD BY the customer", and the audit's DimAccount SK_CustomerID
    # test requires an account's whole range to sit inside its customer's.
    # Stopping the cascade at the close left 603 closed accounts pointing at
    # a customer version that was later superseded, and that test failed.
    after_close = int(scalar(ctr, """select count(*)
        from tpcdi_stage.stg_cmg_account_event e
        join (select CA_ID, min(case when ActionType='CLOSEACCT' then seq end) closed
              from tpcdi_stage.stg_cmg_account group by CA_ID) x on x.CA_ID = e.CA_ID
        where e.explicit = 0 and x.closed is not null and e.seq >= x.closed""", "0"))
    c.want(after_close > 0,
           f"{after_close} cascade events reach already-closed accounts, "
           "which is what keeps them inside their customer's range")

    print("\nX5     Prospect (Clause 4.5.15)")
    rows = scalar(ctr, "select count(*) from Prospect", "0")
    in_file = in_alpine(staging_volume(scale),
                        f"wc -l < /s/sf{scale}/Batch1/Prospect.csv")
    c.want(rows == in_file, f"{rows} rows = Prospect.csv lines",
           f" -- file has {in_file}")
    # Clause 4.5.15.1: the tags are joined in a fixed order, so a nameplate
    # whose tags are out of order would still look plausible.  Checked by
    # rebuilding the expected string from the columns.
    bad = scalar(ctr, """select count(*) from Prospect where
        MarketingNameplate <> nullif(concat_ws('+',
          case when NetWorth > 1000000 or Income > 200000 then 'HighValue' end,
          case when NumberChildren > 3 or NumberCreditCards > 5 then 'Expenses' end,
          case when Age > 45 then 'Boomer' end,
          case when Income < 50000 or CreditRating < 600 or NetWorth < 100000
               then 'MoneyAlert' end,
          case when NumberCars > 3 or NumberCreditCards > 7 then 'Spender' end,
          case when Age < 25 and NetWorth > 1000000 then 'Inherited' end), '')
        or (MarketingNameplate is null) <> (nullif(concat_ws('+',
          case when NetWorth > 1000000 or Income > 200000 then 'HighValue' end,
          case when NumberChildren > 3 or NumberCreditCards > 5 then 'Expenses' end,
          case when Age > 45 then 'Boomer' end,
          case when Income < 50000 or CreditRating < 600 or NetWorth < 100000
               then 'MoneyAlert' end,
          case when NumberCars > 3 or NumberCreditCards > 7 then 'Spender' end,
          case when Age < 25 and NetWorth > 1000000 then 'Inherited' end), '')
          is null)""", "?")
    c.want(bad == "0", "every MarketingNameplate matches its tags, in order",
           f" -- {bad} do not")

    print("\nX6     DimCustomer (Clause 4.5.4)")
    total = scalar(ctr, "select count(*) from DimCustomer", "0")
    want = scalar(ctr, "select count(*) from tpcdi_stage.stg_cmg_customer "
                       "where ActionType in ('NEW','UPDCUST','INACT')", "0")
    c.want(total == want,
           f"{total} rows = the NEW, UPDCUST and INACT actions", f" -- want {want}")
    cust = scalar(ctr, "select count(distinct CustomerID) from DimCustomer", "0")
    newn = scalar(ctr, "select Value from Audit where BatchID=1 and Attribute='C_NEW'", "?")
    c.want(cust == newn, f"{cust} distinct customers = Audit.C_NEW")
    cur = scalar(ctr, "select sum(IsCurrent) from DimCustomer", "0")
    c.want(cur == cust, f"one current row per customer ({cur})")

    print("\nX7     SCD-2 chain integrity")
    for label, sql in (
        ("no zero-length rows",
         "select count(*) from DimCustomer where EffectiveDate = EndDate"),
        ("current rows end 9999-12-31",
         "select count(*) from DimCustomer where IsCurrent=1 and EndDate<>'9999-12-31'"),
        ("no overlapping ranges",
         """select count(*) from DimCustomer a join DimCustomer b
            on a.CustomerID=b.CustomerID and a.SK_CustomerID<>b.SK_CustomerID
            and a.EffectiveDate >= b.EffectiveDate and a.EffectiveDate < b.EndDate"""),
        ("no gaps between versions",
         """select count(*) from (select EndDate, lead(EffectiveDate) over
            (partition by CustomerID order by EffectiveDate) nxt
            from DimCustomer) x where nxt is not null and nxt <> EndDate"""),
        ("one row per customer per day",
         """select count(*) from (select CustomerID, EffectiveDate from DimCustomer
            group by 1,2 having count(*)>1) x"""),
        ("all surrogate keys distinct",
         "select count(*)-count(distinct SK_CustomerID) from DimCustomer")):
        got = scalar(ctr, sql, "?")
        c.want(got == "0", label, f" -- {got} rows")

    print("\nX8     demographics attach only to the current row (Clause 4.5.4.3)")
    leak = scalar(ctr, "select count(*) from DimCustomer "
                       "where IsCurrent=0 and (AgencyID is not null or "
                       "CreditRating is not null or NetWorth is not null or "
                       "MarketingNameplate is not null)", "?")
    c.want(leak == "0", "no superseded row carries demographics",
           f" -- {leak} do")
    matched = int(scalar(ctr, "select count(*) from DimCustomer "
                              "where IsCurrent=1 and AgencyID is not null", "0"))
    c.want(matched > 0, f"{matched} current customers matched a prospect")

    print("\nX9     the alerts (Clause 4.5.4.4, 4.5.4.5)")
    for text, attrs in (("Invalid customer tier", ["C_TIER_INV"]),
                        ("DOB out of range", ["C_DOB_TO", "C_DOB_TY"])):
        got = int(scalar(ctr, "select count(*) from DImessages where MessageSource="
                              f"'DimCustomer' and MessageText='{text}'", "0"))
        want_n = sum(int(scalar(ctr, "select Value from Audit where BatchID=1 "
                                     f"and Attribute='{a}'", "0")) for a in attrs)
        c.want(got == want_n, f'"{text}": {got} = {" + ".join(attrs)} = {want_n}',
               f" -- Audit says {want_n}")

    print("\nX10    DimAccount (Clause 4.5.1)")
    acct = scalar(ctr, "select count(*) from DimAccount", "0")
    ids = scalar(ctr, "select count(distinct AccountID) from DimAccount", "0")
    cur = scalar(ctr, "select sum(IsCurrent) from DimAccount", "0")
    c.want(int(acct) > 0 and cur == ids,
           f"{acct} rows over {ids} accounts, one current each")
    for label, sql in (
        ("every SK_CustomerID resolved",
         "select count(*) from DimAccount where SK_CustomerID is null"),
        ("every SK_BrokerID resolved",
         "select count(*) from DimAccount where SK_BrokerID is null"),
        ("no zero-length rows",
         "select count(*) from DimAccount where EffectiveDate = EndDate"),
        ("current rows end 9999-12-31",
         "select count(*) from DimAccount where IsCurrent=1 and EndDate<>'9999-12-31'"),
        ("no overlapping ranges",
         """select count(*) from DimAccount a join DimAccount b
            on a.AccountID=b.AccountID and a.SK_AccountID<>b.SK_AccountID
            and a.EffectiveDate >= b.EffectiveDate and a.EffectiveDate < b.EndDate"""),
        ("one row per account per day",
         """select count(*) from (select AccountID, EffectiveDate from DimAccount
            group by 1,2 having count(*)>1) x""")):
        got = scalar(ctr, sql, "?")
        c.want(got == "0", label, f" -- {got} rows")
    # Clause 4.4.1.6.1 puts DimAccount/DimCustomer under the same rule as
    # DimSecurity/DimCompany, and the audit tests it the same way: the
    # account's whole range must sit inside its customer row's range.  The
    # cascade is what delivers it, and it only does so because it reaches
    # accounts that have already closed -- see 07_customer_events.sql.
    out = scalar(ctr, """select count(*) from DimAccount a
        join DimCustomer cu on a.SK_CustomerID = cu.SK_CustomerID
        where not (cu.EffectiveDate <= a.EffectiveDate
                   and a.EndDate <= cu.EndDate)""", "?")
    c.want(out == "0", "every account range sits inside its customer's",
           f" -- {out} do not")

    print("\nX11    the audit query's tests for this phase's tables")
    rows_a, err = run_audit_after_validation(ctr)
    if err:
        c.bad(f"the audit query failed: {err[:100]}")
    else:
        res = {}
        for r in rows_a:
            if len(r) >= 3 and (r[0] not in res or r[1] in ("1", "NULL")):
                res[r[0]] = r[2]
        # "batches" needs three BatchIDs, which only Phase 11 supplies.
        deferred = {"DimCustomer batches", "Prospect batches"}
        deferred.add("DimAccount batches")
        mine = [t for t in res
                if t.startswith(("DimCustomer", "Prospect", "DimAccount"))
                and t not in deferred]
        failed = [t for t in mine if res[t] != "OK"]
        c.want(not failed, f"all {len(mine)} tests report OK "
                           "(batches deferred to Phase 11)",
               f" -- failing: {failed}")
        ok_n = sum(1 for r in rows_a if len(r) >= 3 and r[2] == "OK")
        print(f"        whole suite: {ok_n} of {len(rows_a)} OK")

