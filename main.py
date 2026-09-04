"""Entry point.

Modes:
  python main.py dryrun     full decision pass but NO orders (safe rehearsal)
  python main.py once       one decision pass, orders included
  python main.py monitor    one monitor pass (exits, kill switches)
  python main.py run        autonomous loop: monitor every 5 min, decide at :15/:45
  python main.py status     print account and positions
  python main.py close-all  close every open structure now
"""
import argparse
import logging
import time
import traceback

import config as cfg
import executor
import news
import recover
import risk_officer as ro
import scout
import strategist
from alpaca_client import AlpacaClient
from journalist import Journal
from models import Candidate
from statestore import State
from utils import mid, now_et

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("main")


def load_env():
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass


def close_cost_mid(client, struct):
    """Per-share cost to close at mid. Credit structures: debit to buy back.
    Debit structures: value received on sale. None if any leg quote missing."""
    quotes = client.latest_option_quotes([l.symbol for l in struct.legs])
    raw = 0.0
    for l in struct.legs:
        m = mid(quotes.get(l.symbol) or {})
        if m is None:
            return None
        raw += m if l.side == "sell" else -m
    val = raw if struct.is_credit else -raw
    return round(val, 2)


# --------------------------------------------------------------- decision
def decision_pass(client, state, journal, place_orders=True):
    account = client.account()
    journal.equity(account)
    if state.halted().get("halted"):
        journal.event("skip_decision", {"why": "halted", "detail": state.halted()})
        return
    halts = ro.entry_halts(account, now_et())
    if halts:
        journal.event("skip_decision", {"why": halts})
        return
    clock = client.clock()
    if not clock.get("is_open"):
        if place_orders:
            # Never trade a closed market: quotes are stale and queued limit
            # orders would fill blind at the next open. Dryrun still proceeds.
            journal.event("skip_decision", {"why": "market closed",
                                            "next_open": clock.get("next_open")})
            log.info("market closed; skipping trade pass (next open %s). "
                     "Use dryrun for a no-order rehearsal.", clock.get("next_open"))
            return
        journal.event("note", {"msg": "market closed: quotes are stale and any "
                                      "orders would queue until the open"})

    equity = float(account["equity"])
    brief = scout.get_market_brief(client, equity)

    digest, hot = [], set()
    try:
        items = news.fetch_recent_news(client, cfg.CORE_UNDERLYINGS + cfg.SATELLITE_WATCHLIST)
        digest, counts = news.build_digest(items)
        hot = news.storm_symbols(counts)
    except Exception as e:  # news is a bonus signal; never let it kill a cycle
        journal.event("news_error", {"err": str(e)})
    brief["news_digest"] = digest
    brief["news_storm_symbols"] = sorted(hot)
    if hot:
        journal.event("news_storm", {"symbols": sorted(hot),
                                     "note": "new entries on these names blocked this cycle"})

    cands = {c["id"]: Candidate.model_validate(c) for c in brief["candidates"]}
    structs = state.structures()
    live = [s for s in structs if s.status != "closed"]
    risk_state = {
        "equity": equity,
        "daily_pnl_pct": round(ro.daily_pnl_pct(account), 2),
        "open_structures": [{"strategy": s.strategy, "underlying": s.underlying,
                             "expiry": s.expiry, "qty": s.qty,
                             "max_loss": s.max_loss_total} for s in live],
        "open_risk_dollars": sum(s.max_loss_total for s in live),
        "portfolio_risk_cap_dollars": equity * cfg.MAX_TOTAL_OPEN_RISK_PCT / 100,
    }
    journal.event("brief", {"underlyings": brief["underlyings"],
                            "candidate_ids": list(cands), "notes": brief["notes"],
                            "news_headlines": len(digest)})

    out = strategist.decide(brief, risk_state)
    journal.event("strategist", {"regime": out.regime, "view": out.market_view,
                                 "decisions": [d.model_dump() for d in out.decisions]})

    entered = 0
    for dec in out.decisions:
        if entered >= 2:
            break
        if dec.action != "enter" or not dec.candidate_id:
            continue
        cand = cands.get(dec.candidate_id)
        if not cand:
            journal.event("risk_veto", {"decision": dec.model_dump(),
                                        "reasons": ["unknown candidate_id"]})
            continue
        qty, reasons = ro.validate_entry(dec, cand, account, live, now_et(),
                                         hot_symbols=hot)
        journal.event("risk_review", {"candidate": cand.id, "requested_qty": dec.qty,
                                      "approved_qty": qty, "reasons": reasons})
        if qty < 1:
            continue
        if not place_orders:
            journal.event("dryrun_would_open", {"candidate": cand.model_dump(), "qty": qty})
            entered += 1
            continue
        s = executor.open_structure(client, cand, qty, journal)
        if s:
            structs.append(s)
            live.append(s)
            state.save_structures(structs)
            entered += 1


