"""Converts raw Tiingo OHLCV rows into an indicator-enriched DataFrame.

Only the long-window indicators (needing the full fetched history) live here:
SMA 50/200, EMA 8/21, Stochastic(14,3,3), RVOL, plus the weekly/monthly Stochastic
readings used for the multi-timeframe Oscillator Trigger check (rules_engine.py Step
4). Short-window values that only ever need the last few rows (prior-day high, 5-day
swing low) are computed inside rules_engine.evaluate_stock() instead, so they work
identically whether it's given the full history or just a short tail-end snapshot
(see app.py).
"""

import pandas as pd

MIN_HISTORY_DAYS = 300


def to_dataframe(raw_prices: list[dict]) -> pd.DataFrame:
    """Normalize Tiingo's raw JSON rows into a sorted OHLCV DataFrame using adjusted prices.

    Tiingo's response includes both raw (open/high/low/close/volume) and
    adjusted (adjOpen/adjHigh/...) columns. We select only the adjusted ones
    *before* renaming - renaming in place would collide with the raw columns
    of the same target name and silently produce duplicate columns.
    """
    df = pd.DataFrame(raw_prices)
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    df = df[["date", "adjOpen", "adjHigh", "adjLow", "adjClose", "adjVolume"]]
    return df.rename(
        columns={
            "adjOpen": "open",
            "adjHigh": "high",
            "adjLow": "low",
            "adjClose": "close",
            "adjVolume": "volume",
        }
    )


