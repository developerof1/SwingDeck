"""Tests for the shared OHLCV/indicator toolkit (src/data_engine.py) - to_dataframe,
stochastic/resample_ohlcv (newly public), and the new atr/rsi/macd_hist primitives.
Scope is deliberately just the shared math itself; any one setup's own use of these
(periods, thresholds, composition into gate columns) is tested in that setup's own
test file (e.g. tests/test_momentum_continuation_gates.py). The archived V1 engine's
add_indicators()/add_multi_timeframe_stoch() stay untested, per the long-standing
project convention (no behavior change, not wired into the live app).
"""

import pandas as pd
import pytest

from src.data_engine import atr, macd_hist, resample_ohlcv, rsi, stochastic


def _ohlc_df(closes: list[float], highs: list[float] | None = None, lows: list[float] | None = None) -> pd.DataFrame:
    n = len(closes)
    return pd.DataFrame(
        {
            "date": pd.date_range("2024-01-02", periods=n, freq="B"),
            "open": closes,
            "high": highs or [c + 1 for c in closes],
            "low": lows or [c - 1 for c in closes],
            "close": closes,
            "volume": [1_000_000] * n,
        }
    )


def _wilder_rma(values: list[float], n: int) -> list[float]:
    """Independent reimplementation of Wilder's RMA (ewm(alpha=1/n, adjust=False)) as a
    plain recursive loop, to check atr()/rsi() against something other than a second
    call to the same pandas primitive they're built on."""
    out = [values[0]]
    alpha = 1 / n
    for v in values[1:]:
        out.append(alpha * v + (1 - alpha) * out[-1])
    return out


def test_atr_matches_an_independent_wilder_rma_reimplementation():
    df = _ohlc_df(closes=[100, 102, 101, 105, 103, 108], highs=[101, 103, 103, 106, 105, 109], lows=[99, 100, 100, 102, 102, 104])
    true_ranges = [2.0]  # first TR = high-low (no prior close)
    highs, lows, closes = df["high"].tolist(), df["low"].tolist(), df["close"].tolist()
    for i in range(1, len(closes)):
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
        true_ranges.append(tr)
    expected = _wilder_rma(true_ranges, 14)

    result = atr(df, n=14).tolist()
    for got, want in zip(result, expected):
        assert got == pytest.approx(want)


def test_rsi_matches_an_independent_wilder_rma_reimplementation():
    closes = [44, 44.5, 44.3, 44.8, 45.1, 45.0, 45.4, 45.9, 46.2, 46.0, 45.7, 46.1]
    df = _ohlc_df(closes=closes)
    diffs = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    ups = [max(d, 0) for d in diffs]
    downs = [max(-d, 0) for d in diffs]
    up_rma = _wilder_rma(ups, 5)
    down_rma = _wilder_rma(downs, 5)
    expected = [100 - 100 / (1 + u / d) if d else 100.0 for u, d in zip(up_rma, down_rma)]

    result = rsi(df["close"], n=5).tolist()[1:]  # first row has no diff, both sides NaN
    for got, want in zip(result, expected):
        assert got == pytest.approx(want)


def test_macd_hist_matches_an_independent_ema_reimplementation():
    closes = [10, 11, 10.5, 12, 13, 12.5, 14, 13.5, 15, 16, 15.5, 17, 18, 17.5, 19]
    df = _ohlc_df(closes=closes)

    def _ema(values: list[float], span: int) -> list[float]:
        alpha = 2 / (span + 1)
        out = [values[0]]
        for v in values[1:]:
            out.append(alpha * v + (1 - alpha) * out[-1])
        return out

    fast, slow = _ema(closes, 3), _ema(closes, 6)
    macd = [f - s for f, s in zip(fast, slow)]
    signal = _ema(macd, 4)
    expected = [m - s for m, s in zip(macd, signal)]

    result = macd_hist(df["close"], fast=3, slow=6, signal=4).tolist()
    for got, want in zip(result, expected):
        assert got == pytest.approx(want)


def test_stochastic_periods_are_real_parameters_not_hardcoded():
    df = _ohlc_df(closes=[100 + i for i in range(20)])
    k_14, _d_14 = stochastic(df, k_period=14, k_smooth=3, d_smooth=3)
    k_7, _d_7 = stochastic(df, k_period=7, k_smooth=3, d_smooth=3)
    # Different periods must produce different warm-up lengths (first non-NaN index).
    assert k_14.first_valid_index() > k_7.first_valid_index()


def test_resample_ohlcv_aggregates_open_high_low_close_volume():
    df = _ohlc_df(closes=[100, 101, 102], highs=[101, 103, 104], lows=[99, 100, 101])
    df["date"] = pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-03"])  # all one week (Mon-Wed)
    weekly = resample_ohlcv(df, "W-FRI")
    assert len(weekly) == 1
    row = weekly.iloc[0]
    assert row["open"] == 100
    assert row["high"] == 104
    assert row["low"] == 99
    assert row["close"] == 102
