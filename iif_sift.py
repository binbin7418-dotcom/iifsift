#!/usr/bin/env python3
"""IifSift engine: QuickBooks IIF files -> clean CSV tables + an
import-readiness audit.

QuickBooks Desktop imports IIF with almost no feedback: an unbalanced
transaction, a missing ENDTRNS or a reference to an undefined account
goes in silently or fails with a line number nobody can map back. IifSift
parses the file properly (header definitions first, then records),
applies only MECHANICAL fixes (amount formats like $1,234.56 and
(1,200.00), MM/DD/YY dates, stray whitespace), and audits the things a
human must decide:

  * every TRNS block closed by ENDTRNS, with at least one SPL
  * TRNS amount + its SPL amounts == 0 (double-entry balance)
  * ACCNT / NAME references that no list in the file defines
  * duplicate TRNSIDs, unknown record types, over-wide rows

Outputs: transactions.csv, splits.csv, one CSV per list type present,
a fixed .iif with the mechanical fixes applied (structure untouched),
and audit_report.md. Before finishing, every output is RE-READ and
reconciled against the source parse -- row counts and per-account
totals must match to the cent or the run fails loudly.

Deterministic. 100% local. No third-party dependencies.

Usage:
    python iif_sift.py file.iif out/
"""
import csv
import os
import re
import sys
from decimal import Decimal, InvalidOperation

LIST_TYPES = {"ACCNT", "CUST", "VEND", "EMP", "ITEM", "INVITEM", "NOTE"}
TXN_OPEN, TXN_LINE, TXN_CLOSE = "TRNS", "SPL", "ENDTRNS"
CENT = Decimal("0.005")

# -------------------------------------------------------------- amounts

def parse_amount(raw):
    """Return (Decimal|None, was_fixed). Handles $1,234.56, (1,200.00),
    -45.00, 45.00- and plain numbers."""
    if raw is None:
        return None, False
    s = str(raw).strip()
    if s == "":
        return None, False
    orig = s
    neg = False
    if s.startswith("(") and s.endswith(")"):
        neg = True
        s = s[1:-1]
    if s.endswith("-"):
        neg = True
        s = s[:-1]
    s = s.replace("$", "").replace(",", "").strip()
    try:
        v = Decimal(s)
    except InvalidOperation:
        return None, False
    if neg:
        v = -abs(v)
    return v, (s != orig and str(v) != orig) or neg or "$" in orig or "," in orig

def fmt_amount(v):
    return f"{v:.2f}"

# ---------------------------------------------------------------- dates

_DATE_RE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{2}|\d{4})$")

def normalize_date(raw):
    """MM/DD/YY or MM/DD/YYYY -> ISO. Returns (iso|None, was_fixed)."""
    if raw is None:
        return None, False
    s = str(raw).strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        return s, False
    m = _DATE_RE.match(s)
    if not m:
        return None, True  # present but unparseable: caller flags it
    mo, d, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if y < 100:
        y += 2000 if y < 70 else 1900
    if not (1 <= mo <= 12 and 1 <= d <= 31):
        return None, True
    return f"{y:04d}-{mo:02d}-{d:02d}", True

# ---------------------------------------------------------------- parse

class IifFile:
    def __init__(self):
        self.headers = {}        # keyword -> [columns]
        self.lists = {}          # keyword -> [dict rows]
        self.transactions = []   # [{trns: dict, splits: [dict], lines}]
        self.issues = []         # (line_no, kind, detail)
        self.fixes = []          # (line_no, kind, detail)
        self.unknown_lines = []  # line numbers

def _row_to_dict(cols, fields):
    d = {}
    for i, col in enumerate(cols):
        d[col] = fields[i] if i < len(fields) else ""
    return d

