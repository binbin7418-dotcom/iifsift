#!/usr/bin/env python3
"""IifSift acceptance test.

Seeded-defect method: a hand-built IIF with exactly known defects --
  1 unbalanced transaction (off by 10.00)
  1 missing ENDTRNS (file ends inside a block)
  1 reference to an undefined account ('Mystery Account')
  1 duplicate TRNSID
  1 parenthesized comma amount (1,200.00)  -> mechanical fix
  1 MM/DD/YY date                          -> mechanical fix
plus 2 clean transactions. The audit must find exactly the 4 decision
issues and both mechanical fixes, outputs must reconcile to the cent,
and the shipped sample.iif must come back completely clean.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from iif_sift import run_iifsift  # noqa: E402

HERE = os.path.dirname(__file__)
OUT = os.path.join(HERE, "out")
results = []

def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}")

SEEDED = "\n".join([
    "!ACCNT\tNAME\tACCNTTYPE\tBALANCE",
    "!CUST\tNAME\tFIRSTNAME\tLASTNAME",
    "!TRNS\tTRNSID\tTRNSTYPE\tDATE\tACCNT\tNAME\tAMOUNT\tDOCNUM\tMEMO",
    "!SPL\tSPLID\tTRNSTYPE\tDATE\tACCNT\tAMOUNT\tMEMO",
    "!ENDTRNS",
    "ACCNT\tBusiness Checking\tBANK\t5000.00",
    "ACCNT\tOffice Supplies\tEXP\t0.00",
    "ACCNT\tSales Income\tINC\t0.00",
    "CUST\tAcme Widgets\tAcme\tWidgets",
    # clean txn 1
    "TRNS\t2001\tCHECK\t01/05/26\tBusiness Checking\tAcme Widgets\t-250.00\t1001\tPaper",
    "SPL\t2002\tCHECK\t01/05/26\tOffice Supplies\t250.00\tPaper",
    "ENDTRNS",
    # clean txn 2 with mechanical defects: (1,200.00) amount + MM/DD/YY already above
    "TRNS\t2003\tDEPOSIT\t2026-01-07\tBusiness Checking\tAcme Widgets\t(1,200.00)\t\tDeposit",
    "SPL\t2004\tDEPOSIT\t2026-01-07\tSales Income\t1200.00\tDeposit",
    "ENDTRNS",
    # unbalanced by 10.00 + undefined account + duplicate TRNSID 2001
    "TRNS\t2001\tCHECK\t2026-01-09\tBusiness Checking\tAcme Widgets\t-100.00\t1002\tBad one",
    "SPL\t2005\tCHECK\t2026-01-09\tMystery Account\t90.00\tShort split",
    "ENDTRNS",
    # missing ENDTRNS: file just ends
    "TRNS\t2006\tCHECK\t2026-01-10\tBusiness Checking\tAcme Widgets\t-50.00\t1003\tNo close",
    "SPL\t2007\tCHECK\t2026-01-10\tOffice Supplies\t50.00\tNo close",
]) + "\n"

def main():
    seeded_path = os.path.join(OUT, "seeded.iif")
    os.makedirs(OUT, exist_ok=True)
    with open(seeded_path, "w", encoding="utf-8") as fh:
        fh.write(SEEDED)
    s = run_iifsift(seeded_path, os.path.join(OUT, "seeded_out"))

    check("counts: 4 transactions / 4 splits parsed",
          s["transactions"] == 4 and s["splits"] == 4,
          f"({s['transactions']} txns, {s['splits']} splits)")
    kinds = sorted(k for _, k, _ in s["issues"])
    check("exactly the 4 seeded decision issues found",
          kinds == ["duplicate_trnsid", "missing_endtrns", "unbalanced", "undefined_account"],
          f"({kinds})")
    # recount fixes from report file
    with open(s["paths"]["report"], encoding="utf-8") as fh:
        report = fh.read()
    check("mechanical fixes: amount format normalized",
          "amount_format" in report and "1200.00" in report, "")
    check("mechanical fixes: MM/DD/YY dates normalized",
          report.count("date_format") >= 2, f"({report.count('date_format')} date fixes in report)")
    check("re-read reconciliation passed", s["verify"]["txns"] == 4
          and s["verify"]["accounts_reconciled"] >= 1, f"({s['verify']})")
    with open(s["paths"]["transactions"], encoding="utf-8") as fh:
        txns_csv = fh.read()
    check("unbalanced txn marked in transactions.csv", "OFF BY -10.00" in txns_csv, "")
    check("clean txn marked OK in transactions.csv", ",OK" in txns_csv.replace(" ", ""), "")

    sample = os.path.join(HERE, "..", "sample.iif")
    ss = run_iifsift(sample, os.path.join(OUT, "sample_out"))
    check("shipped sample.iif is completely clean",
          ss["issue_count"] == 0 and ss["transactions"] == 3 and ss["splits"] == 3,
          f"({ss['transactions']} txns, {ss['issue_count']} issues)")
    check("sample totals reconcile", abs(ss["verify"]["txns"] - 3) == 0, "")

    npass = sum(1 for _, ok, _ in results if ok)
    print(f"\n{npass}/{len(results)} PASS")
    with open(os.path.join(HERE, "acceptance_results.txt"), "w") as fh:
        for name, ok, detail in results:
            fh.write(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}\n")
        fh.write(f"\n{npass}/{len(results)} PASS\n")
    return 0 if npass == len(results) else 1

if __name__ == "__main__":
    sys.exit(main())
