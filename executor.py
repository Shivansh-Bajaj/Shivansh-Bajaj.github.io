"""Executor: turns approved candidates into Alpaca multi-leg (mleg) orders and
manages closes with escalating limit prices (mid -> concede -> market for
urgent reasons).

Alpaca mleg limit_price is SIGNED: positive = net debit paid, negative = net
credit received. That convention lives only in _limit_for().
"""
import logging
import time
import uuid

from models import OpenStructure
from utils import now_et

log = logging.getLogger("executor")

URGENT_CLOSE_REASONS = {"derisk_deadline", "stop_loss", "expiry_short_strike_risk", "kill_floor"}


def _limit_for(net: float, is_credit: bool, worse: float = 0.0) -> float:
    """net is a positive per-share amount. worse > 0 concedes price to fill:
    for a credit, accept less; for a debit, pay more."""
    px = -(net - worse) if is_credit else (net + worse)
    return round(px, 2)


def _open_legs(cand):
    return [{"symbol": l.symbol, "ratio_qty": str(l.ratio_qty), "side": l.side,
             "position_intent": "sell_to_open" if l.side == "sell" else "buy_to_open"}
            for l in cand.legs]


def _close_legs(struct):
    out = []
    for l in struct.legs:
        side = "buy" if l.side == "sell" else "sell"
        out.append({"symbol": l.symbol, "ratio_qty": str(l.ratio_qty), "side": side,
                    "position_intent": "buy_to_close" if side == "buy" else "sell_to_close"})
    return out


def _await_fill(client, order_id, timeout_sec, poll=3):
    t0 = time.time()
    while time.time() - t0 < timeout_sec:
        o = client.get_order(order_id)
        if o.get("status") in ("filled", "canceled", "expired", "rejected"):
            return o
        time.sleep(poll)
    return client.get_order(order_id)


def _cancel_quietly(client, order_id):
    try:
        client.cancel_order(order_id)
    except Exception:
        pass


def open_structure(client, cand, qty, journal):
    """Place the opening mleg order. Tries mid, then concedes 5c. Returns the
    OpenStructure on fill, else None (never leaves a working order behind)."""
    sid = f"{cand.underlying}-{cand.strategy}-{cand.expiry}-{uuid.uuid4().hex[:6]}"
    for i, worse in enumerate((0.0, 0.05)):
        px = _limit_for(cand.net_mid, cand.is_credit, worse)
        payload = {
            "order_class": "mleg", "qty": str(qty), "type": "limit",
            "limit_price": f"{px:.2f}", "time_in_force": "day",
            "legs": _open_legs(cand), "client_order_id": f"{sid}-open{i}",
        }
        try:
            o = client.submit_order(payload)
        except Exception as e:
            journal.event("order_error", {"structure": sid, "stage": "open",
                                          "err": str(e), "payload": payload})
            return None
        o = _await_fill(client, o["id"], timeout_sec=75)
        if o.get("status") == "filled":
            entry = abs(float(o.get("filled_avg_price") or cand.net_mid))
            s = OpenStructure(
                structure_id=sid, strategy=cand.strategy, underlying=cand.underlying,
                expiry=cand.expiry, legs=cand.legs, qty=qty, entry_net=entry,
                is_credit=cand.is_credit, width=cand.width,
                max_loss_total=cand.max_loss_per_contract * qty,
                short_strikes=cand.short_strikes, opened_at=now_et().isoformat(),
            )
            journal.event("opened", s.model_dump())
            return s
        _cancel_quietly(client, o["id"])
        journal.event("open_unfilled", {"structure": sid, "attempt": i,
                                        "status": o.get("status"), "limit": px})
    return None


def close_structure(client, struct, close_mid, reason, journal):
    """Close with escalating limits; falls back to a market order when quotes
    are missing or when an urgent close keeps failing. Returns True on fill."""
    attempts = [] if close_mid is None else [0.0, 0.05, 0.15]
    for i, worse in enumerate(attempts):
        px = _limit_for(close_mid, is_credit=not struct.is_credit, worse=worse)
        ok, exit_net = _submit_close(client, struct, journal, reason,
                                     {"type": "limit", "limit_price": f"{px:.2f}"}, i)
        if ok:
            _journal_closed(journal, struct, exit_net, reason)
            return True
    if close_mid is None or reason in URGENT_CLOSE_REASONS:
        ok, exit_net = _submit_close(client, struct, journal, reason,
                                     {"type": "market"}, len(attempts))
        if ok:
            _journal_closed(journal, struct, exit_net, reason)
            return True
    journal.event("close_unfilled", {"structure": struct.structure_id, "reason": reason})
    return False


def _submit_close(client, struct, journal, reason, type_fields, attempt_no):
    payload = {
        "order_class": "mleg", "qty": str(struct.qty), "time_in_force": "day",
        "legs": _close_legs(struct),
        "client_order_id": f"{struct.structure_id}-close-{uuid.uuid4().hex[:5]}",
    }
    payload.update(type_fields)
    try:
        o = client.submit_order(payload)
    except Exception as e:
        journal.event("order_error", {"structure": struct.structure_id, "stage": "close",
                                      "attempt": attempt_no, "err": str(e)})
        return False, None
    o = _await_fill(client, o["id"], timeout_sec=60)
    if o.get("status") == "filled":
        fp = o.get("filled_avg_price")
        return True, abs(float(fp)) if fp is not None else None
    _cancel_quietly(client, o["id"])
    journal.event("close_unfilled_attempt", {"structure": struct.structure_id,
                                             "attempt": attempt_no,
                                             "status": o.get("status"), "reason": reason})
    return False, None


def _journal_closed(journal, struct, exit_net, reason):
    rec = {"structure": struct.structure_id, "reason": reason, "exit_net": exit_net}
    if exit_net is not None:
        pnl = (struct.entry_net - exit_net) if struct.is_credit else (exit_net - struct.entry_net)
        rec["pnl_per_share"] = round(pnl, 2)
        rec["pnl_dollars"] = round(pnl * 100 * struct.qty, 2)
    journal.event("closed", rec)
