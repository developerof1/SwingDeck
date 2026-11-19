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

# Round 1 (change note 2026-11-18). All provisional.
# L1-R2 second pass route: weekly close above EMA50 and EMA(L1_R2_EMA_WEEKS), with that
# EMA higher than L1_R2_RISE_WEEKS ago.
L1_R2_EMA_WEEKS = 21
L1_R2_RISE_WEEKS = 2
# L1-R1 breakdown cut: monthly stoch rolled over from overbought (K < D and max K over the
# last L1_R1_MONTHS completed months >= L1_R1_OVERBOUGHT) AND weekly close below
# EMA(L1_R1_EMA_WEEKS) for L1_R1_BELOW_WEEKS consecutive completed weeks.
L1_R1_OVERBOUGHT = 80
L1_R1_MONTHS = 4
L1_R1_BELOW_WEEKS = 2
L1_R1_EMA_WEEKS = 21
L1_R1_MONTHLY_STOCH = (14, 3, 3)

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
# L2-R1 falling-knife guard: no daily signal on a day when Close < EMA21 - K * ATR14.
L2_KNIFE_ATR_K = 1.0

L3_MIN_WEEKS = 40
L3_STOCH = (14, 3, 3)
L3_RSI_N = 14
L3_MACD = (12, 26, 9)
L3_STOCH_FLOOR = 20
L3_W_STOCH = 2
L3_W_RSI = 2
L3_QUALIFY_MIN = 4
# L3-R1: the RSI point only counts while RSI is rising inside [L3_RSI_LO, L3_RSI_HI].
L3_RSI_LO = 40
L3_RSI_HI = 60
# L3-R2: a stoch/RSI point stays valid this many sessions (today included) after it last lit.
L3_TURN_MEMORY_SESSIONS = 25
# S-R1: an L1-pass row is Developing only if armed OR this week's own (not memory-valid)
# score_now reaches this - otherwise Monitored / NO_LIVE_TRIGGER.
L3_LIVE_TRIGGER_MIN = 2

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


def _monthly_stoch_columns(df: pd.DataFrame, completed: pd.DataFrame) -> dict:
    """Monthly Stochastic for L1-R1, on completed calendar months only: months that ended
    before the month of the last completed weekly bar's date (so a month still in progress
    - or the month that bar itself falls in - never feeds it). mo_K/mo_D are the latest
    completed month's readings, mo_Kmax4 the highest K over the last L1_R1_MONTHS completed
    months. All None when the monthly stoch isn't computable (fewer than ~18 completed
    months): L1-R1 can then never fire, which is the "False if not computable" rule."""
    empty = {"mo_K": None, "mo_D": None, "mo_Kmax4": None}
    month_start = completed["date"].iloc[-1].replace(day=1).normalize()
    prior = df[df["date"] < month_start]
    if prior.empty:
        return empty
    monthly = resample_ohlcv(prior, "ME")
    k, d = stochastic(monthly, *L1_R1_MONTHLY_STOCH)
    k_tail = k.iloc[-L1_R1_MONTHS:]
    if len(k_tail) < L1_R1_MONTHS or k_tail.isna().any() or pd.isna(d.iloc[-1]):
        return empty
    return {"mo_K": k.iloc[-1], "mo_D": d.iloc[-1], "mo_Kmax4": k_tail.max()}