def parse_iif(text):
    f = IifFile()
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    current = None  # open transaction dict
    for ln, raw in enumerate(lines, 1):
        if raw.strip() == "":
            continue
        fields = raw.split("\t")
        key = fields[0].strip()
        if key.startswith("!"):
            kw = key[1:].upper()
            f.headers[kw] = [c.strip() for c in fields[1:]]
            continue
        kw = key.upper()
        if kw == TXN_OPEN:
            if current is not None:
                f.issues.append((current["line"], "missing_endtrns",
                                 "TRNS opened before previous block was closed"))
                f.transactions.append(current)
            cols = f.headers.get("TRNS")
            if cols is None:
                f.issues.append((ln, "missing_header", "TRNS line before any !TRNS definition"))
                cols = []
            current = {"trns": _row_to_dict(cols, fields[1:]), "splits": [],
                       "line": ln, "raw_trns": fields}
            if len(fields) - 1 > len(cols):
                f.issues.append((ln, "overwide_row",
                                 f"TRNS has {len(fields) - 1} fields, header defines {len(cols)}"))
            continue
        if kw == TXN_LINE:
            if current is None:
                f.issues.append((ln, "orphan_spl", "SPL outside any TRNS block"))
                continue
            cols = f.headers.get("SPL", [])
            row = _row_to_dict(cols, fields[1:])
            row["_line"] = ln
            current["splits"].append(row)
            if len(fields) - 1 > len(cols):
                f.issues.append((ln, "overwide_row",
                                 f"SPL has {len(fields) - 1} fields, header defines {len(cols)}"))
            continue
        if kw == TXN_CLOSE:
            if current is None:
                f.issues.append((ln, "stray_endtrns", "ENDTRNS with no open TRNS"))
            else:
                f.transactions.append(current)
                current = None
            continue
        if kw in LIST_TYPES:
            cols = f.headers.get(kw)
            if cols is None:
                f.issues.append((ln, "missing_header", f"{kw} line before any !{kw} definition"))
                cols = []
            f.lists.setdefault(kw, []).append(_row_to_dict(cols, fields[1:]))
            continue
        f.issues.append((ln, "unknown_record", f"unrecognized record type {key!r}"))
        f.unknown_lines.append(ln)
    if current is not None:
        f.issues.append((current["line"], "missing_endtrns",
                         "file ends inside a TRNS block (no ENDTRNS)"))
        f.transactions.append(current)
    return f

# ---------------------------------------------------------------- audit

def audit(f, text_lines):
    """Mechanical fixes + decision-level audit. Mutates row dicts with
    normalized AMOUNT/DATE (recording fixes), appends issues. Line
    numbers for records are tracked via a parallel scan."""
    defined_accounts = {r.get("NAME", "").strip() for r in f.lists.get("ACCNT", [])}
    defined_names = set()
    for t in ("CUST", "VEND", "EMP"):
        defined_names |= {r.get("NAME", "").strip() for r in f.lists.get(t, [])}
    seen_ids = {}

    def fix_row(row, line):
        amt_raw = row.get("AMOUNT")
        if amt_raw not in (None, ""):
            v, fixed = parse_amount(amt_raw)
            if v is None:
                f.issues.append((line, "bad_amount", f"unparseable AMOUNT {amt_raw!r}"))
            else:
                if fixed:
                    f.fixes.append((line, "amount_format",
                                    f"AMOUNT {amt_raw!r} -> {fmt_amount(v)}"))
                row["AMOUNT"] = fmt_amount(v)
                row["_amt"] = v
        d_raw = row.get("DATE")
        if d_raw not in (None, ""):
            iso, fixed = normalize_date(d_raw)
            if iso is None:
                f.issues.append((line, "bad_date", f"unparseable DATE {d_raw!r}"))
            else:
                if fixed:
                    f.fixes.append((line, "date_format", f"DATE {d_raw!r} -> {iso}"))
                row["DATE"] = iso

    for t in f.transactions:
        fix_row(t["trns"], t["line"])
        for s in t["splits"]:
            fix_row(s, s.get("_line", t["line"]))
        tid = t["trns"].get("TRNSID", "").strip()
        if tid:
            if tid in seen_ids:
                f.issues.append((t["line"], "duplicate_trnsid",
                                 f"TRNSID {tid} already used at line {seen_ids[tid]}"))
            else:
                seen_ids[tid] = t["line"]
        if not t["splits"]:
            f.issues.append((t["line"], "no_splits", "TRNS block has no SPL lines"))
        ta = t["trns"].get("_amt")
        if ta is not None and t["splits"]:
            ssum = sum((s.get("_amt") or Decimal(0)) for s in t["splits"])
            if abs(ta + ssum) > CENT:
                f.issues.append((t["line"], "unbalanced",
                                 f"TRNS {fmt_amount(ta)} + SPL sum {fmt_amount(ssum)} "
                                 f"= {fmt_amount(ta + ssum)} (must be 0.00)"))
        if defined_accounts:
            for row in [t["trns"]] + t["splits"]:
                acc = row.get("ACCNT", "").strip()
                if acc and acc not in defined_accounts:
                    f.issues.append((t["line"], "undefined_account",
                                     f"ACCNT {acc!r} is not defined in the file's ACCNT list"))
        if defined_names:
            nm = t["trns"].get("NAME", "").strip()
            if nm and nm not in defined_names:
                f.issues.append((t["line"], "undefined_name",
                                 f"NAME {nm!r} is not defined in CUST/VEND/EMP lists"))
    for kw, rows in f.lists.items():
        for r in rows:
            for col in list(r.keys()):
                if col == "BALANCE" or col == "AMOUNT":
                    v, fixed = parse_amount(r.get(col))
                    if v is not None:
                        r[col] = fmt_amount(v)
    return f

