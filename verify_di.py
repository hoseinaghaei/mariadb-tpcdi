#!/usr/bin/env python3
"""Verify the TPC-DI implementation, phase by phase. Windows, macOS or Linux.

    python verify_di.py --scale 5              every phase done so far
    python verify_di.py --scale 5 --phase 3    one phase

Read-only. Three of TPC's four appendix queries INSERT into DImessages, so
where they have to be executed they run inside a transaction and are rolled
back; a check asserts DImessages is unchanged afterwards.

Expected values are read from the source files wherever possible rather than
written down here, so a check still means something at a scale factor nobody
has run before.

Exits non-zero if any check fails.
"""
import argparse
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from benchmark_di import (  # noqa: E402
    ALPINE, DB, DB_PASSWORD, Fail, container_running, docker, docker_ok,
    in_alpine, mariadb, scalar, volume_exists)

GREEN, RED, OFF = "\033[32m", "\033[31m", "\033[0m"
if os.name == "nt" and not os.environ.get("WT_SESSION"):
    GREEN = RED = OFF = ""          # old Windows consoles render the codes raw

APPENDIX = os.path.join(HERE, "sql", "di", "adapted")
# Only the audit query reads; the other three insert into DImessages.
WRITERS = {"tpcdi_validation", "tpcdi_visibility_1", "tpcdi_visibility_2"}


class Checks:
    def __init__(self):
        self.passed = self.failed = 0

    def ok(self, msg):
        print(f"  {GREEN}PASS{OFF}  {msg}")
        self.passed += 1

    def bad(self, msg):
        print(f"  {RED}FAIL{OFF}  {msg}")
        self.failed += 1

    def want(self, condition, msg, detail=""):
        self.ok(msg) if condition else self.bad(f"{msg}{detail}")
        return condition


def q(ctr, sql, default=""):
    return (scalar(ctr, sql, default) or default).strip()


