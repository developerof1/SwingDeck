"""Momentum Continuation evaluation (docs/Momentum Continuation - V2 Logic Improvement
Specs.md, Part A §4-7). Pure logic, no I/O - `df` must already carry the columns from
gates.add_continuation_indicators, sorted ascending by date, and must be the *full*
fetched history (L1/L3 read weekly series derived from the whole thing, not a tail
window - unlike the archived V1 engine, there's no cached-snapshot re-evaluation path
here that would need a minimal-window guarantee).

L1 (Trend) and L2 (Daily) each resolve on their own clock, independently - L2 can be
ARMED/NOT_ARMED while L1 is FAIL or NOT_EVALUABLE, and the combine step below reads both
directly rather than through an intermediate flags list (Part A §7's documented
asymmetry: DAILY_SIGNAL_IN was only ever defined for L1 FAIL in the original pseudocode,
never for NOT_EVALUABLE, even though L2 is independent in both cases - this evaluator
just always computes L2's real state regardless of L1's, so the dashboard copy layer
can read l2_state/armed directly without that gap).

2026-11-16: Monitored is no longer purely an L1-fail/not-evaluable verdict - an L1-pass
row with zero signal on both other layers (L2 not armed AND L3 score 0) also lands in
Monitored, since there's nothing actually "developing" there (see evaluate_stock below).
"""

from dataclasses import dataclass, field

import pandas as pd

from src.setups.momentum_continuation.gates import (
    ARMED_MAX_AGE,
    FLAT_PRIOR_FALL,
    FLAT_TOL,
    FLAT_ZONE,
    L1_MIN_WEEKS,
    L2_MIN_SESSIONS,
    L3_QUALIFY_MIN,
    L3_STOCH_FLOOR,
    L3_W_MACD,
    L3_W_RSI,
    L3_W_STOCH,
)

STATUS_QUALIFIED = "QUALIFIED"
STATUS_DEVELOPING = "DEVELOPING"
STATUS_MONITORED = "MONITORED"

L1_PASS = "PASS"
L1_FAIL = "FAIL"
L1_NOT_EVALUABLE = "NOT_EVALUABLE"

L2_ARMED = "ARMED"
L2_NOT_ARMED = "NOT_ARMED"
L2_NOT_EVALUABLE = "NOT_EVALUABLE"

# status_mapping.py (sibling module) owns the status -> shared-UI-group dict; this file
# only ever produces the status strings above, never the group itself.
REQUIRED_L2_FIELDS = ("atr_14", "ema_ref", "stoch_k", "stoch_d", "stoch_k_prev", "stoch_d_prev")


@dataclass
class EvalResult:
    ticker: str
    status: str
    evaluated_session: str | None = None  # ISO date of the evaluation bar
    l1_state: str = L1_NOT_EVALUABLE
    l2_state: str = L2_NOT_EVALUABLE
    l2_age: int | None = None
    armed: bool = False
    l3_score: int | None = None
    # {"stoch_pts", "rsi_pts", "macd_pts", "k_now", "k_prev", "rsi_now", "rsi_prev",
    #  "hist_now", "hist_prev"}
    l3_detail: dict = field(default_factory=dict)
    stoch_flattening: bool | None = None
    diagnostics: dict = field(default_factory=dict)


def _finite(value) -> bool:
    return value is not None and pd.notna(value)


def _l1_state(latest) -> str:
    completed_weeks = latest.get("l1_completed_weeks")
    if completed_weeks is None or completed_weeks < L1_MIN_WEEKS:
        return L1_NOT_EVALUABLE
    ema50, ema50_4w_ago = latest.get("l1_ema50"), latest.get("l1_ema50_4w_ago")
    if not (_finite(ema50) and _finite(ema50_4w_ago)):
        return L1_NOT_EVALUABLE
    return L1_PASS if ema50 > ema50_4w_ago else L1_FAIL


