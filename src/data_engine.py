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
# Momentum Continuation path must never depend on V1's indicator set computed there
# - only the private _stochastic()/_resample_ohlcv() math helpers are shared.

SMA50_TREND_LOOKBACK_SESSIONS = 20
LOCATION_PROXIMITY_PCT = 3.0
OSCILLATOR_ZONE_MAX = 30


def _add_mtf_stoch(df: pd.DataFrame) -> pd.DataFrame:
    """Attaches the latest *completed* weekly and monthly Stochastic(14,3,3)
    readings to every daily row, for the Trend Gate's Layer 2 MTF momentum veto
    (Monthly %K>%D or rising; Weekly %K rising and >=20 - see evaluate_stock's
    _compute_trend_detail). A weekly/monthly resample bin is labeled by its
    period-end date, so merge_asof(direction="backward") against the daily date
    always matches the most recently *completed* higher-timeframe bar, never
    the still-forming current one. Reuses the daily OHLCV already fetched - no
    extra Tiingo calls.
    """
    weekly = _resample_ohlcv(df, "W-FRI")
    weekly["stoch_k"], weekly["stoch_d"] = _stochastic(weekly)
    weekly["stoch_k_prev"] = weekly["stoch_k"].shift(1)

    monthly = _resample_ohlcv(df, "ME")
    monthly["stoch_k"], monthly["stoch_d"] = _stochastic(monthly)
    monthly["stoch_k_prev"] = monthly["stoch_k"].shift(1)

    df = pd.merge_asof(
        df,
        weekly[["date", "stoch_k", "stoch_d", "stoch_k_prev"]].rename(
            columns={
                "stoch_k": "weekly_stoch_k",
                "stoch_d": "weekly_stoch_d",
                "stoch_k_prev": "weekly_stoch_k_prev",
            }
        ),
        on="date",
        direction="backward",
    )
    df = pd.merge_asof(
        df,
        monthly[["date", "stoch_k", "stoch_d", "stoch_k_prev"]].rename(
            columns={
                "stoch_k": "monthly_stoch_k",
                "stoch_d": "monthly_stoch_d",
                "stoch_k_prev": "monthly_stoch_k_prev",
            }
        ),
        on="date",
        direction="backward",
    )
    return df


def add_continuation_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Indicators for the Momentum Continuation setup: sma_50/sma_200/sma_50_prev20
    (Trend Gate Layer 1), monthly/weekly Stochastic (Trend Gate Layer 2 MTF veto),
    ema_8/ema_21 and the derived ema_ref/location_dpct/location_ok (Location Gate
    - EMAref is whichever of EMA8/EMA21 sits closer to price, tie -> EMA8), daily
    Stochastic(14,3,3) and the derived oscillator_trigger_ok (Oscillator Gate), and
    prev_session_high (for the non-gating recovery_observed annotation).

    Also attaches a handful of non-gating diagnostic columns that only exist to
    drive the "magnitude / direction / recency" UI messaging in status_mapping.py
    (never read by any pass/fail check here or in rules_engine.py):
    ema_ref_is_ema8 (which EMA the Location badge should name), location_dpct_prev
    and stoch_k_prev (yesterday's reading, to say "closing in" vs "drifting away"),
    and oscillator_in_zone (today's zone condition on its own, independent of
    whether the crossover also fired - distinguishes "already oversold, just
    waiting on the cross" from "not oversold yet").
    """
    df = df.copy().sort_values("date").reset_index(drop=True)

    df["sma_50"] = df["close"].rolling(50).mean()
    df["sma_200"] = df["close"].rolling(200).mean()
    df["sma_50_prev20"] = df["sma_50"].shift(SMA50_TREND_LOOKBACK_SESSIONS)

    df["ema_8"] = df["close"].ewm(span=8, adjust=False).mean()
    df["ema_21"] = df["close"].ewm(span=21, adjust=False).mean()
    df["stoch_k"], df["stoch_d"] = _stochastic(df)
    df["stoch_k_prev"] = df["stoch_k"].shift(1)

    df = _add_mtf_stoch(df)

    dist_8 = (df["close"] - df["ema_8"]).abs()
    dist_21 = (df["close"] - df["ema_21"]).abs()
    df["ema_ref_is_ema8"] = dist_8 <= dist_21
    df["ema_ref"] = df["ema_8"].where(df["ema_ref_is_ema8"], df["ema_21"])
    df["location_dpct"] = (df["close"] - df["ema_ref"]).abs() / df["ema_ref"] * 100
    df["location_dpct_prev"] = df["location_dpct"].shift(1)
    df["location_ok"] = df["location_dpct"] <= LOCATION_PROXIMITY_PCT

    crossover = (df["stoch_k"] > df["stoch_d"]) & (df["stoch_k_prev"] <= df["stoch_d"].shift(1))
    zone = (df["stoch_k"] <= OSCILLATOR_ZONE_MAX) | (df["stoch_d"] <= OSCILLATOR_ZONE_MAX)
    df["oscillator_in_zone"] = zone
    df["oscillator_trigger_ok"] = crossover & zone

    df["prev_session_high"] = df["high"].shift(1)
    return df