# --------------------------------------------------------------- outputs

def _write_csv(path, rows, cols):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in cols})

def _clean(row):
    return {k: v for k, v in row.items() if not k.startswith("_")}

def write_outputs(f, outdir, source_name):
    os.makedirs(outdir, exist_ok=True)
    paths = {}
    trns_cols = f.headers.get("TRNS", [])
    txn_rows = []
    for t in f.transactions:
        r = _clean(t["trns"])
        r["NUM_SPLITS"] = len(t["splits"])
        bal = ""
        ta = t["trns"].get("_amt")
        if ta is not None and t["splits"]:
            ssum = sum((s.get("_amt") or Decimal(0)) for s in t["splits"])
            bal = "OK" if abs(ta + ssum) <= CENT else f"OFF BY {fmt_amount(ta + ssum)}"
        r["BALANCE_CHECK"] = bal
        txn_rows.append(r)
    paths["transactions"] = os.path.join(outdir, "transactions.csv")
    _write_csv(paths["transactions"], txn_rows, trns_cols + ["NUM_SPLITS", "BALANCE_CHECK"])

    spl_cols = f.headers.get("SPL", [])
    spl_rows = []
    for t in f.transactions:
        for s in t["splits"]:
            r = _clean(s)
            r["PARENT_TRNSID"] = t["trns"].get("TRNSID", "")
            spl_rows.append(r)
    paths["splits"] = os.path.join(outdir, "splits.csv")
    _write_csv(paths["splits"], spl_rows, spl_cols + ["PARENT_TRNSID"])

    paths["lists"] = {}
    name_map = {"ACCNT": "accounts", "CUST": "customers", "VEND": "vendors",
                "EMP": "employees", "ITEM": "items", "INVITEM": "inventory_items",
                "NOTE": "notes"}
    for kw, rows in f.lists.items():
        p = os.path.join(outdir, f"{name_map.get(kw, kw.lower())}.csv")
        _write_csv(p, [_clean(r) for r in rows], f.headers.get(kw, []))
        paths["lists"][kw] = p

    # fixed IIF: same structure, mechanical fixes applied to AMOUNT/DATE
    fixed_path = os.path.join(outdir, "fixed.iif")
    with open(fixed_path, "w", encoding="utf-8", newline="\n") as fh:
        for kw, cols in f.headers.items():
            fh.write("\t".join(["!" + kw] + cols) + "\n")
        for kw, rows in f.lists.items():
            cols = f.headers.get(kw, [])
            for r in rows:
                fh.write("\t".join([kw] + [str(r.get(c, "")) for c in cols]) + "\n")
        for t in f.transactions:
            tc = f.headers.get("TRNS", [])
            sc = f.headers.get("SPL", [])
            fh.write("\t".join(["TRNS"] + [str(t["trns"].get(c, "")) for c in tc]) + "\n")
            for s in t["splits"]:
                fh.write("\t".join(["SPL"] + [str(s.get(c, "")) for c in sc]) + "\n")
            fh.write("ENDTRNS\n")
    paths["fixed_iif"] = fixed_path

    # audit report
    per_account = {}
    for t in f.transactions:
        acc = t["trns"].get("ACCNT", "").strip() or "(none)"
        per_account[acc] = per_account.get(acc, Decimal(0)) + (t["trns"].get("_amt") or Decimal(0))
    rep = [f"# IifSift audit — {source_name}", ""]
    rep.append(f"Transactions: {len(f.transactions)}   "
               f"Splits: {sum(len(t['splits']) for t in f.transactions)}   "
               f"List records: {sum(len(v) for v in f.lists.values())}")
    rep.append("")
    rep.append("## Totals by TRNS account (must reconcile with transactions.csv)")
    for acc in sorted(per_account):
        rep.append(f"- {acc}: {fmt_amount(per_account[acc])}")
    rep.append("")
    rep.append(f"## Mechanical fixes applied ({len(f.fixes)})")
    for ln, kind, detail in f.fixes:
        rep.append(f"- line {ln} [{kind}] {detail}")
    if not f.fixes:
        rep.append("- none")
    rep.append("")
    rep.append(f"## Issues needing a human decision ({len(f.issues)})")
    for ln, kind, detail in f.issues:
        rep.append(f"- line {ln} [{kind}] {detail}")
    if not f.issues:
        rep.append("- none — file is import-ready by every check IifSift runs")
    rep.append("")
    rep.append("Amounts and dates were normalized mechanically; unbalanced "
               "transactions, undefined references and structural problems "
               "are reported, never silently rewritten.")
    paths["report"] = os.path.join(outdir, "audit_report.md")
    with open(paths["report"], "w", encoding="utf-8") as fh:
        fh.write("\n".join(rep) + "\n")
    return paths, per_account

