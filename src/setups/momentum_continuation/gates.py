"""Gate-input columns for the Momentum Continuation setup (docs/Momentum Continuation -
V2 Logic Improvement Specs.md, Part A). Composes the shared primitives in
src/data_engine.py (stochastic/resample_ohlcv/atr/rsi/macd_hist) with this setup's own
periods/thresholds (the config block below) into the columns rules_engine.py reads to
decide L1/L2/L3. Self-contained: only data_engine's shared math helpers are reused, no
column names or state leak in from the archived V1 engine.
"""

import pandas as pd

from src.data_engine import atr, macd_hist, resample_ohlcv, rsi, stochastic

# --- Config (Part A §1) - every value tunable here, no code change needed elsewhere ---

L1_EMA_WEEKS = 50
L1_LOOKBACK_WEEKS = 4
L1_MIN_WEEKS = 54

L2_ATR_N = 14
L2_ATR_K = 1.5
L2_EMA_FAST = 8
L2_EMA_SLOW = 21
L2_STOCH = (14, 3, 3)
L2_ZONE = 30
L2_GRACE = 3
# Floor below which ATR/EMA/Stoch readings exist but are too thinly warmed-up to trust -
# mirrors L1_MIN_WEEKS's NOT_EVALUABLE gate, just for L2's own (much shorter) daily clock.
L2_MIN_SESSIONS = 60
ARMED_MAX_AGE = 5

L3_MIN_WEEKS = 40
L3_STOCH = (14, 3, 3)
L3_RSI_N = 14
L3_MACD = (12, 26, 9)
L3_STOCH_FLOOR = 20
L3_W_STOCH = 2
L3_W_RSI = 2
L3_QUALIFY_MIN = 4

# MACD_HIST_STEADY_DECLINE badge ("MACD TURNING"): number of trailing completed weekly
# bars that must show a shrinking red histogram - see _l3_columns below and
# rules_engine._macd_hist_steady_decline. Purely informational, no score weight.
MACD_STEADY_WEEKS = 3

FLAT_ZONE = 30
FLAT_TOL = 5
FLAT_PRIOR_FALL = 5


def _weekly_series(df: pd.DataFrame) -> pd.DataFrame:
    """The full weekly series, last row included even if it's only Monday - that row IS
    the spec's "current weekly bar" (Monday open through today's close), used as-is by
    L3. A weekly bar is complete once the last session of that week has closed; this
    checks only the common case (today is a Friday) and intentionally does not special-
    case holiday-shortened weeks (e.g. a Wednesday pre-Thanksgiving close) - such a week
    is treated as "still in progress" one or two sessions longer than strictly necessary,
    then resolves itself once the following week's first session pushes it off the "last
    row" position. Flagged per the spec's own ask (§12) to surface ambiguity rather than
    guess at a holiday calendar."""
    return resample_ohlcv(df, "W-FRI")


def _completed_weekly(df: pd.DataFrame, weekly: pd.DataFrame) -> pd.DataFrame:
    """weekly, minus the in-progress last bar - unless today's session is itself a
    Friday, in which case that bar is already complete (see _weekly_series)."""
    today_is_friday = df["date"].iloc[-1].weekday() == 4
    return weekly if today_is_friday else weekly.iloc[:-1]


def _l1_columns(df: pd.DataFrame, weekly: pd.DataFrame) -> dict:
    completed = _completed_weekly(df, weekly)
    n = len(completed)
    if n < L1_MIN_WEEKS:
        return {"l1_ema50": None, "l1_ema50_4w_ago": None, "l1_completed_weeks": n}
    # EMA, not SMA: SMA50's week-over-week increment stays positive long after a sharp
    # reversal, until price drops below whatever closed 50 weeks ago - on a name like ARM
    # that pre-rally anchor sits far below the current price, so SMA50 structurally can't
    # catch the rollover within any reasonable timeframe. EMA50 weights recent closes far
    # more heavily and has no hard 50-bar cutoff, so it reflects the rollover instead.
    ema50 = completed["close"].ewm(span=L1_EMA_WEEKS, adjust=False).mean()
    return {
        "l1_ema50": ema50.iloc[-1],
        "l1_ema50_4w_ago": ema50.iloc[-1 - L1_LOOKBACK_WEEKS] if n >= L1_EMA_WEEKS + L1_LOOKBACK_WEEKS else None,
        "l1_completed_weeks": n,
    }


