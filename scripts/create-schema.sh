#!/usr/bin/env bash
# Create the TPC-DI Data Warehouse schema in MariaDB, inside Docker.
#
#   bash scripts/create-schema.sh                 scale factor 5 (the default)
#   bash scripts/create-schema.sh --scale 3       any scale factor
#   bash scripts/create-schema.sh --force         replace without asking
#   bash scripts/create-schema.sh --keep          leave an existing one alone
#
# If a warehouse already exists for the chosen scale factor it asks before
# destroying it.  Nothing is installed on the host and no port is published:
# the server is reached with "docker exec".
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

VOL="tpcdi-dw-sf${SCALE}"
CTR="tpcdi-mariadb-sf${SCALE}"
IMG="mariadb:11.4"
DB="tpcdi"
PW="tpcdi"
DDL="$HERE/sql/di/01_schema.sql"

die() { printf '\n  %s\n' "$1" >&2; exit 1; }
mysql_() { docker exec -i "$CTR" mariadb -uroot -p"$PW" "$@"; }

docker info >/dev/null 2>&1 || die "Docker is not running.  Start Docker Desktop and try again."
[ -f "$DDL" ] || die "missing $DDL"

# ---- existing warehouse ---------------------------------------------------
TABLES=0
if docker volume inspect "$VOL" >/dev/null 2>&1; then
  if docker ps -q -f "name=^${CTR}$" | grep -q .; then
    TABLES=$(mysql_ -N -B -e \
      "select count(*) from information_schema.tables where table_schema='$DB'" 2>/dev/null | tr -d ' \r')
  else
    # volume exists but nothing is serving it -- count files instead
    FILES=$(docker run --rm -v "$VOL":/v alpine:3 sh -c 'ls /v 2>/dev/null | wc -l' | tr -d ' ')
    [ "${FILES:-0}" -gt 0 ] && TABLES="?"
  fi
fi

if [ "$TABLES" != 0 ]; then
  echo "A warehouse already exists for scale factor $SCALE:"
  echo "    volume    $VOL"
  echo "    container $CTR $(docker ps -q -f "name=^${CTR}$" | grep -q . && echo '(running)' || echo '(stopped)')"
  [ "$TABLES" = "?" ] && echo "    tables    unknown (server not running)" \
                      || echo "    tables    $TABLES"
  echo
  if [ "$KEEP" = 1 ]; then
    echo "--keep given: leaving it as it is."
    exit 0
  fi
  if [ "$FORCE" = 1 ]; then
    echo "--force given: dropping and recreating."
  elif [ -t 0 ]; then
    printf 'Drop it and create the schema again?  [y/N] '
    read -r REPLY
    case "$REPLY" in [yY]|[yY][eE][sS]) ;;
      *) echo "Left alone.  Nothing was changed."; exit 0;; esac
  else
    die "A warehouse already exists and this is not an interactive terminal.
  Re-run with --force to replace it, or --keep to leave it."
  fi
  docker rm -f "$CTR" >/dev/null 2>&1
  docker volume rm -f "$VOL" >/dev/null || die "could not remove volume $VOL"
  echo "  removed container and volume"
fi

# ---- start the server -----------------------------------------------------
docker rm -f "$CTR" >/dev/null 2>&1
docker volume create "$VOL" >/dev/null
echo
echo "Starting $IMG as $CTR ..."
# The staging area is mounted read-only so LOAD DATA INFILE can reach the
# source files.  Read-only because the server has no business writing there,
# and because a mistake could otherwise damage data that every later
# measurement is taken against.
STAGE="tpcdi-staging-sf${SCALE}"
STAGE_ARGS=""
if docker volume inspect "$STAGE" >/dev/null 2>&1; then
  STAGE_ARGS="-v ${STAGE}:/staging:ro"
  echo "  mounting $STAGE at /staging (read-only)"
else
  echo "  note: no staging volume '$STAGE' yet -- generate it with"
  echo "        bash scripts/generate-data.sh --scale $SCALE"
fi

docker run -d --name "$CTR" \
  -e MARIADB_ROOT_PASSWORD="$PW" -e MARIADB_DATABASE="$DB" \
  -v "$VOL":/var/lib/mysql $STAGE_ARGS \
  "$IMG" --secure-file-priv=/staging >/dev/null || die "could not start $CTR"

printf '  waiting for the server'
for i in $(seq 1 60); do
  if mysql_ -e 'select 1' >/dev/null 2>&1; then echo " ok (${i}s)"; break; fi
  printf '.'; sleep 1
  [ "$i" = 60 ] && { echo; docker logs --tail 20 "$CTR" >&2; die "server did not come up"; }
done

# ---- apply the DDL --------------------------------------------------------
echo "Applying sql/di/01_schema.sql ..."
if ! mysql_ "$DB" < "$DDL" 2>/tmp/ddl.err; then
  echo; echo "SCHEMA CREATION FAILED:" >&2; sed 's/^/    /' /tmp/ddl.err >&2; exit 1
fi

N=$(mysql_ -N -B -e "select count(*) from information_schema.tables where table_schema='$DB'" | tr -d ' \r')
echo
echo "Created $N tables in database '$DB'."
echo
echo "Connect with:"
echo "    docker exec -it $CTR mariadb -uroot -p$PW $DB"
echo "Verify with:"
echo "    bash scripts/verify-phase-03.sh $SCALE"
