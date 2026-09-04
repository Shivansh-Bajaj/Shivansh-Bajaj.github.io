"""Read-only web dashboard for Visheshak.

Serves a single-page overview (strategy explanation + live board) and a JSON
endpoint that proxies Alpaca on every request -- active trades are never
stored; what you see is what the broker reports right now.

Why a tiny stdlib server instead of Flask/FastAPI: no new dependencies, and
the Alpaca keys must stay server-side (the browser never sees them).

Run:  python webui.py          # http://127.0.0.1:8501
      PORT=9000 python webui.py
"""
import csv
import json
import logging
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import recover
import risk_officer as ro
from alpaca_client import AlpacaClient
from utils import mid

log = logging.getLogger("webui")
_HERE = os.path.dirname(os.path.abspath(__file__))
_PAGE = os.path.join(_HERE, "static", "dashboard.html")

client = None  # created in main; module-level so the handler can reach it


def overview() -> dict:
    """One live snapshot: account + open positions, straight from Alpaca."""
    a = client.account()
    positions = client.positions()
    clock = client.clock()
    equity = float(a["equity"])
    return {
        "account": {
            "equity": equity,
            "cash": float(a.get("cash") or 0),
            "options_buying_power": float(a.get("options_buying_power") or 0),
            "daily_pnl_pct": round(ro.daily_pnl_pct(a), 2),
            "daily_pnl_usd": round(equity - float(a.get("last_equity") or equity), 2),
        },
        "market": {
            "is_open": bool(clock.get("is_open")),
            "next_open": clock.get("next_open"),
            "next_close": clock.get("next_close"),
        },
        "positions": [
            {
                "symbol": p.get("symbol"),
                "asset_class": p.get("asset_class"),
                "qty": p.get("qty"),
                "side": p.get("side"),
                "avg_entry": p.get("avg_entry_price"),
                "market_value": p.get("market_value"),
                "unrealized_pl": p.get("unrealized_pl"),
                "unrealized_plpc": p.get("unrealized_plpc"),
            }
            for p in positions
        ],
        "structures": _structures(positions),
        "equity_curve": _equity_curve(),
    }


def _structures(positions) -> list[dict]:
    """Group option legs into spreads/condors and price them at mid, so the
    board shows what actually matters: credit received vs cost to close."""
    out = []
    try:
        structs = recover.rebuild_structures(positions)
        if not structs:
            return out
        legs = [l.symbol for s in structs for l in s.legs]
        quotes = client.latest_option_quotes(legs)
        spots = {}
        for u in {s.underlying for s in structs}:
            try:
                snap = client.stock_snapshots([u]).get(u) or {}
                p = (snap.get("latestTrade") or {}).get("p")
                spots[u] = float(p) if p else None
            except Exception:
                spots[u] = None
        for s in structs:
            raw, missing = 0.0, False
            for l in s.legs:
                m = mid(quotes.get(l.symbol) or {})
                if m is None:
                    missing = True
                    break
                raw += m if l.side == "sell" else -m
            cost = None if missing else round(raw if s.is_credit else -raw, 2)
            if cost is None:
                pnl = None
            elif s.is_credit:
                pnl = round((s.entry_net - cost) * 100 * s.qty, 2)
            else:
                pnl = round((cost - s.entry_net) * 100 * s.qty, 2)
            spot = spots.get(s.underlying)
            dist = None
            if spot and s.short_strikes:
                dist = round(min(abs(spot - k) / spot for k in s.short_strikes) * 100, 2)
            out.append({
                "underlying": s.underlying,
                "strategy": s.strategy,
                "expiry": s.expiry,
                "qty": s.qty,
                "is_credit": s.is_credit,
                "entry_net": s.entry_net,
                "current_cost": cost,
                "pnl": pnl,
                "max_loss": s.max_loss_total,
                "short_strikes": s.short_strikes,
                "spot": spot,
                "short_strike_distance_pct": dist,
            })
    except Exception:
        log.exception("structure grouping failed")
    return out


def _equity_curve(limit=600) -> list[dict]:
    """Recent equity marks from the journal CSV (written by the agent loop)."""
    path = os.path.join(_HERE, "journal", "equity_curve.csv")
    if not os.path.exists(path):
        return []
    try:
        with open(path) as f:
            rows = list(csv.DictReader(f))[-limit:]
        return [{"ts": r["ts_et"], "equity": float(r["equity"])} for r in rows]
    except Exception:
        log.exception("equity curve read failed")
        return []


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802 - http.server API
        try:
            if self.path.split("?")[0] in ("/", "/index.html"):
                with open(_PAGE, "rb") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8")
            elif self.path.split("?")[0] == "/api/overview":
                body = json.dumps(overview()).encode()
                self._send(200, body, "application/json")
            else:
                self._send(404, b'{"error": "not found"}', "application/json")
        except Exception as e:  # surface upstream failures as JSON, keep serving
            log.exception("request failed")
            self._send(502, json.dumps({"error": str(e)}).encode(), "application/json")

    def log_message(self, fmt, *args):
        log.info("%s %s", self.address_string(), fmt % args)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    client = AlpacaClient()
    port = int(os.getenv("PORT", "8501"))
    host = os.getenv("HOST", "127.0.0.1")  # containers set HOST=0.0.0.0
    log.info("Visheshak dashboard on http://%s:%d", host, port)
    ThreadingHTTPServer((host, port), Handler).serve_forever()
