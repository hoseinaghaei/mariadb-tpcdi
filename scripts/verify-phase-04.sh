#!/usr/bin/env bash
# Verify Phase 4: the Audit table and the reference tables.
#
#   bash scripts/verify-phase-04.sh [scale]      default: 5
#
# Read-only.  Counts in the warehouse are compared against the source files
# themselves, not against numbers written down here, so the check still means
# something at a scale factor nobody has run before.
set -u
HERE="$(cd "$(dirname "$0")/.." && pwd)"
SCALE="${1:-5}"
CTR="tpcdi-mariadb-sf${SCALE}"
VOL="tpcdi-staging-sf${SCALE}"
DIR="/staging/sf${SCALE}"
DB=tpcdi
PASS=0; FAIL=0
ok()  { printf '  \033[32mPASS\033[0m  %s\n' "$1"; PASS=$((PASS+1)); }
bad() { printf '  \033[31mFAIL\033[0m  %s\n' "$1"; FAIL=$((FAIL+1)); }
q()   { docker exec -i "$CTR" mariadb -uroot -ptpcdi "$DB" -N -B "$@" 2>/dev/null | tr -d ' \r'; }
stage() { docker run --rm -v "$VOL":/s alpine:3 sh -c "$1" 2>/dev/null | tr -d ' \r'; }

echo "Phase 4 verification -- container $CTR, staging $VOL"
echo
docker info >/dev/null 2>&1 || { echo "  Docker is not running." >&2; exit 1; }
docker ps -q -f "name=^${CTR}$" | grep -q . || {
  echo "  $CTR is not running.  Load it with:"
  echo "    bash scripts/load-reference.sh --scale $SCALE"; exit 1; }

# ---- P1: every audit row, and only the audit rows -------------------------
echo "P1     the Audit table matches the generated CSVs"
WANT=$(stage "T=0; for f in \$(find /s/sf${SCALE} -name '*_audit.csv'); do T=\$((T + \$(wc -l < \$f) - 1)); done; echo \$T")
GOT=$(q -e "select count(*) from Audit")
[ "$GOT" = "$WANT" ] && ok "$GOT rows = CSV data rows (headers excluded)" \
                     || bad "Audit has $GOT rows, CSVs hold $WANT"
# Clause 7.2.6.2: the header row must not be loaded.
HDR=$(q -e "select count(*) from Audit where DataSet='DataSet' or Attribute='Attribute'")
[ "$HDR" = 0 ] && ok "no header rows loaded (Clause 7.2.6.2)" \
               || bad "$HDR header rows leaked into Audit"

# ---- P2: the shape the audit query expects --------------------------------
echo
echo "P2     the Audit table has the shape tpcdi_audit.sql expects"
B=$(q -e "select count(distinct BatchID) from Audit")
MX=$(q -e "select max(BatchID) from Audit")
[ "$B" = 3 ] && [ "$MX" = 3 ] && ok "3 distinct batches, max 3" \
                              || bad "batches=$B max=$MX (expected 3 and 3)"
DS=$(q -e "select count(distinct DataSet) from Audit")
[ "$DS" = 14 ] && ok "14 data sets" || bad "$DS data sets (expected 14)"
FCB=$(q -e "select count(*) from Audit where DataSet='FactCashBalances'")
[ "$FCB" = 0 ] && ok "FactCashBalances absent, as the audit query expects" \
               || bad "FactCashBalances present ($FCB rows)"

# ---- P3: reference tables against their source files ----------------------
echo
echo "P3     reference tables match their source files line for line"
for PAIR in "DimDate:Date.txt" "DimTime:Time.txt" "Industry:Industry.txt" \
            "StatusType:StatusType.txt" "TaxRate:TaxRate.txt" "TradeType:TradeType.txt"; do
  T="${PAIR%%:*}"; F="${PAIR##*:}"
  W=$(stage "wc -l < /s/sf${SCALE}/Batch1/$F")
  G=$(q -e "select count(*) from \`$T\`")
  [ "$G" = "$W" ] && ok "$T $G rows = $F" || bad "$T has $G rows, $F has $W lines"
done

