"""The 5-step swing trading pipeline (docs/SPEC.md Section 5). Pure logic, no I/O -
shared as-is by scripts/refresh_job.py (full history, fixed default thresholds) and
app.py (5-row snapshot, user-adjustable thresholds via the sidebar sliders).
"""

from dataclasses import dataclass, field

STATUS_DISQUALIFIED = "DISQUALIFIED: Trend Failure"
STATUS_WAIT_LOCATION = "WAIT: Extended (Location)"
STATUS_WAIT_TRIGGER = "WAIT: Trigger Pending (Stoch)"
STATUS_WAIT_CONFIRMATION = "WAIT: Awaiting Confirmation"
STATUS_ENTER = "ENTER"

REGIME_MOMENTUM = "Momentum Continuation"
REGIME_PULLBACK = "Pullback Value"

DEFAULT_LOCATION_THRESHOLD_PCT = 1.5
DEFAULT_RVOL_THRESHOLD = 1.2
STOP_LOSS_BUFFER_PCT = 0.5
TARGET_RISK_MULTIPLE = 2.5
MIN_RISK_REWARD = 2.0
STOCH_CROSS_LOOKBACK_DAYS = 2
MOMENTUM_STOCH_K_MIN = 40
MOMENTUM_STOCH_K_MAX = 65
PULLBACK_STOCH_K_OVERSOLD = 20

# Step keys/labels, in pipeline order - shared by the UI to render the checklist.
STEP_TREND = "trend"
STEP_REGIME = "regime"
STEP_LOCATION = "location"
STEP_OSCILLATOR = "oscillator"
STEP_CONFIRMATION = "confirmation"

STEP_LABELS = {
    STEP_TREND: "Macro Trend",
    STEP_REGIME: "Regime",
    STEP_LOCATION: "Location",
    STEP_OSCILLATOR: "Oscillator Trigger",
    STEP_CONFIRMATION: "Confirmation",
}


@dataclass
class StepCheck:
    """One pipeline step's pass/fail, evaluated independently of where the overall
    status stops gating - so the UI can show the full checklist, not just the first
    blocker. `passed` is None when the step genuinely can't be evaluated (no regime
    was established, so Location/Oscillator/Confirmation have no thresholds to use)."""

    key: str
    label: str
    passed: bool | None
    detail: str


@dataclass
class EvalResult:
    ticker: str
    status: str
    reason: str
    regime: str | None = None
    current_price: float | None = None
    dist_to_ema21_pct: float | None = None
    stoch_k: float | None = None
    stoch_d: float | None = None
    rvol: float | None = None
    entry: float | None = None
    stop: float | None = None
    target: float | None = None
    rr: float | None = None
    steps: list[StepCheck] = field(default_factory=list)


def _pct_distance(price: float, level: float) -> float:
    return abs(price - level) / level * 100


def _stoch_crossed_up(df, lookback_days: int) -> bool:
    """True if stoch_k crossed above stoch_d on any of the last `lookback_days` days."""
    recent = df.tail(lookback_days + 1)
    for i in range(1, len(recent)):
        prev, curr = recent.iloc[i - 1], recent.iloc[i]
        if prev["stoch_k"] <= prev["stoch_d"] and curr["stoch_k"] > curr["stoch_d"]:
            return True
    return False


