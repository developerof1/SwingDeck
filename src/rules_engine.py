"""Momentum Continuation - the sole live setup (docs/Momentum Continuation V2 —
Logic.md). Pure logic, no I/O, no prior-run state - unlike the archived V1 engine
(src/rules_engine_v1.py), the only "state" this reads is the 5-session grace
window each of Location and Oscillator carry, and that's read straight off the
indicator-enriched tail of `df`, not stored anywhere. evaluate_stock() only ever
needs the last GRACE_WINDOW_SESSIONS+1 rows plus the lookback columns already
attached by data_engine.add_continuation_indicators().
"""

from dataclasses import dataclass, field

import pandas as pd

from src.data_engine import LOCATION_PROXIMITY_PCT, OSCILLATOR_ZONE_MAX

STATUS_DATA_INVALID = "DATA_INVALID"
STATUS_INELIGIBLE_TREND = "INELIGIBLE_TREND"
STATUS_DEVELOPING = "DEVELOPING"
STATUS_CANDIDATE = "CANDIDATE"

# Step 2 (Location) and Step 3 (Oscillator) each pass if their condition holds
# today, or held on any of the prior GRACE_WINDOW_SESSIONS sessions.
GRACE_WINDOW_SESSIONS = 5

# Monthly Stochastic(14,3,3) needs ~14 months for the first raw %K, +2 months for
# %K's own 3-period smoothing, +2 more for %D's, +1 for the "rising vs. prior
# month" check - roughly 19 months (~400 trading sessions). 420 adds a margin so
# the Trend Gate's monthly layer is never evaluated on an under-warmed value.
DATA_VALID_MIN_SESSIONS = 420

REQUIRED_FIELDS = (
    "sma_200",
    "sma_50",
    "sma_50_prev20",
    "ema_8",
    "ema_21",
    "stoch_k",
    "stoch_d",
    "weekly_stoch_k",
    "weekly_stoch_d",
    "weekly_stoch_k_prev",
    "monthly_stoch_k",
    "monthly_stoch_d",
    "monthly_stoch_k_prev",
)

BADGE_LOCATION_PENDING = "LOCATION_PENDING"
BADGE_OSCILLATOR_PENDING = "OSCILLATOR_PENDING"


@dataclass
class Badge:
    """One Developing reason: which gate (Location/Oscillator) is still
    pending, today's measured value, and the threshold it needs to clear.

    direction ("closing_in"/"drifting_away"/"holding_steady"/None) and, for
    the Oscillator badge, in_zone, exist purely so status_mapping.py can say
    not just *how far* but *which way it's moving* - a bare measured-vs-
    threshold number was flagged as near-useless on its own (see
    swingdeck-message-overhaul-todo memory). ema_label (Location only) names
    which EMA the measured distance is actually from."""

    code: str
    measured: float
    threshold: float
    direction: str | None = None
    in_zone: bool | None = None
    ema_label: str | None = None


@dataclass
class EvalResult:
    ticker: str
    status: str
    evaluated_session: str | None = None  # ISO date of the evaluation bar
    data_valid: bool = False
    trend_pass: bool | None = None
    location_pass: bool | None = None
    oscillator_pass: bool | None = None
    developing_badges: list[Badge] = field(default_factory=list)
    # {"close", "sma_50", "sma_200", "sma_50_prev20", "close_gt_sma200",
    #  "sma50_gt_sma200", "sma50_rising", "monthly_stoch_k", "monthly_stoch_d",
    #  "monthly_stoch_k_prev", "monthly_pass", "weekly_stoch_k",
    #  "weekly_stoch_k_prev", "weekly_pass"}
    trend_detail: dict = field(default_factory=dict)
    # {"dpct", "dpct_prev", "ema_ref", "ema_ref_is_ema8", "pass_today", "pass",
    #  "sessions_ago"}
    location_detail: dict = field(default_factory=dict)
    # {"stoch_k", "stoch_d", "stoch_k_prev", "in_zone", "pass_today", "pass",
    #  "sessions_ago"}
    oscillator_detail: dict = field(default_factory=dict)
    data_invalid_reason: str | None = None
    recovery_observed: bool | None = None
    diagnostics: dict = field(default_factory=dict)


def _finite(value) -> bool:
    return value is not None and pd.notna(value)


