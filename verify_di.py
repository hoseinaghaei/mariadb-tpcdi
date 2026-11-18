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
import sys

import tpcdi
from tpcdi.common import Checks, Fail, banner, docker_ok


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