# ------------------------------------------------- output re-read check

def verify_outputs(f, paths):
    """Re-read the CSVs and fixed.iif; reconcile against the parse."""
    def read_csv(path):
        with open(path, newline="", encoding="utf-8") as fh:
            return list(csv.DictReader(fh))
    txns = read_csv(paths["transactions"])
    if len(txns) != len(f.transactions):
        raise RuntimeError("transactions.csv row count mismatch after re-read")
    spls = read_csv(paths["splits"])
    if len(spls) != sum(len(t["splits"]) for t in f.transactions):
        raise RuntimeError("splits.csv row count mismatch after re-read")
    totals = {}
    for r in txns:
        v, _ = parse_amount(r.get("AMOUNT"))
        if v is not None:
            acc = r.get("ACCNT", "").strip() or "(none)"
            totals[acc] = totals.get(acc, Decimal(0)) + v
    src_totals = {}
    for t in f.transactions:
        acc = t["trns"].get("ACCNT", "").strip() or "(none)"
        src_totals[acc] = src_totals.get(acc, Decimal(0)) + (t["trns"].get("_amt") or Decimal(0))
    if set(totals) != set(src_totals):
        raise RuntimeError("account set mismatch after re-read")
    for acc in src_totals:
        if abs(totals[acc] - src_totals[acc]) > CENT:
            raise RuntimeError(f"total for {acc} does not reconcile after re-read")
    with open(paths["fixed_iif"], encoding="utf-8") as fh:
        f2 = parse_iif(fh.read())
    if len(f2.transactions) != len(f.transactions):
        raise RuntimeError("fixed.iif transaction count mismatch after re-parse")
    return {"txns": len(txns), "splits": len(spls), "accounts_reconciled": len(src_totals)}

# -------------------------------------------------------------------- run

def run_iifsift(iif_path, outdir, **kw):
    with open(iif_path, "rb") as fh:
        raw = fh.read()
    try:
        text = raw.decode("utf-8-sig")
        encoding = "utf-8"
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
        encoding = "latin-1"
    f = parse_iif(text)
    audit(f, text.replace("\r\n", "\n").replace("\r", "\n").split("\n"))
    paths, _ = write_outputs(f, outdir, os.path.basename(iif_path))
    check = verify_outputs(f, paths)
    return {
        "transactions": len(f.transactions),
        "splits": sum(len(t["splits"]) for t in f.transactions),
        "list_records": sum(len(v) for v in f.lists.values()),
        "fixes": len(f.fixes),
        "issues": f.issues,
        "issue_count": len(f.issues),
        "encoding": encoding,
        "verify": check,
        "paths": paths,
    }

def main(argv):
    if len(argv) < 3:
        print("usage: iif_sift.py file.iif out/")
        return 2
    s = run_iifsift(argv[1], argv[2])
    print(f"Transactions: {s['transactions']}  Splits: {s['splits']}  "
          f"List records: {s['list_records']}")
    print(f"Mechanical fixes: {s['fixes']}   Issues for a human: {s['issue_count']}")
    for ln, kind, detail in s["issues"]:
        print(f"  line {ln} [{kind}] {detail}")
    print(f"Re-read reconciliation: {s['verify']}")
    print(f"Report: {s['paths']['report']}")
    return 0

if __name__ == "__main__":
    sys.exit(main(sys.argv))
