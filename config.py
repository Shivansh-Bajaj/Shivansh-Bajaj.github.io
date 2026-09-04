"""All tunable parameters in one place. Times are US/Eastern (ET).

The Risk Officer enforces everything in the RISK section deterministically --
the LLM cannot override any of it.
"""
import os
from datetime import datetime, date
from zoneinfo import ZoneInfo

# Load .env before any os.getenv below; config is imported earlier than
# main's own load_dotenv() call, so it must happen here too (idempotent).
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:  # offline test envs without python-dotenv still work
    pass

ET = ZoneInfo("America/New_York")

# --- Environment (.env) --- every env var the agent reads, in one place ---
ALPACA_API_KEY_ID = os.getenv("ALPACA_API_KEY_ID", "")
ALPACA_API_SECRET_KEY = os.getenv("ALPACA_API_SECRET_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
ALPACA_TRADING_BASE = os.getenv("ALPACA_TRADING_BASE", "https://paper-api.alpaca.markets")
ALPACA_DATA_BASE = os.getenv("ALPACA_DATA_BASE", "https://data.alpaca.markets")

# --- Universe ---
# SPY/QQQ have daily expiries; IWM/DIA list Mon/Wed/Fri weeklies, handled by
# the Scout's empty-chain expiry walk-back.
CORE_UNDERLYINGS = ["SPY", "QQQ", "IWM", "DIA"]
SATELLITE_WATCHLIST = ["NVDA", "AAPL", "MSFT", "META", "AMZN", "TSLA",
                       "GOOGL", "AVGO", "AMD"]

# --- Hackathon window (equity measured as of EOD Thu Sep 3 per organizers) ---
LAST_CREDIT_EXPIRY = date(2026, 9, 3)   # credit structures must expire by the measurement day
LAST_DEBIT_EXPIRY = date(2026, 9, 4)    # debit satellites may use Friday weeklies (force-closed Thu anyway)
NO_NEW_ENTRIES_AFTER = datetime(2026, 9, 3, 14, 0, tzinfo=ET)
DERISK_DEADLINE = datetime(2026, 9, 3, 15, 30, tzinfo=ET)   # close EVERYTHING after this

# --- Risk limits ---
MAX_RISK_PER_TRADE_PCT = 1.0      # max loss of one structure, % of equity
MAX_TOTAL_OPEN_RISK_PCT = 20.0    # sum of max losses across open structures, % of equity
MAX_OPEN_STRUCTURES = 10
MAX_SATELLITE_STRUCTURES = 2
DAILY_LOSS_HALT_PCT = 2.5         # daily drawdown that stops NEW entries for the day
EQUITY_KILL_FLOOR = 92_000.0      # flatten everything and halt permanently
MIN_CREDIT_ABS = 0.15             # skip candidates offering < $0.15 credit
MIN_CREDIT_FRAC_OF_WIDTH = 0.08   # ...or < 8% of spread width

# --- Structure construction ---
SHORT_DELTA_RANGE = (0.15, 0.25)  # target |delta| band for short strikes
SPREAD_WIDTH = {"SPY": 5.0, "QQQ": 5.0, "IWM": 3.0, "DIA": 4.0, "DEFAULT": 5.0}
SATELLITE_WIDTH = {"DEFAULT": 5.0}
# If greeks are missing (common on 0DTE), fall back to %-OTM by days-to-expiry:
OTM_PCT_FALLBACK = {0: 0.005, 1: 0.008, 2: 0.011, 3: 0.013}
MAX_QUOTE_SPREAD_FRAC = 0.35      # skip contracts with (ask-bid)/mid wider than this
CHAIN_STRIKE_BAND = 0.05          # pull strikes within +/-5% of spot
MIN_SATELLITE_MOMENTUM = 1.0      # need >=1% blended momentum to open a satellite
# Multiplier on the momentum score per symbol: wins near-ties for the most
# liquid option markets (tighter spreads, better fills); never overrides a
# clearly stronger mover.
SATELLITE_LIQUIDITY_WEIGHT = {"NVDA": 1.1, "TSLA": 1.1}

# --- Exit management ---
CREDIT_PROFIT_TAKE = 0.35   # buy back when cost <= 65% of credit received (take profit earlier)
CREDIT_STOP_MULT = 1.60     # close when cost >= 1.6x credit received (tighter stop)
DEBIT_PROFIT_TAKE = 0.40    # close when value >= 1.4x debit paid (take profit earlier)
DEBIT_STOP = 0.35           # close when value <= 0.65x debit paid (tighter stop)
EXPIRY_DAY_CLOSE_ET = (15, 0)   # from 3:00pm ET on expiry day, close credit structs near short strike
SHORT_STRIKE_BUFFER = 0.003     # "near" = within 0.3% of spot

# --- Event blackouts: no NEW entries from T-45min to T+15min ---
EVENT_BLACKOUTS = [
    (datetime(2026, 9, 1, 10, 0, tzinfo=ET), "ISM Manufacturing PMI"),
    (datetime(2026, 9, 2, 10, 0, tzinfo=ET), "JOLTS job openings"),
    (datetime(2026, 9, 3, 8, 30, tzinfo=ET), "Initial jobless claims"),
    (datetime(2026, 9, 3, 10, 0, tzinfo=ET), "ISM Services PMI"),
    (datetime(2026, 9, 4, 8, 30, tzinfo=ET), "Nonfarm payrolls"),
]
BLACKOUT_BEFORE_MIN = 45
BLACKOUT_AFTER_MIN = 15

# --- Scheduling ---
DECISION_MINUTES = (5, 15, 25, 35, 45, 55)   # decision pass every 10 minutes
FIRST_DECISION_ET = (9, 45)       # skip the chaotic opening 15 minutes
LAST_DECISION_ET = (15, 15)
MONITOR_INTERVAL_SEC = 300
LOOP_SLEEP_SEC = 20

# --- News layer (Alpaca News API, Benzinga content, free tier) ---
NEWS_LOOKBACK_HOURS = 18
NEWS_DIGEST_MAX = 15              # headlines shown to the Strategist per cycle
NEWS_STORM_WINDOW_MIN = 30
NEWS_STORM_COUNT = 5              # headlines on one symbol in the window => stand down on it
NEWS_STORM_APPLIES_TO_ETF = False # SPY/QQQ macro flow is handled by scheduled blackouts + LLM

# --- LLM (see https://ai.google.dev/gemini-api/docs/models for current model names) ---
LLM_MODEL = os.getenv("LLM_MODEL", "gemini-3.7-flash")
# Tried in order when a call fails (429/503 high demand, truncated JSON, ...).
LLM_FALLBACK_MODELS = [
    m.strip() for m in
    os.getenv(
        "LLM_FALLBACK_MODELS",
        "gemini-3.5-flash,gemini-3.5-flash-lite,gemini-3-flash-preview,"
        "gemini-2.5-flash,gemini-3.1-flash-lite",
    ).split(",")
    if m.strip()
]
LLM_MODELS = [LLM_MODEL] + [m for m in LLM_FALLBACK_MODELS if m != LLM_MODEL]
# Gemini flash "thinks" out of the same output budget; 1500 truncated real runs.
LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_OUTPUT_TOKENS", "8000"))
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.2"))
# top_p / top_k: unset by default so the model's own defaults apply.
LLM_TOP_P = float(os.environ["LLM_TOP_P"]) if os.getenv("LLM_TOP_P") else None
LLM_TOP_K = int(os.environ["LLM_TOP_K"]) if os.getenv("LLM_TOP_K") else None

# --- Misc ---
DATA_FEED = "indicative"          # or "opra" if you have Algo Trader Plus
STATE_DIR = "state"
JOURNAL_DIR = "journal"
