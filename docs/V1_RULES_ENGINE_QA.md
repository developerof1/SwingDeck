# Rules Engine Q&A

Notes captured from a walkthrough of [src/rules_engine.py](../src/rules_engine.py).

## 1. What is the stepped evaluation logic? Gates, ordered stages, or a mixture?

Short answer: it's **ordered stages**, with **hard gates + supporting evidence baked into a few individual steps**. It is not a set of independent gates evaluated in parallel with equal weight — the steps are sequential, and the first one that fails determines the ticker's status, even though (for UI purposes) every step still gets evaluated regardless of where the real gate stopped.

**Step 1 — Macro Trend Filter (hard gate)**
`Price > 50-SMA > 200-SMA`. Strict compound AND — fail it and the ticker is `DISQUALIFIED` (or `WATCH` if it's close and structurally healthy), full stop. Nothing downstream matters until this holds.

**Step 2 — Regime Classification (hard gate, branches the rest of the pipeline)**
Only reached once Step 1 passes. Price vs. EMA21/SMA200 decides `Momentum Continuation` vs. `Pullback Value` vs. no regime. This changes *which thresholds* Steps 3–5 use next (EMA8/EMA21 distance vs. SMA200 distance, stochastic range vs. oversold, momentum target vs. pullback target). No regime → `DISQUALIFIED`.

**Step 3 — Location Test (gate, with a grace-window softening)**
Price must be close enough to its support zone (nearest EMA for Momentum, 200-SMA for Pullback) within a threshold. Fail it and status is `WAIT: Extended`. Not purely "today or nothing" — if it was true within the last `GRACE_WINDOW_DAYS` (5 days), that counts too.

**Step 4 — Oscillator Trigger (gate, with grace window + non-blocking evidence)**
Requires a recent %K/%D cross in the right zone (Momentum range or Pullback oversold), same grace-window treatment as Step 3. Layered on top is the multi-timeframe check (weekly cross-up, monthly rising), which is explicitly *not* a hard requirement: if weekly/monthly data is missing or indeterminate, it degrades to non-blocking rather than failing the ticker. Only an explicit "no" on a computable MTF reading blocks. This is the clearest example of a hard sub-check (daily cross + zone) combined with supporting evidence that only penalizes when it actively contradicts, not when it's merely absent.

**Step 5 — Confirmation & Position Sizing (hard gate, same-day only — no grace)**
Requires breakout above prior-day high AND RVOL above threshold AND R:R above minimum, all evaluated strictly on today's bar (the grace window intentionally doesn't extend here). Any sub-check failing drops status to `WAIT: Confirmation`. All three pass → `ENTER`.

**Net shape**
- Sequential/ordered: a failure at Step N makes Steps N+1 onward moot for the *status* (first-failure-wins).
- All steps are still computed independently every time, purely so the UI checklist can show the whole picture rather than stopping at the first red light.
- Within Steps 3–4, "hard gate" is softened by the grace window (recent-but-not-today still counts), and within Step 4 specifically there's also genuinely non-blocking supporting evidence (MTF) layered on top of the hard daily-cross requirement.

## 2. When nothing is actionable, what makes one candidate more interesting than another?

Progress through the pipeline, plus proximity to the next gate it's failing.

- **How far it got**: a ticker sitting at `WAIT: Confirmation` has already cleared Trend, Regime, Location, and Oscillator — it's one breakout+RVOL bar away from `ENTER`. That's more interesting than one stuck at `WAIT: Extended (Location)`, which hasn't even cleared Step 3 yet. Rank of interest roughly follows `STATUS_WAIT_CONFIRMATION > STATUS_WAIT_TRIGGER > STATUS_WAIT_LOCATION > STATUS_WATCH > STATUS_DISQUALIFIED`.
- **How close it is to the step it's failing**: within a given status, the distance metrics (`location_dist_pct` vs. `location_thresh`, `price_vs_sma50_pct` vs. `watch_thresh`, R:R vs. `MIN_RISK_REWARD`) tell you if it's barely missing or nowhere close.
- **Whether the gap is structural or just price**: for `DISQUALIFIED`/`WATCH`, `sma50_vs_sma200_pct < 0` (structural, resolves over weeks) is less interesting than a pure `price_vs_sma50_pct` miss (price-only gap, could clear tomorrow) — that distinction is exactly why those two fields exist separately.
- **Whether supporting evidence already agrees**: a ticker waiting on Oscillator with the MTF cross/rising already confirmed is a stronger near-miss than one where MTF is also against it.

## 3. What is the Watch logic we have implemented?

`WATCH` is a reclassification of a Step-1 (Trend) failure, not a separate step: when `Price > 50-SMA > 200-SMA` fails, the ticker is normally `DISQUALIFIED`, but it's shown as `WATCH: Approaching Trend Qualification` instead if the failure is structurally healthy — 50-SMA is still above 200-SMA (no death cross) — **and** price is within `DEFAULT_WATCH_THRESHOLD_PCT` (5%) of climbing back above the 50-SMA.

So it's gated on two conditions: `sma50_vs_sma200_pct > 0` (structural trend intact) and `price_vs_sma50_pct >= -watch_thresh` (price-only gap, not a structural one) — [rules_engine.py:377-387](../src/rules_engine.py#L377-L387).

## 4. Exact momentum-continuation regime rule: what makes that branch qualify, and how it selects the reference EMA?

A ticker qualifies for the Momentum Continuation regime when price is at or above the 21-EMA (`price >= ema_21`), and the Location step then measures distance to whichever of the 8-EMA or 21-EMA is numerically closer to price (`min(dist_ema8, dist_ema21)`), not a fixed one of the two — [rules_engine.py:234-235](../src/rules_engine.py#L234-L235), [rules_engine.py:252-254](../src/rules_engine.py#L252-L254).
