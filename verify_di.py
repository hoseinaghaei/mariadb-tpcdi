#!/usr/bin/env python3
"""Verify the TPC-DI implementation, phase by phase. Windows, macOS or Linux.

    python verify_di.py --scale 5              every phase done so far
    python verify_di.py --scale 5 --phase 3    one phase

Read-only. Three of TPC's four appendix queries INSERT into DImessages, so
where they must be executed they run inside a transaction and are rolled back,
and a check asserts DImessages is unchanged afterwards.

Expected values are read from the source files wherever possible rather than
written down here, so a check still means something at a scale factor nobody
has run before.

Exits non-zero if any check fails. The checks themselves live beside the code
they test, in tpcdi/phaseNN_*.py.
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

import tpcdi
from tpcdi.common import (Checks, Fail, banner, container, docker_ok,
                          run_audit_after_validation)


def audit_overview(scale):
    """Print where the audit query stands overall, and who owns what is red.

    The per-phase checks assert only the tests belonging to tables the
    finished phases own.  That is deliberate -- asserting all of them now
    would make every run red until the last phase and the signal would be
    worthless -- but it would be misleading to stop there, because a reader
    could take "passed 100, failed 0" to mean the benchmark passes.  It does
    not yet.  This says so.
    """
    import re
    rows, err = run_audit_after_validation(container(scale))
    if err:
        print(f"\n  the audit query could not run: {err[:100]}")
        return
    claimed = set()
    for path in sorted(os.listdir(os.path.join(HERE, "tpcdi"))):
        if not path.startswith("phase") or not path.endswith(".py"):
            continue
        with open(os.path.join(HERE, "tpcdi", path), encoding="utf-8") as fh:
            m = re.search(r"AUDIT_TESTS\s*=\s*\[(.*?)\]", fh.read(), re.S)
        if m:
            claimed |= set(re.findall(r'"([^"]+)"', m.group(1)))

    ok = sum(1 for r in rows if len(r) >= 3 and r[2] == "OK")
    failing = {}
    for r in rows:
        if len(r) >= 3 and r[2] != "OK":
            failing.setdefault(r[0].split()[0], set()).add(r[0])

    print(f"\n{'=' * 68}\nTPC's audit query: {ok} of {len(rows)} tests OK"
          f"\n{'=' * 68}")
    if not failing:
        print("  every test passes -- the benchmark run is valid (Clause 7.2.6.7).")
        return
    print("  Still failing, by the table they belong to.  These need the data")
    print("  that later phases load; none is a regression in a finished phase.")
    for table, tests in sorted(failing.items(), key=lambda kv: -len(kv[1])):
        mine = " (asserted here, batch 1 only)" if tests & claimed else ""
        print(f"    {table:<20} {len(tests):>2}{mine}")
    print("\n  A phase is finished when its own tests pass.  The run is valid")
    print("  only when all of them do, which is Phase 10 for batch 1 and")
    print("  Phase 11 for batches 2 and 3.")


def run(scale, phase=None):
    if not docker_ok():
        raise Fail("Docker is not running.  Start Docker Desktop and try again.")
    c = Checks()
    for mod in tpcdi.PHASES:
        if phase is not None and mod.NUMBER != phase:
            continue
        banner(mod.NUMBER, mod.TITLE)
        mod.verify(scale, c)
    print(f"\npassed {c.passed}, failed {c.failed}")
    if phase is None:
        audit_overview(scale)
    return 0 if c.failed == 0 else 1


def main():
    ap = argparse.ArgumentParser(description="Verify the TPC-DI implementation.")
    ap.add_argument("--scale", type=int, default=5)
    ap.add_argument("--phase", type=int, choices=tpcdi.numbers())
    a = ap.parse_args()
    return run(a.scale, a.phase)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Fail as exc:
        print(f"\n  {exc}\n", file=sys.stderr)
        sys.exit(1)
