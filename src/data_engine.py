"""Shared OHLCV/indicator toolkit - the only things here are primitives more than one
setup could plausibly need: normalizing raw Tiingo rows, resampling to a higher
timeframe, and the handful of technical indicators (Stochastic, ATR, RSI, MACD) with
their periods as real parameters, not hardcoded. Each setup composes these with its own
periods/thresholds in its own package (see src/setups/momentum_continuation/gates.py) -
this file has no opinion on what any setup does with them.

Also still holds the archived V1 engine's own indicator set (add_indicators,
add_multi_timeframe_stoch, snapshot_tail) - V1 isn't a registered Setup and is no longer
wired into the live app (see src/rules_engine_v1.py), kept only for reference/rollback.
"""

import pandas as pd


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


def stochastic(df: pd.DataFrame, k_period: int = 14, k_smooth: int = 3, d_smooth: int = 3) -> tuple[pd.Series, pd.Series]:
    """Stochastic %K/%D, periods as real parameters (not hardcoded) so any setup/timeframe
    can call this with its own periods - e.g. stochastic(df, k_period=7, k_smooth=3,
    d_smooth=3) for a setup that wants a faster read, without touching any other
    caller's periods. Shared across the daily series and, resampled, the weekly/monthly
    series, so every timeframe agrees on the formula."""
    low = df["low"].rolling(k_period).min()
    high = df["high"].rolling(k_period).max()
    raw_k = 100 * (df["close"] - low) / (high - low)
    k = raw_k.rolling(k_smooth).mean()
    d = k.rolling(d_smooth).mean()
    return k, d


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    """Average True Range, Wilder's RMA smoothing (ewm(alpha=1/n, adjust=False)) to match
    TradingView's default ATR. TR = max(H-L, |H-prevC|, |L-prevC|)."""
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def rsi(series: pd.Series, n: int = 14) -> pd.Series:
    """Wilder RSI: up/down moves each smoothed with the same Wilder RMA as atr() above,
    so both match TradingView's default RSI rather than a plain SMA-based variant."""
    diff = series.diff()
    up = diff.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    down = (-diff.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = up / down
    return 100 - 100 / (1 + rs)


def macd_hist(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.Series:
    """MACD histogram: macd = EMA(fast) - EMA(slow); signal = EMA(signal) of macd;
    hist = macd - signal. All EMAs ewm(adjust=False), matching TradingView's default."""
    macd = series.ewm(span=fast, adjust=False).mean() - series.ewm(span=slow, adjust=False).mean()
    signal_line = macd.ewm(span=signal, adjust=False).mean()
    return macd - signal_line


def resample_ohlcv(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    """Resample daily OHLCV to a higher timeframe (e.g. "W-FRI", "ME"). The bin
    containing today's date is necessarily partial/in-progress if today isn't that bin's
    period-end - callers that need "completed bars only" must exclude it themselves
    (see gates.py's weekly-bar-completeness check), this function just aggregates
    whatever sessions exist in each bin."""
    agg = df.set_index("date").resample(rule).agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    )
    return agg.dropna(subset=["close"]).reset_index()


# --- Archived V1 engine's indicator set (src/rules_engine_v1.py) ---
# Not a registered Setup, no longer wired into the live app - kept for reference/rollback
# only. Uses the shared stochastic()/resample_ohlcv() above, nothing else here.


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Append SMA 50/200, EMA 8/21, Stochastic(14,3,3), and RVOL columns."""
    df = df.copy()

    df["sma_50"] = df["close"].rolling(50).mean()
    df["sma_200"] = df["close"].rolling(200).mean()
    df["ema_8"] = df["close"].ewm(span=8, adjust=False).mean()
    df["ema_21"] = df["close"].ewm(span=21, adjust=False).mean()
    df["stoch_k"], df["stoch_d"] = stochastic(df)

    vol_sma_20 = df["volume"].rolling(20).mean()
    df["rvol"] = df["volume"] / vol_sma_20

    return df


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

    weekly = resample_ohlcv(df, "W-FRI")
    weekly["stoch_k"], weekly["stoch_d"] = stochastic(weekly)
    weekly["stoch_k_prev"] = weekly["stoch_k"].shift(1)
    weekly["stoch_d_prev"] = weekly["stoch_d"].shift(1)

    monthly = resample_ohlcv(df, "ME")
    monthly["stoch_k"], _monthly_d = stochastic(monthly)
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
