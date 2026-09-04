"""Scout: turns raw Alpaca data into a MarketBrief with pre-priced candidates.

Robustness design: the LLM never invents strikes or prices. The Scout builds
fully specified candidates from live quotes; the Strategist can only select
among them (or abstain); the Risk Officer can only shrink or veto.
"""
import logging
from datetime import timedelta

import config as cfg
from models import Candidate, Leg
from utils import mid, now_et, parse_occ, r2

log = logging.getLogger("scout")


# --------------------------------------------------------------- underlyings
def _underlying_stats(client, symbols):
    snaps = client.stock_snapshots(symbols)
    bars = client.stock_daily_bars(symbols)
    out = {}
    for s in symbols:
        snap = snaps.get(s) or {}
        lt = snap.get("latestTrade") or {}
        prev = snap.get("prevDailyBar") or {}
        spot = float(lt["p"]) if lt.get("p") else None
        series = sorted(bars.get(s) or [], key=lambda b: b["t"], reverse=True)
        day_chg = five_day = range_pos = None
        if spot and prev.get("c"):
            day_chg = (spot / float(prev["c"]) - 1) * 100
        if spot and len(series) >= 6:
            five_day = (spot / float(series[5]["c"]) - 1) * 100
        if spot and series:
            hi = max(float(b["h"]) for b in series[:6])
            lo = min(float(b["l"]) for b in series[:6])
            if hi > lo:
                range_pos = (spot - lo) / (hi - lo)
        out[s] = {
            "spot": spot,
            "day_chg_pct": r2(day_chg),
            "five_day_chg_pct": r2(five_day),
            "five_day_range_pos": r2(range_pos),
        }
    return out


# --------------------------------------------------------------------- chain
def _pick_expiry(today, last_allowed):
    """Latest weekday <= last_allowed that is >= today."""
    d = last_allowed
    while d >= today:
        if d.weekday() < 5:
            return d
        d -= timedelta(days=1)
    return None


def _chain(client, underlying, expiry_iso, spot):
    lo = spot * (1 - cfg.CHAIN_STRIKE_BAND)
    hi = spot * (1 + cfg.CHAIN_STRIKE_BAND)
    return client.option_chain(underlying, expiration_date=expiry_iso,
                               strike_gte=lo, strike_lte=hi)


def _leg_view(sym, snap):
    q = snap.get("latestQuote") or {}
    greeks = snap.get("greeks") or {}
    _, _, right, strike = parse_occ(sym)
    iv = snap.get("impliedVolatility")
    if iv is None:
        iv = greeks.get("implied_volatility")
    return {
        "symbol": sym, "strike": strike, "right": right,
        "mid": mid(q), "bid": q.get("bp"), "ask": q.get("ap"),
        "delta": greeks.get("delta"), "iv": iv,
    }


def _quote_ok(v):
    if v["mid"] is None or v["mid"] <= 0.02:
        return False
    try:
        spread = float(v["ask"]) - float(v["bid"])
    except (TypeError, ValueError):
        return False
    return spread <= max(0.10, cfg.MAX_QUOTE_SPREAD_FRAC * v["mid"])


def _atm_iv(views, spot):
    best_d, iv = None, None
    for v in views.values():
        if v["iv"]:
            d = abs(v["strike"] - spot)
            if best_d is None or d < best_d:
                best_d, iv = d, float(v["iv"])
    return iv


# ---------------------------------------------------------------- structures
def _short_by_delta(views_by_strike, side, spot, dte):
    """Pick the short strike: |delta| in SHORT_DELTA_RANGE, else %-OTM fallback
    (greeks are often missing on 0DTE contracts)."""
    lo, hi = cfg.SHORT_DELTA_RANGE
    target = (lo + hi) / 2
    pool = []
    for v in views_by_strike.values():
        if v["delta"] is None or not _quote_ok(v):
            continue
        d = abs(float(v["delta"]))
        if lo <= d <= hi:
            pool.append((abs(d - target), v["strike"], v))
    if pool:
        return min(pool)[2]
    pct = cfg.OTM_PCT_FALLBACK.get(min(dte, 3), 0.013)
    want = spot * (1 - pct) if side == "put" else spot * (1 + pct)
    ok = [v for v in views_by_strike.values() if _quote_ok(v)]
    otm = [v for v in ok if (v["strike"] <= want if side == "put" else v["strike"] >= want)]
    cands = otm or ok
    if not cands:
        return None
    return min(cands, key=lambda v: abs(v["strike"] - want))


