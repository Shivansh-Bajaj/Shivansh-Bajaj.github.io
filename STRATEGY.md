# Strategy Design Notes

This document explains *why* the agent trades the way it does — both as a
learning record and as the rationale judges can evaluate.

## 1. The edge: the volatility risk premium

Index options persistently trade at implied volatilities above the volatility
that subsequently realizes. Buyers pay that markup for insurance and
convexity; sellers collect it as compensation for carrying tail risk. Over
short horizons on liquid indices (SPY, QQQ), systematically selling
out-of-the-money premium with **defined risk** harvests this premium while a
long wing caps the tail. In a 3.5-day contest, theta on 1–3 DTE structures is
the fastest-compounding, most repeatable edge available — far more reliable
than trying to out-predict direction over hours.

The honest caveat: over 3.5 days, variance dominates expectation. The risk
framework exists precisely because a good week proves little and one
unmanaged loss can erase everything. The contest also scores workflow
quality, which rewards the discipline itself.

## 2. Structure playbook

| Regime read | Structure | Why |
|---|---|---|
| Rangebound | Iron condor | Collect premium on both sides; profits from time passing inside the range |
| Uptrend | Put credit spread | Sell downside insurance the trend keeps devaluing; direction-agnostic above the short strike |
| Downtrend | Call credit spread | Mirror image |
| Strong single-name momentum | Debit vertical (satellite) | Small, defined-risk directional bet where premium *buying* is justified by trend |

Short strikes target **15–25 delta**: roughly an 75–85% chance of expiring
worthless, with enough credit to matter. Closer to the money the win rate
drops fast; further out the credit stops paying for the risk. Wings are $5
wide on SPY/QQQ — tight enough to keep per-contract risk near $350–450,
which makes 1%-of-equity sizing land on clean contract counts.

## 3. The math of one trade

Example: SPY at 645, sell the 640/635 put credit spread for a $1.00 credit.

- Max gain: $100/contract (keep the credit).
- Max loss: $400/contract (width $5 − credit $1, ×100).
- Per-trade cap at 1% of $100k = $1,000 → **2 contracts** ($800 max loss).

With exits at 50% profit-take and 2× credit stop, a typical winner makes
~$50/contract and a typical stopped loser costs ~$100/contract (the stop
triggers long before max loss). Breakeven then needs roughly a 67% win rate;
short strikes at ~18 delta win closer to 80% of the time when realized vol
stays at or below implied. That gap *is* the edge — and it is why the stop
matters more than the entry: without it, one pin at max loss undoes eight
winners.

## 4. Position and portfolio sizing

- **1% max loss per structure**: one bad trade is noise, not news.
- **10% total open risk**: even five simultaneous max-loss events (roughly a
  market crash) leave the account above the kill floor.
- **Kill floor $92k**: flatten and stop — a hackathon account down 8% has no
  business averaging down.
- **Daily −2.5% halt on new entries**: bad days correlate; stop feeding them.

## 5. Exit design

- **Profit-take at 50% of credit**: the second half of the credit takes
  longer to earn and carries the same gamma risk as the first. Recycling
  capital into fresh structures earns theta faster per unit of risk.
- **Stop at 2× credit**: caps the realized loss near 1–1.5× the credit
  received, keeping the win/loss ratio compatible with an ~80% win rate.
- **Expiry-day rule (3:00pm ET)**: pin risk — the underlying hovering at the
  short strike into the close — turns a defined-risk trade into an assignment
  lottery. Anything ITM or within 0.3% of a short strike gets closed.
- **Thursday endgame**: no new entries after 2:00pm ET, flat by 3:30pm ET.
  Scoring is EOD Thursday; Friday's NFP print is someone else's problem.

## 6. Event risk

Scheduled macro prints (ISM Manufacturing Tue, JOLTS Wed, claims + ISM
Services Thu) gap the market faster than a 5-minute monitor can react.
Short-premium entries are blacked out 45 minutes before through 15 minutes
after each release. Existing positions ride through with their stops — the
defined-risk wings are the ultimate backstop.

## 7. News: an input layer, not a strategy

Two tempting news-based ideas were considered and rejected as the *core*:

- **True arbitrage** (put-call parity gaps, cross-venue discrepancies) is a
  latency race lost to HFT firms in microseconds. On a paper account it is
  worse than unprofitable: the free indicative feed occasionally shows stale
  or wide quotes, and the fill simulator will happily "monetize" mispricings
  that do not exist in the real order book. That is a bug dressed as an edge,
  and judges will read it as one.
- **News-reaction trading** fails differently: the raw speed edge is gone in
  under a second, and over 3.5 days the watchlist may produce only a handful
  of genuinely tradable headlines — far too few samples for a noisy edge to
  show up, while the premium core earns theta every day regardless.

What survives at our latency is **interpretation**, which happens to be the
one thing an LLM does better than any technical indicator. So news enters
the agent in three places:

1. The **Strategist** reads a compact digest each cycle (Alpaca's free
   Benzinga-powered News API — same keys, no scraping) to confirm or veto its
   regime read, direct or veto the satellite trade, or abstain on fresh
   ambiguity. Headlines older than ~2 hours are treated as already priced in.
2. The **Risk Officer** applies a deterministic **storm gate**: an unusual
   burst of headlines on one name inside 30 minutes mechanically blocks new
   entries there. Code counts; only the LLM interprets. The safety layer
   never guesses sentiment.
3. The **Journal** records when a headline drove a decision, keeping the
   reasoning auditable.

## 8. Why the LLM picks from a menu (the architecture lesson)

The single biggest failure mode of LLM trading agents is a hallucinated
detail: a strike that doesn't exist, a stale price, an ad-hoc position size.
This agent removes the possibility:

1. **Scout** builds candidates only from contracts the chain endpoint
   actually returned, priced at live mids.
2. **Strategist** (LLM) contributes judgment — regime classification,
   selection, abstention — expressed only as candidate ids.
3. **Risk Officer** (pure code) re-validates everything and clamps size. It
   cannot be argued with; a malformed or manipulated LLM output degrades to
   "no trade", never to "bad trade".

Abstaining is a first-class output. A cycle with no trade is logged with the
reasoning, which is exactly the behavior a capital allocator wants to see.

## 9. With more time

- Vol-regime filter (only sell premium when ATM IV is above a realized-vol
  estimate), earnings-date screen for satellites, delta-based dynamic exits,
  Monte Carlo sizing from historical 1–3 DTE distributions, and a proper
  backtest harness on stored chain snapshots.