# ---- P4: the CRLF trap ----------------------------------------------------
echo
echo "P4     no carriage return survived the load"
# Loading these files with the default '\n' terminator leaves \r on the last
# column of every row.  It is invisible in most output, so it is checked
# rather than eyeballed.
CR=$(q -e "select
 (select count(*) from DimDate where DateDesc like '%\r' or FiscalQtrDesc like '%\r')
+(select count(*) from DimTime where SecondDesc like '%\r')
+(select count(*) from Industry where IN_SC_ID like '%\r')
+(select count(*) from StatusType where ST_NAME like '%\r')
+(select count(*) from TradeType where TT_NAME like '%\r')
+(select count(*) from TaxRate where TX_NAME like '%\r')
+(select count(*) from Audit where Attribute like '%\r' or DataSet like '%\r')")
[ "$CR" = 0 ] && ok "0 columns end in a carriage return" || bad "$CR values still carry \\r"

# ---- P5: the boolean trap -------------------------------------------------
echo
echo "P5     true/false mapped to 0/1"
# Clause 2.2.1 Table 2.2.1 says BOOLEAN arrives as "0"/"1".  The generator
# writes "true"/"false".  Assigned straight across, every flag silently
# becomes 0 -- which is why both values are required to be present.
for PAIR in "DimDate:HolidayFlag" "DimTime:MarketHoursFlag" "DimTime:OfficeHoursFlag"; do
  T="${PAIR%%:*}"; C="${PAIR##*:}"
  D=$(q -e "select count(distinct \`$C\`) from \`$T\`")
  BAD=$(q -e "select count(*) from \`$T\` where \`$C\` not in (0,1)")
  if [ "$D" = 2 ] && [ "$BAD" = 0 ]; then
    ok "$T.$C holds both 0 and 1 ($(q -e "select count(*) from \`$T\` where \`$C\`=1") true)"
  else
    bad "$T.$C has $D distinct values, $BAD outside {0,1}"
  fi
done

# ---- P6: a value the audit query will actually read -----------------------
echo
echo "P6     spot checks against the generator's own numbers"
# HR_BROKERS is not the number of employees -- HR.csv holds all of them.
# Clause 4.5.2.1: only EmployeeJobCode 314 are brokers, the rest are ignored.
# Checking against that filter here means Phase 5's DimBroker rule is already
# validated against the generator before a line of it is written.
HRB=$(q -e "select Value from Audit where DataSet='DimBroker' and Attribute='HR_BROKERS'")
HRF=$(stage "awk -F, '\$6==314' /s/sf${SCALE}/Batch1/HR.csv | wc -l")
HRALL=$(stage "wc -l < /s/sf${SCALE}/Batch1/HR.csv")
[ -n "$HRB" ] && [ "$HRB" = "$HRF" ] \
  && ok "HR_BROKERS = $HRB = HR.csv rows with EmployeeJobCode 314 (of $HRALL employees)" \
  || bad "HR_BROKERS = '$HRB', job-code-314 rows = $HRF"
SF=$(q -e "select DValue from Audit where DataSet='Generator' and Attribute='SF'")
WANTSF="${SCALE}000"
case "$SF" in "$WANTSF"*) ok "Generator SF = $SF, confirming -sf x 1000";;
              *) bad "Generator SF = '$SF' (expected ${WANTSF}...)";; esac

# ---- P7: the audit query's own Audit-table tests --------------------------
echo
echo "P7     tpcdi_audit.sql's checks on the Audit table now pass"
docker exec -i "$CTR" mariadb -uroot -ptpcdi "$DB" -N -B \
  --init-command="SET SESSION sql_mode='PIPES_AS_CONCAT'" \
  < "$HERE/sql/di/adapted/tpcdi_audit.sql" > /tmp/p4_audit.tsv 2>/dev/null
for T in "Audit table batches" "Audit table sources"; do
  R=$(awk -F'\t' -v t="$T" '$1==t {print $3}' /tmp/p4_audit.tsv)
  [ "$R" = "OK" ] && ok "\"$T\" -> OK" || bad "\"$T\" -> ${R:-not found}"
done
TOT=$(grep -c . /tmp/p4_audit.tsv)
OKN=$(awk -F'\t' '$3=="OK"' /tmp/p4_audit.tsv | grep -c .)
echo "        whole suite: $OKN of $TOT OK (the rest need the data Phase 5+ loads)"

echo
printf 'passed %d, failed %d\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ] || exit 1