def _stochastic(df: pd.DataFrame, k_period: int = 14, k_smooth: int = 3, d_smooth: int = 3) -> tuple[pd.Series, pd.Series]:
    """Shared Stochastic %K/%D math - used for the daily series and, resampled, for
    the weekly/monthly series below, so all three timeframes agree on the formula."""
    low = df["low"].rolling(k_period).min()
    high = df["high"].rolling(k_period).max()
    raw_k = 100 * (df["close"] - low) / (high - low)
    k = raw_k.rolling(k_smooth).mean()
    d = k.rolling(d_smooth).mean()
    return k, d


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Append SMA 50/200, EMA 8/21, Stochastic(14,3,3), and RVOL columns."""
    df = df.copy()

    df["sma_50"] = df["close"].rolling(50).mean()
    df["sma_200"] = df["close"].rolling(200).mean()
    df["ema_8"] = df["close"].ewm(span=8, adjust=False).mean()
    df["ema_21"] = df["close"].ewm(span=21, adjust=False).mean()
    df["stoch_k"], df["stoch_d"] = _stochastic(df)

    vol_sma_20 = df["volume"].rolling(20).mean()
    df["rvol"] = df["volume"] / vol_sma_20

    return df


def _resample_ohlcv(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    agg = df.set_index("date").resample(rule).agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    )
    return agg.dropna(subset=["close"]).reset_index()


def add_multi_timeframe_stoch(df: pd.DataFrame) -> pd.DataFrame:
    """Attach the weekly and monthly Stochastic readings to every daily row, for the
    Step 4 Oscillator Trigger's multi-timeframe alignment check: monthly %K rising +
    weekly %K/%D cross-up, alongside the existing daily condition (Elder Triple
    Screen-style - see swingdeck-reversal-view-future memory for the related, but
    explicitly separate, reversal-trade idea this is NOT part of).

    Reuses the daily OHLCV already fetched - no extra Tiingo calls. A weekly/monthly
    resample bin is labeled by its period-end date, so merge_asof(direction="backward")
    against the daily date always matches the most recently *completed* higher-
    timeframe bar, never the still-forming current one - keeps the signal stable
    instead of flickering as an in-progress week/month develops.
    """
    df = df.copy().sort_values("date")

    weekly = _resample_ohlcv(df, "W-FRI")
    weekly["stoch_k"], weekly["stoch_d"] = _stochastic(weekly)
    weekly["stoch_k_prev"] = weekly["stoch_k"].shift(1)
    weekly["stoch_d_prev"] = weekly["stoch_d"].shift(1)

    monthly = _resample_ohlcv(df, "ME")
    monthly["stoch_k"], _monthly_d = _stochastic(monthly)
    monthly["stoch_k_prev"] = monthly["stoch_k"].shift(1)

    df = pd.merge_asof(
        df,
        weekly[["date", "stoch_k", "stoch_d", "stoch_k_prev", "stoch_d_prev"]].rename(
            columns={
                "stoch_k": "weekly_stoch_k",
                "stoch_d": "weekly_stoch_d",
                "stoch_k_prev": "weekly_stoch_k_prev",
                "stoch_d_prev": "weekly_stoch_d_prev",
            }
        ),
        on="date",
        direction="backward",
    )
    df = pd.merge_asof(
        df,
        monthly[["date", "stoch_k", "stoch_k_prev"]].rename(
            columns={"stoch_k": "monthly_stoch_k", "stoch_k_prev": "monthly_stoch_k_prev"}
        ),
        on="date",
        direction="backward",
    )
    return df


SNAPSHOT_COLUMNS = [
    "date",
    "high",
    "low",
    "close",
    "sma_50",
    "sma_200",
    "ema_8",
    "ema_21",
    "stoch_k",
    "stoch_d",
    "rvol",
    "weekly_stoch_k",
    "weekly_stoch_d",
    "weekly_stoch_k_prev",
    "weekly_stoch_d_prev",
    "monthly_stoch_k",
    "monthly_stoch_k_prev",
]


def snapshot_tail(df: pd.DataFrame, rows: int = 5) -> list[dict]:
    """Serialize the last `rows` of indicator-enriched data for storage in results.json.

    This is enough for rules_engine.evaluate_stock() to be re-run later (e.g. when the
    UI's threshold sliders move) without needing the full fetched history or another
    Tiingo call - the 5-day swing low and prior-day-high calculations only ever look at
    the last few rows, and SMA/EMA/Stochastic (daily and weekly/monthly) are already
    computed here, not re-derived. Requires add_multi_timeframe_stoch to have already
    been run, or the weekly_*/monthly_* columns won't exist.
    """
    tail = df.tail(rows).copy()
    tail["date"] = tail["date"].dt.strftime("%Y-%m-%d")
    return tail[SNAPSHOT_COLUMNS].to_dict("records")


# --- Momentum Continuation indicators (see docs/Momentum Continuation V2 — Logic.md) ---
# Self-contained and deliberately independent of add_indicators() above: the live
# Momentum Continuation path must never depend on V1's indicator set (ema_8, stoch,
# rvol, weekly/monthly stoch), which only src/rules_engine_v1.py (archived) still uses.

ATR_PERIOD = 14
# Conventional alpha = 2/(N+1) for a 20-period EMA. NOT the same constant as V1's
# ema_21 column (span=21 -> alpha=2/22) - this is simply the standard 20-period EMA
# formula, spelled out as alpha per the spec's exact wording.
EMA20_ALPHA = 2 / 21
HIGHEST_HIGH_WINDOW = 20
DECLINE_LOOKBACK_SESSIONS = 3
SMA50_TREND_LOOKBACK_SESSIONS = 20


def _wilder_atr(df: pd.DataFrame, period: int = ATR_PERIOD) -> pd.Series:
    """Wilder-smoothed Average True Range. Seeded with a simple average of the
    first `period` true-range values, then recursively smoothed - the standard
    Wilder method, distinct from an EMA-based ATR."""
    prev_close = df["close"].shift(1)
    true_range = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    atr = pd.Series(index=df.index, dtype="float64")
    seed_idx = period  # true_range[0] is NaN (no prev_close); the first `period`
    # valid true-range values are indices 1..period, so the seed lands at index `period`.
    if len(true_range) > seed_idx:
        atr.iloc[seed_idx] = true_range.iloc[1 : seed_idx + 1].mean()
        for i in range(seed_idx + 1, len(true_range)):
            atr.iloc[i] = (atr.iloc[i - 1] * (period - 1) + true_range.iloc[i]) / period
    return atr


def add_continuation_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Indicators for the Momentum Continuation setup: sma_50/sma_200 (trend gate),
    sma_50_prev20 (SMA50 20 sessions ago, for the "still rising" trend condition),
    atr_14 (Wilder), ema_20 (alpha=2/21), highest_high_20 (rolling max of `high`
    over the latest 20 sessions, inclusive of the evaluation session),
    close_3ago (close 3 sessions ago, for the recent-decline condition), and
    prev_session_high (for the optional recovery_observed annotation).
    """
    df = df.copy()
    df["sma_50"] = df["close"].rolling(50).mean()
    df["sma_200"] = df["close"].rolling(200).mean()
    df["sma_50_prev20"] = df["sma_50"].shift(SMA50_TREND_LOOKBACK_SESSIONS)
    df["atr_14"] = _wilder_atr(df)
    df["ema_20"] = df["close"].ewm(alpha=EMA20_ALPHA, adjust=False).mean()
    df["highest_high_20"] = df["high"].rolling(HIGHEST_HIGH_WINDOW).max()
    df["close_3ago"] = df["close"].shift(DECLINE_LOOKBACK_SESSIONS)
    df["prev_session_high"] = df["high"].shift(1)
    return df