def _l1_columns(df: pd.DataFrame, weekly: pd.DataFrame) -> dict:
    completed = _completed_weekly(df, weekly)
    n = len(completed)
    if n < L1_MIN_WEEKS:
        return {
            "l1_ema50": None, "l1_ema50_4w_ago": None, "l1_completed_weeks": n,
            "l1_wclose": None, "wk_ema21": None, "wk_ema21_prior": None, "l1_below21_2w": False,
            "mo_K": None, "mo_D": None, "mo_Kmax4": None,
        }
    # EMA, not SMA: SMA50's week-over-week increment stays positive long after a sharp
    # reversal, until price drops below whatever closed 50 weeks ago - on a name like ARM
    # that pre-rally anchor sits far below the current price, so SMA50 structurally can't
    # catch the rollover within any reasonable timeframe. EMA50 weights recent closes far
    # more heavily and has no hard 50-bar cutoff, so it reflects the rollover instead.
    wclose = completed["close"]
    ema50 = wclose.ewm(span=L1_EMA_WEEKS, adjust=False).mean()
    # L1-R2's EMA21 (and its value L1_R2_RISE_WEEKS ago) and L1-R1's own EMA, kept as two
    # separately configured spans even though both default to 21.
    ema_r2 = wclose.ewm(span=L1_R2_EMA_WEEKS, adjust=False).mean()
    ema_r1 = wclose.ewm(span=L1_R1_EMA_WEEKS, adjust=False).mean()
    below = bool(all(wclose.iloc[-i] < ema_r1.iloc[-i] for i in range(1, L1_R1_BELOW_WEEKS + 1)))
    return {
        "l1_ema50": ema50.iloc[-1],
        "l1_ema50_4w_ago": ema50.iloc[-1 - L1_LOOKBACK_WEEKS] if n >= L1_EMA_WEEKS + L1_LOOKBACK_WEEKS else None,
        "l1_completed_weeks": n,
        "l1_wclose": wclose.iloc[-1],
        "wk_ema21": ema_r2.iloc[-1],
        "wk_ema21_prior": ema_r2.iloc[-1 - L1_R2_RISE_WEEKS],
        "l1_below21_2w": below,
        **_monthly_stoch_columns(df, completed),
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


def _l3_points(weekly: pd.DataFrame) -> tuple[int, int]:
    """(stoch_pt, rsi_pt) for the weekly series' last (possibly in-progress) bar vs. the
    one before it - the "_now" points of L3-R1/R2, each L3_W_* or 0:
    stoch: K rising from at/below L3_STOCH_FLOOR; RSI (L3-R1): rising AND inside
    [L3_RSI_LO, L3_RSI_HI] - an early-zone check, not a late "already above 50" one."""
    if len(weekly) < 3:
        return 0, 0
    k, _d = stochastic(weekly, *L3_STOCH)
    weekly_rsi = rsi(weekly["close"], L3_RSI_N)
    k_now, k_prev = k.iloc[-1], k.iloc[-2]
    rsi_now, rsi_prev = weekly_rsi.iloc[-1], weekly_rsi.iloc[-2]
    stoch_pt = L3_W_STOCH if (pd.notna(k_now) and pd.notna(k_prev) and k_now > k_prev and k_prev <= L3_STOCH_FLOOR) else 0
    rsi_pt = (
        L3_W_RSI
        if (pd.notna(rsi_now) and pd.notna(rsi_prev) and rsi_now > rsi_prev and L3_RSI_LO <= rsi_now <= L3_RSI_HI)
        else 0
    )
    return stoch_pt, rsi_pt


def _l3_turn_columns(df: pd.DataFrame, weekly: pd.DataFrame) -> dict:
    """L3-R2 turn memory. Replays each of the last L3_TURN_MEMORY_SESSIONS sessions
    point-in-time (the weekly series as it stood at the close of that session, in-progress
    bar included) to get that day's stoch/RSI points; a point is "memory-valid" if it lit
    on any of those sessions, today included. *_pt_now is today's own reading (what S-R1
    keys off), *_pts the memory-valid value (what feeds Qualified and the Weekly checks)."""
    n = len(df)
    points = [
        _l3_points(weekly if i == n - 1 else _weekly_series(df.iloc[: i + 1]))
        for i in range(max(0, n - L3_TURN_MEMORY_SESSIONS), n)
    ]
    stoch_now, rsi_now = points[-1]
    return {
        "stoch_pt_now": stoch_now,
        "rsi_pt_now": rsi_now,
        "stoch_pts_mem": max(p[0] for p in points),
        "rsi_pts_mem": max(p[1] for p in points),
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
    # L2-R1 falling-knife guard: a signal day is void when Close sits more than
    # L2_KNIFE_ATR_K ATRs below the daily EMA21 (NaN ATR/EMA -> comparison False -> no knife).
    df["l2_knife"] = df["close"] < df["ema_21"] - L2_KNIFE_ATR_K * df["atr_14"]
    df["l2_signal"] = prox_recent & cross_recent & ~df["l2_knife"]

    weekly = _weekly_series(df)
    for key, value in {**_l1_columns(df, weekly), **_l3_columns(weekly), **_l3_turn_columns(df, weekly)}.items():
        df[key] = value

    return df
