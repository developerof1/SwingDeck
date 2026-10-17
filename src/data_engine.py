"""Converts raw Tiingo OHLCV rows into an indicator-enriched DataFrame.

Only the long-window indicators (needing the full fetched history) live here:
SMA 50/200, EMA 8/21, Stochastic(14,3,3), RVOL. Short-window values that only ever
need the last few rows (prior-day high, 5-day swing low) are computed inside
rules_engine.evaluate_stock() instead, so they work identically whether it's given
the full history or just a short tail-end snapshot (see app.py).
"""

import pandas as pd

MIN_HISTORY_DAYS = 300


def to_dataframe(raw_prices: list[dict]) -> pd.DataFrame:
    """Normalize Tiingo's raw JSON rows into a sorted OHLCV DataFrame using adjusted prices."""
    df = pd.DataFrame(raw_prices)
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    df = df.rename(
        columns={
            "adjOpen": "open",
            "adjHigh": "high",
            "adjLow": "low",
            "adjClose": "close",
            "adjVolume": "volume",
        }
    )
    return df[["date", "open", "high", "low", "close", "volume"]]


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Append SMA 50/200, EMA 8/21, Stochastic(14,3,3), and RVOL columns."""
    df = df.copy()

    df["sma_50"] = df["close"].rolling(50).mean()
    df["sma_200"] = df["close"].rolling(200).mean()
    df["ema_8"] = df["close"].ewm(span=8, adjust=False).mean()
    df["ema_21"] = df["close"].ewm(span=21, adjust=False).mean()

    low_14 = df["low"].rolling(14).min()
    high_14 = df["high"].rolling(14).max()
    raw_k = 100 * (df["close"] - low_14) / (high_14 - low_14)
    df["stoch_k"] = raw_k.rolling(3).mean()
    df["stoch_d"] = df["stoch_k"].rolling(3).mean()

    vol_sma_20 = df["volume"].rolling(20).mean()
    df["rvol"] = df["volume"] / vol_sma_20

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
]


def snapshot_tail(df: pd.DataFrame, rows: int = 5) -> list[dict]:
    """Serialize the last `rows` of indicator-enriched data for storage in results.json.

    This is enough for rules_engine.evaluate_stock() to be re-run later (e.g. when the
    UI's threshold sliders move) without needing the full fetched history or another
    Tiingo call - the 5-day swing low and prior-day-high calculations only ever look at
    the last few rows, and SMA/EMA/Stochastic are already computed here, not re-derived.
    """
    tail = df.tail(rows).copy()
    tail["date"] = tail["date"].dt.strftime("%Y-%m-%d")
    return tail[SNAPSHOT_COLUMNS].to_dict("records")
