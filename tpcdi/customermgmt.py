"""Parsing CustomerMgmt.xml.

Clause 2.2.2.5. A stream of Action elements -- NEW, ADDACCT, UPDACCT,
UPDCUST, CLOSEACCT, INACT -- that has to be replayed in document order to
derive the historical state of DimCustomer and DimAccount.

Kept separate from phase07_customer.py because this is pure text handling
with no database in sight, which makes it testable on its own:

    python -m tpcdi.customermgmt --self-test

THE ONE THING THIS FILE EXISTS TO GET RIGHT

Clause 4.5.4.2 draws a distinction the XML makes easy to lose:

    "only the updated fields are supplied a new value, i.e. all missing
     values retain their current values. Fields with empty values
     (e.g. <Element />) should be processed as NULL values."

So within one UPDCUST action:

    <Address>
      <C_ADLINE1>3713 Jarvis Avenue</C_ADLINE1>   a new value
      <C_ZIPCODE>E7O 1P6</C_ZIPCODE>              a new value
    </Address>                                    C_ADLINE2, C_STATE_PROV,
                                                  C_CTRY absent -> UNCHANGED
    <C_PHONE_1>
      <C_CTRY_CODE/>                              present, empty -> NULL
    </C_PHONE_1>

`elem.text or None` collapses those two cases and silently wipes fields on
every update. They are kept apart here and handed to SQL as:

    \\N   absent          -> carry the current value forward
    ''   <Element/>      -> set NULL

which is exactly what the forward-fill in the transform expects.

Memory is independent of the scale factor: iterparse plus clear(), and rows
go out in blocks of CHUNK_ROWS.
"""
import argparse
import io
import sys
import xml.etree.ElementTree as ET

CHUNK_ROWS = 1000

ABSENT = "\\N"      # LOAD DATA reads this as SQL NULL
EMPTY = ""          # a present but empty element

ACTION_TAG = "{http://www.tpc.org/tpc-di}Action"

# Customer attributes, Clause 2.2.2.5.
CUSTOMER_ATTRS = ["C_ID", "C_TAX_ID", "C_GNDR", "C_TIER", "C_DOB"]

# Child elements, as paths relative to Customer.
CUSTOMER_PATHS = [
    ("C_L_NAME", "Name/C_L_NAME"),
    ("C_F_NAME", "Name/C_F_NAME"),
    ("C_M_NAME", "Name/C_M_NAME"),
    ("C_ADLINE1", "Address/C_ADLINE1"),
    ("C_ADLINE2", "Address/C_ADLINE2"),
    ("C_ZIPCODE", "Address/C_ZIPCODE"),
    ("C_CITY", "Address/C_CITY"),
    ("C_STATE_PROV", "Address/C_STATE_PROV"),
    ("C_CTRY", "Address/C_CTRY"),
    ("C_PRIM_EMAIL", "ContactInfo/C_PRIM_EMAIL"),
    ("C_ALT_EMAIL", "ContactInfo/C_ALT_EMAIL"),
]
# Three phones, four parts each.  Assembled into Phone1..3 by the transform,
# not here: Clause 4.5.4.3 gives four conditional cases and that is a
# transformation rule, not a parsing one.
for _n in (1, 2, 3):
    for _part in ("C_CTRY_CODE", "C_AREA_CODE", "C_LOCAL", "C_EXT"):
        CUSTOMER_PATHS.append((f"C_PHONE_{_n}_{_part}",
                               f"ContactInfo/C_PHONE_{_n}/{_part}"))
CUSTOMER_PATHS += [("C_LCL_TX_ID", "TaxInfo/C_LCL_TX_ID"),
                   ("C_NAT_TX_ID", "TaxInfo/C_NAT_TX_ID")]

CUSTOMER_COLUMNS = (["seq", "ActionType", "ActionTS"] + CUSTOMER_ATTRS
                    + [name for name, _ in CUSTOMER_PATHS])

ACCOUNT_ATTRS = ["CA_ID", "CA_TAX_ST"]
ACCOUNT_PATHS = [("CA_B_ID", "CA_B_ID"), ("CA_NAME", "CA_NAME")]
ACCOUNT_COLUMNS = (["seq", "ActionType", "ActionTS", "C_ID"] + ACCOUNT_ATTRS
                   + [name for name, _ in ACCOUNT_PATHS])

ACTION_TYPES = {"NEW", "ADDACCT", "UPDACCT", "UPDCUST", "CLOSEACCT", "INACT"}


class ParseError(Exception):
    pass


def _attr(node, name):
    """An attribute: absent, or present and possibly empty."""
    if node is None or name not in node.attrib:
        return ABSENT
    return _clean(node.attrib[name])


