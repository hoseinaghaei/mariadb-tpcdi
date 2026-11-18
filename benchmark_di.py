#!/usr/bin/env python3
"""Run the TPC-DI benchmark at one scale factor. Windows, macOS or Linux.

    python benchmark_di.py --scale 5

That is the whole thing. It generates the source data, creates the warehouse,
loads it, and verifies every step against TPC's own audit query.

    python benchmark_di.py --scale 5              run every phase in order
    python benchmark_di.py --scale 5 --phase 2    run one phase
    python benchmark_di.py --scale 5 --verify     verify without changing anything
    python benchmark_di.py --scale 5 --force      replace existing data, no prompt
    python benchmark_di.py --scale 5 --keep       never replace anything

Requires Docker. Nothing else -- MariaDB, DIGen, Java 8 and every dependency
live inside containers, so the host needs no database, no JDK and no Python
packages beyond the standard library.

Why Python and not shell: the professor may be on Windows, where bash is not a
given. This drives Docker through subprocess and does nothing platform
specific, so one file serves all three systems.
"""
import argparse
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))

# Keep in step with the scripts/ equivalents and with docker/digen/Dockerfile.
DIGEN_IMAGE = "tpcdi-digen:jre8"
MARIADB_IMAGE = "mariadb:11.4"
DB = "tpcdi"
DB_PASSWORD = "tpcdi"          # local-only container, never published on a port
ALPINE = "alpine:3"

MIN_SCALE = 3                  # Tools/README.txt: range 3 - 2147483647
# Measured: SF=3 is 268 MiB of source data, SF=5 is 465 MiB.  The warehouse
# roughly doubles that, and Docker needs room to work.
DISK_GB = {3: 4, 5: 6}


class Fail(Exception):
    """Something the user has to fix. Printed without a traceback."""


# --------------------------------------------------------------- docker ----

def docker(*args, check=True, capture=True, stdin=None):
    """Run a docker command. Returns CompletedProcess.

    A failure raises Fail with docker's own message rather than a traceback:
    the person running this is a reader of the report, not of Python.
    """
    r = subprocess.run(
        ["docker", *args], check=False, stdin=stdin,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        text=True)
    if check and r.returncode != 0:
        detail = ((r.stderr or "") + (r.stdout or "")).strip().splitlines()
        why = detail[0] if detail else f"exit status {r.returncode}"
        raise Fail(f"docker {' '.join(args[:3])} failed:\n  {why}")
    return r


