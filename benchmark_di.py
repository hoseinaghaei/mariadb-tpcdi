#!/usr/bin/env python3
"""Run the TPC-DI benchmark at one scale factor. Windows, macOS or Linux.

    python benchmark_di.py --scale 5

That is the whole thing. It generates the source data, creates the warehouse
and loads it.

    python benchmark_di.py --scale 5              run every phase in order
    python benchmark_di.py --scale 5 --phase 2    run one phase
    python benchmark_di.py --scale 5 --force      replace existing data, no prompt
    python benchmark_di.py --scale 5 --keep       never replace anything
    python benchmark_di.py --scale 5 --verify     check without changing anything

Requires Docker. Nothing else -- MariaDB, DIGen, Java 8 and every dependency
live inside containers, so the host needs no database, no JDK and no Python
packages beyond the standard library.

Why Python and not shell: the professor may be on Windows, where bash is not a
given. This drives Docker through subprocess and does nothing platform
specific, so one file serves all three systems.

The work itself lives in tpcdi/phaseNN_*.py, one module per phase.
"""
import argparse
import sys

import tpcdi
from tpcdi.common import ALPINE, MIN_SCALE, Fail, banner, docker, docker_ok

# Measured: SF=3 is 268 MiB of source data, SF=5 is 465 MiB.  The warehouse
# roughly doubles that and Docker needs room to work.
DISK_GB = {3: 4, 5: 6}


def preflight(scale):
    if scale < MIN_SCALE:
        raise Fail(f"DIGen's minimum scale factor is {MIN_SCALE} "
                   "(tools/Tools/README.txt).")
    if not docker_ok():
        raise Fail("Docker is not running.  Start Docker Desktop and try again.")
    need = DISK_GB.get(scale, max(4, scale * 2))
    out = docker("run", "--rm", ALPINE, "df", "-BG", "/", check=False).stdout
    try:
        avail = int(out.splitlines()[1].split()[3].rstrip("G"))
        if avail < need:
            print(f"  warning: Docker reports {avail}G free, scale factor "
                  f"{scale} wants about {need}G")
    except (IndexError, ValueError):
        pass


def main():
    ap = argparse.ArgumentParser(
        description="Run the TPC-DI benchmark against MariaDB, in Docker.")
    ap.add_argument("--scale", type=int, default=5, help="scale factor (default 5)")
    ap.add_argument("--phase", type=int, choices=tpcdi.numbers(),
                    help="run one phase instead of all of them")
    ap.add_argument("--force", action="store_true",
                    help="replace existing data without asking")
    ap.add_argument("--keep", action="store_true",
                    help="never replace anything that already exists")
    ap.add_argument("--verify", action="store_true",
                    help="verify without changing anything")
    a = ap.parse_args()

    if a.force and a.keep:
        raise Fail("--force and --keep contradict each other.")

    if a.verify:
        import verify_di
        return verify_di.run(a.scale, a.phase)

    preflight(a.scale)
    for mod in tpcdi.PHASES:
        if a.phase is not None and mod.NUMBER != a.phase:
            continue
        banner(mod.NUMBER, mod.DESCRIPTION)
        mod.run(a.scale, a.force, a.keep)

    print(f"\nVerify with:\n    python verify_di.py --scale {a.scale}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main() or 0)
    except Fail as exc:
        print(f"\n  {exc}\n", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        sys.exit(130)
