#!/usr/bin/env python3
"""Adapt TPC's appendix SQL to MariaDB.

Reads the pristine files in tools/appendices/ and writes MariaDB versions to
sql/di/adapted/.  Every change is one of the substitutions listed below and
nothing else -- run with --diff to see exactly what changed.

The TPC header in each file names two substitutions a sponsor is expected to
make (FROM DUMMY_TABLE and ||).  Three more are forced by MariaDB's dialect.
Clause 3.4.11 covers this: where a system cannot run the query as supplied,
the sponsor follows the SQL compliance rules.

  1. FROM DUMMY TABLE        removed.  MariaDB allows SELECT with no FROM.
                             (The || case needs no edit: PIPES_AS_CONCAT.)
  2. cast(x as bigint)       -> cast(x as signed).  MariaDB's cast targets are
                             SIGNED/UNSIGNED; BIGINT is not one.
  3. unaliased derived table -> given an alias.  MariaDB requires one.
  4. FULL JOIN               emulated as LEFT JOIN UNION RIGHT JOIN.  MariaDB
                             has no FULL OUTER JOIN.  See below.

On the FULL JOIN
----------------
Two places use it, both the same shape: Audit FULL JOIN DImessages, with the
ON clause referencing only DImessages, followed by two INNER joins that
require both sides to be non-null.

Those inner joins discard every NULL-extended row a full join would add, so a
plain INNER JOIN would give the same answer here.  That is an argument, not a
measurement, and quietly weakening an audit is worth nothing -- so the default
output emulates the full join faithfully and `--inner` writes the simplified
form beside it.  Phase 6 compares the two on loaded data; if they agree, the
argument is evidence.
"""
import argparse, pathlib, re, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "tools" / "appendices"
OUT = ROOT / "sql" / "di" / "adapted"

FILES = ["AppendixA/tpcdi_audit.sql", "AppendixB/tpcdi_validation.sql",
         "AppendixC/tpcdi_visibility_1.sql", "AppendixC/tpcdi_visibility_2.sql"]

# The two full-join blocks, matched structurally rather than by line number.
FULLJOIN = re.compile(
    r"(?P<sel>\tselect distinct\(a\.BatchID\), m\.MessageSource,[^\n]*\n)"
    r"\tfrom Audit a *\n"
    r"\tfull join DImessages m on (?P<on>[^\n]*)\n"
    r"(?P<m1>\tjoin DImessages m1 on [^\n]*\n)"
    r"(?P<m2>\tjoin DImessages m2 on [^\n]*\n)"
    r"(?P<where>\twhere a\.BatchID in \(1, 2, 3\)\n)")


def emulate(m):
    """A FULL JOIN B ON c  ==  (A LEFT JOIN B ON c) UNION (A RIGHT JOIN B ON c)

    The downstream joins refer to the DImessages side as `m`, so after folding
    the full join into a derived table those references are rewritten to the
    derived table's alias."""
    on = m.group("on")
    fix = lambda s: s.replace("m.MessageSource", "a.MessageSource")
    return (
        fix(m.group("sel")) +
        "\tfrom ( select a.BatchID as BatchID, m.MessageSource as MessageSource\n"
        f"\t       from Audit a left join DImessages m on {on}\n"
        "\t       union\n"
        "\t       select a.BatchID as BatchID, m.MessageSource as MessageSource\n"
        f"\t       from Audit a right join DImessages m on {on}\n"
        "\t     ) a\n" +
        fix(m.group("m1")) + fix(m.group("m2")) + m.group("where"))


def inner(m):
    """The simplification: plain INNER JOIN, kept only for comparison."""
    return (m.group("sel") + "\tfrom Audit a \n"
            f"\tjoin DImessages m on {m.group('on')}\n"
            + m.group("m1") + m.group("m2") + m.group("where"))


def adapt(text, fulljoin):
    text = re.sub(r"[ \t]*FROM DUMMY TABLE", "", text)
    text = re.sub(r"as bigint\)", "as signed)", text, flags=re.I)
    text = re.sub(r"^\) *$", ") dt", text, flags=re.M)      # derived-table alias
    text, n = FULLJOIN.subn(fulljoin, text)
    return text, n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--inner", action="store_true",
                    help="also write the INNER JOIN variant of the audit query")
    ap.add_argument("--diff", action="store_true", help="print what changed")
    ap.add_argument("--out", help="write to this directory instead of sql/di/adapted "
                                  "(so a verifier can regenerate without overwriting)")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()

    global OUT
    if a.out:
        OUT = pathlib.Path(a.out)
    OUT.mkdir(parents=True, exist_ok=True)
    total = 0
    for rel in FILES:
        src = SRC / rel
        if not src.exists():
            sys.exit(f"missing {src}")
        text, n = adapt(src.read_text(), emulate)
        dst = OUT / pathlib.Path(rel).name
        dst.write_text(text)
        total += n
        if not a.quiet:
            print(f"  {dst}"
                  + (f"   ({n} full join{'s' if n != 1 else ''} emulated)" if n else ""))
        if a.inner and n:
            text2, _ = adapt(src.read_text(), inner)
            alt = OUT / (dst.stem + ".innerjoin.sql")
            alt.write_text(text2)
            if not a.quiet:
                print(f"  {alt}   (simplified, for comparison)")
        if a.diff:
            import difflib
            for line in difflib.unified_diff(
                    src.read_text().splitlines(), text.splitlines(),
                    "pristine", "adapted", lineterm="", n=0):
                if line[:1] in "+-" and line[:3] not in ("---", "+++") and line.strip(" +-"):
                    print("      " + line)
    if total != 2:
        sys.exit(f"expected 2 full joins, rewrote {total} -- the source changed shape")


if __name__ == "__main__":
    main()