def _find_strike(views_by_strike, target):
    v = views_by_strike.get(target) or views_by_strike.get(round(target, 2))
    return v


def _credit_vertical(u, expiry, views_by_strike, right, spot, dte, width, equity):
    side = "put" if right == "P" else "call"
    short = _short_by_delta(views_by_strike, side, spot, dte)
    if not short:
        return None
    tgt = short["strike"] - width if right == "P" else short["strike"] + width
    long_ = _find_strike(views_by_strike, tgt)
    if not long_ or long_["mid"] is None:
        return None
    credit = round(short["mid"] - long_["mid"], 2)
    if credit < cfg.MIN_CREDIT_ABS or credit < cfg.MIN_CREDIT_FRAC_OF_WIDTH * width:
        return None
    max_loss = round((width - credit) * 100, 2)
    strat = "put_credit_spread" if right == "P" else "call_credit_spread"
    tag = "pcs" if right == "P" else "ccs"
    return Candidate(
        id=f"{u}-{tag}-{expiry.isoformat()}",
        strategy=strat, underlying=u, expiry=expiry.isoformat(),
        legs=[Leg(symbol=short["symbol"], side="sell"),
              Leg(symbol=long_["symbol"], side="buy")],
        net_mid=credit, is_credit=True, width=width,
        max_loss_per_contract=max_loss,
        max_gain_per_contract=round(credit * 100, 2),
        short_strikes=[short["strike"]],
        short_deltas=[r2(short["delta"])] if short["delta"] is not None else [],
        max_qty_at_risk_cap=max(0, int((equity * cfg.MAX_RISK_PER_TRADE_PCT / 100) // max_loss)),
        notes=(f"sell {short['strike']}{right} @{short['mid']:.2f} / "
               f"buy {long_['strike']}{right} @{long_['mid']:.2f}"),
    )


def _iron_condor(u, pcs, ccs, equity):
    credit = round(pcs.net_mid + ccs.net_mid, 2)
    width = max(pcs.width, ccs.width)
    max_loss = round(width * 100 - credit * 100, 2)
    if max_loss <= 0:
        return None
    return Candidate(
        id=f"{u}-ic-{pcs.expiry}",
        strategy="iron_condor", underlying=u, expiry=pcs.expiry,
        legs=pcs.legs + ccs.legs,
        net_mid=credit, is_credit=True, width=width,
        max_loss_per_contract=max_loss,
        max_gain_per_contract=round(credit * 100, 2),
        short_strikes=pcs.short_strikes + ccs.short_strikes,
        short_deltas=pcs.short_deltas + ccs.short_deltas,
        max_qty_at_risk_cap=max(0, int((equity * cfg.MAX_RISK_PER_TRADE_PCT / 100) // max_loss)),
        notes=f"{pcs.notes} + {ccs.notes}",
    )


def _satellite(client, stats, today, equity, notes):
    scored = []
    for s in cfg.SATELLITE_WATCHLIST:
        st = stats.get(s) or {}
        if st.get("five_day_chg_pct") is None or st.get("day_chg_pct") is None or not st.get("spot"):
            continue
        raw = 0.6 * st["five_day_chg_pct"] + 0.4 * st["day_chg_pct"]
        # Liquidity tie-break: nudge the most liquid option markets ahead on
        # near-ties without letting them override a genuinely stronger mover.
        scored.append((raw * cfg.SATELLITE_LIQUIDITY_WEIGHT.get(s, 1.0), s))
    if not scored:
        return None
    scored.sort()
    low_score, low_name = scored[0]
    top_score, top_name = scored[-1]
    pick, score = (top_name, top_score) if abs(top_score) >= abs(low_score) else (low_name, low_score)
    if abs(score) < cfg.MIN_SATELLITE_MOMENTUM:
        return None
    bull = score > 0
    spot = stats[pick]["spot"]
    expiry = _pick_expiry(today, cfg.LAST_DEBIT_EXPIRY)
    if not expiry:
        return None
    right = "C" if bull else "P"
    chain = _chain(client, pick, expiry.isoformat(), spot)
    views = {}
    for sym, snap in chain.items():
        v = _leg_view(sym, snap)
        if v["right"] == right:
            views[v["strike"]] = v
    ok = [v for v in views.values() if _quote_ok(v)]
    if not ok:
        notes.append(f"satellite {pick}: no usable {right} quotes for {expiry}")
        return None
    near = min(ok, key=lambda v: abs(v["strike"] - spot))
    width = cfg.SATELLITE_WIDTH["DEFAULT"]
    tgt = near["strike"] + width if bull else near["strike"] - width
    far = _find_strike(views, tgt)
    if not far or far["mid"] is None:
        beyond = [v for v in views.values()
                  if (v["strike"] >= tgt if bull else v["strike"] <= tgt) and v["mid"] is not None]
        if not beyond:
            return None
        far = min(beyond, key=lambda v: abs(v["strike"] - tgt))
        width = abs(far["strike"] - near["strike"])
    debit = round(near["mid"] - far["mid"], 2)
    if debit <= 0.05 or debit >= width:
        return None
    strat = "bull_call_debit_spread" if bull else "bear_put_debit_spread"
    max_loss = round(debit * 100, 2)
    return Candidate(
        id=f"{pick}-{'bull' if bull else 'bear'}-{expiry.isoformat()}",
        strategy=strat, underlying=pick, expiry=expiry.isoformat(),
        legs=[Leg(symbol=near["symbol"], side="buy"),
              Leg(symbol=far["symbol"], side="sell")],
        net_mid=debit, is_credit=False, width=width,
        max_loss_per_contract=max_loss,
        max_gain_per_contract=round((width - debit) * 100, 2),
        short_strikes=[],
        max_qty_at_risk_cap=max(0, int((equity * cfg.MAX_RISK_PER_TRADE_PCT / 100) // max_loss)),
        notes=(f"blended momentum {score:+.1f}%: buy {near['strike']}{right} "
               f"@{near['mid']:.2f} / sell {far['strike']}{right} @{far['mid']:.2f}"),
    )


# ---------------------------------------------------------------------- main
def get_market_brief(client, equity: float) -> dict:
    today = now_et().date()
    notes = []
    stats = _underlying_stats(client, cfg.CORE_UNDERLYINGS + cfg.SATELLITE_WATCHLIST)
    candidates = []

    base_expiry = _pick_expiry(today, cfg.LAST_CREDIT_EXPIRY)
    if not base_expiry:
        notes.append("no valid credit expiry left in the hackathon window")
    else:
        for u in cfg.CORE_UNDERLYINGS:
            spot = stats.get(u, {}).get("spot")
            if not spot:
                notes.append(f"no spot for {u}")
                continue
            # Not every ETF lists every weekday (IWM/DIA are Mon/Wed/Fri):
            # walk the expiry back a weekday at a time until a chain exists.
            expiry, chain = base_expiry, {}
            try:
                for _ in range(3):
                    chain = _chain(client, u, expiry.isoformat(), spot)
                    if chain:
                        break
                    prev = _pick_expiry(today, expiry - timedelta(days=1))
                    if not prev:
                        break
                    notes.append(f"{u}: no {expiry} chain, trying {prev}")
                    expiry = prev
            except Exception as e:
                notes.append(f"chain fetch failed for {u}: {e}")
                continue
            if not chain:
                notes.append(f"{u}: no listed chain at or before {base_expiry}")
                continue
            dte = (expiry - today).days
            puts, calls = {}, {}
            for sym, snap in chain.items():
                v = _leg_view(sym, snap)
                (puts if v["right"] == "P" else calls)[v["strike"]] = v
            iv = _atm_iv(calls or puts, spot)
            stats[u]["atm_iv_pct"] = r2(iv * 100) if iv else None
            width = cfg.SPREAD_WIDTH.get(u, cfg.SPREAD_WIDTH["DEFAULT"])
            pcs = _credit_vertical(u, expiry, puts, "P", spot, dte, width, equity)
            ccs = _credit_vertical(u, expiry, calls, "C", spot, dte, width, equity)
            for c in (pcs, ccs):
                if c:
                    candidates.append(c)
            if pcs and ccs:
                ic = _iron_condor(u, pcs, ccs, equity)
                if ic:
                    candidates.append(ic)
            if not (pcs or ccs):
                notes.append(f"no viable credit structures for {u} {expiry} "
                             f"(thin quotes or credit below minimum)")

    try:
        sat = _satellite(client, stats, today, equity, notes)
        if sat:
            candidates.append(sat)
    except Exception as e:
        notes.append(f"satellite scan failed: {e}")

    return {
        "asof_et": now_et().isoformat(),
        "today": today.isoformat(),
        "underlyings": stats,
        "candidates": [c.model_dump() for c in candidates],
        "notes": notes,
    }