def _path(node, path):
    """An element reached by path: absent, or present and possibly empty."""
    if node is None:
        return ABSENT
    found = node.find(path)
    if found is None:
        return ABSENT
    return _clean(found.text or "")


def _clean(value):
    r"""Strip, and reject anything that would corrupt the TSV.

    The generator does not produce tabs, newlines or backslashes in these
    fields; if one appears it is better to stop than to write a row that
    loads into the wrong columns.
    """
    v = value.strip()
    if "\t" in v or "\n" in v or "\\" in v:
        raise ParseError(f"field contains a TSV metacharacter: {v!r}")
    return v


class _Chunked:
    """Collects rows and writes them to a sink in blocks of `chunk`.

    Same reasoning as tpcdi/finwire.py: doing this explicitly keeps the block
    size a property of this code rather than of Python's file buffering.
    """

    def __init__(self, sink, chunk=CHUNK_ROWS):
        self.sink, self.chunk, self.rows, self.n = sink, chunk, [], 0

    def add(self, cells):
        self.rows.append("\t".join(cells))
        self.n += 1
        if len(self.rows) >= self.chunk:
            self.flush()

    def flush(self):
        if self.rows:
            self.sink.write("\n".join(self.rows) + "\n")
            self.rows.clear()


def parse_to(source, customer_sink, account_sink, chunk=CHUNK_ROWS):
    """Parse the document, writing customer and account rows to two sinks.

    One row per Action goes to `customer_sink`, carrying every customer field
    as absent or present. One row per <Account> goes to `account_sink`; an
    action may carry none (INACT, UPDCUST) or several (NEW, ADDACCT).

    Returns (counts-by-action-type, customer-rows, account-rows).
    """
    customers = _Chunked(customer_sink, chunk)
    accounts = _Chunked(account_sink, chunk)
    counts = {t: 0 for t in ACTION_TYPES}
    seq = 0

    for event, elem in ET.iterparse(source, events=("end",)):
        if elem.tag != ACTION_TAG:
            continue
        seq += 1
        action_type = elem.get("ActionType", "")
        action_ts = elem.get("ActionTS", "")
        if action_type not in ACTION_TYPES:
            raise ParseError(f"action {seq}: unknown ActionType {action_type!r}")
        if not action_ts:
            raise ParseError(f"action {seq}: missing ActionTS")
        counts[action_type] += 1

        customer = elem.find("Customer")
        if customer is None:
            raise ParseError(f"action {seq}: no Customer element")

        row = [str(seq), action_type, action_ts.replace("T", " ")]
        row += [_attr(customer, a) for a in CUSTOMER_ATTRS]
        row += [_path(customer, p) for _, p in CUSTOMER_PATHS]
        customers.add(row)

        for account in customer.findall("Account"):
            arow = [str(seq), action_type, action_ts.replace("T", " "),
                    _attr(customer, "C_ID")]
            arow += [_attr(account, a) for a in ACCOUNT_ATTRS]
            arow += [_path(account, p) for _, p in ACCOUNT_PATHS]
            accounts.add(arow)

        # iterparse keeps the whole tree otherwise, which would defeat the
        # point of streaming it.
        elem.clear()

    customers.flush()
    accounts.flush()
    return counts, customers.n, accounts.n


# ------------------------------------------------------------- self-test ---

_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<TPCDI:Actions xmlns:TPCDI="http://www.tpc.org/tpc-di">
 <TPCDI:Action ActionType="NEW" ActionTS="2007-07-07T04:28:56">
  <Customer C_ID="7" C_TAX_ID="111" C_GNDR="F" C_TIER="3" C_DOB="1940-12-02">
   <Name><C_L_NAME>Joannis</C_L_NAME><C_F_NAME>Adara</C_F_NAME><C_M_NAME/></Name>
   <Address><C_ADLINE1>4779 Weller Way</C_ADLINE1><C_ADLINE2/>
    <C_ZIPCODE>92624</C_ZIPCODE><C_CITY>Columbus</C_CITY>
    <C_STATE_PROV>Ontario</C_STATE_PROV><C_CTRY>Canada</C_CTRY></Address>
   <ContactInfo><C_PRIM_EMAIL>a@b.com</C_PRIM_EMAIL><C_ALT_EMAIL/>
    <C_PHONE_1><C_CTRY_CODE>1</C_CTRY_CODE><C_AREA_CODE>872</C_AREA_CODE>
     <C_LOCAL>523-8928</C_LOCAL><C_EXT/></C_PHONE_1></ContactInfo>
   <TaxInfo><C_LCL_TX_ID>CA3</C_LCL_TX_ID><C_NAT_TX_ID>YT3</C_NAT_TX_ID></TaxInfo>
   <Account CA_ID="70" CA_TAX_ST="1"><CA_B_ID>15746</CA_B_ID>
    <CA_NAME>Acct</CA_NAME></Account>
  </Customer>
 </TPCDI:Action>
 <TPCDI:Action ActionType="UPDCUST" ActionTS="2007-07-17T07:33:48">
  <Customer C_ID="7" C_TIER="2">
   <Address><C_ADLINE1>3713 Jarvis Avenue</C_ADLINE1>
    <C_CITY>Winston-Salem</C_CITY></Address>
   <ContactInfo><C_PHONE_1><C_CTRY_CODE/><C_AREA_CODE>872</C_AREA_CODE>
     <C_LOCAL>880-7964</C_LOCAL><C_EXT/></C_PHONE_1></ContactInfo>
  </Customer>
 </TPCDI:Action>
 <TPCDI:Action ActionType="CLOSEACCT" ActionTS="2007-07-15T18:28:19">
  <Customer C_ID="7"><Account CA_ID="70"/></Customer>
 </TPCDI:Action>