def evaluate_stock(
    ticker: str,
    df,
    location_thresh: float = DEFAULT_LOCATION_THRESHOLD_PCT,
    rvol_thresh: float = DEFAULT_RVOL_THRESHOLD,
) -> EvalResult:
    """Evaluate one ticker's latest row through the 5-step pipeline.

    `df` must already carry the indicator columns from data_engine.add_indicators
    (sma_50, sma_200, ema_8, ema_21, stoch_k, stoch_d, rvol) and have at least 5 rows
    of raw high/low/close, sorted ascending by date - either the full fetched history
    or a short snapshot tail both work (see data_engine.snapshot_tail).

    Every step is evaluated regardless of whether an earlier one failed, so callers
    can render a full pass/fail checklist (`EvalResult.steps`) instead of only the
    single blocking reason. The overall `status`/`reason` are still determined by the
    first failing step, exactly as the pipeline is defined - that decision logic is
    unchanged.
    """
    latest = df.iloc[-1]
    price = latest["close"]
    steps: list[StepCheck] = []

    # Step 1: Macro Trend Filter (hard gate)
    trend_ok = bool(price > latest["sma_50"] > latest["sma_200"])
    steps.append(
        StepCheck(
            STEP_TREND,
            STEP_LABELS[STEP_TREND],
            trend_ok,
            f"Price ${price:.2f} {'>' if price > latest['sma_50'] else '≤'} "
            f"50-SMA ${latest['sma_50']:.2f} "
            f"{'>' if latest['sma_50'] > latest['sma_200'] else '≤'} "
            f"200-SMA ${latest['sma_200']:.2f}",
        )
    )

    # Step 2: Regime Classification
    if price >= latest["ema_21"]:
        regime = REGIME_MOMENTUM
    elif price >= latest["sma_200"]:
        regime = REGIME_PULLBACK
    else:
        regime = None
    steps.append(
        StepCheck(
            STEP_REGIME,
            STEP_LABELS[STEP_REGIME],
            regime is not None,
            f"{regime} regime" if regime else "Below 21-EMA and below 200-SMA - no valid regime",
        )
    )

    dist_ema21 = _pct_distance(price, latest["ema_21"])

    # Step 3: Location Test
    if regime == REGIME_MOMENTUM:
        dist_ema8 = _pct_distance(price, latest["ema_8"])
        nearest = min(dist_ema8, dist_ema21)
        location_ok = nearest <= location_thresh
        location_detail = f"{nearest:.2f}% from nearest EMA (needs ≤{location_thresh:.1f}%)"
    elif regime == REGIME_PULLBACK:
        dist_sma200 = _pct_distance(price, latest["sma_200"])
        location_ok = dist_sma200 <= location_thresh
        location_detail = f"{dist_sma200:.2f}% from 200-SMA (needs ≤{location_thresh:.1f}%)"
    else:
        location_ok = None
        location_detail = "Not applicable - no valid regime"
    steps.append(StepCheck(STEP_LOCATION, STEP_LABELS[STEP_LOCATION], location_ok, location_detail))

    # Step 4: Oscillator Trigger
    crossed = _stoch_crossed_up(df, STOCH_CROSS_LOOKBACK_DAYS)
    stoch_k = latest["stoch_k"]
    if regime == REGIME_MOMENTUM:
        in_range = MOMENTUM_STOCH_K_MIN <= stoch_k <= MOMENTUM_STOCH_K_MAX
        trigger_ok = crossed and in_range
        trigger_detail = (
            f"%K {stoch_k:.1f} ({'in' if in_range else 'outside'} "
            f"{MOMENTUM_STOCH_K_MIN}-{MOMENTUM_STOCH_K_MAX} range), "
            f"{'crossed up' if crossed else 'no recent %K/%D cross'}"
        )
    elif regime == REGIME_PULLBACK:
        oversold = stoch_k < PULLBACK_STOCH_K_OVERSOLD
        trigger_ok = crossed and oversold
        trigger_detail = (
            f"%K {stoch_k:.1f} ({'oversold' if oversold else 'not oversold'}, "
            f"needs <{PULLBACK_STOCH_K_OVERSOLD}), {'crossed up' if crossed else 'no recent %K/%D cross'}"
        )
    else:
        trigger_ok = None
        trigger_detail = "Not applicable - no valid regime"
    steps.append(StepCheck(STEP_OSCILLATOR, STEP_LABELS[STEP_OSCILLATOR], trigger_ok, trigger_detail))

    # Step 5: Confirmation & Position Sizing
    entry = stop = target = rr = None
    if regime is not None:
        prior_day_high = df["high"].iloc[-2]
        swing_low_5d = df["low"].tail(5).min()
        confirmed = price > prior_day_high and latest["rvol"] >= rvol_thresh

        entry = prior_day_high + 0.05
        stop = swing_low_5d * (1 - STOP_LOSS_BUFFER_PCT / 100)
        risk = entry - stop

        if regime == REGIME_MOMENTUM:
            target = entry + risk * TARGET_RISK_MULTIPLE
        else:
            target = min(latest["ema_21"], latest["sma_50"])
        rr = (target - entry) / risk if risk > 0 else 0.0

        rr_ok = rr >= MIN_RISK_REWARD
        confirmation_ok = confirmed and rr_ok
        if not confirmed:
            confirmation_detail = (
                f"Price ${price:.2f} vs prior-day high ${prior_day_high:.2f}, "
                f"RVOL {latest['rvol']:.2f} (needs ≥{rvol_thresh:.2f})"
            )
        elif not rr_ok:
            confirmation_detail = f"R:R {rr:.2f} below the {MIN_RISK_REWARD} minimum"
        else:
            confirmation_detail = f"Confirmed above prior-day high, R:R {rr:.2f}"
    else:
        confirmation_ok = None
        confirmation_detail = "Not applicable - no valid regime"
    steps.append(StepCheck(STEP_CONFIRMATION, STEP_LABELS[STEP_CONFIRMATION], confirmation_ok, confirmation_detail))

    # Overall status/reason: the first failing step in pipeline order wins, exactly
    # as the original gated implementation determined it.
    common = dict(
        ticker=ticker,
        regime=regime,
        current_price=price,
        dist_to_ema21_pct=dist_ema21 if regime is not None else None,
        stoch_k=latest["stoch_k"] if regime is not None else None,
        stoch_d=latest["stoch_d"] if regime is not None else None,
        rvol=latest["rvol"] if regime is not None else None,
        entry=entry,
        stop=stop,
        target=target,
        rr=rr,
        steps=steps,
    )

    if not trend_ok:
        return EvalResult(
            status=STATUS_DISQUALIFIED,
            current_price=price,
            reason="Price/SMA50/SMA200 not in required trend order",
            ticker=ticker,
            steps=steps,
        )

    if regime is None:
        return EvalResult(
            status=STATUS_DISQUALIFIED,
            current_price=price,
            reason="Below EMA21 and below SMA200 - no valid regime",
            ticker=ticker,
            steps=steps,
        )

    if not location_ok:
        return EvalResult(status=STATUS_WAIT_LOCATION, reason="Price extended from the support zone", **common)

    if not trigger_ok:
        return EvalResult(
            status=STATUS_WAIT_TRIGGER,
            reason="No qualifying stochastic %K/%D cross in the lookback window",
            **common,
        )

    if not confirmed:
        return EvalResult(
            status=STATUS_WAIT_CONFIRMATION,
            reason="Price has not confirmed above prior-day high with sufficient RVOL",
            **common,
        )

    if rr < MIN_RISK_REWARD:
        return EvalResult(
            status=STATUS_WAIT_CONFIRMATION,
            reason=f"R:R {rr:.2f} is below the {MIN_RISK_REWARD} minimum",
            **common,
        )

    return EvalResult(
        status=STATUS_ENTER,
        reason="All 5 steps passed",
        **common,
    )
