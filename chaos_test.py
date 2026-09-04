"""Offline chaos & guardrail tests. No network, no API keys required.

Run:  python chaos_test.py

Each scenario simulates a failure or dangerous situation and asserts that the
deterministic layers (Risk Officer, Executor pricing, Strategist fail-safe,
client retry logic) respond correctly. Include the output in your submission
as robustness evidence.
"""
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx

import alpaca_client
import config as cfg
import news as news_mod
import risk_officer as ro
import strategist
from executor import _limit_for
from models import Candidate, Decision, Leg, OpenStructure

ET = ZoneInfo("America/New_York")
RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond)))
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  [{detail}]" if detail else ""))


ACCOUNT = {"equity": "100000", "last_equity": "100000"}
MONDAY_11 = datetime(2026, 8, 31, 11, 0, tzinfo=ET)


def cand(**kw):
    base = dict(
        id="c1", strategy="put_credit_spread", underlying="SPY",
        expiry="2026-09-03",
        legs=[Leg(symbol="SPY260903P00640000", side="sell"),
              Leg(symbol="SPY260903P00635000", side="buy")],
        net_mid=1.00, is_credit=True, width=5.0,
        max_loss_per_contract=400.0, max_gain_per_contract=100.0,
        short_strikes=[640.0], max_qty_at_risk_cap=2,
    )
    base.update(kw)
    return Candidate(**base)


def struct(**kw):
    base = dict(
        structure_id="s1", strategy="put_credit_spread", underlying="SPY",
        expiry="2026-09-03",
        legs=[Leg(symbol="SPY260903P00640000", side="sell"),
              Leg(symbol="SPY260903P00635000", side="buy")],
        qty=1, entry_net=1.00, is_credit=True, width=5.0,
        max_loss_total=400.0, short_strikes=[640.0], opened_at="t",
    )
    base.update(kw)
    return OpenStructure(**base)


print("== Risk Officer: entry validation ==")

q, r = ro.validate_entry(Decision(action="enter", candidate_id="c1", qty=10),
                         cand(), ACCOUNT, [], MONDAY_11)
check("oversized qty clamped to per-trade cap (10 -> 2)", q == 2, f"qty={q} {r}")

q, r = ro.validate_entry(Decision(action="enter", candidate_id="c1", qty=1),
                         cand(), ACCOUNT, [struct()], MONDAY_11)
check("duplicate structure vetoed", q == 0, str(r))

q, r = ro.validate_entry(Decision(action="enter", candidate_id="c1", qty=1),
                         cand(expiry="2026-09-04"), ACCOUNT, [], MONDAY_11)
check("credit expiry past Sep 3 vetoed", q == 0, str(r))

t_blk = datetime(2026, 9, 1, 9, 30, tzinfo=ET)  # 30 min before ISM
q, r = ro.validate_entry(Decision(action="enter", candidate_id="c1", qty=1),
                         cand(), ACCOUNT, [], t_blk)
check("entry blocked in ISM blackout window", q == 0, str(r))

tiny = cand(net_mid=0.10)
q, r = ro.validate_entry(Decision(action="enter", candidate_id="c1", qty=1),
                         tiny, ACCOUNT, [], MONDAY_11)
check("too-small credit vetoed", q == 0, str(r))

_cap = float(ACCOUNT["equity"]) * cfg.MAX_TOTAL_OPEN_RISK_PCT / 100
big_open = struct(structure_id="b", strategy="call_credit_spread",
                  underlying="QQQ", max_loss_total=_cap - 200.0)
q, r = ro.validate_entry(Decision(action="enter", candidate_id="c1", qty=2),
                         cand(), ACCOUNT, [big_open], MONDAY_11)
check(f"portfolio risk cap blocks entry ({_cap - 200:.0f}/{_cap:.0f} used)",
      q == 0, str(r))

print("\n== Risk Officer: halts ==")

halts = ro.entry_halts({"equity": "97000", "last_equity": "100000"}, MONDAY_11)
check("daily -3% halts new entries", any("daily loss" in h for h in halts), str(halts))

halts = ro.entry_halts({"equity": "91000", "last_equity": "100000"}, MONDAY_11)
check("kill floor flagged at 91k", any("kill floor" in h for h in halts), str(halts))

late = datetime(2026, 9, 3, 14, 30, tzinfo=ET)
halts = ro.entry_halts(ACCOUNT, late)
check("no new entries after Thu 14:00 cutoff", any("cutoff" in h for h in halts), str(halts))

print("\n== Risk Officer: exit signals ==")

s = struct()
check("profit take at 50% of credit", ro.exit_reason(s, 0.45, 655.0, MONDAY_11) == "profit_take")
check("stop loss at 2x credit", ro.exit_reason(s, 2.10, 641.0, MONDAY_11) == "stop_loss")
check("hold in between", ro.exit_reason(s, 1.00, 655.0, MONDAY_11) is None)

