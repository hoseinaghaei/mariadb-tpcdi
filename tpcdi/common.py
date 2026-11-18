"""Shared plumbing: Docker, MariaDB, prompting, and the check tally.

Nothing benchmark-specific lives here. Each phase module owns its own loading
and its own checks; this is only what all of them need.
"""
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Keep in step with docker/digen/Dockerfile.
DIGEN_IMAGE = "tpcdi-digen:jre8"
MARIADB_IMAGE = "mariadb:11.4"
ALPINE = "alpine:3"
DB = "tpcdi"
DB_PASSWORD = "tpcdi"          # local-only container, never published on a port

MIN_SCALE = 3                  # tools/Tools/README.txt: range 3 - 2147483647

APPENDIX_DIR = os.path.join(ROOT, "sql", "di", "adapted")
# Only the audit query reads; the other three INSERT into DImessages.
APPENDIX_WRITERS = {"tpcdi_validation", "tpcdi_visibility_1", "tpcdi_visibility_2"}

GREEN, RED, OFF = "\033[32m", "\033[31m", "\033[0m"
if os.name == "nt" and not os.environ.get("WT_SESSION"):
    GREEN = RED = OFF = ""     # older Windows consoles render the codes raw


class Fail(Exception):
    """Something the user has to fix. Printed without a traceback."""


def staging_volume(scale):
    return f"tpcdi-staging-sf{scale}"


def warehouse_volume(scale):
    return f"tpcdi-dw-sf{scale}"


def container(scale):
    return f"tpcdi-mariadb-sf{scale}"


def staging_dir(scale):
    return f"/staging/sf{scale}"


def banner(number, text):
    print(f"\n{'=' * 68}\nPhase {number} -- {text}\n{'=' * 68}")


# --------------------------------------------------------------- docker ----

def docker(*args, check=True, capture=True, stdin=None):
    """Run a docker command.

    A failure raises Fail carrying docker's own message rather than a
    traceback: the person running this reads reports, not stack traces.
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
        docker("info")
        return True
    except (Fail, FileNotFoundError):
        return False


def volume_exists(name):
    return docker("volume", "inspect", name, check=False).returncode == 0


def container_running(name):
    return bool(docker("ps", "-q", "-f", f"name=^{name}$").stdout.strip())


def container_exists(name):
    return bool(docker("ps", "-a", "-q", "-f", f"name=^{name}$").stdout.strip())


def in_alpine(volume, shell_cmd, mount="/s"):
    """Run a shell command in alpine with a volume mounted read-only."""
    return docker("run", "--rm", "-v", f"{volume}:{mount}:ro", ALPINE,
                  "sh", "-c", shell_cmd, check=False).stdout.strip()


# -------------------------------------------------------------- mariadb ----

def mariadb(ctr, sql=None, sql_file=None, database=DB,
            tabs=False, pipes_as_concat=False, check=True):
    """Send SQL to the server. Returns stdout; raises Fail on error."""
    cmd = ["exec", "-i", ctr, "mariadb", "-uroot", f"-p{DB_PASSWORD}", database]
    if tabs:
        cmd += ["-N", "-B"]
    if pipes_as_concat:
        cmd += ["--init-command=SET SESSION sql_mode='PIPES_AS_CONCAT'"]
    if sql is not None:
        r = docker(*cmd, "-e", sql, check=False)
    else:
        with open(sql_file, "rb") as fh:
            r = subprocess.run(["docker", *cmd], stdin=fh, text=True,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if check and r.returncode != 0:
        err = (r.stderr or "").strip().splitlines()
        msg = next((l for l in err if l.startswith("ERROR")), "\n".join(err[-3:]))
        raise Fail(f"SQL failed: {msg}")
    return r.stdout


def scalar(ctr, sql, default=""):
    out = mariadb(ctr, sql=sql, tabs=True, check=False)
    first = (out or "").strip().splitlines()
    return first[0].strip() if first else default


def run_sql_text(ctr, text, database=DB):
    """Pipe a block of SQL into the server without writing a temporary file."""
    proc = subprocess.Popen(
        ["docker", "exec", "-i", ctr, "mariadb", "-uroot",
         f"-p{DB_PASSWORD}", database],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True)
    _, err = proc.communicate(text)
    if proc.returncode != 0:
        line = next((l for l in (err or "").splitlines() if l.startswith("ERROR")),
                    (err or "").strip()[:300])
        raise Fail(f"load failed: {line}")


def wait_for_server(ctr, seconds=90):
    for i in range(seconds):
        if docker("exec", ctr, "mariadb", "-uroot", f"-p{DB_PASSWORD}",
                  "-e", "select 1", check=False).returncode == 0:
            return i + 1
        time.sleep(1)
    logs = docker("logs", "--tail", "20", ctr, check=False).stdout
    raise Fail(f"the server did not come up within {seconds}s\n{logs}")


def run_appendix(ctr, name):
    """Execute one of TPC's appendix queries. Returns (rows, error-or-None).

    A writer is wrapped in a transaction and rolled back. Running them bare
    once left 130 rows in DImessages, which then broke the audit query with
    "Subquery returns more than 1 row" as soon as Audit held data -- a
    verifier that mutates what it verifies is worse than none.
    """
    path = os.path.join(APPENDIX_DIR, f"{name}.sql")
    if not os.path.isfile(path):
        return [], f"missing {path}"
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    if name in APPENDIX_WRITERS:
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


def audit_summary(rows):
    tally = {}
    for r in rows:
        if len(r) >= 3:
            tally[r[2]] = tally.get(r[2], 0) + 1
    print("        result breakdown:")
    for result, n in sorted(tally.items(), key=lambda kv: -kv[1])[:4]:
        print(f"        {n:>6}  {result}")


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


# ----------------------------------------------------------------- checks --

class Checks:
    """Tally of verification results, shared across phases."""

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
        return bool(condition)