def direction_of(current, prev) -> str | None:
    """Public (not rules_engine-internal) because status_mapping.py also needs
    it to describe a *passing* gate's trend, not just a pending badge's. Lower
    is always "closer to qualifying" for both diagnostics this feeds
    (Location's %-from-EMA, Oscillator's %K) - so one comparison serves both.
    None when either reading is missing (e.g. no prior-session value yet)."""
    if not (_finite(current) and _finite(prev)):
        return None
    if current < prev:
        return "closing_in"
    if current > prev:
        return "drifting_away"
    return "holding_steady"


def _compute_trend_detail(latest) -> dict:
    close, sma_50, sma_200 = latest.get("close"), latest.get("sma_50"), latest.get("sma_200")
    sma_50_prev20 = latest.get("sma_50_prev20")
    monthly_k, monthly_d = latest.get("monthly_stoch_k"), latest.get("monthly_stoch_d")
    monthly_k_prev = latest.get("monthly_stoch_k_prev")
    weekly_k, weekly_k_prev = latest.get("weekly_stoch_k"), latest.get("weekly_stoch_k_prev")

    detail = {
        "close": close,
        "sma_50": sma_50,
        "sma_200": sma_200,
        "sma_50_prev20": sma_50_prev20,
        "monthly_stoch_k": monthly_k,
        "monthly_stoch_d": monthly_d,
        "monthly_stoch_k_prev": monthly_k_prev,
        "weekly_stoch_k": weekly_k,
        "weekly_stoch_k_prev": weekly_k_prev,
    }

    if all(_finite(v) for v in (close, sma_50, sma_200, sma_50_prev20)):
        detail["close_gt_sma200"] = bool(close > sma_200)
        detail["sma50_gt_sma200"] = bool(sma_50 > sma_200)
        detail["sma50_rising"] = bool(sma_50 > sma_50_prev20)
    else:
        detail["close_gt_sma200"] = None
        detail["sma50_gt_sma200"] = None
        detail["sma50_rising"] = None

    if all(_finite(v) for v in (monthly_k, monthly_d, monthly_k_prev)):
        detail["monthly_pass"] = bool(monthly_k > monthly_d or monthly_k > monthly_k_prev)
    else:
        detail["monthly_pass"] = None

    if all(_finite(v) for v in (weekly_k, weekly_k_prev)):
        detail["weekly_pass"] = bool(weekly_k > weekly_k_prev and weekly_k >= 20)
    else:
        detail["weekly_pass"] = None

    return detail


def _window_values(df: pd.DataFrame, column: str) -> list:
    """Oldest -> newest (today last), one boolean per session over the grace
    window - location_ok/oscillator_trigger_ok are plain boolean columns (a
    comparison against a still-NaN warm-up value evaluates to False, never
    NaN), so no finiteness handling is needed here."""
    return [bool(v) for v in df[column].tail(GRACE_WINDOW_SESSIONS + 1)]


def _gate_status(ok_values: list) -> dict:
    """pass_today: did it hold on the latest session. pass: did it hold today
    or on any of the prior GRACE_WINDOW_SESSIONS sessions. sessions_ago: how
    many sessions before today it last held (0 = today), None if never in the
    window - lets the UI say e.g. "triggered 3 sessions ago"."""
    pass_today = bool(ok_values[-1]) if ok_values else False
    sessions_ago = None
    for offset, ok in enumerate(reversed(ok_values)):
        if ok:
            sessions_ago = offset
            break
    return {"pass_today": pass_today, "pass": sessions_ago is not None, "sessions_ago": sessions_ago}


def _as_bool_or_none(value) -> bool | None:
    return bool(value) if _finite(value) else None


def _compute_location_detail(df: pd.DataFrame) -> dict:
    latest = df.iloc[-1]
    gate = _gate_status(_window_values(df, "location_ok"))
    return {
        "dpct": latest.get("location_dpct"),
        "dpct_prev": latest.get("location_dpct_prev"),
        "ema_ref": latest.get("ema_ref"),
        "ema_ref_is_ema8": _as_bool_or_none(latest.get("ema_ref_is_ema8")),
        **gate,
    }


def _compute_oscillator_detail(df: pd.DataFrame) -> dict:
    latest = df.iloc[-1]
    gate = _gate_status(_window_values(df, "oscillator_trigger_ok"))
    return {
        "stoch_k": latest.get("stoch_k"),
        "stoch_d": latest.get("stoch_d"),
        "stoch_k_prev": latest.get("stoch_k_prev"),
        "in_zone": _as_bool_or_none(latest.get("oscillator_in_zone")),
        **gate,
    }


