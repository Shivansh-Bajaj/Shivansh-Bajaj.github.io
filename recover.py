"""Rebuild open-structure state from the broker's own position list.

Needed for ephemeral hosts (Hugging Face Spaces): if the container restarts,
`state/structures.json` is gone but the positions still exist at Alpaca. A
blind agent would neither manage exits nor count open risk, so on boot we
reconstruct a conservative OpenStructure for every option position group.

Reconstruction is best-effort: entry prices come from Alpaca's avg_entry_price
per leg, and unknown fields err toward *more* caution (max loss assumes full
width). Anything unrecognizable is grouped as a "recovered" structure that the
monitor can still flatten by the deadline.
"""
import logging
import re
from collections import defaultdict
from datetime import datetime

from models import Leg, OpenStructure

log = logging.getLogger("recover")

_OCC = re.compile(r"^([A-Z.]{1,6})(\d{6})([CP])(\d{8})$")


def _parse_occ(symbol: str):
    m = _OCC.match(symbol)
    if not m:
        return None
    root, ymd, cp, strike = m.groups()
    return {
        "underlying": root,
        "expiry": f"20{ymd[0:2]}-{ymd[2:4]}-{ymd[4:6]}",
        "type": "call" if cp == "C" else "put",
        "strike": int(strike) / 1000.0,
    }


def _classify(legs):
    """(strategy, is_credit, width, short_strikes) from parsed legs.
    legs: list of dicts with type/strike/side."""
    shorts = [l for l in legs if l["side"] == "sell"]
    longs = [l for l in legs if l["side"] == "buy"]
    puts = [l for l in legs if l["type"] == "put"]
    calls = [l for l in legs if l["type"] == "call"]
    short_strikes = sorted(l["strike"] for l in shorts)

    if len(legs) == 4 and len(puts) == 2 and len(calls) == 2 and len(shorts) == 2:
        width = max(abs(puts[0]["strike"] - puts[1]["strike"]),
                    abs(calls[0]["strike"] - calls[1]["strike"]))
        return "iron_condor", True, width, short_strikes
    if len(legs) == 2 and len(shorts) == 1 and len(longs) == 1 \
            and legs[0]["type"] == legs[1]["type"]:
        s, b = shorts[0], longs[0]
        width = abs(s["strike"] - b["strike"])
        if s["type"] == "put":
            strat = "put_credit_spread" if s["strike"] > b["strike"] else "bear_put_debit_spread"
        else:
            strat = "call_credit_spread" if s["strike"] < b["strike"] else "bull_call_debit_spread"
        return strat, strat.endswith("credit_spread"), width, short_strikes
    return None, len(shorts) >= len(longs), 0.0, short_strikes


def rebuild_structures(positions) -> list[OpenStructure]:
    """Group Alpaca option positions into OpenStructures. Equity positions and
    unparseable symbols are ignored (journaled by the caller via the count)."""
    groups = defaultdict(list)
    for p in positions:
        info = _parse_occ(p.get("symbol", ""))
        if not info:
            continue
        qty = int(float(p.get("qty", 0)))
        if qty == 0:
            continue
        info["side"] = "buy" if qty > 0 else "sell"
        info["qty"] = abs(qty)
        info["avg"] = float(p.get("avg_entry_price") or 0)
        info["symbol"] = p["symbol"]
        groups[(info["underlying"], info["expiry"])].append(info)

    out = []
    for (underlying, expiry), legs in sorted(groups.items()):
        strategy, is_credit, width, short_strikes = _classify(legs)
        qty = min(l["qty"] for l in legs)
        # Per-share net at entry: credit received or debit paid (both positive).
        raw = sum(l["avg"] for l in legs if l["side"] == "sell") \
            - sum(l["avg"] for l in legs if l["side"] == "buy")
        entry_net = round(abs(raw), 2)
        if is_credit and width:
            # credit spread / condor: worst case = width - credit received
            max_loss_total = round(max(width - entry_net, 0.01) * 100 * qty, 2)
        elif is_credit:
            # width unknown (unrecognized group): assume a full $5 wing
            max_loss_total = round(max(5.0 - entry_net, 0.01) * 100 * qty, 2)
        else:
            # debit: worst case = premium paid
            max_loss_total = round(entry_net * 100 * qty, 2)
        out.append(OpenStructure(
            structure_id=f"{underlying}-{strategy or 'unknown'}-{expiry}-recovered",
            strategy=strategy or "iron_condor",  # placeholder; exits still work on price
            underlying=underlying,
            expiry=expiry,
            legs=[Leg(symbol=l["symbol"], side=l["side"], ratio_qty=1) for l in legs],
            qty=qty,
            entry_net=entry_net,
            is_credit=is_credit,
            width=width,
            max_loss_total=max_loss_total,
            short_strikes=short_strikes,
            opened_at=datetime.now().isoformat(timespec="seconds"),
            status="open",
        ))
        if strategy is None:
            log.warning("recovered unrecognized leg group on %s %s (%d legs); "
                        "treating conservatively", underlying, expiry, len(legs))
    return out


def reconcile(client, state, journal):
    """On boot: if local state lost track of positions the broker still holds,
    rebuild. Never overwrites a state file that already knows about them."""
    known = {l.symbol for s in state.structures() if s.status != "closed"
             for l in s.legs}
    positions = [p for p in client.positions()
                 if _parse_occ(p.get("symbol", ""))]
    unknown = [p for p in positions if p["symbol"] not in known]
    if not unknown:
        return
    rebuilt = rebuild_structures(unknown)
    structs = state.structures() + rebuilt
    state.save_structures(structs)
    journal.event("state_recovered", {
        "reason": "broker positions not in local state (fresh disk?)",
        "structures": [s.structure_id for s in rebuilt],
    })
    log.warning("recovered %d structure(s) from broker positions", len(rebuilt))
