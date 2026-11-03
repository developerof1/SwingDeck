"""Momentum Continuation - the sole live setup (docs/Momentum Continuation V2 —
Logic.md). Pure logic, no I/O, no prior-run state - unlike the archived V1 engine
(src/rules_engine_v1.py), there are no grace windows or regimes to carry forward,
so evaluate_stock() only ever needs the latest completed bar plus the lookback
columns already attached by data_engine.add_continuation_indicators().
"""

from dataclasses import dataclass, field

import pandas as pd

STATUS_DATA_INVALID = "DATA_INVALID"
STATUS_INELIGIBLE_TREND = "INELIGIBLE_TREND"
STATUS_INELIGIBLE_PULLBACK = "INELIGIBLE_PULLBACK"
STATUS_DEVELOPING = "DEVELOPING"
STATUS_CANDIDATE = "CANDIDATE"

# Candidate pullback/location bands (ATR units).
DEPTH_MIN, DEPTH_MAX = 1.0, 3.0
EMA_PROX_MIN, EMA_PROX_MAX = -1.0, 0.5

# Developing's looser floor/ceiling - shares Candidate's EMA-proximity floor (-1.0)
# and depth ceiling (3.0), so the only two ways a stock can be Developing-not-
# Candidate are a shallow depth in [0.5, 1.0) or an EMA distance in (0.5, 1.0].
DEV_DEPTH_MIN = 0.5
DEV_EMA_PROX_MAX = 1.0

DATA_VALID_MIN_SESSIONS = 252

REQUIRED_FIELDS = (
    "sma_200",
    "sma_50",
    "sma_50_prev20",
    "atr_14",
    "ema_20",
    "highest_high_20",
    "close_3ago",
)

BADGE_SHALLOW_PULLBACK = "SHALLOW_PULLBACK"
BADGE_EXTENDED_FROM_EMA20 = "EXTENDED_FROM_EMA20"


@dataclass
class Badge:
    """One Developing near-miss reason: which Candidate-only sub-condition
    missed, by how much, and what it needed."""

    code: str
    measured: float
    threshold: float


@dataclass
class EvalResult:
    ticker: str
    status: str
    evaluated_session: str | None = None  # ISO date of the evaluation bar
    data_valid: bool = False
    trend_pass: bool | None = None
    pullback_pass: bool | None = None
    developing_badges: list[Badge] = field(default_factory=list)
    # {"close_gt_sma200": bool, "sma50_gt_sma200": bool, "sma50_rising": bool,
    #  "close": float, "sma_50": float, "sma_200": float, "sma_50_prev20": float}
    trend_detail: dict = field(default_factory=dict)
    # {"decline": bool, "depth_atr": float, "ema_dist_atr": float,
    #  "close_3ago": float, "atr_14": float, "highest_high_20": float, "ema_20": float}
    pullback_detail: dict = field(default_factory=dict)
    data_invalid_reason: str | None = None
    recovery_observed: bool | None = None
    diagnostics: dict = field(default_factory=dict)


def _finite(value) -> bool:
    return value is not None and pd.notna(value)


def _compute_trend_detail(latest) -> dict:
    close, sma_50, sma_200 = latest.get("close"), latest.get("sma_50"), latest.get("sma_200")
    sma_50_prev20 = latest.get("sma_50_prev20")
    detail = {"close": close, "sma_50": sma_50, "sma_200": sma_200, "sma_50_prev20": sma_50_prev20}
    if all(_finite(v) for v in (close, sma_50, sma_200, sma_50_prev20)):
        detail["close_gt_sma200"] = bool(close > sma_200)
        detail["sma50_gt_sma200"] = bool(sma_50 > sma_200)
        detail["sma50_rising"] = bool(sma_50 > sma_50_prev20)
    else:
        detail["close_gt_sma200"] = None
        detail["sma50_gt_sma200"] = None
        detail["sma50_rising"] = None
    return detail


def _compute_pullback_detail(latest) -> dict:
    close = latest.get("close")
    close_3ago = latest.get("close_3ago")
    atr_14 = latest.get("atr_14")
    highest_high_20 = latest.get("highest_high_20")
    ema_20 = latest.get("ema_20")
    detail = {
        "close_3ago": close_3ago,
        "atr_14": atr_14,
        "highest_high_20": highest_high_20,
        "ema_20": ema_20,
    }
    if all(_finite(v) for v in (close, close_3ago, atr_14, highest_high_20, ema_20)) and atr_14 > 0:
        detail["decline"] = bool(close < close_3ago)
        detail["depth_atr"] = (highest_high_20 - close) / atr_14
        detail["ema_dist_atr"] = (close - ema_20) / atr_14
    else:
        detail["decline"] = None
        detail["depth_atr"] = None
        detail["ema_dist_atr"] = None
    return detail


