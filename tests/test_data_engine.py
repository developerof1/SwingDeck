"""Tests for the Momentum Continuation indicator set (src/data_engine.add_continuation_indicators).
V1's add_indicators()/add_multi_timeframe_stoch() are archived-path-only and stay untested.
"""

import pandas as pd
import pytest

from src.data_engine import EMA20_ALPHA, add_continuation_indicators


def _constant_range_df(n: int = 30, start_close: float = 100.0) -> pd.DataFrame:
    """n rows, close rising by 1 each session, high/low = close +/- 1 - gives a
    constant true range of 2.0 every session, so Wilder ATR14 has an exact,
    hand-verifiable value (2.0) once seeded."""
    closes = [start_close + i for i in range(n)]
    return pd.DataFrame(
        {
            "date": pd.date_range("2025-01-01", periods=n, freq="B"),
            "open": closes,
            "high": [c + 1 for c in closes],
            "low": [c - 1 for c in closes],
            "close": closes,
            "volume": [1_000_000] * n,
        }
    )


def test_wilder_atr_converges_to_constant_true_range():
    df = add_continuation_indicators(_constant_range_df(30))
    # Seed lands at index 14 (ATR_PERIOD); every TR before/after is exactly 2.0,
    # so Wilder's recursive smoothing should hold at exactly 2.0 from the seed on.
    atr_from_seed = df["atr_14"].iloc[14:]
    assert atr_from_seed.notna().all()
    assert atr_from_seed.apply(lambda v: v == pytest.approx(2.0)).all()
    assert df["atr_14"].iloc[:14].isna().all()


def test_ema20_uses_alpha_2_over_21_not_v1s_span21_constant():
    assert EMA20_ALPHA == pytest.approx(2 / 21)

    df = _constant_range_df(10)
    result = add_continuation_indicators(df)

    # Independently reimplement the ewm(adjust=False) recursion with alpha=2/21
    # and compare row-by-row - a tautology-free check that the right constant
    # actually reached pandas' .ewm() call, not just that EMA20_ALPHA equals 2/21.
    closes = df["close"].tolist()
    expected = [closes[0]]
    for c in closes[1:]:
        expected.append((1 - EMA20_ALPHA) * expected[-1] + EMA20_ALPHA * c)

    for actual, exp in zip(result["ema_20"].tolist(), expected):
        assert actual == pytest.approx(exp)


def test_highest_high_20_is_inclusive_of_evaluation_session():
    df = _constant_range_df(25)
    result = add_continuation_indicators(df)
    # close/high rise monotonically, so the 20-session highest high is always the
    # evaluation session's own high (inclusive window).
    last = result.iloc[-1]
    assert last["highest_high_20"] == pytest.approx(last["high"])


def test_close_3ago_and_sma50_prev20_are_simple_shifts():
    # sma_50 needs 50 rows to produce a first non-NaN value, and sma_50_prev20
    # shifts that by another 20 - 80 rows leaves the last row well past both.
    df = _constant_range_df(80)
    result = add_continuation_indicators(df)
    last = result.iloc[-1]
    assert last["close_3ago"] == pytest.approx(df["close"].iloc[-4])
    assert last["sma_50_prev20"] == pytest.approx(result["sma_50"].iloc[-21])


def test_prev_session_high_is_shift_one():
    df = _constant_range_df(10)
    result = add_continuation_indicators(df)
    assert result["prev_session_high"].iloc[-1] == pytest.approx(df["high"].iloc[-2])
    assert pd.isna(result["prev_session_high"].iloc[0])