def evaluate_stock(ticker: str, df: pd.DataFrame) -> EvalResult:
    """Evaluate one ticker's latest completed bar.

    `df` must already carry the columns from data_engine.add_continuation_indicators
    (sma_50/sma_200/sma_50_prev20, monthly/weekly stochastic, ema_8/ema_21,
    location_dpct/location_ok, daily stoch_k/stoch_d/oscillator_trigger_ok,
    prev_session_high), sorted ascending by date. Only the last GRACE_WINDOW_SESSIONS+1
    rows are read - earlier rows exist solely to let the rolling/shifted/resampled
    indicator columns be computed upstream; no prior-run state is read or written here.
    """
    latest = df.iloc[-1]
    evaluated_session = (
        latest["date"].strftime("%Y-%m-%d") if hasattr(latest["date"], "strftime") else str(latest["date"])
    )

    data_valid = len(df) >= DATA_VALID_MIN_SESSIONS and all(
        _finite(latest.get(field_name)) for field_name in REQUIRED_FIELDS
    )

    trend_detail = _compute_trend_detail(latest)

    close = latest.get("close")
    prev_session_high = latest.get("prev_session_high")
    recovery_observed = bool(close > prev_session_high) if _finite(close) and _finite(prev_session_high) else None

    common = dict(
        ticker=ticker,
        evaluated_session=evaluated_session,
        data_valid=data_valid,
        trend_detail=trend_detail,
        recovery_observed=recovery_observed,
        diagnostics={
            "close": close,
            "sma_50": latest.get("sma_50"),
            "sma_200": latest.get("sma_200"),
            "ema_8": latest.get("ema_8"),
            "ema_21": latest.get("ema_21"),
        },
    )

    if not data_valid:
        sessions = len(df)
        if sessions < DATA_VALID_MIN_SESSIONS:
            reason = f"Only {sessions} completed sessions available, need at least {DATA_VALID_MIN_SESSIONS}"
        else:
            missing_field = next(
                (name for name in REQUIRED_FIELDS if not _finite(latest.get(name))), None
            )
            reason = (
                f"Missing/non-finite value for '{missing_field}' on the evaluation session"
                if missing_field
                else "Missing or non-finite indicator values for the evaluation session"
            )
        return EvalResult(status=STATUS_DATA_INVALID, data_invalid_reason=reason, **common)

    trend_pass = bool(
        trend_detail["close_gt_sma200"]
        and trend_detail["sma50_gt_sma200"]
        and trend_detail["sma50_rising"]
        and trend_detail["monthly_pass"]
        and trend_detail["weekly_pass"]
    )
    if not trend_pass:
        return EvalResult(status=STATUS_INELIGIBLE_TREND, trend_pass=False, **common)

    location_detail = _compute_location_detail(df)
    oscillator_detail = _compute_oscillator_detail(df)
    location_pass = location_detail["pass"]
    oscillator_pass = oscillator_detail["pass"]

    if location_pass and oscillator_pass:
        return EvalResult(
            status=STATUS_CANDIDATE,
            trend_pass=True,
            location_pass=True,
            oscillator_pass=True,
            location_detail=location_detail,
            oscillator_detail=oscillator_detail,
            **common,
        )

    badges = []
    if not location_pass:
        badges.append(
            Badge(
                code=BADGE_LOCATION_PENDING,
                measured=location_detail["dpct"],
                threshold=LOCATION_PROXIMITY_PCT,
                direction=direction_of(location_detail.get("dpct"), location_detail.get("dpct_prev")),
                ema_label="EMA8" if location_detail.get("ema_ref_is_ema8") else "EMA21",
            )
        )
    if not oscillator_pass:
        badges.append(
            Badge(
                code=BADGE_OSCILLATOR_PENDING,
                measured=oscillator_detail["stoch_k"],
                threshold=OSCILLATOR_ZONE_MAX,
                direction=direction_of(oscillator_detail.get("stoch_k"), oscillator_detail.get("stoch_k_prev")),
                in_zone=oscillator_detail.get("in_zone"),
            )
        )

    return EvalResult(
        status=STATUS_DEVELOPING,
        trend_pass=True,
        location_pass=location_pass,
        oscillator_pass=oscillator_pass,
        location_detail=location_detail,
        oscillator_detail=oscillator_detail,
        developing_badges=badges,
        **common,
    )
