"""Phase 2 -- generate the source data with DIGen.

Plan step 2. Report: report/phases/phase-02-generate-source-data.md
"""
import os
import subprocess
import time

from .common import (DIGEN_IMAGE, ROOT, Checks, Fail, container, container_exists,
                     docker, in_alpine, may_replace, remove_container,
                     staging_volume,
                     volume_exists, warehouse_volume)

NUMBER = 2
TITLE = "source data"
DESCRIPTION = "generate the source data with DIGen"


def run(scale, force=False, keep=False, **_opts):
    vol = staging_volume(scale)
    jar = os.path.join(ROOT, "tools", "Tools", "DIGen.jar")
    if not os.path.isfile(jar):
        raise Fail(
            "tools/Tools/DIGen.jar not found.\n"
            "  Download TPC-DI_Tools_v1.1.0.zip from tpc.org and unpack it into\n"
            "  tools/ so that tools/Tools/DIGen.jar exists.  See tools/MANIFEST.md.")

    if _has_data(vol):
        n = in_alpine(vol, "find /s -type f 2>/dev/null | wc -l")
        size = in_alpine(vol, "du -sh /s 2>/dev/null | cut -f1")
        if not may_replace(f"Scale factor {scale} source data",
                           [f"volume {vol}", f"{n} files, {size}"], force, keep):
            return
        # The warehouse container mounts this volume read-only, so Docker
        # refuses to remove it while that container exists.  Phase 3 recreates
        # the container, so dropping it costs nothing -- but the schema and any
        # loaded data go with it, so say so rather than doing it silently.
        ctr = container(scale)
        if container_exists(ctr):
            print(f"  removing {ctr} first -- it holds {vol} open")
            print("  (phase 3 recreates it; the warehouse is rebuilt from scratch)")
            remove_container(ctr)
            docker("volume", "rm", "-f", warehouse_volume(scale), check=False)
        docker("volume", "rm", "-f", vol)

    print(f"Building {DIGEN_IMAGE} ...")
    docker("build", "-q", "-f",
           os.path.join(ROOT, "docker", "digen", "Dockerfile"),
           "-t", DIGEN_IMAGE, os.path.join(ROOT, "tools"))
    arch = docker("image", "inspect", DIGEN_IMAGE,
                  "--format", "{{.Architecture}}/{{.Os}}").stdout.strip()
    print(f"  ok ({arch})")

    docker("volume", "create", vol)
    print(f"\nGenerating scale factor {scale} into {vol} ...")
    print("  PDGF asks twice -- once to show the bankmark licence, then for YES.")
    print("  Both are answered here; section 1.4 permits academic and research")
    print("  use and the full text is tools/Tools/PDGF/LICENSE.txt.")

    started = time.time()
    out = _run_digen(vol, scale)
    elapsed = time.time() - started

    # NOT the exit code.  DIGen returns 0 after printing "Data generation
    # aborted" and leaving the directory empty, so success is judged by the
    # report it writes.
    report = in_alpine(vol, f"cat /s/sf{scale}/digen_report.txt 2>/dev/null")
    if "End Time:" not in report:
        tail = [l for l in out.splitlines() if l.strip()][-12:]
        raise Fail("generation failed -- no End Time in digen_report.txt\n\n"
                   + "\n".join("    " + l for l in tail))

    print(f"\nDone in {elapsed:.0f}s.")
    for line in report.splitlines():
        if any(k in line for k in ("Scale Factor", "TotalRecords for", "Java version")):
            print("   ", line.strip())
    print("    on disk:", in_alpine(vol, f"du -sh /s/sf{scale} | cut -f1"))


def _has_data(vol):
    if not volume_exists(vol):
        return False
    n = in_alpine(vol, "find /s -type f 2>/dev/null | wc -l")
    return n.isdigit() and int(n) > 0


