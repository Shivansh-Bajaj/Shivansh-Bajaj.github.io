# CONTEXT.md — decision record and handoff

Everything decided in the design sessions (claude.ai, Aug 30 2026) so future
sessions don't rediscover or re-litigate it. `CLAUDE.md` points here.

## 1. The event

- Alpaca AI Trading Agents Hackathon. Fresh official paper account, $100k.
- Trading starts Mon Aug 31, 9:30am ET. **Scored on total account equity as
  of EOD Thu Sep 3** (Sep 3 exercises/assignments included). ~3.5 trading
  days; no market holidays in the window (Labor Day is Sep 7).
- Judged on equity PLUS creativity / autonomy / robustness of the agent
  workflow. Options workflow emphasized by organizers.
- Free "indicative" options feed is real-time for latest quotes/chains.
- Trading API over REST is explicitly sufficient; unofficial SDKs need
  written justification (we avoid them entirely).
- Pre-event work is allowed but MUST be disclosed in the README (section
  exists — keep it accurate). No official-account trades before the start.
- User timezone IST = ET+9:30. Open 7:00pm IST, close 1:30am IST.

## 2. The user

Learning trading-strategy design; comfortable with options spreads (no need
to explain basics; do explain design reasoning). Wants the agent to be a
learning artifact as much as a submission. Prefers visual explanations.

## 3. Strategy (decided — see STRATEGY.md for full rationale)

- **Core (~70–80% of risk budget):** short-dated defined-risk premium selling
  on SPY/QQQ. Playbook: uptrend → put credit spread; downtrend → call credit
  spread; rangebound → iron condor; unclear/event → abstain. Short strikes
  15–25 delta, $5 wings, expiries ≤ Thu Sep 3.
- **Satellite (≤2 positions):** momentum debit verticals on mega-caps
  (NVDA/AAPL/MSFT/META/AMZN/TSLA), expiry ≤ Fri Sep 4 (single names list
  Friday weeklies; safe because everything force-closes Thu 3:30pm ET).
- **Exits:** credit — take profit at 50% of credit, stop at 2× credit; debit
  — +60% / −50%. Expiry-day rule: from 3:00pm ET close any credit structure
  ITM or within 0.3% of a short strike (pin risk).
- **Expectancy sketch:** ~18Δ short wins ≈80%; managed winner ≈ +$50/contract,
  managed loser ≈ −$100 → EV ≈ +$20/contract/trade; breakeven win rate 67%.
- **Endgame:** no new entries after Thu 2:00pm ET; flat by 3:30pm ET — before
  measurement and before Friday NFP.

## 4. Rejected alternatives (do not re-propose without new information)

- **True arbitrage** (parity gaps, cross-venue): latency race lost to HFT;
  on paper the indicative feed shows stale/wide quotes and the fill simulator
  "monetizes" mispricings that don't exist — a bug dressed as an edge.
- **News-reaction as the core strategy:** speed edge gone in <1s; over 3.5
  days the watchlist yields too few tradable headlines for a noisy edge,
  while premium selling earns theta daily regardless.
- **What was kept:** news as a *subtractive input layer* — LLM reads a
  headline digest (regime confirm/veto, satellite direction, abstain on
  fresh ambiguity); deterministic "storm gate" blocks new entries on any
  single name with ≥5 headlines in 30 min (code counts, only the LLM
  interprets); journal cites headlines that drove decisions. News can never
  create or enlarge a position.

## 5. Architecture (separation of powers)

Scout (data → priced candidate menu) → Strategist (LLM: picks ids or
abstains) → Risk Officer (pure code: veto/clamp/kill switches) → Executor
(mleg orders) — with a Monitor pass every 5 min (exits, kill floor) and a
Journalist recording everything (journal/journal.jsonl + equity_curve.csv).
Decision passes at :15/:45 between 9:45 and 15:15 ET.

Design principle: the LLM contributes judgment where language and regime
reading matter; every rule with a safety consequence has a deterministic
twin. For each prompt rule, "what if the LLM ignores it?" must answer
"a worse trade inside the limits" or "no trade" — never "money at risk
beyond limits". The one deliberately code-free rule is the playbook mapping
(regime → structure): violating it costs edge, not safety.

