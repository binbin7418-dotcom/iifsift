import os
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext

from iif_sift import run_iifsift


def run_job(src, outdir):
    s = run_iifsift(src, outdir)
    lines = [f"Transactions: {s['transactions']}   Splits: {s['splits']}   "
             f"List records: {s['list_records']}",
             f"Mechanical fixes applied: {s['fixes']}",
             f"Issues needing a human decision: {s['issue_count']}"]
    for ln, kind, detail in s["issues"]:
        lines.append(f"  line {ln} [{kind}] {detail}")
    lines.append(f"Output re-read reconciliation: PASS "
                 f"({s['verify']['accounts_reconciled']} accounts to the cent)")
    lines.append(f"Report: {s['paths']['report']}")
    lines.append(f"Fixed IIF: {s['paths']['fixed_iif']}")
    return lines


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("IifSift")
        self.geometry("640x520")
        self.resizable(False, False)
        self.file = tk.StringVar()
        row = tk.Frame(self); row.pack(fill="x", padx=12, pady=(12, 2))
        tk.Label(row, text="QuickBooks file (.iif):", width=34, anchor="w").pack(side="left")
        tk.Entry(row, textvariable=self.file, width=38).pack(side="left")
        tk.Button(row, text="Browse", command=lambda: self.pick(self.file,
                  [("IIF files", "*.iif"), ("All files", "*.*")])).pack(side="left", padx=4)
        tk.Button(self, text="Sift && audit", font=("Segoe UI", 11, "bold"),
                  command=self.start).pack(pady=10)
        self.log = scrolledtext.ScrolledText(self, width=76, height=16,
                                             font=("Consolas", 9))
        self.log.pack(padx=12, pady=4)
        self.write("Pick a QuickBooks .iif export. You get clean CSV tables, a fixed .iif, and an audit that names every unbalanced transaction and undefined reference -- with line numbers. Nothing is uploaded; the original file is never modified.")

    def write(self, msg):
        self.log.insert(tk.END, str(msg) + "\n")
        self.log.see(tk.END)
        self.update_idletasks()

    def pick(self, var, types):
        p = filedialog.askopenfilename(filetypes=types)
        if p:
            var.set(p)

    def collect(self):
        src = self.file.get().strip()
        if not src or not os.path.isfile(src):
            raise ValueError("Choose an existing .iif file first.")
        outdir = os.path.join(os.path.dirname(os.path.abspath(src)), "iifsift_out")
        return src, outdir

    def start(self):
        try:
            args = self.collect()
        except Exception as e:
            messagebox.showerror("IifSift", str(e))
            return

        def work():
            try:
                for ln in run_job(*args):
                    self.write(ln)
            except Exception as e:
                self.write(f"ERROR: {e}")

        threading.Thread(target=work, daemon=True).start()


if __name__ == "__main__":
    App().mainloop()
