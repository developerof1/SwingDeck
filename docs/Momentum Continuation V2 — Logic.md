# Momentum Continuation V2 — Logic & UI Specification

Status: **Provisional** research ruleset. Implementation does not imply a validated improvement
in profitability.

History: this doc originally described a simplified, single-pass pipeline (no MTF veto, no
oscillator trigger, no grace windows). On 2026-11-05 it was rewritten to match
`momentum-continuation-specs-v2.pdf`, a fuller spec the user supplied that reinstates those
pieces. This version is authoritative; the code in `src/rules_engine.py` and
`src/data_engine.py` implements exactly what's below.

1. Purpose and scope

Identify temporary pullbacks within established uptrends using completed daily bars, for
after-close review and manual entry timing in TradingView/Webull.

V2 contains one setup: Momentum Continuation, evaluated as a sequential three-gate pipeline.
Evaluate eligibility on the latest completed regular-session daily bar. Do not use an unfinished
current-session bar.

```text
Watchlist
   ↓
Data validity (≥420 completed sessions)
   ↓
Step 1 — Trend Gate (hard veto, same-day only)
   ↓
Step 2 — Location Gate (today, or via 5-session grace window)
   ↓
Step 3 — Oscillator Trigger Gate (today, or via 5-session grace window)
   ↓
Status: Candidate / Developing / Other Monitored / Data Invalid
```

2. Data-validity gate

Require at least `DATA_VALID_MIN_SESSIONS = 420` completed daily sessions (raised from 252 —
Monthly Stochastic(14,3,3) needs roughly 19 months of monthly bars to warm up: ~14 months for
the first raw %K, +2 for %K's own 3-period smoothing, +2 more for %D's, +1 for the "rising vs.
prior month" check; 420 trading sessions adds a margin), plus finite values for every field in
`rules_engine.REQUIRED_FIELDS`. Failure → `DATA_INVALID`.

3. Step 1 — Trend Gate (hard veto)

All of the following must hold on the evaluation session, with no grace window:

- **Layer 1 (daily structure):** `Close > SMA200` AND `SMA50 > SMA200` AND
  `SMA50_today > SMA50_20_sessions_ago`. Price is explicitly permitted to trade below SMA50
  during a pullback — only the three conditions above matter.
- **Layer 2 (MTF momentum veto):**
  - Monthly: `(%K_monthly > %D_monthly) OR (%K_monthly > %K_monthly_prev_month)`.
  - Weekly: `(%K_weekly > %K_weekly_prev_week) AND (%K_weekly >= 20)`.
  - Both the monthly and weekly Stochastic(14,3,3) readings are taken from the most recently
    *completed* higher-timeframe bar (never a still-forming current week/month).

Any failure → `INELIGIBLE_TREND` (Other Monitored).

4. Step 2 — Location Gate

- `EMAref` = whichever of `EMA8`/`EMA21` sits numerically closer to `Close` (tie → `EMA8`).
- `Dpct = |Close − EMAref| / EMAref × 100`.
- Passes if `Dpct ≤ 3.0%` today, **or** on any of the prior 5 sessions (grace window).

(An earlier draft of this gate also required a same-day decline, an ATR-based pullback-depth
band, and EMA20/ATR proximity — the user confirmed that trio was included in the PDF spec in
error. It is not implemented, not even as a diagnostic.)

5. Step 3 — Oscillator Trigger Gate

Uses daily Stochastic(14,3,3):

- `Crossover = (%K > %D) AND (%K_prev ≤ %D_prev)`.
- `Zone = (%K ≤ 30) OR (%D ≤ 30)`.
- Passes if `Crossover AND Zone` held today, **or** on any of the prior 5 sessions (grace window).

6. Status classification

```python
if not data_valid:
    status = "DATA_INVALID"
elif not trend_pass:
    status = "INELIGIBLE_TREND"
elif location_pass and oscillator_pass:
    status = "CANDIDATE"
else:
    status = "DEVELOPING"
```

| Status Tier | Requirements |
|---|---|
| **Candidate** (UI: Qualified) | Data valid, Trend Gate passes, Location and Oscillator both pass (today or via grace window). |
| **Developing** | Data valid, Trend Gate passes, but Location and/or Oscillator still pending — even after their grace windows. Carries a `LOCATION_PENDING` and/or `OSCILLATOR_PENDING` badge. |
| **Other Monitored** | Trend Gate fails (`INELIGIBLE_TREND`), or data is invalid. There is no separate terminal "ineligible location/oscillator" status — a Trend-passing stock stays Developing indefinitely until Location and Oscillator both clear. |

7. Recovery annotation (unchanged, non-gating)

`recovery_observed = Close_today > High_previous_session`. Informational only; never changes
status.

8. Out of scope

- Profitability claims or optimized thresholds.
- Stops, targets, position sizing, R:R, automated orders.
- Adjustable thresholds/sliders — every value above is fixed per this spec, not user-configurable.
