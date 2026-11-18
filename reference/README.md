# Third-party reference material

**Nothing in here is authoritative and nothing in here should be copied without
checking it against the specification first.** It is kept because reading how
someone else resolved an ambiguous clause is useful, not because they were right.

## `qasim-mysql/`

From `QasimKhan5x/TPC-DI` — a TPC-DI v1.1.0 implementation in Python ETL against
**MySQL**, which is the closest published architecture to ours.

| File | What | How to treat it |
|---|---|---|
| `preparation.sql` | DDL for all 20 warehouse tables in MySQL dialect | A cross-check for our own Clause 3.2 reading, never a substitute for it. Types and nullability must be verified field by field. |
| `fk.sql` | Foreign keys | Compare against Table 4.3 (Clause 4.3.3). |
| `tpcdi_audit.mysql-adapted.sql` | The audit query with TPC's portability placeholders resolved for MySQL | A worked example of the substitution the TPC header asks for. Our own adaptation should be derived from the pristine file in `../../tools/appendices/`, then diffed against this. |

**Their own README states the implementation does not fully pass the audit** —
"Future Work: Ensure all tests pass in audit". So agreement with this repository
is weak evidence and disagreement is not evidence of our being wrong. Use it to
generate hypotheses, then settle them against Clause 3.2 and Clause 4.