def run_appendix(ctr, name):
    """Execute an appendix query. Returns (rows, error-or-None).

    A writer is wrapped in a transaction and rolled back: running them bare
    once left 130 rows in DImessages, which then broke the audit query with
    "Subquery returns more than 1 row" as soon as Audit held data.
    """
    path = os.path.join(APPENDIX, f"{name}.sql")
    if not os.path.isfile(path):
        return [], f"missing {path}"
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    if name in WRITERS:
        text = "START TRANSACTION;\n" + text + "\nROLLBACK;\n"
    proc = subprocess.Popen(
        ["docker", "exec", "-i", ctr, "mariadb", "-uroot", f"-p{DB_PASSWORD}",
         DB, "-N", "-B", "--init-command=SET SESSION sql_mode='PIPES_AS_CONCAT'"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True)
    out, err = proc.communicate(text)
    error = next((l for l in (err or "").splitlines() if l.startswith("ERROR")), None)
    rows = [l.split("\t") for l in out.splitlines() if l.strip()]
    return rows, error


# ------------------------------------------------------------- phase 2 ----

def verify_generate(scale, c):
    vol = f"tpcdi-staging-sf{scale}"
    d = f"/s/sf{scale}"
    if not volume_exists(vol):
        c.bad(f"volume {vol} does not exist -- run phase 2 first")
        return

    print("V1/V3  generator report")
    report = in_alpine(vol, f"cat {d}/digen_report.txt 2>/dev/null")
    c.want(bool(report), "digen_report.txt present")
    c.want(f"Scale Factor: {scale}" in report, f"scale factor {scale}")
    c.want("End Time:" in report, "run finished (End Time written)",
           " -- generation aborted")
    counts = [l.split(":")[-1].strip() for l in report.splitlines()
              if "TotalRecords for Batch" in l]
    c.want(len(counts) == 3 and all(x.isdigit() and int(x) > 0 for x in counts),
           f"three non-zero batch row counts: {' '.join(counts)}")

    print("\nV2/V8  layout and size")
    for b in ("Batch1", "Batch2", "Batch3"):
        c.want(in_alpine(vol, f"[ -d {d}/{b} ] && echo y") == "y", f"{b} present")
    fw = in_alpine(vol, f"ls {d}/Batch1 | grep -c '^FINWIRE....Q.$'")
    # 203 quarters, 1967Q1-2017Q3: date-driven, identical at every scale.
    c.want(fw == "203", f"203 FINWIRE files", f" -- got {fw}")
    print(f"        size on disk: {in_alpine(vol, f'du -sh {d} | cut -f1')}")

    print("\nV4     file inventory (Clauses 2.2.2.1-2.2.2.20)")
    b1 = ("BatchDate.txt CashTransaction.txt CustomerMgmt.xml DailyMarket.txt "
          "Date.txt HR.csv HoldingHistory.txt Industry.txt Prospect.csv "
          "StatusType.txt TaxRate.txt Time.txt Trade.txt TradeHistory.txt "
          "TradeType.txt WatchHistory.txt").split()
    bn = ("Account.txt BatchDate.txt CashTransaction.txt Customer.txt "
          "DailyMarket.txt HoldingHistory.txt Prospect.csv Trade.txt "
          "WatchHistory.txt").split()
    missing = []
    present = set(in_alpine(vol, f"ls {d}/Batch1 {d}/Batch2 {d}/Batch3").split())
    for f in b1:
        if f not in present:
            missing.append(f"Batch1/{f}")
    for f in bn:
        if f not in present:
            missing.append(f"Batch2,3/{f}")
    c.want(not missing, "every expected file present", f" -- missing {missing}")

    print("\nV5     CDC columns (Clause 1.3.2)")
    first = in_alpine(vol, f"head -1 {d}/Batch1/Trade.txt | cut -d'|' -f1")
    c.want(first not in ("I", "U", "D"), "Batch1 has no CDC flag",
           f" -- got '{first}'")
    for b in ("Batch2", "Batch3"):
        flag = in_alpine(vol, f"head -1 {d}/{b}/Trade.txt | cut -d'|' -f1")
        c.want(flag in ("I", "U", "D"), f"{b} CDC flag present ({flag})")

    print("\nV6     FINWIRE record types at column 16 (Clause 2.2.2.8)")
    types = in_alpine(vol, f"cat {d}/Batch1/FINWIRE????Q? | cut -c16-18 "
                           "| sort | uniq -c")
    for line in types.splitlines():
        print("       ", line.strip())
    counts = {}
    for line in types.splitlines():
        n, _, kind = line.strip().partition(" ")
        counts[kind.strip()] = int(n)
    c.want(set(counts) == {"CMP", "SEC", "FIN"},
           "every record classifies as CMP/SEC/FIN", f" -- saw {sorted(counts)}")
    # CMP = 0.5 x SF x 1000 and SEC = 0.8 x SF x 1000.  Confirmed at sf3
    # (1500/2400) and sf5 (2500/4000), and by Generator_audit.csv's own
    # SF value, which reads 5000 for -sf 5.
    c.want(counts.get("CMP") == 5 * scale * 100,
           f"CMP records {counts.get('CMP')} = 0.5 x SF x 1000")
    c.want(counts.get("SEC") == 8 * scale * 100,
           f"SEC records {counts.get('SEC')} = 0.8 x SF x 1000")

    print("\nV7     audit coverage (what tpcdi_audit.sql reads)")
    sets = in_alpine(vol, f"cat {d}/*_audit.csv {d}/Batch*/*_audit.csv "
                          "| cut -d, -f1 | sort -u | grep -v DataSet")
    names = sorted(x.strip() for x in sets.split() if x.strip())
    print(f"        data sets: {' '.join(names)}")
    c.want(len(names) == 14, f"14 audit data sets", f" -- got {len(names)}")
    c.want("FactCashBalances" not in names,
           "FactCashBalances absent, as the audit query expects")


# ------------------------------------------------------------- phase 3 ----

SPEC_TABLES = ("Audit DimAccount DimBroker DimCompany DimCustomer DimDate "
               "DImessages DimSecurity DimTime DimTrade FactCashBalances "
               "FactHoldings FactMarketHistory FactWatches Financial Industry "
               "Prospect StatusType TaxRate TradeType").split()


def verify_schema(scale, c):
    ctr = f"tpcdi-mariadb-sf{scale}"
    if not container_running(ctr):
        c.bad(f"{ctr} is not running -- run phase 3 first")
        return

    print("S1     the twenty tables of Clause 3.2")
    got = mariadb(ctr, sql=f"select table_name from information_schema.tables "
                           f"where table_schema='{DB}' order by table_name",
                  tabs=True).split()
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
        p = q(ctr, "select datetime_precision from information_schema.columns "
                   f"where table_schema='{DB}' and table_name='{table}' "
                   f"and column_name='{col}'")
        c.want(p == "3", f"{table}.{col} is {kind}(3)", f" -- precision {p}")

    print("\nS4     adapted appendix SQL differs only by the documented substitutions")
    # Regenerate into a scratch directory, never in place: regenerating in
    # place would overwrite tampering before the diff could see it, which is
    # exactly the bug this check had on first writing.
    with tempfile.TemporaryDirectory() as tmp:
        r = subprocess.run([sys.executable,
                            os.path.join(HERE, "scripts", "adapt-appendix.py"),
                            "--inner", "--quiet", "--out", tmp],
                           capture_output=True, text=True)
        if r.returncode != 0:
            c.bad(f"adapt-appendix.py failed: {r.stderr.strip()[:120]}")
        else:
            drift = []
            for name in sorted(os.listdir(tmp)):
                a, b = os.path.join(tmp, name), os.path.join(APPENDIX, name)
                if not os.path.isfile(b) or open(a, "rb").read() != open(b, "rb").read():
                    drift.append(name)
            c.want(not drift,
                   "sql/di/adapted is exactly what adapt-appendix.py produces",
                   f" -- drifted: {drift}")

    print("\nS5     TPC's appendix queries run against the schema")
    print("        (this is the real column-name check: their SQL, our tables)")
    before = q(ctr, "select count(*) from DImessages", "0")
    for name in ("tpcdi_visibility_1", "tpcdi_visibility_2", "tpcdi_validation",
                 "tpcdi_audit", "tpcdi_audit.innerjoin"):
        _, err = run_appendix(ctr, name)
        c.want(err is None, f"{name} runs", f" -- {(err or '')[:90]}")
    after = q(ctr, "select count(*) from DImessages", "0")
    c.want(before == after, f"DImessages unchanged by this check ({after} rows)")

    print("\nS6     the audit query returns its full set of tests")
    full, _ = run_appendix(ctr, "tpcdi_audit")
    inner, _ = run_appendix(ctr, "tpcdi_audit.innerjoin")
    c.want(len(full) >= 120, f"{len(full)} tests returned")
    c.want(full == inner,
           "full-join emulation and inner-join simplification agree")
    summarise(full)


def summarise(rows):
    tally = {}
    for r in rows:
        if len(r) >= 3:
            tally[r[2]] = tally.get(r[2], 0) + 1
    print("        result breakdown:")
    for result, n in sorted(tally.items(), key=lambda kv: -kv[1])[:4]:
        print(f"        {n:>6}  {result}")


# ------------------------------------------------------------- phase 4 ----

REFERENCE_FILES = [("DimDate", "Date.txt"), ("DimTime", "Time.txt"),
                   ("Industry", "Industry.txt"), ("StatusType", "StatusType.txt"),
                   ("TaxRate", "TaxRate.txt"), ("TradeType", "TradeType.txt")]


def verify_reference(scale, c):
    ctr, vol = f"tpcdi-mariadb-sf{scale}", f"tpcdi-staging-sf{scale}"
    d = f"/s/sf{scale}"
    if not container_running(ctr):
        c.bad(f"{ctr} is not running -- run phase 4 first")
        return

    print("P1     the Audit table matches the generated CSVs")
    want = in_alpine(vol, f"T=0; for f in $(find {d} -name '*_audit.csv'); do "
                          "T=$((T + $(wc -l < $f) - 1)); done; echo $T")
    got = q(ctr, "select count(*) from Audit", "0")
    c.want(got == want, f"{got} rows = CSV data rows (headers excluded)",
           f" -- CSVs hold {want}")
    hdr = q(ctr, "select count(*) from Audit where DataSet='DataSet' "
                 "or Attribute='Attribute'", "0")
    c.want(hdr == "0", "no header rows loaded (Clause 7.2.6.2)",
           f" -- {hdr} leaked")

    print("\nP2     the Audit table has the shape tpcdi_audit.sql expects")
    b = q(ctr, "select count(distinct BatchID) from Audit")
    mx = q(ctr, "select max(BatchID) from Audit")
    c.want(b == "3" and mx == "3", "3 distinct batches, max 3",
           f" -- batches={b} max={mx}")
    ds = q(ctr, "select count(distinct DataSet) from Audit")
    c.want(ds == "14", "14 data sets", f" -- got {ds}")
    fcb = q(ctr, "select count(*) from Audit where DataSet='FactCashBalances'")
    c.want(fcb == "0", "FactCashBalances absent, as the audit query expects")

    print("\nP3     reference tables match their source files line for line")
    for table, fname in REFERENCE_FILES:
        want = in_alpine(vol, f"wc -l < {d}/Batch1/{fname}")
        got = q(ctr, f"select count(*) from `{table}`", "0")
        c.want(got == want, f"{table} {got} rows = {fname}",
               f" -- file has {want} lines")

    print("\nP4     no carriage return survived the load")
    # Loading with the default '\n' leaves \r on the last column of every row.
    # Invisible in most output, so it is asserted rather than eyeballed.
    cr = q(ctr, r"""select
 (select count(*) from DimDate where DateDesc like '%\r' or FiscalQtrDesc like '%\r')
+(select count(*) from DimTime where SecondDesc like '%\r')
+(select count(*) from Industry where IN_SC_ID like '%\r')
+(select count(*) from StatusType where ST_NAME like '%\r')
+(select count(*) from TradeType where TT_NAME like '%\r')
+(select count(*) from TaxRate where TX_NAME like '%\r')
+(select count(*) from Audit where Attribute like '%\r' or DataSet like '%\r')""",
           "?")
    c.want(cr == "0", "0 columns end in a carriage return", f" -- {cr} do")

    print("\nP5     true/false mapped to 0/1")
    # Clause 2.2.1 Table 2.2.1 says BOOLEAN arrives as "0"/"1".  The generator
    # writes "true"/"false".  Assigned straight across every flag becomes 0,
    # which is why both values are required to be present.
    for table, col in (("DimDate", "HolidayFlag"), ("DimTime", "MarketHoursFlag"),
                       ("DimTime", "OfficeHoursFlag")):
        distinct = q(ctr, f"select count(distinct `{col}`) from `{table}`")
        outside = q(ctr, f"select count(*) from `{table}` where `{col}` not in (0,1)")
        true_n = q(ctr, f"select count(*) from `{table}` where `{col}`=1")
        c.want(distinct == "2" and outside == "0",
               f"{table}.{col} holds both 0 and 1 ({true_n} true)",
               f" -- {distinct} distinct, {outside} outside (0,1)")

    print("\nP6     spot checks against the generator's own numbers")
    # HR_BROKERS is not the employee count -- HR.csv holds all of them, and
    # Clause 4.5.2.1 keeps only EmployeeJobCode 314.  Checking against that
    # filter validates Phase 5's DimBroker rule before it is written.
    hrb = q(ctr, "select Value from Audit where DataSet='DimBroker' "
                 "and Attribute='HR_BROKERS'")
    brokers = in_alpine(vol, f"awk -F, '$6==314' {d}/Batch1/HR.csv | wc -l")
    employees = in_alpine(vol, f"wc -l < {d}/Batch1/HR.csv")
    c.want(hrb == brokers,
           f"HR_BROKERS = {hrb} = HR.csv rows with EmployeeJobCode 314 "
           f"(of {employees} employees)", f" -- job-code rows = {brokers}")
    sf = q(ctr, "select DValue from Audit where DataSet='Generator' "
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


# ------------------------------------------------------------------ main ---

VERIFIERS = [(2, "source data", verify_generate),
             (3, "warehouse schema", verify_schema),
             (4, "Audit and reference tables", verify_reference)]


def run(scale, phase=None):
    if not docker_ok():
        raise Fail("Docker is not running.  Start Docker Desktop and try again.")
    c = Checks()
    for number, title, fn in VERIFIERS:
        if phase is not None and number != phase:
            continue
        print(f"\n{'=' * 68}\nPhase {number} -- {title}\n{'=' * 68}")
        fn(scale, c)
    print(f"\npassed {c.passed}, failed {c.failed}")
    return 0 if c.failed == 0 else 1


def main():
    ap = argparse.ArgumentParser(description="Verify the TPC-DI implementation.")
    ap.add_argument("--scale", type=int, default=5)
    ap.add_argument("--phase", type=int, choices=[v[0] for v in VERIFIERS])
    a = ap.parse_args()
    return run(a.scale, a.phase)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Fail as exc:
        print(f"\n  {exc}\n", file=sys.stderr)
        sys.exit(1)