def _l2_age_and_armed(df: pd.DataFrame) -> tuple[int | None, bool]:
    """Sessions since l2_signal was last True (0 = today), searching back at most
    ARMED_MAX_AGE sessions - that's the only range "armed" ever cares about, so there's
    no reason to search further."""
    window = df["l2_signal"].tail(ARMED_MAX_AGE + 1).tolist()  # oldest -> newest
    for age, signaled in enumerate(reversed(window)):
        if signaled:
            return age, True
    return None, False


def _l2_not_evaluable(df: pd.DataFrame, latest) -> bool:
    if len(df) < L2_MIN_SESSIONS:
        return True
    return not all(_finite(latest.get(name)) for name in REQUIRED_L2_FIELDS)


def _l3_score_and_detail(latest) -> tuple[int, dict]:
    k_now, k_prev = latest.get("weekly_k_now"), latest.get("weekly_k_prev")
    rsi_now, rsi_prev = latest.get("weekly_rsi_now"), latest.get("weekly_rsi_prev")
    hist_now, hist_prev = latest.get("weekly_hist_now"), latest.get("weekly_hist_prev")

    stoch_pts = L3_W_STOCH if (_finite(k_now) and _finite(k_prev) and k_now > k_prev and k_prev <= L3_STOCH_FLOOR) else 0
    rsi_pts = L3_W_RSI if (_finite(rsi_now) and _finite(rsi_prev) and rsi_now > rsi_prev) else 0
    macd_pts = L3_W_MACD if (_finite(hist_now) and _finite(hist_prev) and hist_now > hist_prev) else 0

    detail = {
        "stoch_pts": stoch_pts, "rsi_pts": rsi_pts, "macd_pts": macd_pts,
        "k_now": k_now, "k_prev": k_prev, "rsi_now": rsi_now, "rsi_prev": rsi_prev,
        "hist_now": hist_now, "hist_prev": hist_prev,
    }
    return stoch_pts + rsi_pts + macd_pts, detail


def _stoch_flattening(latest) -> bool:
    k_now, k_prev, k_prevprev = latest.get("weekly_k_now"), latest.get("weekly_k_prev"), latest.get("weekly_k_prevprev")
    if not all(_finite(v) for v in (k_now, k_prev, k_prevprev)):
        return False
    return bool(k_now <= FLAT_ZONE and abs(k_now - k_prev) <= FLAT_TOL and (k_prev - k_prevprev) <= -FLAT_PRIOR_FALL)


def evaluate_stock(ticker: str, df: pd.DataFrame) -> EvalResult:
    """Evaluate one ticker's latest session. `df` must already carry the columns from
    gates.add_continuation_indicators, sorted ascending by date."""
    latest = df.iloc[-1]
    evaluated_session = (
        latest["date"].strftime("%Y-%m-%d") if hasattr(latest["date"], "strftime") else str(latest["date"])
    )

    l1_state = _l1_state(latest)
    not_evaluable = _l2_not_evaluable(df, latest)
    l2_age, armed = (None, False) if not_evaluable else _l2_age_and_armed(df)
    l2_state = L2_NOT_EVALUABLE if not_evaluable else (L2_ARMED if armed else L2_NOT_ARMED)

    common = dict(
        ticker=ticker,
        evaluated_session=evaluated_session,
        l1_state=l1_state,
        l2_state=l2_state,
        l2_age=l2_age,
        armed=armed,
        diagnostics={"close": latest.get("close"), "atr_14": latest.get("atr_14")},
    )

    if l1_state != L1_PASS:
        return EvalResult(status=STATUS_MONITORED, **common)

    l3_score, l3_detail = _l3_score_and_detail(latest)
    stoch_flattening = _stoch_flattening(latest)
    if armed and l3_score >= L3_QUALIFY_MIN:
        status = STATUS_QUALIFIED
    elif not armed and l3_score == 0:
        # Trend passes but neither the daily signal nor any weekly sub-check has fired -
        # nothing is actually in motion here, so this isn't "developing" toward anything;
        # it belongs with the rest of the watch-and-wait names in Monitored.
        status = STATUS_MONITORED
    else:
        status = STATUS_DEVELOPING

    return EvalResult(
        status=status,
        l3_score=l3_score,
        l3_detail=l3_detail,
        stoch_flattening=stoch_flattening,
        **common,
    )
