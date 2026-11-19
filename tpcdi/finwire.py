"""Parsing the FINWIRE files.

Clause 2.2.2.8.  203 fixed-width files, one per quarter, carrying three record
layouts interleaved.  Kept separate from phase06_finwire.py because this is
pure text handling with no database in sight, which makes it testable on its
own.

Everything here is derived from Table 2.2.8 and written up, with the offsets
worked out, in report/file-to-table-mapping.md section 4.

Two things the layout does that a naive reader gets wrong:

  * The record type is three characters at position 16 (0-indexed 15:18) and
    nothing else distinguishes the layouts.  Read it wrong and a company
    parses as a security with no error at all.

  * SEC and FIN end with CoNameOrCIK, which is EITHER a 10-digit CIK OR a
    60-character company name.  Half the records are each way.  It is the one
    field whose width is not fixed, so it is taken as "the rest of the line".
"""

# (name, start, width) -- 0-indexed, from Table 2.2.8.
CMP_FIELDS = [
    ("PTS", 0, 15), ("CompanyName", 18, 60), ("CIK", 78, 10),
    ("Status", 88, 4), ("IndustryID", 92, 2), ("SPrating", 94, 4),
    ("FoundingDate", 98, 8), ("AddrLine1", 106, 80), ("AddrLine2", 186, 80),
    ("PostalCode", 266, 12), ("City", 278, 25), ("StateProvince", 303, 20),
    ("Country", 323, 24), ("CEOname", 347, 46), ("Description", 393, 150),
]

SEC_FIELDS = [
    ("PTS", 0, 15), ("Symbol", 18, 15), ("IssueType", 33, 6),
    ("Status", 39, 4), ("Name", 43, 70), ("ExID", 113, 6),
    ("ShOut", 119, 13), ("FirstTradeDate", 132, 8),
    ("FirstTradeExchg", 140, 8), ("Dividend", 148, 12),
]
SEC_TAIL = 160          # CoNameOrCIK runs from here to end of line

FIN_FIELDS = [
    ("PTS", 0, 15), ("Year", 18, 4), ("Quarter", 22, 1),
    ("QtrStartDate", 23, 8), ("PostingDate", 31, 8), ("Revenue", 39, 17),
    ("Earnings", 56, 17), ("EPS", 73, 12), ("DilutedEPS", 85, 12),
    ("Margin", 97, 12), ("Inventory", 109, 17), ("Assets", 126, 17),
    ("Liabilities", 143, 17), ("ShOut", 160, 13), ("DilutedShOut", 173, 13),
]
FIN_TAIL = 186

LAYOUTS = {"CMP": (CMP_FIELDS, None),
           "SEC": (SEC_FIELDS, SEC_TAIL),
           "FIN": (FIN_FIELDS, FIN_TAIL)}

COLUMNS = {
    "CMP": [n for n, _, _ in CMP_FIELDS],
    "SEC": [n for n, _, _ in SEC_FIELDS] + ["CoNameOrCIK"],
    "FIN": [n for n, _, _ in FIN_FIELDS] + ["CoNameOrCIK"],
}


class ParseError(Exception):
    pass


def record_type(line):
    """The three characters at column 16 -- the only discriminator there is."""
    return line[15:18]


def pts_to_datetime(pts):
    """YYYYMMDD-HHMMSS -> 'YYYY-MM-DD HH:MM:SS', which MariaDB accepts."""
    if len(pts) != 15 or pts[8] != "-":
        raise ParseError(f"malformed PTS {pts!r}")
    return (f"{pts[0:4]}-{pts[4:6]}-{pts[6:8]} "
            f"{pts[9:11]}:{pts[11:13]}:{pts[13:15]}")


def _cell(value):
    r"""One TSV cell.

    Fields are space-padded, so everything is stripped.  Clause 4.5.3.2: "In
    cases where the input data is all blanks, a NULL value is used in the
    target" -- \N is how LOAD DATA spells NULL.  Tabs and backslashes would
    corrupt the TSV, so they are rejected rather than silently mangled; the
    generator does not produce them.
    """
    v = value.strip()
    if not v:
        return "\\N"
    if "\t" in v or "\\" in v or "\n" in v:
        raise ParseError(f"field contains a TSV metacharacter: {v!r}")
    return v


def parse_line(line):
    """Returns (record_type, [cells]) or raises ParseError.

    The line must already have had its line ending removed -- every file is
    CRLF, so a stray \\r would end up inside the last field.
    """
    kind = record_type(line)
    if kind not in LAYOUTS:
        raise ParseError(f"unknown record type {kind!r}")
    fields, tail = LAYOUTS[kind]

    needed = tail if tail is not None else fields[-1][1] + fields[-1][2]
    if len(line) < needed:
        raise ParseError(f"{kind} record is {len(line)} chars, needs {needed}")

    cells = []
    for name, start, width in fields:
        raw = line[start:start + width]
        cells.append(pts_to_datetime(raw.strip()) if name == "PTS" else _cell(raw))
    if tail is not None:
        cells.append(_cell(line[tail:]))
    return kind, cells


def parse_stream(lines):
    """Parse an iterable of lines into {record_type: [row, ...]}.

    Order is not preserved deliberately: the SCD-2 transform sorts by PTS in
    SQL rather than trusting the order records arrive in.  See
    report/file-to-table-mapping.md section 5.5.
    """
    out = {k: [] for k in LAYOUTS}
    counts = {k: 0 for k in LAYOUTS}
    for n, line in enumerate(lines, 1):
        line = line.rstrip("\r\n")
        if not line:
            continue
        try:
            kind, cells = parse_line(line)
        except ParseError as exc:
            raise ParseError(f"line {n}: {exc}") from None
        out[kind].append("\t".join(cells))
        counts[kind] += 1
    return out, counts
