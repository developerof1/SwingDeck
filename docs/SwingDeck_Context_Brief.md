# SwingDeck — Context for Logic Agent

## 1. What SwingDeck Is

SwingDeck is a personal swing-trading dashboard (Streamlit, Python, Tiingo daily data, git-as-DB
persistence, deployed on Streamlit Cloud, password-gated). It tracks a manually curated watchlist
of tickers and evaluates each one nightly against a defined, non-discretionary rules engine to
flag which stocks are currently in a tradeable technical state — for after-close review and manual
entry timing in TradingView/Webull. It does not place trades, size positions, or claim
profitability; it's a screening/surveillance tool for a specific style of setup.

The rules engine is organized by **"setup"** — a named, self-contained pattern definition with its
own gates and thresholds. Multiple setups can run against the same watchlist simultaneously, each
producing its own status per ticker. **Momentum Continuation is the only live setup today.** More
setups (e.g., a planned reversal/bottom-fishing setup) are expected to follow, each added as an
independent module without altering existing ones.

## 2. Momentum Continuation — Rule Set (as implemented)

**Status:** Provisional research ruleset, implemented exactly as specified below
(`src/rules_engine.py`, `src/data_engine.py`). No thresholds are user-adjustable.

**Evaluation basis:** latest *completed* daily bar only — never an in-progress session.

**Pipeline:**
```
Watchlist → Data Validity → Step 1 Trend Gate (hard veto) → Step 2 Location Gate (+5-session grace)
→ Step 3 Oscillator Gate (+5-session grace) → Status
```

**Step 0 — Data Validity**
- Requires ≥420 completed daily sessions (margin above the ~400 needed for Monthly
  Stochastic(14,3,3) to warm up) AND finite values for every required indicator field.
- Fail → `DATA_INVALID`.

**Step 1 — Trend Gate (hard veto, same-day only, no grace window)**
- *Layer 1 (daily structure):* `Close > SMA200` AND `SMA50 > SMA200` AND
  `SMA50_today > SMA50_20_sessions_ago`. (Price may trade below SMA50 during a pullback — not
  checked.)
- *Layer 2 (MTF momentum veto)*, using completed higher-timeframe bars only:
  - Monthly: `(%K_monthly > %D_monthly) OR (%K_monthly > %K_monthly_prev_month)`
  - Weekly: `(%K_weekly > %K_weekly_prev_week) AND (%K_weekly ≥ 20)`
- Both Stochastic readings are Stochastic(14,3,3).
- Fail any condition → `INELIGIBLE_TREND` ("Other Monitored").

**Step 2 — Location Gate**
- `EMAref` = whichever of EMA8/EMA21 is numerically closer to Close (tie → EMA8).
- `Dpct = |Close − EMAref| / EMAref × 100`.
- Pass if `Dpct ≤ 3.0%` today **or** on any of the prior 5 sessions (grace window).
- *(Note: an earlier draft also required same-day decline + ATR-based pullback depth +
  EMA20/ATR proximity — confirmed as a spec error and explicitly NOT implemented.)*

**Step 3 — Oscillator Trigger Gate**
- Daily Stochastic(14,3,3).
- `Crossover = (%K > %D) AND (%K_prev ≤ %D_prev)`
- `Zone = (%K ≤ 30) OR (%D ≤ 30)`
- Pass if `Crossover AND Zone` held today **or** on any of the prior 5 sessions (grace window).

**Status Classification**
```python
if not data_valid:            → DATA_INVALID
elif not trend_pass:          → INELIGIBLE_TREND        (UI: "Other Monitored")
elif location_pass and oscillator_pass: → CANDIDATE      (UI: "Qualified")
else:                          → DEVELOPING              (carries LOCATION_PENDING / OSCILLATOR_PENDING badges)
```
- No separate terminal "ineligible location/oscillator" state — a trend-passing stock stays
  `DEVELOPING` indefinitely until both Location and Oscillator clear.

**Non-gating annotation**
- `recovery_observed = Close_today > High_previous_session` — informational only, never affects
  status.
