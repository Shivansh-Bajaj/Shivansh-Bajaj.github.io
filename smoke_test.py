"""Offline end-to-end smoke test: runs a FULL decision pass against a
synthetic Alpaca client (no network, no keys) with a stubbed Strategist.

Proves the Scout -> Strategist -> Risk Officer -> (dryrun) Executor plumbing
works end to end: candidates get built from a chain, the stub picks one with
an oversized qty, and the Risk Officer must clamp it.

Run:  python smoke_test.py
"""
import json
import os
import shutil
import sys
import tempfile

import config as cfg
import strategist
from models import Decision, StrategistOutput

SPOTS = {"SPY": 645.0, "QQQ": 600.0, "IWM": 290.0, "DIA": 440.0,
         "NVDA": 180.0, "AAPL": 230.0,
         "MSFT": 520.0, "META": 780.0, "AMZN": 240.0, "TSLA": 350.0,
         "GOOGL": 210.0, "AVGO": 310.0, "AMD": 175.0}
MOM = {"NVDA": (3.0, 1.5)}  # (five_day %, day %); everything else ~flat


def occ(u, right, k):
    return f"{u}260903{right}{int(round(k * 1000)):08d}"


class FakeClient:
    feed = "indicative"

    def clock(self):
        return {"is_open": True}

    def account(self):
        return {"equity": "100000", "last_equity": "100000", "cash": "100000"}

    def stock_snapshots(self, symbols):
        out = {}
        for s in symbols:
            spot = SPOTS[s]
            day = MOM.get(s, (0.3, 0.1))[1]
            out[s] = {"latestTrade": {"p": spot},
                      "prevDailyBar": {"c": spot / (1 + day / 100)},
                      "dailyBar": {"h": spot * 1.004, "l": spot * 0.996}}
        return out

    def stock_daily_bars(self, symbols, days_back=12):
        out = {}
        for s in symbols:
            spot = SPOTS[s]
            five = MOM.get(s, (0.3, 0.1))[0]
            bars = []
            for i in range(7):
                c = spot / (1 + five / 100) if i == 5 else spot * (1 - 0.001 * i)
                bars.append({"t": f"2026-08-{26 - i:02d}", "c": c,
                             "h": c * 1.004, "l": c * 0.996})
            out[s] = bars
        return out

    def option_chain(self, underlying, expiration_date=None, type_=None,
                     strike_gte=None, strike_lte=None, max_contracts=600):
        spot = SPOTS[underlying]
        lo = strike_gte or spot * 0.95
        hi = strike_lte or spot * 1.05
        chain = {}
        k = float(int(lo))
        while k <= hi:
            for right in ("P", "C"):
                if type_ and ((type_ == "put") != (right == "P")):
                    continue
                dist = (spot - k) if right == "P" else (k - spot)
                if dist < 0:  # ITM side: rough numbers, the Scout won't pick these
                    delta, price = 0.55, abs(dist) + 2.0
                else:
                    delta = max(0.02, 0.5 - dist * 0.03)
                    price = max(0.06, round(delta * 4, 2))
                m = round(price, 2)
                chain[occ(underlying, right, k)] = {
                    "latestQuote": {"bp": round(m - 0.02, 2), "ap": round(m + 0.02, 2)},
                    "greeks": {"delta": -delta if right == "P" else delta},
                    "impliedVolatility": 0.14,
                }
            k += 1.0
        return chain

    def news(self, symbols=None, start=None, limit=50):
        return [
            {"headline": "Chipmaker rallies after guidance hike", "symbols": ["NVDA"],
             "created_at": "2026-08-31T13:00:00Z", "source": "benzinga"},
            {"headline": "Indexes drift ahead of ISM data", "symbols": ["SPY", "QQQ"],
             "created_at": "2026-08-31T12:30:00Z", "source": "benzinga"},
        ]


def stub_decide(brief, risk_state, llm=None):
    cands = brief["candidates"]
    assert cands, "scout produced no candidates from the synthetic chain"
    pick = cands[0]["id"]
    return StrategistOutput(
        regime="rangebound",
        market_view=f"[stub] selecting {pick} with an oversized qty on purpose",
        decisions=[Decision(action="enter", candidate_id=pick, qty=5,
                            rationale="smoke test: qty must get clamped",
                            confidence=0.6)],
    )


def run():
    strategist.decide = stub_decide
    import main  # after the stub so nothing real is called

    tmp = tempfile.mkdtemp(prefix="smoke_")
    state = main.State(base_dir=tmp)
    journal = main.Journal(base_dir=tmp)
    main.decision_pass(FakeClient(), state, journal, place_orders=False)

    with open(os.path.join(tmp, cfg.JOURNAL_DIR, "journal.jsonl")) as f:
        events = [json.loads(line) for line in f]
    kinds = [e["kind"] for e in events]
    brief = next(e for e in events if e["kind"] == "brief")["payload"]
    review = next((e for e in events if e["kind"] == "risk_review"), {"payload": {}})["payload"]
    shutil.rmtree(tmp, ignore_errors=True)

    print("\ncandidates built:", brief.get("candidate_ids"))
    print("news headlines in brief:", brief.get("news_headlines"))
    print("risk review:", review)

    ok = (
        "brief" in kinds and "strategist" in kinds
        and "risk_review" in kinds and "dryrun_would_open" in kinds
        and len(brief.get("candidate_ids") or []) >= 3
        and 1 <= review.get("approved_qty", 0) < 5
    )
    if ok:
        print("\nSMOKE TEST PASSED: full pipeline ran offline; "
              "Risk Officer clamped the oversized qty.")
        return 0
    print("\nSMOKE TEST FAILED")
    return 1


if __name__ == "__main__":
    sys.exit(run())
