"""The 5-step swing trading pipeline (docs/SPEC.md Section 5). Pure logic, no I/O -
shared as-is by scripts/refresh_job.py (full history, fixed default thresholds) and
app.py (5-row snapshot, user-adjustable thresholds via the sidebar sliders).
"""

from dataclasses import dataclass, field

import pandas as pd

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

# Location and Oscillator don't have to hold on the exact same calendar day Step 5's
# breakout+RVOL trigger fires - a multi-day base (pull back to the EMA, oscillator
# turns, THEN the breakout bar prints a couple days later once price has already
# drifted outside the Location band) is normal, and requiring literal same-day
# coincidence on all three was missing those setups. Each one just has to have been
# true within this many calendar days of today. Confirmation itself (breakout + RVOL)
# stays strictly same-day - this only widens the window on the two steps that feed it.
GRACE_WINDOW_DAYS = 5

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
    # The distance the Location step actually gates on - nearest-of-EMA8/EMA21 in a
    # Momentum regime, distance-to-200-SMA in a Pullback regime. Distinct from
    # dist_to_ema21_pct above, which is always the EMA21 distance specifically and
    # isn't what's being tested once the regime is Pullback.
    location_dist_pct: float | None = None
    stoch_k: float | None = None
    stoch_d: float | None = None
    rvol: float | None = None
    # Step 1's compound gate (Price > SMA50 AND SMA50 > SMA200) collapses to one
    # DISQUALIFIED status regardless of how close a ticker is - these two let the UI
    # distinguish a "price-only" gap (could clear tomorrow on a good day) from a
    # "structural" gap (SMA50 still below SMA200, resolves over weeks not days).
    # Signed, not absolute - negative means price/SMA50 is below the level it's
    # measured against. Always computed (sma_50/sma_200 are present on every row),
    # regardless of whether Step 1 actually failed.
    price_vs_sma50_pct: float | None = None
    sma50_vs_sma200_pct: float | None = None
    entry: float | None = None
    stop: float | None = None
    target: float | None = None
    rr: float | None = None
    # Confirmation is really 3 ANDed sub-checks (breakout, RVOL, R:R), but only RVOL
    # and R:R ever got their own column - so a row could show both of those green
    # while the Confirmation ball was still red, because this third one was failing
    # invisibly. prior_day_high/breakout_ok expose it so it can get a column too.
    prior_day_high: float | None = None
    breakout_ok: bool | None = None
    # Today's raw Location/Oscillator reading, *before* GRACE_WINDOW_DAYS is applied -
    # callers that persist state (refresh_job.py) need this to know whether to bump
    # the stored "last qualified" date forward, as opposed to `steps[...].passed`
    # (STEP_LOCATION/STEP_OSCILLATOR), which already reflects the grace-extended gate
    # used to decide whether the pipeline keeps going.
    location_ok_today: bool | None = None
    oscillator_triggered_today: bool | None = None
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


def _within_grace(last_date: str | None, as_of, days: int) -> bool:
    """True if `last_date` (an ISO date string, or None/missing) falls within `days`
    calendar days at or before `as_of` (today's row's date)."""
    if not last_date:
        return False
    try:
        last = pd.Timestamp(last_date).normalize()
    except (ValueError, TypeError):
        return False
    delta = (pd.Timestamp(as_of).normalize() - last).days
    return 0 <= delta <= days


def _weekly_crossed_up(latest) -> bool | None:
    """None (indeterminate, non-blocking) if the ticker doesn't have enough history
    yet for a weekly reading - only an explicit False blocks the Oscillator step."""
    k, d = latest.get("weekly_stoch_k"), latest.get("weekly_stoch_d")
    k_prev, d_prev = latest.get("weekly_stoch_k_prev"), latest.get("weekly_stoch_d_prev")
    if any(v is None or pd.isna(v) for v in (k, d, k_prev, d_prev)):
        return None
    return bool(k_prev <= d_prev and k > d)


def _monthly_rising(latest) -> bool | None:
    k, k_prev = latest.get("monthly_stoch_k"), latest.get("monthly_stoch_k_prev")
    if any(v is None or pd.isna(v) for v in (k, k_prev)):
        return None
    return bool(k > k_prev)