</TPCDI:Actions>
"""


def _self_test():
    """The distinction this file exists for, asserted rather than assumed."""
    cust, acct = io.StringIO(), io.StringIO()
    counts, nc, na = parse_to(io.BytesIO(_SAMPLE.encode()), cust, acct)
    rows = [r.split("\t") for r in cust.getvalue().strip().split("\n")]
    arows = [r.split("\t") for r in acct.getvalue().strip().split("\n")]
    col = {c: i for i, c in enumerate(CUSTOMER_COLUMNS)}
    acol = {c: i for i, c in enumerate(ACCOUNT_COLUMNS)}
    new, upd = rows[0], rows[1]
    ok = True

    def check(label, got, want):
        nonlocal ok
        good = got == want
        ok = ok and good
        print(f"  {'PASS' if good else 'FAIL'}  {label:<52} {got!r}")
        if not good:
            print(f"        expected {want!r}")

    print("counts:", {k: v for k, v in counts.items() if v})
    check("NEW: a value is itself", new[col["C_ADLINE1"]], "4779 Weller Way")
    check("NEW: <C_M_NAME/> is empty, not absent", new[col["C_M_NAME"]], EMPTY)
    check("NEW: <C_ALT_EMAIL/> is empty", new[col["C_ALT_EMAIL"]], EMPTY)
    check("NEW: missing phone 2 is absent", new[col["C_PHONE_2_C_LOCAL"]], ABSENT)
    check("NEW: ActionTS has no T", new[col["ActionTS"]], "2007-07-07 04:28:56")

    # The whole point: inside one UPDCUST, three lines apart.
    check("UPDCUST: supplied field is the new value",
          upd[col["C_ADLINE1"]], "3713 Jarvis Avenue")
    check("UPDCUST: absent C_ADLINE2 must CARRY", upd[col["C_ADLINE2"]], ABSENT)
    check("UPDCUST: absent C_STATE_PROV must CARRY",
          upd[col["C_STATE_PROV"]], ABSENT)
    check("UPDCUST: absent C_CTRY must CARRY", upd[col["C_CTRY"]], ABSENT)
    check("UPDCUST: <C_CTRY_CODE/> must become NULL",
          upd[col["C_PHONE_1_C_CTRY_CODE"]], EMPTY)
    check("UPDCUST: absent attribute C_TAX_ID must CARRY",
          upd[col["C_TAX_ID"]], ABSENT)
    check("UPDCUST: supplied attribute C_TIER is the new value",
          upd[col["C_TIER"]], "2")

    check("accounts: one row per <Account>", str(na), "2")
    check("CLOSEACCT: self-closing Account gives CA_ID",
          arows[1][acol["CA_ID"]], "70")
    check("CLOSEACCT: its CA_NAME is absent",
          arows[1][acol["CA_NAME"]], ABSENT)
    check("customer rows: one per action", str(nc), "3")

    print("\n" + ("all checks passed" if ok else "SELF-TEST FAILED"))
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--self-test", action="store_true",
                    help="parse a built-in sample and assert the hard cases")
    ap.add_argument("path", nargs="?", help="a CustomerMgmt.xml to summarise")
    a = ap.parse_args()
    if a.self_test or not a.path:
        return _self_test()
    with open(a.path, "rb") as fh:
        counts, nc, na = parse_to(fh, io.StringIO(), io.StringIO())
    print(f"{nc} actions, {na} accounts")
    for k, v in sorted(counts.items()):
        print(f"  {k:<10} {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