def docker_ok():
    try:
        docker("info", check=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def volume_exists(name):
    return docker("volume", "inspect", name, check=False).returncode == 0


def container_running(name):
    out = docker("ps", "-q", "-f", f"name=^{name}$").stdout.strip()
    return bool(out)


def in_alpine(volume, shell_cmd, mount="/s"):
    """Run a shell command inside alpine with a volume mounted read-only."""
    r = docker("run", "--rm", "-v", f"{volume}:{mount}:ro", ALPINE,
               "sh", "-c", shell_cmd, check=False)
    return r.stdout.strip()


# ------------------------------------------------------------- the server --

def mariadb(container, sql=None, sql_file=None, database=DB,
            tabs=False, pipes_as_concat=False, check=True):
    """Send SQL to the server. Returns stdout; raises Fail on error."""
    cmd = ["exec", "-i", container, "mariadb",
           "-uroot", f"-p{DB_PASSWORD}", database]
    if tabs:
        cmd += ["-N", "-B"]
    if pipes_as_concat:
        cmd += ["--init-command=SET SESSION sql_mode='PIPES_AS_CONCAT'"]
    if sql is not None:
        cmd += ["-e", sql]
        r = docker(*cmd, check=False)
    else:
        with open(sql_file, "rb") as fh:
            r = subprocess.run(["docker", *cmd], stdin=fh, text=True,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if check and r.returncode != 0:
        err = (r.stderr or "").strip().splitlines()
        msg = next((l for l in err if l.startswith("ERROR")), "\n".join(err[-3:]))
        raise Fail(f"SQL failed: {msg}")
    return r.stdout


def scalar(container, sql, default=None):
    out = mariadb(container, sql=sql, tabs=True, check=False).strip()
    return out.splitlines()[0].strip() if out.strip() else default


def wait_for_server(container, seconds=90):
    for i in range(seconds):
        r = docker("exec", container, "mariadb", "-uroot",
                   f"-p{DB_PASSWORD}", "-e", "select 1", check=False)
        if r.returncode == 0:
            return i + 1
        time.sleep(1)
    logs = docker("logs", "--tail", "20", container, check=False).stdout
    raise Fail(f"the server did not come up within {seconds}s\n{logs}")


# ------------------------------------------------------------- prompting ---

def may_replace(what, detail, force, keep):
    """Ask before destroying something. Returns True to go ahead."""
    print(f"{what} already exists:")
    for line in detail:
        print(f"    {line}")
    print()
    if keep:
        print("--keep given: leaving it as it is.")
        return False
    if force:
        print("--force given: replacing it.")
        return True
    if not sys.stdin.isatty():
        raise Fail("It already exists and this is not an interactive terminal.\n"
                   "  Re-run with --force to replace it, or --keep to leave it.")
    try:
        reply = input("Delete it and build it again?  [y/N] ")
    except EOFError:
        reply = ""
    if reply.strip().lower() not in ("y", "yes"):
        print("Left alone.  Nothing was changed.")
        return False
    return True


# --------------------------------------------------------------- phase 2 ---

def phase_generate(scale, force, keep):
    """Generate the staging area with DIGen."""
    vol = f"tpcdi-staging-sf{scale}"
    tools = os.path.join(HERE, "tools", "Tools", "DIGen.jar")
    if not os.path.isfile(tools):
        raise Fail(
            "tools/Tools/DIGen.jar not found.\n"
            "  Download TPC-DI_Tools_v1.1.0.zip from tpc.org and unpack it into\n"
            "  tools/ so that tools/Tools/DIGen.jar exists.  See tools/MANIFEST.md.")

    if volume_exists(vol):
        n = in_alpine(vol, "find /s -type f 2>/dev/null | wc -l")
        if n.isdigit() and int(n) > 0:
            size = in_alpine(vol, "du -sh /s 2>/dev/null | cut -f1")
            if not may_replace(f"Scale factor {scale} source data",
                               [f"volume {vol}", f"{n} files, {size}"], force, keep):
                return
            # The warehouse container mounts this volume read-only, so Docker
            # refuses to remove it while that container exists.  Phase 3
            # recreates the container, so dropping it here costs nothing --
            # but the schema and any loaded data go with it, so say so.
            ctr = f"tpcdi-mariadb-sf{scale}"
            if docker("ps", "-a", "-q", "-f", f"name=^{ctr}$").stdout.strip():
                print(f"  removing {ctr} first -- it holds {vol} open")
                print("  (phase 3 recreates it; the warehouse is rebuilt from scratch)")
                docker("rm", "-f", ctr, check=False)
                docker("volume", "rm", "-f", f"tpcdi-dw-sf{scale}", check=False)
            docker("volume", "rm", "-f", vol)

    print(f"Building {DIGEN_IMAGE} ...")
    docker("build", "-q", "-f", os.path.join(HERE, "docker", "digen", "Dockerfile"),
           "-t", DIGEN_IMAGE, os.path.join(HERE, "tools"))
    arch = docker("image", "inspect", DIGEN_IMAGE,
                  "--format", "{{.Architecture}}/{{.Os}}").stdout.strip()
    print(f"  ok ({arch})")

    docker("volume", "create", vol)
    print(f"\nGenerating scale factor {scale} into {vol} ...")
    print("  PDGF asks twice -- once to show the bankmark licence, then for YES.")
    print("  Both are answered here; section 1.4 permits academic and research use")
    print("  and the full text is tools/Tools/PDGF/LICENSE.txt.")

    started = time.time()
    # PDGF needs stdin to stay OPEN after the two answers: it drops into an
    # interactive shell and, on end-of-file, reads null commands until its own
    # flood guard kills the run.  Holding the pipe handle until the process
    # exits does that, and unlike a fifo or a sleep it works on Windows too.
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
    # Deliberately NOT communicate(): it closes stdin, and PDGF reacts to
    # end-of-file by reading null commands until its flood guard kills the
    # run.  Reading stdout to EOF blocks until DIGen exits while leaving the
    # stdin handle open, which is the behaviour the fifo was faking in shell.
    out = proc.stdout.read()
    proc.wait()
    try:
        proc.stdin.close()
    except (BrokenPipeError, OSError):
        pass
    elapsed = time.time() - started

    # NOT the exit code.  DIGen returns 0 after printing "Data generation
    # aborted" and leaving the directory empty, so success is judged by the
    # report it writes.
    report = in_alpine(vol, f"cat /s/sf{scale}/digen_report.txt 2>/dev/null")
    if "End Time:" not in report:
        tail = "\n".join(l for l in out.splitlines() if l.strip())[-1200:]
        raise Fail("generation failed -- no End Time in digen_report.txt\n\n"
                   + "\n".join("    " + l for l in tail.splitlines()[-12:]))

    print(f"\nDone in {elapsed:.0f}s.")
    for line in report.splitlines():
        if any(k in line for k in ("Scale Factor", "TotalRecords for", "Java version")):
            print("   ", line.strip())
    print("    on disk:", in_alpine(vol, f"du -sh /s/sf{scale} | cut -f1"))


# --------------------------------------------------------------- phase 3 ---

def phase_schema(scale, force, keep):
    """Create the twenty warehouse tables."""
    vol, ctr = f"tpcdi-dw-sf{scale}", f"tpcdi-mariadb-sf{scale}"
    stage = f"tpcdi-staging-sf{scale}"
    ddl = os.path.join(HERE, "sql", "di", "01_schema.sql")
    if not os.path.isfile(ddl):
        raise Fail(f"missing {ddl}")

    if volume_exists(vol):
        detail = [f"volume {vol}", f"container {ctr}"]
        if container_running(ctr):
            n = scalar(ctr, f"select count(*) from information_schema.tables "
                            f"where table_schema='{DB}'", "?")
            detail.append(f"{n} tables")
        if not may_replace(f"A warehouse for scale factor {scale}", detail, force, keep):
            return
        docker("rm", "-f", ctr, check=False)
        docker("volume", "rm", "-f", vol)

    docker("rm", "-f", ctr, check=False)
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

    print(f"\nStarting {MARIADB_IMAGE} as {ctr} ...")
    docker("run", "-d", "--name", ctr,
           "-e", f"MARIADB_ROOT_PASSWORD={DB_PASSWORD}", "-e", f"MARIADB_DATABASE={DB}",
           *mounts, MARIADB_IMAGE, "--secure-file-priv=/staging")
    print(f"  ready after {wait_for_server(ctr)}s")

    print("Applying sql/di/01_schema.sql ...")
    mariadb(ctr, sql_file=ddl)
    n = scalar(ctr, f"select count(*) from information_schema.tables "
                    f"where table_schema='{DB}'")
    print(f"\nCreated {n} tables in database '{DB}'.")


# --------------------------------------------------------------- phase 4 ---

REFERENCE_TABLES = ["DimDate", "DimTime", "Industry", "StatusType",
                    "TaxRate", "TradeType"]


def phase_reference(scale, force, keep):
    """Load the Audit table and the reference tables."""
    ctr = f"tpcdi-mariadb-sf{scale}"
    stagedir = f"/staging/sf{scale}"
    if not container_running(ctr):
        raise Fail(f"{ctr} is not running.  Run phase 3 first.")
    if docker("exec", ctr, "test", "-d", f"{stagedir}/Batch1", check=False).returncode:
        raise Fail(f"the server cannot see {stagedir}/Batch1.\n"
                   "  Run phase 2, then phase 3 again so the volume gets mounted.")

    tables = ["Audit"] + REFERENCE_TABLES
    present = []
    for t in tables:
        n = scalar(ctr, f"select count(*) from `{t}`", "0")
        if n and n.isdigit() and int(n) > 0:
            present.append(f"{t}: {n}")
    if present:
        if not may_replace(f"Phase 4 tables for scale factor {scale}",
                           present, force, keep):
            return
        for t in tables:
            mariadb(ctr, sql=f"delete from `{t}`", check=False)
        print("  emptied")

    # The Audit table first, deliberately: it is the generator's own record of
    # what it produced, and nearly every test in tpcdi_audit.sql compares the
    # warehouse against a row in it.
    print("\nLoading the Audit table ...")
    files = docker("exec", ctr, "sh", "-c",
                   f"find {stagedir} -name '*_audit.csv' | sort").stdout.split()
    if not files:
        raise Fail(f"no *_audit.csv found under {stagedir}")
    # LOAD DATA takes no wildcard, so the files are enumerated.  Clause
    # 7.2.6.2: the first row of each is field names and must NOT be loaded.
    batch = "\n".join(
        f"LOAD DATA INFILE '{f}' INTO TABLE Audit\n"
        "  FIELDS TERMINATED BY ',' LINES TERMINATED BY '\\r\\n' IGNORE 1 LINES\n"
        "  (DataSet, @BatchID, @Date, Attribute, @Value, @DValue)\n"
        "  SET BatchID = nullif(@BatchID,''), `Date` = nullif(@Date,''),\n"
        "      Value = nullif(@Value,''), DValue = nullif(@DValue,'');"
        for f in files)
    _run_sql_text(ctr, batch)
    print(f"  {len(files)} files -> {scalar(ctr, 'select count(*) from Audit')} rows")

    print(f"\nLoading the reference tables from {stagedir}/Batch1 ...")
    src = os.path.join(HERE, "sql", "di", "02_load_reference.sql")
    with open(src, encoding="utf-8") as fh:
        text = fh.read().replace("__STAGING__", f"{stagedir}/Batch1")
    _run_sql_text(ctr, text)
    for t in REFERENCE_TABLES:
        print(f"  {t:<12} {scalar(ctr, f'select count(*) from `{t}`')} rows")


def _run_sql_text(container, text):
    """Pipe a block of SQL into the server without a temporary file on disk."""
    proc = subprocess.Popen(
        ["docker", "exec", "-i", container, "mariadb",
         "-uroot", f"-p{DB_PASSWORD}", DB],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True)
    _, err = proc.communicate(text)
    if proc.returncode != 0:
        line = next((l for l in (err or "").splitlines() if l.startswith("ERROR")),
                    (err or "").strip()[:300])
        raise Fail(f"load failed: {line}")


# ------------------------------------------------------------------ main ---

PHASES = [
    (2, "generate",  "generate the source data with DIGen", phase_generate),
    (3, "schema",    "create the twenty warehouse tables",  phase_schema),
    (4, "reference", "load the Audit and reference tables", phase_reference),
]


def preflight(scale):
    if scale < MIN_SCALE:
        raise Fail(f"DIGen's minimum scale factor is {MIN_SCALE} "
                   "(tools/Tools/README.txt).")
    if not docker_ok():
        raise Fail("Docker is not running.  Start Docker Desktop and try again.")
    need = DISK_GB.get(scale, max(4, scale * 2))
    free = docker("run", "--rm", ALPINE, "df", "-BG", "/", check=False).stdout
    try:
        avail = int(free.splitlines()[1].split()[3].rstrip("G"))
        if avail < need:
            print(f"  warning: Docker reports {avail}G free, scale factor "
                  f"{scale} wants about {need}G")
    except (IndexError, ValueError):
        pass


def main():
    ap = argparse.ArgumentParser(
        description="Run the TPC-DI benchmark against MariaDB, in Docker.")
    ap.add_argument("--scale", type=int, default=5, help="scale factor (default 5)")
    ap.add_argument("--phase", type=int, choices=[p[0] for p in PHASES],
                    help="run one phase instead of all of them")
    ap.add_argument("--force", action="store_true",
                    help="replace existing data without asking")
    ap.add_argument("--keep", action="store_true",
                    help="never replace anything that already exists")
    ap.add_argument("--verify", action="store_true",
                    help="verify without changing anything (see verify_di.py)")
    a = ap.parse_args()

    if a.force and a.keep:
        raise Fail("--force and --keep contradict each other.")

    if a.verify:
        import verify_di
        return verify_di.run(a.scale, phase=a.phase)

    preflight(a.scale)
    chosen = [p for p in PHASES if a.phase is None or p[0] == a.phase]
    for number, name, description, fn in chosen:
        print(f"\n{'=' * 68}\nPhase {number} -- {description}\n{'=' * 68}")
        fn(a.scale, a.force, a.keep)

    print(f"\nVerify with:\n    python benchmark_di.py --scale {a.scale} --verify")
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
