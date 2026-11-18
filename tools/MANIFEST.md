# TPC-DI tools: what is in the package, and what is not

Inventory of Phase 1, run 2026-11-18. The zip itself is not committed — TPC's
licence is accepted per person, so each group member downloads their own copy
from tpc.org. This file records what that copy must contain.

## Provenance

| | |
|---|---|
| Downloaded file | `6BA64B28-052A-414B-B62C-86968AC8F1C7-TPC-DI-Tool.zip` |
| Size | 6,676,639 bytes |
| SHA256 | `d9c10de5dafa781ddd4df72453f50af2f0c9c7ccfa0cbae99ed0be70a2ede22b` |
| Unpacks to | `Tools/`, 108 files, 8,587,504 bytes |
| Obtained from | https://www.tpc.org/TPC_Documents_Current_Versions/download_programs/tools-download-request5.asp?bm_type=TPC-DI&bm_vers=1.1.0&mode=CURRENT-ONLY |

The UUID prefix is TPC's per-download wrapper, not part of the package name. The
same pattern appears on the TPC-DS tools zip.

## What it contains

| Path | What |
|---|---|
| `Tools/DIGen.jar` | The data generator, v1.1.0. SHA256 `39ac04392f0e26dfe939a8836cc5e704fd26bb5acab7fe63ea6ea3c31fb3c2f4` |
| `Tools/DIGen_Build.zip` | Eclipse project and compiled classes for DIGen itself |
| `Tools/PDGF/pdgf.jar` | Parallel Data Generation Framework, R1343 TPC-DI 12/2014. SHA256 `a1d50984c5d3b6139180359784ebf3bde07b51a9b95d468c65b5810a834f76fd` |
| `Tools/PDGF/plugins/tpc-di.jar` | The TPC-DI plugin, with `tpc-di_src.jar` beside it |
| `Tools/PDGF/config/tpc-di-schema.xml` | The generation schema — every source table and its scaling rule |
| `Tools/PDGF/config/tpc-di-generation.xml` | Output formatting and file layout |
| `Tools/PDGF/config/tpc-di-Audit/` | 15 audit definitions: Account,Customer CustomerMgmt,DailyMarket Date,FINWIRE General,HR Industry,Prospect StatusType,TaxRate Time,TradeType WatchHistory |
| `Tools/PDGF/dicts/` | Name, address, TPC-E and pseudo-text dictionaries |
| `Tools/PDGF/extlib/` | javassist, xercesImpl, xml-apis, log4j 1.2.15, commons-net 3.3 |
| `Tools/README.txt`, `Tools/Note.txt`, `Tools/DIGen.asp` | Usage |

## What it does NOT contain

**No SQL. None at all.** Searching the archive for `.sql`, "compare", "expected",
"validation" or "visibility" returns nothing.

Specifically missing, all four referenced by the specification:

| Missing | Referenced at | Consequence |
|---|---|---|
| `tpcdi_audit.sql` | Appendix A, Clause 7.2.6.6 | No supplied pass/fail oracle |
| `tpcdi_validation.sql` | Appendix B, Clause 7.4 | No supplied phase-boundary capture — and this is what writes the PCR rows the metric is timed from |
| `tpcdi_visibility_1.sql`, `tpcdi_visibility_2.sql` | Appendix C, Clause 7.3 | No supplied visibility queries |
| Comparison tool + expected result data | Clause 8.2.1.1 | No row-level diff against TPC's own output |
| DDL for the twenty warehouse tables | — | We write the schema from Clause 3.2 |

The specification says the appendices "can be obtained from the download section
of the TPC web site" and that they are "stored in a single archive file". **That
archive is not published.** The TPC-DI entry on the current-specifications page
links exactly two artifacts, verified by reading the page HTML on 2026-11-18:

1. `pdf` → `TPC-DI_v1.1.0.pdf`
2. `Download TPC-DI_Tools_v1.1.0.zip` → the request form above

Consistent with this, tpc.org states there are no published TPC-DI results. The
appendices and comparison tool appear to have been prepared for auditors and
never released. Treat them as unavailable.

## Update: the missing SQL was recovered elsewhere

The four appendix files are now in `appendices/`, recovered from public
repositories and verified. See `appendices/PROVENANCE.md` for the evidence and
for what is still missing (the comparison tool and expected result data, which
were found nowhere). This does not change the finding above: TPC's package does
not contain them.

## DIGen usage, from `Tools/README.txt` — authoritative, not third-party

```
java -jar DIGen.jar <options>
  -sf <sf>    scale factor. Default 5. Range: 3 - 2147483647
  -o <dir>    output directory
  -jvm <opts> options for the generation JVM, e.g. -jvm "-Xms1g -Xmx2g"
  -v          version      -h  usage      -d  debug
```

Dependencies, verbatim: "Requires Java SE 7 **or above**", PDGF in the same
directory. This contradicts the widely repeated claim that DIGen will not run on
a JVM newer than 1.8. The claim is still plausible — `extlib/` ships javassist
and log4j 1.2.15 from 2014, both of which commonly break on Java 9+ — but it is
now a claim against the vendor's own README and must be settled by running it,
not by citation.

A `digen_report.txt` is written to the output directory, must not be modified,
and carries the per-batch row counts the performance metric needs.

## Scaling constants, from `tpc-di-schema.xml`

The generation schema is parameterised on `${SF}` (default 5000 in the file,
overridden by `-sf`). Observed constants:

| Constant | Value | Governs |
|---|---|---|
| `CScaling` | 0.005 | Customers |
| `AScaling` | 0.01 | Accounts |
| `CMScaling` | 5 | CustomerMgmt actions |
| `FWCMPScaling` | 0.5 | FINWIRE companies |
| `FWSECScaling` | 0.8 | FINWIRE securities |
| `THistScaling` | 130 | Historical trades |
| `WHScalingH` | 300.0 | Watch history |
| `HRBrokerScale` | 0.3 | Brokers |
| `CInsertPct`, `AInsertPct` | 0.7 | Insert vs update mix |
| `DateBeginDate` – `DateEndDate` | 1950-01-01 – 2021-01-01 | `DimDate` span |

**Do not derive row counts from these yet.** Several depend on date spans
expressed as further expressions, and a naive reading gives figures far smaller
than the "SF 10 ≈ 1 GB" claim circulating in third-party repositories. The two
cannot both be right. `digen_report.txt` settles it; generate first, then write
the numbers down.
