# Visheshak — video narration script (~3 minutes)

Record: QuickTime/OBS screen capture. Open `presentation/slides.html` in a
full-screen browser window (arrow keys / click to advance). At slide 7,
switch to the live dashboard tab and a terminal running
`bash deploy/logs.sh journal`. Speak conversationally; the timings are
guides, not a metronome.

---

**[0:00 – Slide 1 · Title]**
This is Visheshak — Sanskrit for "the discerner." It's an autonomous options
trading agent built for the Alpaca hackathon, and it's built around one
uncomfortable observation about AI trading.

**[0:15 – Slide 2 · The bet]**
Most AI trading demos hand the language model the keys and hope. We bet the
opposite way. LLMs are genuinely good at reading a market regime — synthesizing
price action and headlines into "this looks rangebound." They should never be
trusted to write an order ticket. So we built a system where the model has
judgment, but code has power.

**[0:40 – Slide 3 · Architecture]**
Four roles, strictly separated. The Scout — pure code — prices every candidate
trade from live option chains and builds a menu. The Strategist — the LLM —
reads the market brief and a news digest, and may only pick items off that
menu, or abstain. It cannot invent a strike, a price, or a quantity; the API
response is a constrained schema. Then the Risk Officer — pure deterministic
code, no LLM anywhere near it — holds absolute veto: it clamps sizes, blocks
entries, and can kill the whole book. Only then does the Executor touch orders.

**[1:10 – Slide 4 · Strategy]**
The strategy itself: sell time with defined risk. Short-dated credit spreads
and iron condors on SPY and QQQ — structures where the maximum loss is known
before entry — chosen by a regime playbook: uptrend, put credit spread;
downtrend, call credit spread; rangebound, iron condor; unclear, do nothing.
Plus at most two small momentum trades on mega-caps.

**[1:35 – Slide 5 · The veto]**
Every safety rule lives in code, not in the prompt. One percent max loss per
structure. Ten percent total open risk. A daily loss halt. An equity kill
floor that flattens everything. Blackouts around macro releases. And a news
storm gate that stands down on any name with an unusual headline burst. Our
design test for every prompt rule: what happens if the LLM ignores it? The
answer must always be — a worse trade inside the limits, or no trade. Never
money at risk beyond limits.

**[2:05 – Slide 6 · Robustness]**
Every failure path falls toward flat. If the model fails, a fallback chain of
models tries next — and two bad answers mean abstain. If an order won't fill,
the executor walks away rather than leaving stale orders queued. And if the
infrastructure itself dies, the agent rebuilds its position state from the
broker on restart — this journal entry is from our real deployment, where it
recovered a live iron condor after a server rebuild. It never trades blind.

**[2:30 – Slide 7 · Live — SWITCH TO DASHBOARD]**
And here it is, live. *(switch tabs)* The dashboard shows the portfolio, and —
more importantly — each structure the way a trader thinks about it: the credit
we received, what it costs to close now, and how much buffer remains to the
short strikes. Below, the equity curve, straight from the journal. *(switch to
terminal)* And this is the journal itself — every market brief, every LLM
rationale, every risk verdict, every order, appended in real time. The whole
reasoning trail is auditable after the fact.

**[2:55 – Slide 8 · Close]**
Visheshak. Judgment from the model, discipline from the code — autonomous
since the opening bell. Thank you.

---

## Recording checklist
- [ ] Browser at 100% zoom, full screen (⌃⌘F), bookmarks bar hidden
- [ ] Dashboard tab open at http://132.226.103.59:8501 with positions visible
- [ ] Terminal tab: `bash deploy/logs.sh journal` already scrolled to fresh events
- [ ] Do a 15-second test recording to check mic level
- [ ] Total target: under 3:15
