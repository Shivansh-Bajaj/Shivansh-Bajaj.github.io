from typing import Literal, Optional

from pydantic import BaseModel, Field

Strategy = Literal[
    "put_credit_spread",
    "call_credit_spread",
    "iron_condor",
    "bull_call_debit_spread",
    "bear_put_debit_spread",
]


class Leg(BaseModel):
    symbol: str          # OCC contract symbol, e.g. SPY260903P00640000
    side: Literal["buy", "sell"]
    ratio_qty: int = 1


class Candidate(BaseModel):
    """A fully specified, pre-priced trade built by the Scout from live quotes.

    The LLM can only SELECT among candidates (or abstain); it never invents
    strikes, symbols, or prices.
    """
    id: str
    strategy: Strategy
    underlying: str
    expiry: str                      # YYYY-MM-DD
    legs: list[Leg]
    net_mid: float                   # per-share: credit received (+) or debit paid (+)
    is_credit: bool
    width: float
    max_loss_per_contract: float     # dollars
    max_gain_per_contract: float     # dollars
    short_strikes: list[float] = []
    short_deltas: list[float] = []
    max_qty_at_risk_cap: int = 0     # sizing anchor at MAX_RISK_PER_TRADE_PCT
    notes: str = ""


class Decision(BaseModel):
    action: Literal["enter", "abstain"]
    candidate_id: Optional[str] = None
    qty: int = 0
    rationale: str = ""
    confidence: float = Field(default=0.5, ge=0, le=1)


class StrategistOutput(BaseModel):
    regime: Literal["uptrend", "downtrend", "rangebound", "high_uncertainty"]
    market_view: str
    decisions: list[Decision] = []


class OpenStructure(BaseModel):
    structure_id: str
    strategy: Strategy
    underlying: str
    expiry: str
    legs: list[Leg]                  # opening sides
    qty: int
    entry_net: float                 # per-share credit(+) or debit(+) actually filled
    is_credit: bool
    width: float
    max_loss_total: float            # dollars, all contracts
    short_strikes: list[float] = []
    opened_at: str = ""
    status: Literal["open", "closing", "closed"] = "open"
    close_reason: Optional[str] = None
