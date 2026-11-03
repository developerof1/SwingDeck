# Momentum Continuation V2 — Logic & UI Specification

Status: **Provisional** research ruleset. Implementation does not imply a validated improvement in profitability.

1. Purpose and scope

Identify temporary pullbacks within established uptrends using completed daily bars, for after-close review and manual entry timing in TradingView/Webull.

V2 contains one setup: Momentum Continuation.

- Disable Pullback Value evaluation and fallback routing.
- A symbol failing Momentum Continuation remains ineligible.
- Preserve V1 separately for research comparisons.
- Historical V1 labels do not determine V2 eligibility. A previously labeled Pullback Value symbol can qualify only if it independently passes the new rules.

2. Processing flow

```text
Watchlist
   ↓
Data validity
   ↓
Established uptrend
   ↓
Current pullback
   ↓
CANDIDATE + optional recovery annotation
```

Evaluate eligibility on the latest completed regular-session daily bar. Do not use an unfinished current-session bar.

3. Claude Code: step-wise implementation changes

#Step 1 — CHANGE: universe selection

Current mode: predefined watchlist.

- Evaluate every supplied watchlist symbol.
- Do not enforce security-type, $10 price, or liquidity exclusions.
- Show low liquidity as a warning, not an eligibility failure.

Future market-wide mode: keep a separate, configurable universe-selection layer with these provisional defaults:

| Filter | Default |
|---|---|
| Security type | US exchange-listed common stocks |
| Exclusions | ETFs, preferreds, warrants, OTC |
| Minimum close | $10 |
| Minimum liquidity | 20-session median daily dollar volume ≥ $20 million |

Dollar volume means daily close × daily volume. Use the median of the latest 20 completed sessions.

#Step 2 — NEW: data-validity gate

Require:

- At least 252 completed daily sessions.
- Valid OHLCV sufficient to calculate every required indicator.
- Consistent split-adjusted price and volume.
- Finite indicator values and `ATR14 > 0`.

Failure → `DATA_INVALID`. Do not substitute zero or silently pass missing values.

#Step 3 — CHANGE: trend gate

Replace V1:

```text
Close > SMA50 > SMA200
```

With:

```text
Close > SMA200
AND SMA50 > SMA200
AND SMA50_today > SMA50_20_sessions_ago
```

Remove `Close >= EMA21` as a regime-selection requirement.

Price below SMA50 is permitted if all new conditions pass.

#Step 4 — CHANGE: pullback/location gate

Remove nearest-EMA8/EMA21 distance selection. Require all three:

| Condition | Exact rule |
|---|---|
| Recent decline | `Close_today < Close_3_sessions_ago` |
| Pullback depth | `1.0 <= (HighestHigh20 − Close) / ATR14 <= 3.0` |
| EMA proximity | `−1.0 <= (Close − EMA20) / ATR14 <= 0.5` |

Definitions:

- `HighestHigh20`: maximum daily high over the latest 20 completed sessions, including the evaluation session.
- `ATR14`: Wilder-smoothed true range over 14 sessions.
- `EMA20`: conventional EMA with alpha `2 / 21`.
- Session offsets refer to trading sessions, not calendar days.

All conditions must pass on the evaluation bar. No grace windows.

#Step 5 — CUT: obsolete eligibility conditions

Remove these from candidate eligibility:

- Daily stochastic cross and zone requirements.
- Weekly/monthly oscillator requirements or vetoes.
- Location and oscillator grace windows.
- Prior-day-high breakout requirement.
- RVOL threshold.
- Minimum projected reward-to-risk.
- Pullback Value branch-specific rules.

Existing optional diagnostics may remain, but their values or absence must not affect eligibility.

#Step 6 — NEW: deterministic status assignment

```python
if not data_valid:
    status = "DATA_INVALID"
elif not trend_pass:
    status = "INELIGIBLE_TREND"
elif not pullback_pass:
    status = "INELIGIBLE_PULLBACK"
else:
    status = "CANDIDATE"
```

Store each individual condition’s result and measured value for the checklist. If valid data allows it, calculate both trend and pullback diagnostics even when the trend gate fails.

#Step 7 — NEW: recovery annotation

For valid data, calculate:

```text
recovery_observed = Close_today > High_previous_session
```

- This requires a close above the previous session’s high—not merely an intraday crossing.
- It is an annotation, not a candidate gate.
- It must not upgrade an ineligible symbol to `CANDIDATE`.
- Missing inputs → unknown, not false.

#Step 8 — CHANGE: execution boundary

Remove automatic `ENTER` status.

A candidate is a review opportunity, not an instruction to buy. Keep stops, position sizing, targets, and trade execution outside screening eligibility.

For the separate research module:

- Baseline: candidate → next-session opening entry.
- Alternative: candidate plus recovery observed → next-session opening entry.
- Model opening gaps and slippage; do not assume a retrospective same-day breakout fill.

4. UX: required changes

| UI area | Required change |
|---|---|
| Setup selector | Show Momentum Continuation only; remove Pullback Value routing and results. |
| Primary results | Default to `CANDIDATE` symbols; provide access to ineligible symbols and data errors. |
| Status labels | Candidate, Ineligible—Trend, Ineligible—Pullback, Data Invalid. |
| Old statuses | Remove `ENTER`, `WAIT: Confirmation`, `WAIT: Trigger`, `WAIT: Extended`, and the old 5%-below-SMA50 `WATCH` classification. |
| Checklist | Three sections: Data, Trend, Pullback. Show actual values beside required conditions. |
| Recovery | Separate badge: “Recovery observed,” with tooltip: “Close above previous session’s high; optional annotation.” |
| Optional indicators | Place stochastic, MTF readings, RVOL, and projected R:R in a clearly labeled diagnostics area—not the eligibility checklist. |
| Liquidity | Watchlist-mode warning below the provisional liquidity floor; no disqualification. |
| Timestamp | Display the completed session evaluated, not just the latest refresh time. |
| Ranking | Do not label optional-indicator agreement or checklist progress as higher expected profitability. |

Suggested candidate explanation:

> Established uptrend with a current volatility-adjusted pullback. Review manually; recovery confirmation is optional.

5. Acceptance criteria

Claude should add tests confirming:

1. Every candidate passes all trend and pullback conditions on the same completed bar.
2. Pullback Value is never evaluated or used as fallback.
3. A symbol below SMA50 can qualify under the new rules.
4. ATR-band endpoints are inclusive; trend inequalities remain strict.
5. Historical location or stochastic events cannot rescue a current failure.
6. Missing optional indicators do not change eligibility.
7. Recovery observed does not change candidate status.
8. Watchlist symbols are not excluded by future market-wide filters.
9. An unfinished bar cannot alter the completed-session result.
10. UI statuses and checklist values match the engine output.

6. Out of scope

- Profitability claims or optimized thresholds.
- A new Pullback Value strategy.
- Automated orders.
- Mandatory stops, targets, sizing, or R:R screening.
- Return-based ranking before validation.

The deliverable is a simpler, reproducible candidate screener. Whether its candidates produce profitable trades remains a separate validation task.