# ---------------------------------------------------------------- monitor
def monitor_pass(client, state, journal):
    account = client.account()
    journal.equity(account)
    structs = state.structures()
    live = [s for s in structs if s.status != "closed"]
    if float(account["equity"]) <= cfg.EQUITY_KILL_FLOOR:
        if live:
            journal.event("kill_floor", {"equity": account["equity"]})
            _close_all(client, state, journal, structs, live, "kill_floor")
        state.set_halt(f"equity {account['equity']} <= kill floor {cfg.EQUITY_KILL_FLOOR}")
        return
    if not live:
        return
    spots = {}
    for u in {s.underlying for s in live}:
        try:
            snap = client.stock_snapshots([u]).get(u) or {}
            p = (snap.get("latestTrade") or {}).get("p")
            spots[u] = float(p) if p else None
        except Exception:
            spots[u] = None
    changed = False
    for s in live:
        try:
            cost = close_cost_mid(client, s)
        except Exception as e:
            journal.event("quote_error", {"structure": s.structure_id, "err": str(e)})
            cost = None
        reason = ro.exit_reason(s, cost, spots.get(s.underlying), now_et())
        if reason and executor.close_structure(client, s, cost, reason, journal):
            s.status, s.close_reason = "closed", reason
            changed = True
    if changed:
        state.save_structures(structs)


def _close_all(client, state, journal, structs, live, reason):
    for s in live:
        try:
            cost = close_cost_mid(client, s)
        except Exception:
            cost = None
        if executor.close_structure(client, s, cost, reason, journal):
            s.status, s.close_reason = "closed", reason
    state.save_structures(structs)


# ------------------------------------------------------------------- loop
def run_loop(client, state, journal):
    journal.event("loop_start", {
        "kill_floor": cfg.EQUITY_KILL_FLOOR,
        "daily_halt_pct": cfg.DAILY_LOSS_HALT_PCT,
        "derisk_deadline": cfg.DERISK_DEADLINE.isoformat(),
    })
    last_decision_key = None
    last_monitor = 0.0
    while True:
        try:
            clock = client.clock()
            if not clock.get("is_open"):
                log.info("market closed; next open %s", clock.get("next_open"))
                time.sleep(300)
                continue
            t = now_et()
            if time.time() - last_monitor >= cfg.MONITOR_INTERVAL_SEC:
                monitor_pass(client, state, journal)
                last_monitor = time.time()
            hm = (t.hour, t.minute)
            in_window = cfg.FIRST_DECISION_ET <= hm <= cfg.LAST_DECISION_ET
            key = (t.date().isoformat(), t.hour, t.minute)
            if t.minute in cfg.DECISION_MINUTES and in_window and key != last_decision_key:
                last_decision_key = key
                decision_pass(client, state, journal)
        except Exception:
            log.error("loop error:\n%s", traceback.format_exc())
            try:
                journal.event("loop_error", {"trace": traceback.format_exc()[-1500:]})
            except Exception:
                pass
            time.sleep(30)
        time.sleep(cfg.LOOP_SLEEP_SEC)


# -------------------------------------------------------------------- cli
def cmd_status(client):
    a = client.account()
    print(f"equity={a['equity']} cash={a['cash']} "
          f"options_bp={a.get('options_buying_power')} "
          f"daily={ro.daily_pnl_pct(a):+.2f}%")
    for p in client.positions():
        print(f"  {p['symbol']:>22} qty={p['qty']:>5} "
              f"mv={p.get('market_value')} uPnL={p.get('unrealized_pl')}")


if __name__ == "__main__":
    load_env()
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=["run", "once", "dryrun", "monitor", "status", "close-all"])
    args = ap.parse_args()
    client = AlpacaClient()
    state, journal = State(), Journal()
    if args.mode in ("run", "once", "monitor", "close-all"):
        # Ephemeral-host safety: if the disk was wiped (container restart),
        # rediscover open positions from the broker before doing anything.
        try:
            recover.reconcile(client, state, journal)
        except Exception:
            log.error("state reconcile failed:\n%s", traceback.format_exc())
    if args.mode == "run":
        run_loop(client, state, journal)
    elif args.mode == "once":
        decision_pass(client, state, journal, place_orders=True)
    elif args.mode == "dryrun":
        decision_pass(client, state, journal, place_orders=False)
    elif args.mode == "monitor":
        monitor_pass(client, state, journal)
    elif args.mode == "status":
        cmd_status(client)
    else:
        structs = state.structures()
        live = [s for s in structs if s.status != "closed"]
        _close_all(client, state, journal, structs, live, "manual_close_all")
