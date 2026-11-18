# The TPC-DI appendices: where these came from, and why we trust them

The specification refers to four SQL files as Appendices A, B and C, and says
they "can be obtained from the download section of the TPC web site" as "a single
archive file with directories containing the supporting files for each Appendix".

**That archive is not published.** TPC's current-specifications page links exactly
two TPC-DI artifacts — the specification PDF and `TPC-DI_Tools_v1.1.0.zip` — and
the tools zip contains the generator and no SQL at all (see `../MANIFEST.md`).

The files here were recovered from public repositories. That makes their
provenance a question we have to answer rather than assume, so here is the
evidence.

## Where they came from

Primary copy, used verbatim:

- `shannon-barrow/databricks-tpc-di`, at
  `src/incremental_batches/audit_validation/Appendix_v1.1.0/AppendixA|B|C/`

## Why we believe they are unmodified TPC originals

1. **The directory layout is the one the specification describes.** Both repos
   that carry them use `Appendix_v1.1.0/AppendixA/`, `AppendixB/`, `AppendixC/` —
   "directories containing the supporting files for each Appendix", exactly as
   written. That is the shape of the unpacked official archive, not something a
   reimplementer would invent.

2. **Two unrelated repositories carry byte-identical copies.** Verified by
   SHA-256 on 2026-11-18:

   | File | SHA-256 (first 16) | Bytes |
   |---|---|---|
   | `tpcdi_audit.sql` | `32eb0f55a08b8cf5` | 60,761 |
   | `tpcdi_validation.sql` | `9ee9ef8144c7f53b` | 5,944 |
   | `tpcdi_visibility_1.sql` | `a95eb53868fa42cf` | 5,164 |
   | `tpcdi_visibility_2.sql` | `a22645d5c9d2095d` | 3,362 |

   The second copy is `hitachisolutionsamerica/dataestate-benchmarks` at
   `tpc_di/databricks/src/audit_validation/Appendix_v1.1.0/`. Both sit in the
   Databricks ecosystem, so this is corroboration rather than full independence.

3. **The TPC headers are intact**, including the version banner and the
   "Portability Substitutions" block listing the DB2, Oracle and SQL Server forms
   of `FROM DUMMY_TABLE` and `||`.

4. **The portability placeholders are still unsubstituted.** The literal text
   `FROM DUMMY TABLE` appears throughout, unresolved. Nobody who had adapted the
   file to run on their own system would leave those in; they are what the
   sponsor is expected to replace. This is the strongest single signal that the
   file is as shipped.

5. **A third, independently adapted copy agrees on content.**
   `QasimKhan5x/TPC-DI` carries the same four files with the headers stripped, the
   placeholders resolved and `CURRENT_TIMESTAMP()` rewritten for MySQL. Same test
   names, same structure, same thresholds — a derivative of the same original,
   adapted exactly as the header instructs. Kept at
   `../../reference/qasim-mysql/` as a worked example of the substitution.

6. **The content cross-checks against the specification.** Seven assertions in
   `tpcdi_audit.sql` were checked against clauses read independently from the PDF,
   and all seven agree:

   | Audit query asserts | Specification |
   |---|---|
   | `Audit` covers 3 batches | Batch1/2/3, Clause 2.3 |
   | 4 PCR records in `DImessages` | Clauses 7.2.3.5, 7.2.4.2, 7.2.5.3 |
   | `DImessages` batches 0–3 | Initialization is batch 0 |
   | All DW tables empty before Batch1 | Clause 7.2.3 |
   | 13 audit data sets | Clause 2.2.2.20 |
   | 24 validation messages per batch | Clause 7.4 |
   | 17 validation message sources | Clause 7.4 |

## What this gives us

`tpcdi_audit.sql` contains **130 tests**, distributed:

| Subject | Tests | | Subject | Tests |
|---|---|---|---|---|
| DimCustomer | 23 | | FactWatches | 6 |
| DimAccount | 15 | | FactMarketHistory | 6 |
| DimTrade | 14 | | DimBroker | 6 |
| DimSecurity | 13 | | Prospect | 5 |
| DimCompany | 13 | | DImessages | 5 |
| FactHoldings | 8 | | FactCashBalances | 4 |
| Financial | 6 | | Audit / Batch / Data | 6 |

They check row counts and sums against the `Audit` table, SCD-2 chain integrity
(`Overlap`, `End of Time`, `consolidation`, `IsCurrent`, `EffectiveDate`,
`EndDate`), key uniqueness, and the `DImessages` contents.

## What is still missing

**The comparison tool and expected result data (Clause 8.2.1.1) were not found
anywhere.** Searched the three repositories above for `expected`, `compar`,
`qualification`, `AppendixD`, `AppendixE` — nothing. They appear to be auditor
material that was never released. So there is still no row-level diff against
TPC's own output; the audit query is the oracle.

## Caveat to carry into the report

These are recovered files, not files TPC handed us. The evidence above is strong
but it is evidence, not a receipt. The report should say where they came from and
what was done to check them, rather than implying the tools package supplied them.

## Licence

TPC material, reproduced with the copyright headers intact, as the specification's
legal notice permits for the document and its appendices. Not our work and not
ours to relicense.
