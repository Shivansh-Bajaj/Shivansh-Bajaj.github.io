"""Risk Officer: deterministic guardrails. Pure functions, no I/O, no LLM --
fully unit-testable (see chaos_test.py). Nothing the Strategist says can
override anything here.
"""
from datetime import date, timedelta

import config as cfg


def daily_pnl_pct(account) -> float:
    eq = float(account["equity"])
    last = float(account.get("last_equity") or 0)
    return (eq / last - 1) * 100 if last else 0.0


def active_blackout(now_et):
    for t, label in cfg.EVENT_BLACKOUTS:
        start = t - timedelta(minutes=cfg.BLACKOUT_BEFORE_MIN)
        end = t + timedelta(minutes=cfg.BLACKOUT_AFTER_MIN)
        if start <= now_et <= end:
            return label
    return None


def entry_halts(account, now_et) -> list[str]:
    """Reasons why NO new entries may be opened right now (empty = clear)."""
    reasons = []
    eq = float(account["equity"])
    if eq <= cfg.EQUITY_KILL_FLOOR:
        reasons.append(f"kill floor breached: equity {eq:.0f} <= {cfg.EQUITY_KILL_FLOOR:.0f}")
    dp = daily_pnl_pct(account)
    if dp <= -cfg.DAILY_LOSS_HALT_PCT:
        reasons.append(f"daily loss halt: {dp:.2f}% <= -{cfg.DAILY_LOSS_HALT_PCT}%")
    if now_et >= cfg.NO_NEW_ENTRIES_AFTER:
        reasons.append("past the no-new-entries cutoff before measurement")
    b = active_blackout(now_et)
    if b:
        reasons.append(f"event blackout: {b}")
    return reasons


def validate_entry(decision, cand, account, open_structs, now_et, hot_symbols=frozenset()):
    """Returns (approved_qty, reasons). approved_qty == 0 means vetoed.
    May clamp qty down to fit per-trade and portfolio risk caps.

    hot_symbols: names currently in a news storm (see news.storm_symbols) --
    mechanically blocked, no sentiment judgment involved."""
    eq = float(account["equity"])
    halts = entry_halts(account, now_et)
    if halts:
        return 0, halts
    if cand.underlying in hot_symbols:
        return 0, [f"news storm on {cand.underlying}: unusual headline flow in the last "
                   f"{cfg.NEWS_STORM_WINDOW_MIN} min -- standing down until it settles"]
    if decision.qty < 1:
        return 0, ["requested qty < 1"]

    exp = date.fromisoformat(cand.expiry)
    last_exp = cfg.LAST_CREDIT_EXPIRY if cand.is_credit else cfg.LAST_DEBIT_EXPIRY
    if exp > last_exp:
        return 0, [f"expiry {cand.expiry} beyond allowed {last_exp.isoformat()}"]
    if exp < now_et.date():
        return 0, ["expiry in the past"]

    if cand.is_credit and (cand.net_mid < cfg.MIN_CREDIT_ABS
                           or cand.net_mid < cfg.MIN_CREDIT_FRAC_OF_WIDTH * cand.width):
        return 0, [f"credit {cand.net_mid:.2f} too small for width {cand.width}"]
    if cand.max_loss_per_contract <= 0:
        return 0, ["non-positive max loss (bad candidate math)"]

    live = [s for s in open_structs if s.status != "closed"]
    if len(live) >= cfg.MAX_OPEN_STRUCTURES:
        return 0, [f"max open structures ({cfg.MAX_OPEN_STRUCTURES}) reached"]
    if not cand.is_credit:
        sats = [s for s in live if not s.is_credit]
        if len(sats) >= cfg.MAX_SATELLITE_STRUCTURES:
            return 0, [f"max satellite structures ({cfg.MAX_SATELLITE_STRUCTURES}) reached"]
    for s in live:
        if (s.underlying == cand.underlying and s.expiry == cand.expiry
                and s.strategy == cand.strategy):
            return 0, [f"duplicate: {cand.strategy} on {cand.underlying} {cand.expiry} already open"]

    reasons = []
    per_trade_cap = eq * cfg.MAX_RISK_PER_TRADE_PCT / 100
    qty = min(decision.qty, int(per_trade_cap // cand.max_loss_per_contract))
    if qty < 1:
        return 0, [f"max loss/contract ${cand.max_loss_per_contract:.0f} exceeds "
                   f"per-trade cap ${per_trade_cap:.0f}"]
    if qty < decision.qty:
        reasons.append(f"qty clamped {decision.qty} -> {qty} by per-trade risk cap")

    open_risk = sum(s.max_loss_total for s in live)
    total_cap = eq * cfg.MAX_TOTAL_OPEN_RISK_PCT / 100
    room = total_cap - open_risk
    qty2 = min(qty, int(room // cand.max_loss_per_contract)) if room > 0 else 0
    if qty2 < 1:
        return 0, [f"portfolio risk cap: ${open_risk:.0f} of ${total_cap:.0f} already committed"]
    if qty2 < qty:
        reasons.append(f"qty clamped {qty} -> {qty2} by portfolio risk cap")

    return qty2, reasons or ["approved"]


def exit_reason(struct, close_cost, spot, now_et):
    """Decide whether an open structure must be closed now.

    close_cost (per share): for credit structures, the debit to buy it back;
    for debit structures, the value received if sold. None if quotes missing.
    Returns a reason string, or None to keep holding.
    """
    if now_et >= cfg.DERISK_DEADLINE:
        return "derisk_deadline"

    e = struct.entry_net
    if close_cost is not None and e > 0:
        if struct.is_credit:
            if close_cost <= e * (1 - cfg.CREDIT_PROFIT_TAKE):
                return "profit_take"
            if close_cost >= e * cfg.CREDIT_STOP_MULT:
                return "stop_loss"
        else:
            if close_cost >= e * (1 + cfg.DEBIT_PROFIT_TAKE):
                return "profit_take"
            if close_cost <= e * (1 - cfg.DEBIT_STOP):
                return "stop_loss"

    if struct.is_credit and spot:
        exp = date.fromisoformat(struct.expiry)
        if exp == now_et.date() and (now_et.hour, now_et.minute) >= cfg.EXPIRY_DAY_CLOSE_ET:
            for k in struct.short_strikes:
                near = abs(spot - k) / spot <= cfg.SHORT_STRIKE_BUFFER
                itm = ((struct.strategy == "put_credit_spread" and spot <= k)
                       or (struct.strategy == "call_credit_spread" and spot >= k))
                if near or itm:
                    return "expiry_short_strike_risk"
            if struct.strategy == "iron_condor" and len(struct.short_strikes) >= 2:
                lo, hi = min(struct.short_strikes), max(struct.short_strikes)
                if spot <= lo or spot >= hi:
                    return "expiry_short_strike_risk"
    return None
