#!/usr/bin/env bash
# Phase 4: load the Audit table and the reference tables.
#
#   bash scripts/load-reference.sh                 scale factor 5
#   bash scripts/load-reference.sh --scale 3       any scale factor
#   bash scripts/load-reference.sh --force         reload without asking
#   bash scripts/load-reference.sh --keep          leave existing rows alone
#
# The Audit table is loaded first, deliberately: it is the generator's own
# record of what it produced, and nearly every test in tpcdi_audit.sql
# compares the warehouse against it.  Nothing can be verified until it exists.
set -u
HERE="$(cd "$(dirname "$0")/.." && pwd)"
SCALE=5; FORCE=0; KEEP=0
while [ $# -gt 0 ]; do
  case "$1" in
    --scale) SCALE="${2:?--scale needs a number}"; shift 2;;
    --scale=*) SCALE="${1#*=}"; shift;;
    --force) FORCE=1; shift;;
    --keep)  KEEP=1; shift;;
    -h|--help) sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'; exit 0;;
    *) echo "unknown option: $1" >&2; exit 2;;
  esac
done
case "$SCALE" in ''|*[!0-9]*) echo "--scale must be a whole number" >&2; exit 2;; esac

CTR="tpcdi-mariadb-sf${SCALE}"
DB=tpcdi
STAGEDIR="/staging/sf${SCALE}"
TABLES="Audit DimDate DimTime Industry StatusType TaxRate TradeType"

die() { printf '\n  %s\n' "$1" >&2; exit 1; }
sql() { docker exec -i "$CTR" mariadb -uroot -ptpcdi "$DB" -N -B "$@"; }
sqlf() { docker exec -i "$CTR" mariadb -uroot -ptpcdi "$DB" "$@"; }

docker info >/dev/null 2>&1 || die "Docker is not running."
docker ps -q -f "name=^${CTR}$" | grep -q . || die "$CTR is not running.
  Create the schema first:  bash scripts/create-schema.sh --scale $SCALE"
docker exec "$CTR" test -d "$STAGEDIR/Batch1" 2>/dev/null || die \
  "the server cannot see $STAGEDIR/Batch1.
  Generate the data, then recreate the container so it gets mounted:
    bash scripts/generate-data.sh --scale $SCALE
    bash scripts/create-schema.sh --scale $SCALE --force"

# ---- anything already loaded? ---------------------------------------------
EXISTING=0; SUMMARY=""
for T in $TABLES; do
  N=$(sql -e "select count(*) from \`$T\`" 2>/dev/null | tr -d ' \r')
  N=${N:-0}
  [ "$N" -gt 0 ] && { EXISTING=$((EXISTING+N)); SUMMARY="$SUMMARY    $T: $N
"; }
done

if [ "$EXISTING" -gt 0 ]; then
  echo "Phase 4 tables already hold data for scale factor $SCALE:"
  printf '%s' "$SUMMARY"
  echo
  if [ "$KEEP" = 1 ]; then
    echo "--keep given: leaving it as it is."
    echo "Verify with:  bash scripts/verify-phase-04.sh $SCALE"
    exit 0
  fi
  if [ "$FORCE" = 1 ]; then
    echo "--force given: emptying those tables and reloading."
  elif [ -t 0 ]; then
    printf 'Empty them and load again?  [y/N] '
    read -r REPLY
    case "$REPLY" in [yY]|[yY][eE][sS]) ;;
      *) echo "Left alone.  Nothing was changed."; exit 0;; esac
  else
    die "Data already present and this is not an interactive terminal.
  Re-run with --force to reload, or --keep to leave it."
  fi
  for T in $TABLES; do sql -e "delete from \`$T\`" >/dev/null 2>&1; done
  echo "  emptied"
fi

# ---- the Audit table, first -----------------------------------------------
# 238 files at SF=5, scattered across the batch directories.  LOAD DATA takes
# no wildcard, so they are enumerated and loaded one by one.  Clause 7.2.6.2:
# the first row of every audit file is field names and must NOT be loaded.
echo
echo "Loading the Audit table ..."
FILES=$(docker exec "$CTR" sh -c "find $STAGEDIR -name '*_audit.csv' | sort")
NFILES=$(printf '%s\n' "$FILES" | grep -c .)
[ "$NFILES" -gt 0 ] || die "no *_audit.csv found under $STAGEDIR"

AUDIT_SQL=$(mktemp)
printf '%s\n' "$FILES" | while read -r F; do
  [ -n "$F" ] || continue
  cat >> "$AUDIT_SQL" <<SQL
LOAD DATA INFILE '$F' INTO TABLE Audit
  FIELDS TERMINATED BY ',' LINES TERMINATED BY '\r\n' IGNORE 1 LINES
  (DataSet, @BatchID, @Date, Attribute, @Value, @DValue)
  SET BatchID = nullif(@BatchID,''), \`Date\` = nullif(@Date,''),
      Value = nullif(@Value,''), DValue = nullif(@DValue,'');
SQL
done

if ! sqlf < "$AUDIT_SQL" 2>/tmp/p4.err; then
  echo "AUDIT LOAD FAILED:" >&2; sed 's/^/    /' /tmp/p4.err >&2; exit 1
fi
rm -f "$AUDIT_SQL"
echo "  $NFILES files -> $(sql -e 'select count(*) from Audit' | tr -d ' \r') rows"

# ---- the reference tables --------------------------------------------------
echo
echo "Loading the reference tables from $STAGEDIR/Batch1 ..."
REF_SQL=$(mktemp)
sed "s#__STAGING__#${STAGEDIR}/Batch1#g" "$HERE/sql/di/02_load_reference.sql" > "$REF_SQL"
if ! sqlf < "$REF_SQL" 2>/tmp/p4.err; then
  echo "REFERENCE LOAD FAILED:" >&2; sed 's/^/    /' /tmp/p4.err >&2; exit 1
fi
rm -f "$REF_SQL"

for T in DimDate DimTime Industry StatusType TaxRate TradeType; do
  printf '  %-12s %s rows\n' "$T" "$(sql -e "select count(*) from \`$T\`" | tr -d ' \r')"
done

echo
echo "Verify with:"
echo "    bash scripts/verify-phase-04.sh $SCALE"
