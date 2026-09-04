# CLAUDE.md — Alpaca hackathon trading agent

Autonomous defined-risk options agent for the Alpaca AI Trading Agents
Hackathon (paper trading only). Trading window: Mon Aug 31 2026 9:30 ET →
scored on total account equity **EOD Thu Sep 3**. The user is in IST
(ET+9:30); market open = 7:00pm IST.

Read `CONTEXT.md` before any design change, strategy discussion, or
non-trivial refactor — it holds every decision already made and why.
Read `STRATEGY.md` for the trading rationale. Do not re-litigate settled
decisions (e.g. "should we do news arbitrage?" — rejected, see CONTEXT.md).

## Commands

- `pip install -r requirements.txt` (Python 3.10+; httpx, pydantic v2, anthropic, python-dotenv)
- `python chaos_test.py` — 28 offline guardrail checks, no keys needed. Must pass.
- `python smoke_test.py` — offline end-to-end decision pass vs a synthetic market. Must pass.
- `python main.py dryrun` — full live-API pass (data + LLM + risk), **no orders**. Default verification.
- `python main.py once | run | monitor | status | close-all` — order-placing modes.
- Env in `.env` (copy `.env.example`): `ALPACA_API_KEY_ID`, `ALPACA_API_SECRET_KEY`,
  `ANTHROPIC_API_KEY`, optional `LLM_MODEL`. Never commit `.env`.

## Invariants — do not violate, do not "improve" away

1. **The LLM never constructs trades.** `strategist.py` may only select
   candidate ids from the Scout's pre-priced menu, or abstain. Never give it
   free-form strikes, symbols, prices, or order fields.
2. **`risk_officer.py` stays pure**: no I/O, no LLM, no network. Every limit
   lives in `config.py` and is enforced in code. A new rule is not done until
   `chaos_test.py` has a check for it.
3. **Orders are built only in `executor.py`.** Alpaca mleg `limit_price` is
   SIGNED (positive = debit, negative = credit); that convention exists only
   inside `_limit_for()`. Never inline order payloads elsewhere.
4. **Every failure path degrades to "no trade" or "close position"** — never
   to an unvalidated trade. Keep the parse-fail → abstain behavior.
5. **The news layer is subtractive only**: digest context for the LLM, storm
   gate veto for the Risk Officer. News must never create or size a trade.
6. **Journal everything.** State changes and decisions write events via
   `journalist.py`. Never remove or bypass journaling — it is submission
   evidence.
7. **Raw REST via httpx only** (hackathon FAQ compliance). No alpaca-py, no
   unofficial SDKs. All times ET via `zoneinfo`; config deadlines are
   load-bearing.

## Safety rails for you (Claude Code)

- Never run `main.py run`, `once`, or `close-all` yourself without the user
  explicitly asking in that session. `dryrun`, `status`, and the two test
  scripts are always fine.
- Never widen a risk limit in `config.py` (sizes, caps, floors, deadlines)
  unless the user explicitly requests that exact change.
- After touching `scout.py`, `strategist.py`, `risk_officer.py`,
  `executor.py`, or `main.py`: run `chaos_test.py` and `smoke_test.py` before
  reporting done.
- Live-API field names were verified against docs but not yet against the
  real endpoints — treat the first `dryrun` traceback as expected shakeout,
  fix minimally.
- Keep the README's pre-event disclosure section intact and updated; the
  hackathon rules require it.

## Style

Match the existing code: small pure functions, pydantic v2 models, docstrings
that explain *why*, config over constants, no new dependencies without asking.