def _l3_columns(weekly: pd.DataFrame) -> dict:
    """Current (possibly in-progress) vs. the prior completed bar, and the bar before
    that (for the flattening badge's "prior fall" check) - always weekly.iloc[-1]/[-2]/
    [-3], regardless of whether today is a Friday: on a Friday the current bar already
    equals the completed bar (same row), but "prev" for the week-over-week delta is
    still last week's bar either way.

    weekly_hist_t1..t{MACD_STEADY_WEEKS} are the MACD_HIST_STEADY_DECLINE badge's trailing
    completed-bar window (t1 = last completed bar, t2 the one before that, ...) - kept as
    individual scalar columns, not a list, since a list value would break the "same value
    on every row" broadcast add_continuation_indicators relies on. None until there's
    enough weekly history for the full window.
    """
    hist_cols = {f"weekly_hist_t{i}": None for i in range(1, MACD_STEADY_WEEKS + 1)}
    if len(weekly) < 3:
        return {
            "weekly_k_now": None, "weekly_k_prev": None, "weekly_k_prevprev": None,
            "weekly_rsi_now": None, "weekly_rsi_prev": None,
            "weekly_hist_now": None, **hist_cols,
        }
    k, _d = stochastic(weekly, *L3_STOCH)
    weekly_rsi = rsi(weekly["close"], L3_RSI_N)
    weekly_hist = macd_hist(weekly["close"], *L3_MACD)
    if len(weekly) >= MACD_STEADY_WEEKS + 1:
        hist_cols = {f"weekly_hist_t{i}": weekly_hist.iloc[-1 - i] for i in range(1, MACD_STEADY_WEEKS + 1)}
    return {
        "weekly_k_now": k.iloc[-1], "weekly_k_prev": k.iloc[-2], "weekly_k_prevprev": k.iloc[-3],
        "weekly_rsi_now": weekly_rsi.iloc[-1], "weekly_rsi_prev": weekly_rsi.iloc[-2],
        "weekly_hist_now": weekly_hist.iloc[-1], **hist_cols,
    }


def add_continuation_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Indicators for the Momentum Continuation setup: daily atr_14/ema_8/ema_21 and the
    derived ema_ref/prox_day (L2 ATR-proximity leg), daily Stochastic(14,3,3) and the
    derived cross_day (L2 crossover leg), l2_signal (prox_day AND cross_day, each true on
    any of the last L2_GRACE sessions, independently), plus the weekly-derived L1/L3
    readings (same value on every row - only the latest row's is ever read, see
    rules_engine.evaluate_stock) and the flattening badge's inputs.
    """
    df = df.copy().sort_values("date").reset_index(drop=True)

    df["atr_14"] = atr(df, L2_ATR_N)
    df["ema_8"] = df["close"].ewm(span=L2_EMA_FAST, adjust=False).mean()
    df["ema_21"] = df["close"].ewm(span=L2_EMA_SLOW, adjust=False).mean()
    df["stoch_k"], df["stoch_d"] = stochastic(df, *L2_STOCH)
    df["stoch_k_prev"] = df["stoch_k"].shift(1)
    df["stoch_d_prev"] = df["stoch_d"].shift(1)

    dist_8 = (df["close"] - df["ema_8"]).abs()
    dist_21 = (df["close"] - df["ema_21"]).abs()
    df["ema_ref_is_ema8"] = dist_8 <= dist_21
    df["ema_ref"] = df["ema_8"].where(df["ema_ref_is_ema8"], df["ema_21"])
    df["prox_day"] = (df["close"] - df["ema_ref"]).abs() <= L2_ATR_K * df["atr_14"]

    df["cross_day"] = (
        (df["stoch_k"] > df["stoch_d"])
        & (df["stoch_k_prev"] <= df["stoch_d_prev"])
        & ((df["stoch_k"] <= L2_ZONE) | (df["stoch_d"] <= L2_ZONE))
    )

    prox_recent = df["prox_day"].astype(int).rolling(L2_GRACE, min_periods=1).max().astype(bool)
    cross_recent = df["cross_day"].astype(int).rolling(L2_GRACE, min_periods=1).max().astype(bool)
    df["l2_signal"] = prox_recent & cross_recent

    weekly = _weekly_series(df)
    for key, value in {**_l1_columns(df, weekly), **_l3_columns(weekly)}.items():
        df[key] = value

    return df