after_deadline = datetime(2026, 9, 3, 16, 0, tzinfo=ET)
check("derisk deadline forces close",
      ro.exit_reason(s, 1.00, 655.0, after_deadline) == "derisk_deadline")

expiry_pm = datetime(2026, 9, 3, 15, 10, tzinfo=ET)
check("expiry-day short-strike breach closes (spot 639 vs short 640)",
      ro.exit_reason(s, 1.00, 639.0, expiry_pm) == "expiry_short_strike_risk")
check("expiry-day comfortably OTM holds",
      ro.exit_reason(s, 1.00, 655.0, expiry_pm) is None)

print("\n== Executor: mleg limit-price sign convention ==")

check("credit open -> negative limit (-1.00)", _limit_for(1.00, True) == -1.00)
check("debit open -> positive limit (0.80)", _limit_for(0.80, False) == 0.80)
check("conceding on credit lowers received (-0.95)", _limit_for(1.00, True, 0.05) == -0.95)
check("conceding on debit pays more (0.85)", _limit_for(0.80, False, 0.05) == 0.85)

print("\n== Strategist: fail-safe on garbage LLM output ==")

out = strategist.decide({"candidates": []}, {}, llm=lambda s, u: "definitely not json {{{")
check("garbage LLM -> abstain fail-safe",
      out.regime == "high_uncertainty"
      and out.decisions and out.decisions[0].action == "abstain")

out = strategist.decide({"candidates": []}, {},
                        llm=lambda s, u: '{"regime": "martian", "market_view": "x", "decisions": []}')
check("schema-violating LLM -> abstain fail-safe", out.decisions[0].action == "abstain")

print("\n== News layer: storm gate ==")

hot = news_mod.storm_symbols({"NVDA": 6, "SPY": 9, "AAPL": 2})
check("storm gate flags single names, exempts core ETFs", hot == {"NVDA"}, str(hot))

q, r = ro.validate_entry(Decision(action="enter", candidate_id="n1", qty=1),
                         cand(id="n1", underlying="NVDA"), ACCOUNT, [], MONDAY_11,
                         hot_symbols={"NVDA"})
check("entry vetoed during news storm", q == 0, str(r))

_now = datetime(2026, 9, 1, 14, 0, tzinfo=timezone.utc)
_fresh = [{"headline": f"TSLA headline {i}", "symbols": ["TSLA"],
           "created_at": (_now - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")}
          for i in range(5)]
_digest, _counts = news_mod.build_digest(_fresh, now_utc=_now)
check("burst of 5 fresh headlines counted for one symbol",
      _counts.get("TSLA") == 5, str(_counts))
check("...and that burst triggers the storm gate",
      news_mod.storm_symbols(_counts) == {"TSLA"})

_digest, _ = news_mod.build_digest([
    {"headline": "Chipmaker soars on earnings", "symbols": ["NVDA"], "created_at": "junk"},
    {"headline": "Chipmaker soars on earnings", "symbols": ["NVDA"]},
    {"symbols": ["XYZ"]},
    {"headline": "Second story", "symbols": None, "created_at": None},
    "not even a dict",
])
check("digest dedupes and survives junk items",
      [x["headline"] for x in _digest] == ["Chipmaker soars on earnings", "Second story"])

print("\n== Alpaca client: transient failures retried ==")


class _Resp:
    status_code = 200
    text = '{"ok": true}'

    def json(self):
        return {"ok": True}


class _FlakyHTTP:
    def __init__(self):
        self.n = 0

    def request(self, *a, **k):
        self.n += 1
        if self.n < 3:
            raise httpx.ConnectError("simulated network flap")
        return _Resp()


cl = alpaca_client.AlpacaClient(key_id="test", secret="test")
cl.backoff_base = 0.0
flaky = _FlakyHTTP()
cl._http = flaky
res = cl._req("GET", "https://example.invalid/x")
check("two network failures then success (3 attempts)",
      res == {"ok": True} and flaky.n == 3, f"attempts={flaky.n}")


class _RejectHTTP:
    class _R:
        status_code = 403
        text = "forbidden"

    def request(self, *a, **k):
        return self._R()


cl2 = alpaca_client.AlpacaClient(key_id="test", secret="test")
cl2.backoff_base = 0.0
cl2._http = _RejectHTTP()
try:
    cl2._req("GET", "https://example.invalid/x")
    check("4xx raises immediately (no futile retries)", False)
except alpaca_client.AlpacaError:
    check("4xx raises immediately (no futile retries)", True)

print()
failed = [n for n, ok in RESULTS if not ok]
print(f"{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
if failed:
    print("FAILED:", ", ".join(failed))
    sys.exit(1)
print("All guardrails behaving as designed.")
