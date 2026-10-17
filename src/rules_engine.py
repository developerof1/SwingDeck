"""The 5-step swing trading pipeline (docs/SPEC.md Section 5). Pure logic, no I/O -
shared as-is by scripts/refresh_job.py (full history, fixed default thresholds) and
app.py (5-row snapshot, user-adjustable thresholds via the sidebar sliders).
"""

from dataclasses import dataclass

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
    """
    latest = df.iloc[-1]
    price = latest["close"]

    # Step 1: Macro Trend Filter (hard gate)
    if not (price > latest["sma_50"] > latest["sma_200"]):
        return EvalResult(
            ticker=ticker,
            status=STATUS_DISQUALIFIED,
            current_price=price,
            reason="Price/SMA50/SMA200 not in required trend order",
        )

    # Step 2: Regime Classification
    if price >= latest["ema_21"]:
        regime = REGIME_MOMENTUM
    elif price >= latest["sma_200"]:
        regime = REGIME_PULLBACK
    else:
        return EvalResult(
            ticker=ticker,
            status=STATUS_DISQUALIFIED,
            current_price=price,
            reason="Below EMA21 and below SMA200 - no valid regime",
        )

    dist_ema21 = _pct_distance(price, latest["ema_21"])

    # Step 3: Location Test
    if regime == REGIME_MOMENTUM:
        dist_ema8 = _pct_distance(price, latest["ema_8"])
        location_ok = dist_ema8 <= location_thresh or dist_ema21 <= location_thresh
    else:
        location_ok = _pct_distance(price, latest["sma_200"]) <= location_thresh

    if not location_ok:
        return EvalResult(
            ticker=ticker,
            status=STATUS_WAIT_LOCATION,
            regime=regime,
            current_price=price,
            dist_to_ema21_pct=dist_ema21,
            stoch_k=latest["stoch_k"],
            stoch_d=latest["stoch_d"],
            rvol=latest["rvol"],
            reason="Price extended from the support zone",
        )

    # Step 4: Oscillator Trigger
    crossed = _stoch_crossed_up(df, STOCH_CROSS_LOOKBACK_DAYS)
    if regime == REGIME_MOMENTUM:
        trigger_ok = crossed and MOMENTUM_STOCH_K_MIN <= latest["stoch_k"] <= MOMENTUM_STOCH_K_MAX
    else:
        trigger_ok = crossed and latest["stoch_k"] < PULLBACK_STOCH_K_OVERSOLD

    if not trigger_ok:
        return EvalResult(
            ticker=ticker,
            status=STATUS_WAIT_TRIGGER,
            regime=regime,
            current_price=price,
            dist_to_ema21_pct=dist_ema21,
            stoch_k=latest["stoch_k"],
            stoch_d=latest["stoch_d"],
            rvol=latest["rvol"],
            reason="No qualifying stochastic %K/%D cross in the lookback window",
        )

    # Step 5: Confirmation & Position Sizing
    prior_day_high = df["high"].iloc[-2]
    swing_low_5d = df["low"].tail(5).min()

    confirmed = price > prior_day_high and latest["rvol"] >= rvol_thresh
    if not confirmed:
        return EvalResult(
            ticker=ticker,
            status=STATUS_WAIT_CONFIRMATION,
            regime=regime,
            current_price=price,
            dist_to_ema21_pct=dist_ema21,
            stoch_k=latest["stoch_k"],
            stoch_d=latest["stoch_d"],
            rvol=latest["rvol"],
            reason="Price has not confirmed above prior-day high with sufficient RVOL",
        )

    entry = prior_day_high + 0.05
    stop = swing_low_5d * (1 - STOP_LOSS_BUFFER_PCT / 100)
    risk = entry - stop

    if regime == REGIME_MOMENTUM:
        target = entry + risk * TARGET_RISK_MULTIPLE
    else:
        target = min(latest["ema_21"], latest["sma_50"])

    rr = (target - entry) / risk if risk > 0 else 0.0

    if rr < MIN_RISK_REWARD:
        return EvalResult(
            ticker=ticker,
            status=STATUS_WAIT_CONFIRMATION,
            regime=regime,
            current_price=price,
            dist_to_ema21_pct=dist_ema21,
            stoch_k=latest["stoch_k"],
            stoch_d=latest["stoch_d"],
            rvol=latest["rvol"],
            entry=entry,
            stop=stop,
            target=target,
            rr=rr,
            reason=f"R:R {rr:.2f} is below the {MIN_RISK_REWARD} minimum",
        )

    return EvalResult(
        ticker=ticker,
        status=STATUS_ENTER,
        regime=regime,
        current_price=price,
        dist_to_ema21_pct=dist_ema21,
        stoch_k=latest["stoch_k"],
        stoch_d=latest["stoch_d"],
        rvol=latest["rvol"],
        entry=entry,
        stop=stop,
        target=target,
        rr=rr,
        reason="All 5 steps passed",
    )
