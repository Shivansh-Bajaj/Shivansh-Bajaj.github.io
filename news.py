"""News layer built on Alpaca's free News API (Benzinga content).

Division of labor -- the whole design in one line:
  the LLM *interprets* headlines (direction, relevance, staleness);
  deterministic code only *counts* them (storm gate) and never guesses sentiment.

Why news is an input layer here, not a strategy: reacting to headlines faster
than the market is a latency race we lose by seconds; what survives at our
speed is interpretation (regime confirmation, satellite direction, knowing
when to abstain). See STRATEGY.md for the full reasoning.
"""
import logging
from collections import Counter
from datetime import datetime, timedelta, timezone

import config as cfg

log = logging.getLogger("news")


def fetch_recent_news(client, symbols, hours_back=None, limit=50):
    hours = hours_back if hours_back is not None else cfg.NEWS_LOOKBACK_HOURS
    start = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return client.news(symbols=symbols, start=start, limit=limit)


def build_digest(items, max_items=None, now_utc=None):
    """Compact, LLM-ready digest + per-symbol headline counts for the storm gate.

    Returns (digest: list[dict], counts_30m: dict[symbol, int]).
    Tolerates missing/garbage fields; dedupes repeated headlines.
    """
    max_items = max_items if max_items is not None else cfg.NEWS_DIGEST_MAX
    now_utc = now_utc or datetime.now(timezone.utc)
    cutoff = now_utc - timedelta(minutes=cfg.NEWS_STORM_WINDOW_MIN)
    seen, digest = set(), []
    counts_30m = Counter()
    for it in items or []:
        if not isinstance(it, dict):
            continue
        headline = (it.get("headline") or "").strip()
        if not headline or headline.lower() in seen:
            continue
        seen.add(headline.lower())
        syms = [s for s in (it.get("symbols") or []) if isinstance(s, str)]
        created = it.get("created_at") or ""
        ts = None
        try:
            ts = datetime.fromisoformat(str(created).replace("Z", "+00:00"))
        except ValueError:
            pass
        if ts is not None and ts >= cutoff:
            for s in syms:
                counts_30m[s] += 1
        if len(digest) < max_items:
            digest.append({
                "at_utc": str(created),
                "symbols": syms[:6],
                "headline": headline[:160],
                "source": it.get("source") or "",
            })
    return digest, dict(counts_30m)


def storm_symbols(counts_30m: dict) -> set:
    """Deterministic gate: symbols with unusual headline flow right now.

    A storm means 'something is happening that we have not priced' -- the agent
    stands down on NEW entries in that name and lets the Strategist read the
    tape next cycle instead of guessing into a fast market.
    """
    hot = set()
    for sym, n in (counts_30m or {}).items():
        if not cfg.NEWS_STORM_APPLIES_TO_ETF and sym in cfg.CORE_UNDERLYINGS:
            continue
        if n >= cfg.NEWS_STORM_COUNT:
            hot.add(sym)
    return hot