def evaluate_stock(ticker: str, df: pd.DataFrame) -> EvalResult:
    """Evaluate one ticker's latest completed bar.

    `df` must already carry the columns from data_engine.add_continuation_indicators
    (sma_50, sma_200, sma_50_prev20, atr_14, ema_20, highest_high_20, close_3ago,
    prev_session_high), sorted ascending by date. Only the last row is evaluated -
    historical rows exist solely to let the rolling/shifted indicator columns be
    computed upstream; no prior-run state is read or written here.
    """
    latest = df.iloc[-1]
    evaluated_session = (
        latest["date"].strftime("%Y-%m-%d") if hasattr(latest["date"], "strftime") else str(latest["date"])
    )

    data_valid = len(df) >= DATA_VALID_MIN_SESSIONS and all(
        _finite(latest.get(field_name)) for field_name in REQUIRED_FIELDS
    )
    atr_14 = latest.get("atr_14")
    if data_valid and not (_finite(atr_14) and atr_14 > 0):
        data_valid = False

    trend_detail = _compute_trend_detail(latest)
    pullback_detail = _compute_pullback_detail(latest)

    close = latest.get("close")
    prev_session_high = latest.get("prev_session_high")
    recovery_observed = bool(close > prev_session_high) if _finite(close) and _finite(prev_session_high) else None

    common = dict(
        ticker=ticker,
        evaluated_session=evaluated_session,
        data_valid=data_valid,
        trend_detail=trend_detail,
        pullback_detail=pullback_detail,
        recovery_observed=recovery_observed,
        diagnostics={
            "close": close,
            "sma_50": latest.get("sma_50"),
            "sma_200": latest.get("sma_200"),
            "atr_14": atr_14,
            "ema_20": latest.get("ema_20"),
            "highest_high_20": latest.get("highest_high_20"),
        },
    )

    if not data_valid:
        sessions = len(df)
        reason = (
            f"Only {sessions} completed sessions available, need at least {DATA_VALID_MIN_SESSIONS}"
            if sessions < DATA_VALID_MIN_SESSIONS
            else "Missing or non-finite indicator values for the evaluation session"
        )
        return EvalResult(status=STATUS_DATA_INVALID, data_invalid_reason=reason, **common)

    trend_pass = bool(
        trend_detail["close_gt_sma200"] and trend_detail["sma50_gt_sma200"] and trend_detail["sma50_rising"]
    )
    if not trend_pass:
        return EvalResult(status=STATUS_INELIGIBLE_TREND, trend_pass=False, **common)

    decline = pullback_detail["decline"]
    depth_atr = pullback_detail["depth_atr"]
    ema_dist_atr = pullback_detail["ema_dist_atr"]

    candidate_pass = bool(decline and DEPTH_MIN <= depth_atr <= DEPTH_MAX and EMA_PROX_MIN <= ema_dist_atr <= EMA_PROX_MAX)
    if candidate_pass:
        return EvalResult(status=STATUS_CANDIDATE, trend_pass=True, pullback_pass=True, **common)

    developing_pass = bool(
        decline
        and DEV_DEPTH_MIN <= depth_atr <= DEPTH_MAX
        and EMA_PROX_MIN <= ema_dist_atr <= DEV_EMA_PROX_MAX
        and (depth_atr < DEPTH_MIN or ema_dist_atr > EMA_PROX_MAX)
    )
    if developing_pass:
        badges = []
        if depth_atr < DEPTH_MIN:
            badges.append(Badge(code=BADGE_SHALLOW_PULLBACK, measured=depth_atr, threshold=DEPTH_MIN))
        if ema_dist_atr > EMA_PROX_MAX:
            badges.append(Badge(code=BADGE_EXTENDED_FROM_EMA20, measured=ema_dist_atr, threshold=EMA_PROX_MAX))
        return EvalResult(
            status=STATUS_DEVELOPING, trend_pass=True, pullback_pass=False, developing_badges=badges, **common
        )

    return EvalResult(status=STATUS_INELIGIBLE_PULLBACK, trend_pass=True, pullback_pass=False, **common)