def _run_digen(vol, scale):
    """Drive DIGen, answering both prompts and keeping stdin open.

    PDGF prints the licence, waits for ENTER, prints the terms, waits for YES,
    then drops into an interactive command shell.  On end-of-file it reads
    null commands until its own flood guard aborts the run -- so stdin must
    stay open for the whole generation.

    Deliberately NOT subprocess.communicate(): that closes stdin.  Reading
    stdout to EOF blocks until DIGen exits while leaving the handle open,
    which is what the shell version was faking with a fifo and a sleep, and
    unlike a fifo it works on Windows.
    """
    proc = subprocess.Popen(
        ["docker", "run", "--rm", "-i", "-v", f"{vol}:/staging", DIGEN_IMAGE,
         "-o", f"/staging/sf{scale}", "-sf", str(scale)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True)
    try:
        proc.stdin.write("\nYES\n")
        proc.stdin.flush()
    except (BrokenPipeError, OSError):
        pass
    out = proc.stdout.read()
    proc.wait()
    try:
        proc.stdin.close()
    except (BrokenPipeError, OSError):
        pass
    return out


# ---------------------------------------------------------------- checks ---

BATCH1_FILES = (
    "BatchDate.txt CashTransaction.txt CustomerMgmt.xml DailyMarket.txt "
    "Date.txt HR.csv HoldingHistory.txt Industry.txt Prospect.csv "
    "StatusType.txt TaxRate.txt Time.txt Trade.txt TradeHistory.txt "
    "TradeType.txt WatchHistory.txt").split()

INCREMENTAL_FILES = (
    "Account.txt BatchDate.txt CashTransaction.txt Customer.txt "
    "DailyMarket.txt HoldingHistory.txt Prospect.csv Trade.txt "
    "WatchHistory.txt").split()


def verify(scale, c: Checks):
    vol, d = staging_volume(scale), f"/s/sf{scale}"
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
    # 203 quarters, 1967Q1-2017Q3: date-driven, so identical at every scale.
    fw = in_alpine(vol, f"ls {d}/Batch1 | grep -c '^FINWIRE....Q.$'")
    c.want(fw == "203", "203 FINWIRE files", f" -- got {fw}")
    print(f"        size on disk: {in_alpine(vol, f'du -sh {d} | cut -f1')}")

    print("\nV4     file inventory (Clauses 2.2.2.1-2.2.2.20)")
    present = set(in_alpine(vol, f"ls {d}/Batch1 {d}/Batch2 {d}/Batch3").split())
    missing = [f"Batch1/{f}" for f in BATCH1_FILES if f not in present]
    missing += [f"Batch2,3/{f}" for f in INCREMENTAL_FILES if f not in present]
    c.want(not missing, "every expected file present", f" -- missing {missing}")

    print("\nV5     CDC columns (Clause 1.3.2)")
    first = in_alpine(vol, f"head -1 {d}/Batch1/Trade.txt | cut -d'|' -f1")
    c.want(first not in ("I", "U", "D"), "Batch1 has no CDC flag",
           f" -- got '{first}'")
    for b in ("Batch2", "Batch3"):
        flag = in_alpine(vol, f"head -1 {d}/{b}/Trade.txt | cut -d'|' -f1")
        c.want(flag in ("I", "U", "D"), f"{b} CDC flag present ({flag})",
               f" -- got '{flag}'")

    print("\nV6     FINWIRE record types at column 16 (Clause 2.2.2.8)")
    types = in_alpine(vol, f"cat {d}/Batch1/FINWIRE????Q? | cut -c16-18 "
                           "| sort | uniq -c")
    counts_by_kind = {}
    for line in types.splitlines():
        print("       ", line.strip())
        n, _, kind = line.strip().partition(" ")
        counts_by_kind[kind.strip()] = int(n)
    c.want(set(counts_by_kind) == {"CMP", "SEC", "FIN"},
           "every record classifies as CMP/SEC/FIN",
           f" -- saw {sorted(counts_by_kind)}")
    # CMP = 0.5 x SF x 1000, SEC = 0.8 x SF x 1000.  Confirmed at sf3
    # (1500/2400) and sf5 (2500/4000), and corroborated by Generator_audit.csv,
    # whose own SF value reads 5000 for -sf 5.
    c.want(counts_by_kind.get("CMP") == 5 * scale * 100,
           f"CMP records {counts_by_kind.get('CMP')} = 0.5 x SF x 1000")
    c.want(counts_by_kind.get("SEC") == 8 * scale * 100,
           f"SEC records {counts_by_kind.get('SEC')} = 0.8 x SF x 1000")

    print("\nV7     audit coverage (what tpcdi_audit.sql reads)")
    sets = in_alpine(vol, f"cat {d}/*_audit.csv {d}/Batch*/*_audit.csv "
                          "| cut -d, -f1 | sort -u | grep -v DataSet")
    names = sorted(x.strip() for x in sets.split() if x.strip())
    print(f"        data sets: {' '.join(names)}")
    c.want(len(names) == 14, "14 audit data sets", f" -- got {len(names)}")
    c.want("FactCashBalances" not in names,
           "FactCashBalances absent, as the audit query expects")
