"""Thin wrapper over Alpaca's Trading & Market Data REST APIs (paper).

Uses raw REST per the hackathon FAQ (the Trading API is explicitly sufficient;
no unofficial SDKs involved). Transient failures (429/5xx/network) are retried
with exponential backoff; 4xx errors surface immediately.

Multi-leg orders per Alpaca docs: order_class="mleg", each leg carries
symbol/side/ratio_qty/position_intent, and the net limit_price is SIGNED:
positive = debit you pay, negative = credit you receive.
"""
import logging
import time
from datetime import date, timedelta

import httpx

import config as cfg

log = logging.getLogger("alpaca")

TRADING_BASE = cfg.ALPACA_TRADING_BASE
DATA_BASE = cfg.ALPACA_DATA_BASE
RETRYABLE = {429, 500, 502, 503, 504}


class AlpacaError(RuntimeError):
    pass


class AlpacaClient:
    def __init__(self, key_id=None, secret=None, feed=None, timeout=20.0):
        self.key_id = key_id or cfg.ALPACA_API_KEY_ID
        self.secret = secret or cfg.ALPACA_API_SECRET_KEY
        if not self.key_id or not self.secret:
            raise AlpacaError("Set ALPACA_API_KEY_ID and ALPACA_API_SECRET_KEY (see .env.example)")
        self.feed = feed or cfg.DATA_FEED
        self.backoff_base = 1.5
        self._http = httpx.Client(
            timeout=timeout,
            headers={
                "APCA-API-KEY-ID": self.key_id,
                "APCA-API-SECRET-KEY": self.secret,
                "accept": "application/json",
            },
        )

    # ------------------------------------------------------------------ core
    def _req(self, method, url, params=None, json_body=None, retries=3):
        last_err = None
        for attempt in range(retries):
            try:
                r = self._http.request(method, url, params=params, json=json_body)
            except httpx.TransportError as e:
                last_err = AlpacaError(f"network: {e}")
            else:
                if r.status_code < 400:
                    return r.json() if r.text.strip() else {}
                if r.status_code not in RETRYABLE:
                    raise AlpacaError(f"HTTP {r.status_code} {method} {url}: {r.text[:400]}")
                last_err = AlpacaError(f"HTTP {r.status_code} (retryable): {r.text[:200]}")
            if attempt < retries - 1:
                sleep = self.backoff_base * (2 ** attempt)
                log.warning("retrying %s %s in %.1fs (%s)", method, url, sleep, last_err)
                time.sleep(sleep)
        raise last_err

    # --------------------------------------------------------------- trading
    def clock(self):
        return self._req("GET", f"{TRADING_BASE}/v2/clock")

    def account(self):
        return self._req("GET", f"{TRADING_BASE}/v2/account")

    def positions(self):
        return self._req("GET", f"{TRADING_BASE}/v2/positions")

    def open_orders(self):
        return self._req("GET", f"{TRADING_BASE}/v2/orders", params={"status": "open", "limit": 100})

    def get_order(self, order_id):
        return self._req("GET", f"{TRADING_BASE}/v2/orders/{order_id}")

    def cancel_order(self, order_id):
        return self._req("DELETE", f"{TRADING_BASE}/v2/orders/{order_id}")

    def submit_order(self, payload: dict):
        return self._req("POST", f"{TRADING_BASE}/v2/orders", json_body=payload)

    # ----------------------------------------------------------- market data
    def stock_snapshots(self, symbols):
        resp = self._req("GET", f"{DATA_BASE}/v2/stocks/snapshots",
                         params={"symbols": ",".join(symbols)})
        return resp.get("snapshots") or resp

    def stock_daily_bars(self, symbols, days_back=12):
        start = (date.today() - timedelta(days=days_back)).isoformat()
        resp = self._req("GET", f"{DATA_BASE}/v2/stocks/bars", params={
            "symbols": ",".join(symbols), "timeframe": "1Day",
            "start": start, "limit": 1000,
        })
        return resp.get("bars") or {}

    def option_chain(self, underlying, expiration_date=None, type_=None,
                     strike_gte=None, strike_lte=None, max_contracts=600):
        """Latest quote + greeks per contract of `underlying` (paginated)."""
        params = {"feed": self.feed, "limit": 200}
        if expiration_date:
            params["expiration_date"] = expiration_date
        if type_:
            params["type"] = type_
        if strike_gte is not None:
            params["strike_price_gte"] = f"{strike_gte:.2f}"
        if strike_lte is not None:
            params["strike_price_lte"] = f"{strike_lte:.2f}"
        out, token = {}, None
        while True:
            if token:
                params["page_token"] = token
            resp = self._req("GET", f"{DATA_BASE}/v1beta1/options/snapshots/{underlying}",
                             params=params)
            out.update(resp.get("snapshots") or {})
            token = resp.get("next_page_token")
            if not token or len(out) >= max_contracts:
                return out

    def news(self, symbols=None, start=None, limit=50):
        """Alpaca News API (Benzinga content). Free tier: 200 calls/min."""
        params = {"limit": min(limit, 50), "sort": "desc"}
        if symbols:
            params["symbols"] = ",".join(symbols)
        if start:
            params["start"] = start
        resp = self._req("GET", f"{DATA_BASE}/v1beta1/news", params=params)
        return resp.get("news") or []

    def latest_option_quotes(self, symbols):
        quotes = {}
        for i in range(0, len(symbols), 100):
            chunk = symbols[i:i + 100]
            resp = self._req("GET", f"{DATA_BASE}/v1beta1/options/quotes/latest",
                             params={"symbols": ",".join(chunk), "feed": self.feed})
            quotes.update(resp.get("quotes") or {})
        return quotes