def _mtf_gate(weekly_cross: bool | None, monthly_rising: bool | None) -> bool:
    """Missing higher-timeframe history degrades to non-blocking rather than failing
    the trigger outright - only an explicit False from a computable reading blocks."""
    return weekly_cross is not False and monthly_rising is not False


def _mtf_detail(weekly_cross: bool | None, monthly_rising: bool | None) -> str:
    def fmt(ok: bool | None) -> str:
        return "yes" if ok else ("no" if ok is False else "n/a")

    return f"weekly cross-up: {fmt(weekly_cross)}, monthly rising: {fmt(monthly_rising)}"


def evaluate_stock(
    ticker: str,
    df,
    location_thresh: float = DEFAULT_LOCATION_THRESHOLD_PCT,
    rvol_thresh: float = DEFAULT_RVOL_THRESHOLD,
    location_last_ok_date: str | None = None,
    oscillator_last_triggered_date: str | None = None,
    grace_days: int = GRACE_WINDOW_DAYS,
) -> EvalResult:
    """Evaluate one ticker's latest row through the 5-step pipeline.

    `df` must already carry the indicator columns from data_engine.add_indicators and
    data_engine.add_multi_timeframe_stoch (sma_50, sma_200, ema_8, ema_21, stoch_k,
    stoch_d, rvol, weekly_stoch_*, monthly_stoch_*) and have at least 5 rows of raw
    high/low/close, sorted ascending by date - either the full fetched history or a
    short snapshot tail both work (see data_engine.snapshot_tail).

    `location_last_ok_date`/`oscillator_last_triggered_date` are the ISO dates those
    steps last qualified as of a *previous* run (see refresh_job.py's prior-state
    diffing) - they let those two steps keep gating the pipeline forward for up to
    `grace_days` after they last held true, instead of requiring them to be true on
    the exact same day as Step 5's breakout+RVOL confirmation. Omit them (the default)
    to evaluate today's row in isolation, with no grace window applied.

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
    sma_50 = latest["sma_50"]
    sma_200 = latest["sma_200"]
    trend_ok = bool(price > sma_50 > sma_200)
    price_vs_sma50_pct = (price - sma_50) / sma_50 * 100
    sma50_vs_sma200_pct = (sma_50 - sma_200) / sma_200 * 100
    steps.append(
        StepCheck(
            STEP_TREND,
            STEP_LABELS[STEP_TREND],
            trend_ok,
            f"Price ${price:.2f} {'>' if price > sma_50 else '≤'} "
            f"50-SMA ${sma_50:.2f} "
            f"{'>' if sma_50 > sma_200 else '≤'} "
            f"200-SMA ${sma_200:.2f}",
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
        location_dist = min(dist_ema8, dist_ema21)
        location_ok_today = bool(location_dist <= location_thresh)
        location_detail = f"{location_dist:.2f}% from nearest EMA (needs ≤{location_thresh:.1f}%)"
    elif regime == REGIME_PULLBACK:
        location_dist = _pct_distance(price, latest["sma_200"])
        location_ok_today = bool(location_dist <= location_thresh)
        location_detail = f"{location_dist:.2f}% from 200-SMA (needs ≤{location_thresh:.1f}%)"
    else:
        location_dist = None
        location_ok_today = None
        location_detail = "Not applicable - no valid regime"

    if location_ok_today is None:
        location_grace = False
    else:
        location_grace = (not location_ok_today) and _within_grace(
            location_last_ok_date, latest["date"], grace_days
        )
    location_ok = None if location_ok_today is None else (location_ok_today or location_grace)
    if location_grace:
        location_detail += f" - outside today, but qualified within the last {grace_days}d"
    steps.append(StepCheck(STEP_LOCATION, STEP_LABELS[STEP_LOCATION], location_ok, location_detail))

    # Step 4: Oscillator Trigger
    crossed = _stoch_crossed_up(df, STOCH_CROSS_LOOKBACK_DAYS)
    weekly_cross = _weekly_crossed_up(latest)
    monthly_rising = _monthly_rising(latest)
    mtf_ok = _mtf_gate(weekly_cross, monthly_rising)
    mtf_note = _mtf_detail(weekly_cross, monthly_rising)
    stoch_k = latest["stoch_k"]
    if regime == REGIME_MOMENTUM:
        in_range = MOMENTUM_STOCH_K_MIN <= stoch_k <= MOMENTUM_STOCH_K_MAX
        oscillator_triggered_today = bool(crossed and in_range and mtf_ok)
        trigger_detail = (
            f"%K {stoch_k:.1f} ({'in' if in_range else 'outside'} "
            f"{MOMENTUM_STOCH_K_MIN}-{MOMENTUM_STOCH_K_MAX} range), "
            f"{'crossed up' if crossed else 'no recent %K/%D cross'}; {mtf_note}"
        )
    elif regime == REGIME_PULLBACK:
        oversold = stoch_k < PULLBACK_STOCH_K_OVERSOLD
        oscillator_triggered_today = bool(crossed and oversold and mtf_ok)
        trigger_detail = (
            f"%K {stoch_k:.1f} ({'oversold' if oversold else 'not oversold'}, "
            f"needs <{PULLBACK_STOCH_K_OVERSOLD}), {'crossed up' if crossed else 'no recent %K/%D cross'}; {mtf_note}"
        )
    else:
        oscillator_triggered_today = None
        trigger_detail = "Not applicable - no valid regime"

    if oscillator_triggered_today is None:
        oscillator_grace = False
    else:
        oscillator_grace = (not oscillator_triggered_today) and _within_grace(
            oscillator_last_triggered_date, latest["date"], grace_days
        )
    trigger_ok = None if oscillator_triggered_today is None else (oscillator_triggered_today or oscillator_grace)
    if oscillator_grace:
        trigger_detail += f" - outside today, but triggered within the last {grace_days}d"
    steps.append(StepCheck(STEP_OSCILLATOR, STEP_LABELS[STEP_OSCILLATOR], trigger_ok, trigger_detail))

    # Step 5: Confirmation & Position Sizing
    entry = stop = target = rr = None
    if regime is not None:
        prior_day_high = df["high"].iloc[-2]
        swing_low_5d = df["low"].tail(5).min()
        breakout_ok = price > prior_day_high
        confirmed = breakout_ok and latest["rvol"] >= rvol_thresh

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
        prior_day_high = None
        breakout_ok = None
        confirmation_ok = None
        confirmation_detail = "Not applicable - no valid regime"
    steps.append(StepCheck(STEP_CONFIRMATION, STEP_LABELS[STEP_CONFIRMATION], confirmation_ok, confirmation_detail))

    # Overall status/reason: the first failing step in pipeline order wins, exactly
    # as the original gated implementation determined it. location_ok/trigger_ok are
    # already grace-extended above, so a ticker whose Location/Oscillator qualified a
    # few days ago but not today can still reach Confirmation instead of getting stuck
    # re-reporting WAIT: Extended/Trigger Pending every day in between.
    common = dict(
        ticker=ticker,
        regime=regime,
        current_price=price,
        dist_to_ema21_pct=dist_ema21,
        price_vs_sma50_pct=price_vs_sma50_pct,
        sma50_vs_sma200_pct=sma50_vs_sma200_pct,
        location_dist_pct=location_dist,
        stoch_k=latest["stoch_k"],
        stoch_d=latest["stoch_d"],
        rvol=latest["rvol"],
        entry=entry,
        stop=stop,
        target=target,
        rr=rr,
        prior_day_high=prior_day_high,
        breakout_ok=breakout_ok,
        location_ok_today=location_ok_today,
        oscillator_triggered_today=oscillator_triggered_today,
        steps=steps,
    )

    if not trend_ok:
        gap_kind = "structural gap" if sma50_vs_sma200_pct < 0 else "price-only gap"
        return EvalResult(
            status=STATUS_DISQUALIFIED,
            reason=(
                f"Price {price_vs_sma50_pct:+.2f}% vs 50-SMA (${sma_50:.2f}); "
                f"50-SMA {sma50_vs_sma200_pct:+.2f}% vs 200-SMA (${sma_200:.2f}) - {gap_kind}"
            ),
            **common,
        )

    if regime is None:
        return EvalResult(
            status=STATUS_DISQUALIFIED,
            reason="Below EMA21 and below SMA200 - no valid regime",
            **common,
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
