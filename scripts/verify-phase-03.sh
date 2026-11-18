#!/usr/bin/env bash
# Verify Phase 3: the TPC-DI Data Warehouse schema in MariaDB.
#
#   bash scripts/verify-phase-03.sh [scale]      default: 5
#
# Read-only.  Exits non-zero if any check fails.
set -u
HERE="$(cd "$(dirname "$0")/.." && pwd)"
SCALE="${1:-5}"
CTR="tpcdi-mariadb-sf${SCALE}"
DB=tpcdi
MODE="SET SESSION sql_mode='PIPES_AS_CONCAT'"
PASS=0; FAIL=0
ok()  { printf '  \033[32mPASS\033[0m  %s\n' "$1"; PASS=$((PASS+1)); }
bad() { printf '  \033[31mFAIL\033[0m  %s\n' "$1"; FAIL=$((FAIL+1)); }
q()   { docker exec -i "$CTR" mariadb -uroot -ptpcdi "$DB" -N -B \
          --init-command="$MODE" "$@" 2>/dev/null; }
runfile() { docker exec -i "$CTR" mariadb -uroot -ptpcdi "$DB" \
          --init-command="$MODE" "$@"; }

echo "Phase 3 verification -- container $CTR"
echo
docker info >/dev/null 2>&1 || { echo "  Docker is not running." >&2; exit 1; }
docker ps -q -f "name=^${CTR}$" | grep -q . || {
  echo "  $CTR is not running.  Create it with:"
  echo "    bash scripts/create-schema.sh --scale $SCALE"; exit 1; }

# ---- the twenty tables ----------------------------------------------------
echo "S1     the twenty tables of Clause 3.2"
WANT="Audit DimAccount DimBroker DimCompany DimCustomer DimDate DImessages
DimSecurity DimTime DimTrade FactCashBalances FactHoldings FactMarketHistory
FactWatches Financial Industry Prospect StatusType TaxRate TradeType"
GOT=$(q -e "select table_name from information_schema.tables where table_schema='$DB' order by table_name" | tr -d '\r')
MISS=""
for T in $WANT; do echo "$GOT" | grep -qix "$T" || MISS="$MISS $T"; done
N=$(echo "$GOT" | grep -c .)
[ "$N" = 20 ] && ok "20 tables present" || bad "$N tables (expected 20)"
[ -z "$MISS" ] && ok "every table named in Clause 3.2 exists" || bad "missing:$MISS"
EXTRA=$(echo "$GOT" | while read -r t; do echo "$WANT" | grep -qiw "$t" || echo "$t"; done | tr '\n' ' ')
[ -z "$(echo "$EXTRA" | tr -d ' ')" ] && ok "no unexpected tables" || bad "unexpected: $EXTRA"

# ---- no approximate numeric types ----------------------------------------
echo
echo "S2     exact numeric storage (Clause 3.2 forbids approximate types)"
APPROX=$(q -e "select concat(table_name,'.',column_name,' ',data_type) from information_schema.columns where table_schema='$DB' and data_type in ('float','double','real')" | tr '\n' ' ')
[ -z "$(echo "$APPROX" | tr -d ' ')" ] && ok "no float/double/real columns" \
                                       || bad "approximate types found: $APPROX"

# ---- millisecond precision ------------------------------------------------
echo
echo "S3     millisecond precision (Clause 3.2 base types)"
DT=$(q -e "select datetime_precision from information_schema.columns where table_schema='$DB' and table_name='DImessages' and column_name='MessageDateAndTime'" | tr -d ' \r')
[ "$DT" = 3 ] && ok "DImessages.MessageDateAndTime is DATETIME(3)" \
              || bad "DImessages.MessageDateAndTime precision $DT (expected 3)"
TM=$(q -e "select datetime_precision from information_schema.columns where table_schema='$DB' and table_name='DimTime' and column_name='TimeValue'" | tr -d ' \r')
[ "$TM" = 3 ] && ok "DimTime.TimeValue is TIME(3)" || bad "DimTime.TimeValue precision $TM (expected 3)"

# ---- the adapted files match the pristine ones ----------------------------
echo
echo "S4     adapted appendix SQL differs only by the documented substitutions"
# Regenerate into a scratch directory and compare.  Regenerating in place
# would overwrite any tampering before the diff could see it -- which is
# exactly the bug this check had on first writing: a deliberately corrupted
# tpcdi_audit.sql passed 14 of 14.
TMPA=$(mktemp -d)
if python3 "$HERE/scripts/adapt-appendix.py" --inner --quiet --out "$TMPA" >/tmp/adapt.out 2>&1; then
  DRIFT=$(diff -rq "$TMPA" "$HERE/sql/di/adapted" 2>&1 | head -5)
  [ -z "$DRIFT" ] && ok "sql/di/adapted is exactly what adapt-appendix.py produces" \
                  || bad "sql/di/adapted has drifted: $(echo "$DRIFT" | tr '\n' '; ')"
else
  bad "adapt-appendix.py failed: $(tail -1 /tmp/adapt.out)"
fi
rm -rf "$TMPA"

# ---- TPC's own SQL runs against our schema --------------------------------
echo
echo "S5     TPC's appendix queries run against the schema"
echo "        (this is the real column-name check: their SQL, our tables)"
for Q in tpcdi_visibility_1 tpcdi_visibility_2 tpcdi_validation tpcdi_audit tpcdi_audit.innerjoin; do
  ERR=$(runfile < "$HERE/sql/di/adapted/$Q.sql" 2>&1 >/dev/null | grep -m1 "^ERROR")
  [ -z "$ERR" ] && ok "$Q runs" || bad "$Q: $(echo "$ERR" | cut -c1-90)"
done

# ---- the audit returns a full test suite ----------------------------------
echo
echo "S6     the audit query returns its full set of tests"
q < "$HERE/sql/di/adapted/tpcdi_audit.sql" > /tmp/p3_full.tsv
q < "$HERE/sql/di/adapted/tpcdi_audit.innerjoin.sql" > /tmp/p3_inner.tsv
NT=$(grep -c . /tmp/p3_full.tsv)
[ "$NT" -ge 120 ] && ok "$NT tests returned" || bad "only $NT tests returned"
if diff -q /tmp/p3_full.tsv /tmp/p3_inner.tsv >/dev/null; then
  ok "full-join emulation and inner-join simplification agree"
else
  bad "the two audit variants disagree -- investigate before trusting either"
fi
echo "        result breakdown (an empty warehouse is expected to fail many):"
awk -F'\t' '{print $3}' /tmp/p3_full.tsv | sort | uniq -c | sort -rn | head -4 | sed 's/^/        /'

echo
printf 'passed %d, failed %d\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ] || exit 1