## 6. Risk limits (config.py — load-bearing numbers)

1% max loss per structure; 10% total open risk; max 5 structures / 2
satellites; daily −2.5% halts new entries; equity kill floor $92,000
(flatten + permanent halt); min credit $0.15 and 8% of width; quote filter:
skip contracts with spread > max($0.10, 35% of mid); event blackouts (no new
entries −45/+15 min): ISM Mfg Tue 9/1 10:00, JOLTS Wed 9/2 10:00, claims Thu
9/3 8:30, ISM Services Thu 9/3 10:00, NFP Fri 9/4 8:30 (post-measurement).

## 7. Verified Alpaca API facts (checked against docs Aug 30)

- Trading base `https://paper-api.alpaca.markets`; data base
  `https://data.alpaca.markets`; auth headers `APCA-API-KEY-ID` /
  `APCA-API-SECRET-KEY`.
- Multi-leg orders: `POST /v2/orders` with `order_class="mleg"`, top-level
  qty/type/time_in_force, legs[] of {symbol, side, ratio_qty,
  position_intent}; **net `limit_price` is signed: positive=debit,
  negative=credit**; up to 4 legs.
- Chain: `GET /v1beta1/options/snapshots/{underlying}` with feed=indicative,
  expiration_date, strike_price_gte/lte; paginated via next_page_token;
  snapshot = latestQuote{bp,ap} + greeks{delta,…} + impliedVolatility.
- **Greeks are often missing on 0DTE** (IV solver unstable as T→0, penny
  quotes below intrinsic, vendor nulls instead of shipping garbage). Fallback
  ladder picks strikes by %-OTM: 0.5/0.8/1.1/1.3% for 0–3 DTE — that is
  0.8%·√DTE ≈ a ~0.9σ (~17Δ) distance at SPY's typical ~13% IV. Known
  weakness: static vs vol regime; upgrade = scale by the ATM IV the Scout
  already computes.
- News API: `GET /v1beta1/news` (Benzinga content), free tier, same keys.
- Expiry picking: walk back from the deadline skipping weekends
  (`_pick_expiry`); credit deadline Sep 3, debit Sep 4. Known
  simplifications: assumes the chosen weekday has listed contracts (true for
  SPY/QQQ dailies + Friday mega-cap weeklies; empty chain is noted, not
  retried a day earlier) and ignores market holidays (none this window).

## 8. Current state (as of handoff)

- All 18 source files written; `chaos_test.py` 28/28 PASS; `smoke_test.py`
  PASS (offline synthetic end-to-end with Risk Officer clamping 5→2).
- **Not yet validated against the live Alpaca API** — the first
  `python main.py dryrun` with real keys is the expected shakeout for any
  field-name mismatch. Fix minimally; don't refactor mid-shakeout.
- LLM: Anthropic Messages API, model `claude-sonnet-5` (env-overridable),
  temperature 0.2, prompt in `prompts/strategist_system.md` (7 rules + strict
  JSON contract; 2 parse failures → fail-safe abstain).

## 9. Runbook

- **Tonight (Sun):** create official + throwaway paper accounts; enable
  options **Level 3** on both; fill `.env` with throwaway keys +
  ANTHROPIC_API_KEY; run both test scripts; `python main.py dryrun`.
- **Mon:** switch `.env` to official keys; start `python main.py run` before
  7:00pm IST (9:30 ET). Keep machine awake (`nohup … &` on a server works).
- **Thu:** agent stops entries 2pm ET, flattens 3:30pm ET automatically.
- Watch `journal/journal.jsonl`; events map to modules: `brief` (Scout),
  `strategist` (LLM), `risk_review`/`risk_veto` (RO), `opened`/`closed`
  (Executor), `news_storm` (news layer).

## 10. Backlog (nice-to-have, in priority order)

1. Scale the %-OTM fallback ladder by ATM IV (one-line spirit:
   `pct *= atm_iv / 0.13`).
2. Empty-chain retry: walk expiry back one more day instead of skipping.
3. Commit the final `journal/` + an equity-curve chart into the repo for
   judges; polish README submission section.
4. Market-wide shock detector (macro headline burst → pause all entries).
5. Holiday calendar in `_pick_expiry` (needed only beyond this hackathon).
