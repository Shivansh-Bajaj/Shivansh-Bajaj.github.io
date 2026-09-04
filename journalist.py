"""Journalist: append-only decision log (journal/journal.jsonl) and equity
curve (journal/equity_curve.csv). This is the agent's black box recorder --
every LLM view, risk verdict, order, and error lands here. Great submission
evidence."""
import csv
import json
import os

import config as cfg
from utils import now_et


class Journal:
    def __init__(self, base_dir="."):
        self.jdir = os.path.join(base_dir, cfg.JOURNAL_DIR)
        os.makedirs(self.jdir, exist_ok=True)
        self.path = os.path.join(self.jdir, "journal.jsonl")
        self.curve = os.path.join(self.jdir, "equity_curve.csv")

    def event(self, kind: str, payload):
        rec = {"ts_et": now_et().isoformat(), "kind": kind, "payload": payload}
        with open(self.path, "a") as f:
            f.write(json.dumps(rec, default=str) + "\n")
        print(f"[{rec['ts_et']}] {kind}: {json.dumps(payload, default=str)[:400]}")

    def equity(self, account: dict):
        new = not os.path.exists(self.curve)
        eq = float(account["equity"])
        last = float(account.get("last_equity") or 0)
        dp = (eq / last - 1) * 100 if last else 0.0
        with open(self.curve, "a", newline="") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["ts_et", "equity", "cash", "daily_pnl_pct"])
            w.writerow([now_et().isoformat(), eq, account.get("cash"), f"{dp:.3f}"])
