# Phase 2 — how to test it by hand

Every command is a single line. Run them from the repository root:

```
cd ~/Desktop/projects/mariadb-tpcdi
```

All ten were run against the live volume on 2026-11-18, so the expected output
shown is what actually appears.

---

## 1. Confirm you have the same tools package

```
shasum -a 256 tools/*TPC-DI-Tool.zip
```

Expect `d9c10de5dafa781ddd4df72453f50af2f0c9c7ccfa0cbae99ed0be70a2ede22b`,
matching `tools/MANIFEST.md`.

## 2. Build the generator image

```
docker build -f docker/digen/Dockerfile -t tpcdi-digen:jre8 tools/
```

## 3. Generate the data

Skip to step 4 if you only want to check data that already exists.

```
docker volume rm -f tpcdi-staging-sf5 && docker volume create tpcdi-staging-sf5
```

```
( printf '\nYES\n'; sleep 120 ) | docker run --rm -i -v tpcdi-staging-sf5:/staging tpcdi-digen:jre8 -o /staging/sf5 -sf 5
```

About 29 seconds of work, then it sits idle until the `sleep` finishes. That is
deliberate: it holds stdin open so PDGF's command shell does not read EOF and
trip its own flood guard. Ctrl-C is safe once `DIGen completed successfully`
appears.

## 4. The generator's own report — validations V1 and V3

```
docker run --rm -v tpcdi-staging-sf5:/staging alpine:3 cat /staging/sf5/digen_report.txt
```

Expect:

```
Start Time: 2026-11-18T17:36:47+0000
End Time: 2026-11-18T17:37:18+0000
DIGen Version: 1.1.0
Scale Factor: 5
... TotalRecords for Batch1: 7804509
... TotalRecords for Batch2: 33380
... TotalRecords for Batch3: 33455
... TotalRecords all Batches: 7871344
Java version: Temurin 1.8.0_512
```

**The `End Time` line is the real pass condition.** DIGen exits 0 even when it
aborts with an empty directory, so the exit code proves nothing.

Those three batch counts are `RH`, `RI1` and `RI2` in the throughput metric
(Clause 7.5).

## 5. Layout and size — V2 and V8

```
docker run --rm -v tpcdi-staging-sf5:/staging alpine:3 ls /staging/sf5
```

Expect `Batch1`, `Batch2`, `Batch3`, the three `Batch*_audit.csv`,
`Generator_audit.csv` and `digen_report.txt`.

```
docker run --rm -v tpcdi-staging-sf5:/staging alpine:3 du -sh /staging/sf5
```

Expect `465.3M`.

```
docker run --rm -v tpcdi-staging-sf5:/staging alpine:3 sh -c 'ls /staging/sf5/Batch1 | grep -c "^FINWIRE....Q.$"'
```

Expect `203` — one file per quarter, FINWIRE1967Q1 through FINWIRE2017Q3.

## 6. Incremental batch contents — V4

```
docker run --rm -v tpcdi-staging-sf5:/staging alpine:3 ls /staging/sf5/Batch2
```

Expect nine data files and their audit CSVs. Note that `Account.txt` and
`Customer.txt` appear here but **not** in Batch1 — per Clause 1.3.2 those two
sources exist only for Incremental Updates.

## 7. CDC columns — V5, the most telling check

```
docker run --rm -v tpcdi-staging-sf5:/staging alpine:3 head -1 /staging/sf5/Batch1/Trade.txt
```

```
0|2012-07-07 00:02:08|CMPT|TMB|0|AAAAAAAAAAAADMO|2939|9.57|2|12646|10.02|58.95|27.31|1611.19
```

```
docker run --rm -v tpcdi-staging-sf5:/staging alpine:3 head -1 /staging/sf5/Batch2/Trade.txt
```

```
U|2475485|618871|2017-07-08 15:07:12|CNCL|TLS|0|AAAAAAAAAAAADQS|8151|6.58|588|16906||||
```

Batch1 begins with data. Batch2 begins with `U` (the row was updated) and
`2475485` (the database sequence number) — two extra leading columns. This is why
the historical and incremental loaders cannot share a parser.

## 8. FINWIRE record types — V6

```
docker run --rm -v tpcdi-staging-sf5:/staging alpine:3 sh -c 'cat /staging/sf5/Batch1/FINWIRE????Q? | cut -c16-18 | sort | uniq -c'
```

Expect exactly three lines:

```
   2500 CMP
 198061 FIN
   4000 SEC
```

**A fourth line means a record failed to classify**, and the fixed-width parser
in Phase 3 would silently drop it.

## 9. Audit coverage — V7

```
docker run --rm -v tpcdi-staging-sf5:/staging alpine:3 sh -c 'cat /staging/sf5/*_audit.csv /staging/sf5/Batch*/*_audit.csv | cut -d, -f1 | sort -u | grep -v DataSet'
```

Expect fourteen names:

```
Batch DimAccount DimBroker DimCompany DimCustomer DimSecurity DimTrade
DimTradeHistory FactHoldings FactMarketHistory FactWatches Financial
Generator Prospect
```

`FactCashBalances` is deliberately absent. The audit query carries a comment
saying no audit data is generated for that table and excludes it by name, so its
"13 data sets" test still passes.

## 10. Optional — confirm the Java 8 ceiling yourself

```
sed 's|temurin:8-jre|temurin:21-jre|' docker/digen/Dockerfile > /tmp/D21 && docker build -q -f /tmp/D21 -t tpcdi-digen:jre21 tools/ && docker run --rm tpcdi-digen:jre21 -v; docker rmi -f tpcdi-digen:jre21
```

Expect `NoClassDefFoundError: org/apache/commons/cli/ParseException` thrown from
Eclipse's `JarRsrcLoader`, instead of a version string. DIGen is a jar-in-jar
whose classloading does not survive the Java 9 module system.

---

## Or use the two scripts

Generate (asks before destroying anything that already exists):

```
python benchmark_di.py --phase 2                 # scale factor 5
python benchmark_di.py --scale 3 --phase 2       # any scale factor >= 3
python benchmark_di.py --phase 2 --force         # replace without asking
python benchmark_di.py --phase 2 --keep          # leave existing data alone
```

Then verify:

```
python verify_di.py --phase 2                     # scale factor 5
python verify_di.py --scale 3 --phase 2   # any generated volume
```

The verifier runs steps 4 to 9, prints seventeen checks and exits non-zero if
any fails. It takes the scale factor from the volume name, so it works against
any generated volume rather than only SF=5.

Checked that it fails when it should: against a volume holding only an empty
report and a bare `Batch1` directory it exits 1; against a volume that does not
exist, 1; against a name it cannot parse, 2.

## Measured at two scale factors

| | SF=3 | SF=5 |
|---|---|---|
| Batch1 rows | 4,539,962 | 7,804,509 |
| Batch2 rows | 19,900 | 33,380 |
| Batch3 rows | 19,855 | 33,455 |
| On disk | 268.5 MiB | 465.3 MiB |
| Generation | 8 s | 29 s |
| FINWIRE files | 203 | 203 |
| FINWIRE CMP / SEC | 1,500 / 2,400 | 2,500 / 4,000 |

The FINWIRE file count is date-driven, not scale-driven, so it is 203 at every
scale factor. The record counts confirm the scaling rule inferred in Phase 2:
`CMP = 0.5 x SF x 1000` and `SEC = 0.8 x SF x 1000`, which is now asserted by
the verifier rather than assumed.
