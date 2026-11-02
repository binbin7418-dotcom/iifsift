# IifSift

Turn a QuickBooks **IIF export into clean CSV tables** — and audit it the way QuickBooks never will before you import anything.

## Why

QuickBooks Desktop imports IIF almost blind. A transaction whose splits don't balance, a block missing its ENDTRNS, a reference to an account the file never defines — it goes in silently, or dies with an error that doesn't tell you which line or why. IifSift parses the file properly (header definitions first, records second), then splits it into reviewable tables and audits the decision-level problems by name and line number:

- **Balance** — every TRNS must net to 0.00 against its SPL lines; offenders are printed with the exact off-by amount
- **Structure** — missing ENDTRNS, SPL lines outside a block, over-wide rows, unknown record types
- **References** — ACCNT / NAME values used but never defined in the file's lists
- **Duplicates** — repeated TRNSIDs

Mechanical dirt is fixed, not flagged: `$1,234.56` and `(1,200.00)` amounts, MM/DD/YY dates, stray whitespace. Anything requiring judgment is **reported, never silently rewritten**.

## Use

**App (Windows):** launch `IifSift.exe`, pick an `.iif` file, click **Sift & audit**. Output lands in an `iifsift_out` folder next to the file:

- `transactions.csv` — one row per TRNS, with split count and a BALANCE_CHECK column (OK / OFF BY x.xx)
- `splits.csv` — one row per SPL, with its parent TRNSID
- `accounts.csv` / `customers.csv` / `vendors.csv` / … — one CSV per list type present
- `fixed.iif` — same structure, mechanical fixes applied (imports cleaner, decides nothing for you)
- `audit_report.md` — per-account totals, every fix applied, every issue with its line number

**Command line:** `python iif_sift.py file.iif out/`

A clean sample is included (`sample.iif` + its generated `sample_out/`).

## Verified

Acceptance test (9/9 PASS, `test/acceptance_results.txt`): a seeded IIF with exactly 4 decision defects (unbalanced by 10.00, missing ENDTRNS, undefined account, duplicate TRNSID) and 2 mechanical defects ((1,200.00) amount, MM/DD/YY dates) — the audit found exactly the 4 issues and both fixes; outputs re-read and reconciled to the cent; the shipped sample file comes back completely clean (3 transactions, 0 issues).

Before any run finishes, IifSift re-reads its own CSVs and the fixed .iif and reconciles row counts and per-account totals against the source parse. A mismatch fails the run loudly instead of shipping bad tables.

## Scope

Tab-delimited IIF (the standard QuickBooks export). Sign convention audited as double-entry: TRNS + Σ SPL = 0. 100% local — your books never leave your machine.

## Build from source

`pip install pyinstaller`, then `pyinstaller iifsift.spec` (the engine is pure standard library). Or push to GitHub and let the included Actions workflow build the Windows package.
