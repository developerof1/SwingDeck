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

2026-11-18 (Round 1, docs/Momentum_Continuation_V2_ChangeNote_R1_2026-11-18.md): L1 gains a
breakdown cut (L1-R1) and a second pass route (L1-R2); the falling-knife guard (L2-R1) lives
in gates.py's l2_signal; the RSI band and turn memory (L3-R1/R2) are computed in gates.py and
read here; and Developing now needs a live trigger (S-R1) - an L1-pass row with no armed
daily signal AND no weekly point lit *this week* is Monitored, flagged NO_LIVE_TRIGGER.
"""

from dataclasses import dataclass, field

import pandas as pd

from src.setups.momentum_continuation.gates import (
    ARMED_MAX_AGE,
    FLAT_PRIOR_FALL,
    FLAT_TOL,
    FLAT_ZONE,
    L1_MIN_WEEKS,
    L1_R1_OVERBOUGHT,
    L2_MIN_SESSIONS,
    L3_LIVE_TRIGGER_MIN,
    L3_QUALIFY_MIN,
    MACD_STEADY_WEEKS,
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
    # Memory-valid score (L3-R2: points stay valid for L3_TURN_MEMORY_SESSIONS) - feeds
    # Qualified and the Weekly check marks.
    l3_score: int | None = None
    # {"stoch_pts", "rsi_pts", "k_now", "k_prev", "rsi_now", "rsi_prev"} - stoch_pts/rsi_pts
    # are the memory-valid points, matching l3_score.
    l3_detail: dict = field(default_factory=dict)
    # This week's own points only (no memory) - what S-R1's live-trigger test keys off.
    l3_score_now: int | None = None
    stoch_flattening: bool | None = None
    macd_turning: bool | None = None
    flags: list = field(default_factory=list)
    diagnostics: dict = field(default_factory=dict)


def _finite(value) -> bool:
    return value is not None and pd.notna(value)


def _py(value):
    """JSON-safe scalar for the diagnostics/log columns: numpy bool/int/float -> Python
    natives (json.dump rejects np.bool_/np.int64), NaN/None -> None."""
    if value is None:
        return None
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, float) and pd.isna(value):
        return None
    return value


def _l1_eval(latest) -> tuple[str, dict]:
    """(L1 state, the separately-logged pieces: l1_slope_pass, l1_r2_pass, l1_mroll,
    l1_below21_2w, l1_r1_cut).

    L1 = PASS if (slope_pass or r2_pass) and not r1_cut, else FAIL:
      slope_pass (original test, zero tolerance): EMA50 higher than L1_LOOKBACK_WEEKS ago.
      r2_pass (L1-R2): weekly close above both EMA50 and EMA21, EMA21 rising over
        L1_R2_RISE_WEEKS.
      r1_cut (L1-R1 breakdown): monthly stoch rolled over from overbought (mK < mD and the
        highest mK of the last L1_R1_MONTHS months >= L1_R1_OVERBOUGHT) AND the weekly close
        below EMA21 for L1_R1_BELOW_WEEKS straight completed weeks. Releases on its own as
        soon as either half stops being true. A missing monthly stoch (short history) means
        no cut, never an error."""
    pieces = {
        "l1_slope_pass": False, "l1_r2_pass": False, "l1_mroll": False,
        "l1_below21_2w": bool(latest.get("l1_below21_2w") or False), "l1_r1_cut": False,
    }
    completed_weeks = latest.get("l1_completed_weeks")
    if completed_weeks is None or completed_weeks < L1_MIN_WEEKS:
        return L1_NOT_EVALUABLE, pieces
    ema50, ema50_4w_ago = latest.get("l1_ema50"), latest.get("l1_ema50_4w_ago")
    if not (_finite(ema50) and _finite(ema50_4w_ago)):
        return L1_NOT_EVALUABLE, pieces

    pieces["l1_slope_pass"] = bool(ema50 > ema50_4w_ago)

    wclose, ema21, ema21_prior = latest.get("l1_wclose"), latest.get("wk_ema21"), latest.get("wk_ema21_prior")
    if all(_finite(v) for v in (wclose, ema21, ema21_prior)):
        pieces["l1_r2_pass"] = bool(wclose > ema50 and wclose > ema21 and ema21 > ema21_prior)

    mo_k, mo_d, mo_kmax = latest.get("mo_K"), latest.get("mo_D"), latest.get("mo_Kmax4")
    if all(_finite(v) for v in (mo_k, mo_d, mo_kmax)):
        pieces["l1_mroll"] = bool(mo_k < mo_d and mo_kmax >= L1_R1_OVERBOUGHT)
    pieces["l1_r1_cut"] = pieces["l1_mroll"] and pieces["l1_below21_2w"]

    passed = (pieces["l1_slope_pass"] or pieces["l1_r2_pass"]) and not pieces["l1_r1_cut"]
    return (L1_PASS if passed else L1_FAIL), pieces


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


def _points(latest, key: str) -> int:
    value = latest.get(key)
    return int(value) if _finite(value) else 0


def _l3_scores_and_detail(latest) -> tuple[int, int, dict]:
    """(score, score_now, detail). The weekly point logic itself (stoch rising from the
    floor; RSI rising inside the L3_RSI_LO..L3_RSI_HI band; 25-session turn memory) is
    computed per session in gates.py - this just sums what it handed over:
    score = memory-valid stoch + RSI points (Qualified, Weekly check marks);
    score_now = this week's own points only (S-R1 live trigger)."""
    k_now, k_prev = latest.get("weekly_k_now"), latest.get("weekly_k_prev")
    rsi_now, rsi_prev = latest.get("weekly_rsi_now"), latest.get("weekly_rsi_prev")

    stoch_pts, rsi_pts = _points(latest, "stoch_pts_mem"), _points(latest, "rsi_pts_mem")
    score_now = _points(latest, "stoch_pt_now") + _points(latest, "rsi_pt_now")

    detail = {
        "stoch_pts": stoch_pts, "rsi_pts": rsi_pts,
        "k_now": k_now, "k_prev": k_prev, "rsi_now": rsi_now, "rsi_prev": rsi_prev,
    }
    return stoch_pts + rsi_pts, score_now, detail


