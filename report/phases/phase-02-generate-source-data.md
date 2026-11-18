# Phase 2 — Generate the source data at SF=5

**Status: BLOCKED** on a licence acceptance that is not mine to give (§6).
Everything up to that point works.

Plan step: `../01-steps-and-validation.md` Step 2.
Started 2026-11-18.

---

## 1. Goal

Produce the TPC-DI staging area at Scale Factor 5 — `Batch1`, `Batch2`, `Batch3`
plus `digen_report.txt` — entirely inside Docker, and settle four questions the
plan left open:

1. Does DIGen run on a modern JVM, or is the Java-8 ceiling real?
2. How large is SF=5 actually, given the generator's scaling constants
   contradict the third-party "SF 10 ≈ 1 GB" figure?
3. Do the generated `*_audit.csv` files carry the `DataSet`/`Attribute` pairs
   that the 130 audit tests read?
4. Do the Batch2/3 OLTP files carry the CDC columns that Batch1's do not?

## 2. Where the inputs come from

| Input | Location | Notes |
|---|---|---|
| `DIGen.jar`, `PDGF/` | `tools/Tools/` | From `TPC-DI_Tools_v1.1.0.zip`, not committed. SHA-256 in `../../tools/MANIFEST.md` |
| Usage | `tools/Tools/README.txt` | `-sf` default 5, range 3–2147483647 |
| Expected file inventory | Spec Clauses 2.2.2.1–2.2.2.20 | `../../spec/tpc-di_v1.1.0.pdf` |
| Expected directory layout | Spec Clause 2.3 | `Batch1`/`Batch2`/`Batch3` |
| Row counts for the metric | `digen_report.txt` (generated) | Clause 6.3 — must not be modified |

Output goes to `staging/sf5/`, which is gitignored: it is large and exactly
reproducible from a scale factor.

## 3. How it runs — Docker only

`docker/digen/Dockerfile` pins `eclipse-temurin:8-jre` and copies the tools in.
Built arm64-native on Apple Silicon, no emulation.

```
docker build -f docker/digen/Dockerfile -t tpcdi-digen:jre8 tools/
docker run --rm -v "$PWD/staging:/staging" tpcdi-digen:jre8 -o /staging/sf5 -sf 5
```

Nothing is installed on the host. The professor needs Docker and nothing else —
in particular, no legacy JDK.

## 4. The validations this phase must pass

Each is a separate check with a definite pass condition, run **inside the
container** so the result does not depend on the host.

| # | Check | Pass condition | Source of truth |
|---|---|---|---|
| V1 | Generator completes | Exit code 0, no stack trace | — |
| V2 | Batch directories | `Batch1`, `Batch2`, `Batch3` all present | Clause 2.3 |
| V3 | Report written | `digen_report.txt` exists, non-empty, names a row count per batch | Clause 6.3 |
| V4 | File inventory | Every file named in Clauses 2.2.2.1–2.2.2.20 present in the batch the spec assigns it to | Clause 2.2 |
| V5 | CDC columns | Batch2/3 OLTP files begin with `CDC_FLAG`/`CDC_DSN`; the Batch1 equivalents do not | Clause 1.3.2 |
| V6 | FINWIRE layout | Quarterly files exist; record type at column 16 is one of `CMP`/`SEC`/`FIN` for every record, none unclassified | Clause 2.2.2.8 |
| V7 | Audit coverage | The `DataSet`/`Attribute` pairs the 130 audit tests read all appear in the generated `*_audit.csv` | `tools/appendices/AppendixA/tpcdi_audit.sql` |
| V8 | Size recorded | Staging size on disk measured, per batch | Feeds Step 12 sizing |

V7 is the one that matters most. The audit query compares against the `Audit`
table using literal pairs such as `DataSet = 'DimBroker' and Attribute =
'HR_BROKERS'`. If the generator does not emit a pair that a test reads, that test
can never pass and we need to know now, not in Phase 6.

## 5. What happened

### Resolved: DIGen crashes on Linux over a filename case mismatch

First run died immediately:

```
java.io.IOException: Cannot run program "/opt/java/openjdk/bin/java"
  (in directory "/opt/tpcdi/pdgf"): error=2, No such file or directory
	at org.tpc.di.digen.DIGen.executePDGF(DIGen.java:189)
```

DIGen spawns its generation JVM with the working directory hard-coded as `pdgf`
in lower case. The package ships the directory as `PDGF`. macOS and Windows are
case-insensitive by default, so this never surfaces there; on Linux — which is
where any container runs — the child process cannot start.

Fixed with a symlink rather than a rename, so that any reference to the
upper-case name still resolves:

```dockerfile
RUN ln -s PDGF /opt/tpcdi/pdgf
```

Worth carrying into the report: the generator as shipped does not run on Linux
unmodified, and modifying DIGen is forbidden by Clause 6.2.2. A symlink outside
the jar is not a modification of DIGen, which is why it is the right fix.

### Resolved: the Java-8 ceiling, partly

DIGen 1.1.0 starts and reports its version correctly on
`openjdk 1.8.0_512 (Temurin, arm64)`. So Java 8 works. Whether a *newer* JVM
fails is still untested and is deferred until after a successful baseline run —
testing it first would confound a generator failure with a JVM failure.

Note the package disagrees with itself: `README.txt` says "Java SE 7 or above",
while `DIGen.asp` says "Requires Java 1.7".

### BLOCKED: PDGF requires an interactive licence acceptance

Second run reached PDGF, which printed the bankmark End-User License Agreement
and stopped at:

```
If you have read and agree to this terms of use, please type (uppercase!):  YES  and press [ENTER]
```

With no stdin attached the pipe closed and PDGF exited:
`DIGen failed with non-zero return code from PDGF.`

This is not a bug and not a test failure. It is a consent gate.

## 6. Why this is stopped rather than worked around

Answering `YES` is trivially automatable — `echo YES | docker run -i ...` — and
that is what every published pipeline does. It is being left to the user anyway,
because the prompt asks for a representation about a person: *"if you have read
and agree"*. That is not something to assert on someone else's behalf, and the
agreement binds the individual and, if accepted for an institution, the
institution too.

What the licence says, having read it:

- **§1.4 permits our use.** Use is granted for producing TPC Benchmark DI results
  "and for internal, academic, and research purposes only". University coursework
  sits squarely inside that.
- **§1.5** forbids sublicensing, leasing, renting or lending the rights — which is
  consistent with the decision already taken not to commit the tools package, and
  the reason each group member downloads their own copy.
- The acceptance clause states that accepting on behalf of an entity warrants
  authority to bind it.

Full text: `tools/Tools/PDGF/LICENSE.txt`, 24,627 bytes, bundled in the TPC
download.

**Needed from the user:** confirmation to answer `YES` on their behalf, or they
run the first generation themselves and the container picks up from there.

## 7. Not yet run

V1 through V8 are all still pending — the generator has not produced data. No
numbers from this phase should be quoted anywhere yet.
