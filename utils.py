import json
import os
import tempfile
from datetime import datetime, date
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


def now_et() -> datetime:
    return datetime.now(tz=ET)


def parse_occ(symbol: str):
    """'SPY260903P00640000' -> ('SPY', date(2026,9,3), 'P', 640.0)"""
    strike = int(symbol[-8:]) / 1000.0
    right = symbol[-9]
    d = symbol[-15:-9]
    exp = date(2000 + int(d[:2]), int(d[2:4]), int(d[4:6]))
    return symbol[:-15], exp, right, strike


def mid(quote: dict):
    """Midpoint of an Alpaca quote ({'bp': bid, 'ap': ask}); None if unusable."""
    try:
        bp, ap = float(quote.get("bp") or 0), float(quote.get("ap") or 0)
    except (TypeError, ValueError):
        return None
    if bp <= 0 or ap <= 0 or ap < bp:
        return None
    return (bp + ap) / 2.0


def r2(x):
    return None if x is None else round(float(x), 2)


def atomic_write_json(path: str, obj):
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d)
    with os.fdopen(fd, "w") as f:
        json.dump(obj, f, indent=2, default=str)
    os.replace(tmp, path)


def read_json(path: str, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default