def _stoch_flattening(latest) -> bool:
    k_now, k_prev, k_prevprev = latest.get("weekly_k_now"), latest.get("weekly_k_prev"), latest.get("weekly_k_prevprev")
    if not all(_finite(v) for v in (k_now, k_prev, k_prevprev)):
        return False
    return bool(k_now <= FLAT_ZONE and abs(k_now - k_prev) <= FLAT_TOL and (k_prev - k_prevprev) <= -FLAT_PRIOR_FALL)


def _macd_hist_steady_decline(latest) -> bool:
    """MACD_HIST_STEADY_DECLINE ("MACD TURNING" badge, informational only - no score
    weight, no status effect). A red (negative) histogram that has been shrinking toward
    zero for MACD_STEADY_WEEKS straight completed bars, with today's (possibly
    in-progress) bar continuing the rise - deliberately anchored to the negative zone,
    not sign-agnostic: a red histogram shrinking is a building-reversal signal, a green
    one growing is just existing momentum."""
    hist_now = latest.get("weekly_hist_now")
    completed = [latest.get(f"weekly_hist_t{i}") for i in range(1, MACD_STEADY_WEEKS + 1)]
    if not _finite(hist_now) or not all(_finite(v) for v in completed):
        return False
    if not all(h < 0 for h in completed):
        return False
    # completed[0] is t-1 (most recent), completed[-1] is t-MACD_STEADY_WEEKS (oldest) -
    # each must be less negative than the one before it.
    if not all(completed[i] > completed[i + 1] for i in range(len(completed) - 1)):
        return False
    return bool(hist_now > completed[0])


def _log_columns(latest, l1_pieces: dict, score_now: int, no_live_trigger: bool) -> dict:
    """The Round 1 daily-log columns (change note "Log additions"), stored under the row's
    `diagnostics` so each new rule can be reviewed on its own."""
    return {
        **l1_pieces,
        "wk_ema21": _py(latest.get("wk_ema21")),
        "mo_K": _py(latest.get("mo_K")),
        "mo_D": _py(latest.get("mo_D")),
        "mo_Kmax4": _py(latest.get("mo_Kmax4")),
        "l2_knife": bool(latest.get("l2_knife") or False),
        "stoch_pt_now": _points(latest, "stoch_pt_now"),
        "rsi_pt_now": _points(latest, "rsi_pt_now"),
        "score_now": score_now,
        "no_live_trigger": no_live_trigger,
    }


def evaluate_stock(ticker: str, df: pd.DataFrame) -> EvalResult:
    """Evaluate one ticker's latest session. `df` must already carry the columns from
    gates.add_continuation_indicators, sorted ascending by date."""
    latest = df.iloc[-1]
    evaluated_session = (
        latest["date"].strftime("%Y-%m-%d") if hasattr(latest["date"], "strftime") else str(latest["date"])
    )

    l1_state, l1_pieces = _l1_eval(latest)
    not_evaluable = _l2_not_evaluable(df, latest)
    l2_age, armed = (None, False) if not_evaluable else _l2_age_and_armed(df)
    l2_state = L2_NOT_EVALUABLE if not_evaluable else (L2_ARMED if armed else L2_NOT_ARMED)
    score_now = _points(latest, "stoch_pt_now") + _points(latest, "rsi_pt_now")

    common = dict(
        ticker=ticker,
        evaluated_session=evaluated_session,
        l1_state=l1_state,
        l2_state=l2_state,
        l2_age=l2_age,
        armed=armed,
    )

    def diagnostics(no_live_trigger: bool) -> dict:
        return {
            "close": latest.get("close"), "atr_14": latest.get("atr_14"),
            **_log_columns(latest, l1_pieces, score_now, no_live_trigger),
        }

    if l1_state != L1_PASS:
        flags = []
        if l1_state == L1_FAIL:
            flags.append("L1_FAILED")
            if l1_pieces["l1_r1_cut"]:
                flags.append("L1_BREAKDOWN")
            if armed:
                flags.append("DAILY_SIGNAL_IN")
        else:
            flags.append("NOT_EVALUABLE")
        return EvalResult(status=STATUS_MONITORED, flags=flags, diagnostics=diagnostics(False), **common)

    l3_score, l3_score_now, l3_detail = _l3_scores_and_detail(latest)
    stoch_flattening = _stoch_flattening(latest)
    macd_turning = _macd_hist_steady_decline(latest)
    flags: list = []
    no_live_trigger = False
    if armed and l3_score >= L3_QUALIFY_MIN:
        status = STATUS_QUALIFIED
    elif armed or l3_score_now >= L3_LIVE_TRIGGER_MIN:
        status = STATUS_DEVELOPING
    else:
        # S-R1: Trend passes but there is no live trigger - no armed daily signal and no
        # weekly point lit this week (a point carried by turn memory doesn't count). Nothing
        # to time right now, so it sits with the watch-and-wait names in Monitored.
        status = STATUS_MONITORED
        no_live_trigger = True
        flags.append("NO_LIVE_TRIGGER")

    return EvalResult(
        status=status,
        l3_score=l3_score,
        l3_score_now=l3_score_now,
        l3_detail=l3_detail,
        stoch_flattening=stoch_flattening,
        macd_turning=macd_turning,
        flags=flags,
        diagnostics=diagnostics(no_live_trigger),
        **common,
    )
