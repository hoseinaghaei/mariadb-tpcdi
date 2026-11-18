#!/usr/bin/env bash
# Generate the TPC-DI staging area with DIGen, inside Docker.
#
#   bash scripts/generate-data.sh                 scale factor 5 (the default)
#   bash scripts/generate-data.sh --scale 20      any scale factor DIGen accepts
#   bash scripts/generate-data.sh --force         regenerate without asking
#   bash scripts/generate-data.sh --keep          leave existing data alone
#
# If data already exists for the chosen scale factor it asks before destroying
# it.  Nothing is installed on the host: DIGen runs in a container pinned to
# Java 8, and the data lands in a named Docker volume.
set -u

HERE="$(cd "$(dirname "$0")/.." && pwd)"
SCALE=5; FORCE=0; KEEP=0
while [ $# -gt 0 ]; do
  case "$1" in
    --scale) SCALE="${2:?--scale needs a number}"; shift 2;;
    --scale=*) SCALE="${1#*=}"; shift;;
    --force) FORCE=1; shift;;
    --keep)  KEEP=1; shift;;
    -h|--help) sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'; exit 0;;
    *) echo "unknown option: $1" >&2; exit 2;;
  esac
done

case "$SCALE" in ''|*[!0-9]*) echo "--scale must be a whole number" >&2; exit 2;; esac
if [ "$SCALE" -lt 3 ]; then
  echo "DIGen's minimum scale factor is 3 (Tools/README.txt)." >&2; exit 2
fi

VOL="tpcdi-staging-sf${SCALE}"
IMAGE="tpcdi-digen:jre8"

die() { printf '\n  %s\n' "$1" >&2; exit 1; }

# ---- preflight ------------------------------------------------------------
docker info >/dev/null 2>&1 || die "Docker is not running.  Start Docker Desktop and try again."

if [ ! -f "$HERE/tools/Tools/DIGen.jar" ]; then
  die "tools/Tools/DIGen.jar not found.
  Download TPC-DI_Tools_v1.1.0.zip from tpc.org and unpack it into tools/
  so that tools/Tools/DIGen.jar exists.  See tools/MANIFEST.md."
fi

# ---- existing data --------------------------------------------------------
EXISTING=0
if docker volume inspect "$VOL" >/dev/null 2>&1; then
  FILES=$(docker run --rm -v "$VOL":/staging alpine:3 \
            sh -c 'find /staging -type f 2>/dev/null | wc -l' 2>/dev/null | tr -d ' ')
  [ -n "${FILES:-}" ] && [ "$FILES" -gt 0 ] && EXISTING=$FILES
fi

if [ "$EXISTING" -gt 0 ]; then
  SIZE=$(docker run --rm -v "$VOL":/staging alpine:3 sh -c 'du -sh /staging 2>/dev/null | cut -f1')
  echo "Scale factor $SCALE already has data in volume '$VOL':"
  echo "    $EXISTING files, $SIZE"
  docker run --rm -v "$VOL":/staging alpine:3 \
      sh -c "grep -E 'Scale Factor|End Time' /staging/sf${SCALE}/digen_report.txt 2>/dev/null" \
      | sed 's/^/    /'
  echo

  if [ "$KEEP" = 1 ]; then
    echo "--keep given: leaving it as it is."
    echo "Verify it with:  bash scripts/verify-phase-02.sh $VOL"
    exit 0
  fi

  if [ "$FORCE" = 1 ]; then
    echo "--force given: deleting and regenerating."
  elif [ -t 0 ]; then
    printf 'Delete it and generate again?  [y/N] '
    read -r REPLY
    case "$REPLY" in
      [yY]|[yY][eE][sS]) ;;
      *) echo "Left alone.  Nothing was changed."; exit 0;;
    esac
  else
    die "Data already exists and this is not an interactive terminal.
  Re-run with --force to replace it, or --keep to leave it."
  fi

  docker volume rm -f "$VOL" >/dev/null || die "could not remove volume $VOL"
  echo "  removed $VOL"
fi

# ---- build ----------------------------------------------------------------
echo
echo "Building $IMAGE ..."
docker build -q -f "$HERE/docker/digen/Dockerfile" -t "$IMAGE" "$HERE/tools" >/dev/null \
  || die "image build failed"
echo "  ok ($(docker image inspect "$IMAGE" --format '{{.Architecture}}/{{.Os}}'))"

# ---- generate -------------------------------------------------------------
docker volume create "$VOL" >/dev/null

cat <<'NOTE'

DIGen is about to run.  PDGF asks twice: once to display the bankmark
licence, then for YES to accept it.  Both answers are supplied
automatically, and stdin is held open afterwards because PDGF drops into
an interactive shell and aborts if it reads end-of-file.

Accepting that licence is what lets this run.  Section 1.4 permits
academic and research use; the full text is tools/Tools/PDGF/LICENSE.txt.

NOTE

echo "Generating scale factor $SCALE into $VOL ..."
START=$(date +%s)

FIFO="$(mktemp -u "${TMPDIR:-/tmp}/digen.XXXXXX")"
mkfifo "$FIFO" || die "could not create fifo"
# Answer both prompts, then keep the pipe open until DIGen exits.
( printf '\nYES\n'; while :; do sleep 1; done ) > "$FIFO" 2>/dev/null &
HOLDER=$!
cleanup() { kill "$HOLDER" 2>/dev/null; rm -f "$FIFO"; }
trap cleanup EXIT INT TERM

LOG="$(mktemp "${TMPDIR:-/tmp}/digen-log.XXXXXX")"
docker run --rm -i -v "$VOL":/staging "$IMAGE" -o "/staging/sf${SCALE}" -sf "$SCALE" \
    < "$FIFO" > "$LOG" 2>&1
cleanup; trap - EXIT INT TERM

ELAPSED=$(( $(date +%s) - START ))

# ---- did it work? ---------------------------------------------------------
# NOT the exit code: DIGen returns 0 even after printing "Data generation
# aborted" and leaving the directory empty.  Judge it by the report instead.
REPORT=$(docker run --rm -v "$VOL":/staging alpine:3 \
           cat "/staging/sf${SCALE}/digen_report.txt" 2>/dev/null)

if ! printf '%s' "$REPORT" | grep -q 'End Time:'; then
  echo
  echo "GENERATION FAILED -- no End Time in digen_report.txt." >&2
  echo "Last lines of the generator log:" >&2
  grep -vE '^[[:space:]]*$' "$LOG" | tail -15 | sed 's/^/    /' >&2
  echo >&2
  echo "Full log kept at: $LOG" >&2
  exit 1
fi

rm -f "$LOG"
echo
echo "Done in ${ELAPSED}s."
printf '%s\n' "$REPORT" | grep -E 'Scale Factor|TotalRecords for|Java version' | sed 's/^/    /'
echo "    on disk: $(docker run --rm -v "$VOL":/staging alpine:3 sh -c "du -sh /staging/sf${SCALE} | cut -f1")"
echo
echo "Now verify it:"
echo "    bash scripts/verify-phase-02.sh $VOL"
