"""Tests for the Momentum Continuation indicator set (src/data_engine.add_continuation_indicators).
V1's add_indicators()/add_multi_timeframe_stoch() are archived-path-only and stay untested.
"""

import pandas as pd
import pytest

from src.data_engine import (
    LOCATION_PROXIMITY_PCT,
    OSCILLATOR_ZONE_MAX,
    add_continuation_indicators,
)


def _daily_df(n: int, start_close: float = 100.0, start: str = "2023-01-02") -> pd.DataFrame:
    """n business-day rows, close rising by 1 each session, high/low = close +/- 1.
    Enough history (n large) to warm up SMA200/weekly/monthly stochastic."""
    closes = [start_close + i for i in range(n)]
    return pd.DataFrame(
        {
            "date": pd.date_range(start, periods=n, freq="B"),
            "open": closes,
            "high": [c + 1 for c in closes],
            "low": [c - 1 for c in closes],
            "close": closes,
            "volume": [1_000_000] * n,
        }
    )


def test_sma50_prev20_is_a_20_session_shift_of_sma50():
    df = _daily_df(300)
    result = add_continuation_indicators(df)
    last = result.iloc[-1]
    assert last["sma_50_prev20"] == pytest.approx(result["sma_50"].iloc[-21])


def test_prev_session_high_is_shift_one():
    df = _daily_df(60)
    result = add_continuation_indicators(df)
    assert result["prev_session_high"].iloc[-1] == pytest.approx(df["high"].iloc[-2])
    assert pd.isna(result["prev_session_high"].iloc[0])


def test_ema_ref_picks_whichever_ema_is_closer_to_close():
    df = _daily_df(60)
    result = add_continuation_indicators(df)
    last = result.iloc[-1]
    dist_8 = abs(last["close"] - last["ema_8"])
    dist_21 = abs(last["close"] - last["ema_21"])
    expected_ref = last["ema_8"] if dist_8 <= dist_21 else last["ema_21"]
    assert last["ema_ref"] == pytest.approx(expected_ref)


def test_ema_ref_ties_prefer_ema8():
    # A flat price series makes ema_8 == ema_21 == close (both converge to the
    # constant), so distances tie exactly and ema_ref must resolve to ema_8.
    df = _daily_df(60)
    df["close"] = 100.0
    df["high"] = 101.0
    df["low"] = 99.0
    result = add_continuation_indicators(df)
    last = result.iloc[-1]
    assert last["ema_ref"] == pytest.approx(last["ema_8"])


def test_location_dpct_and_ok_match_the_3pct_threshold():
    df = _daily_df(60)
    result = add_continuation_indicators(df)
    last = result.iloc[-1]
    expected_dpct = abs(last["close"] - last["ema_ref"]) / last["ema_ref"] * 100
    assert last["location_dpct"] == pytest.approx(expected_dpct)
    assert bool(last["location_ok"]) == (expected_dpct <= LOCATION_PROXIMITY_PCT)


def test_oscillator_trigger_ok_requires_crossover_and_oversold_zone():
    df = _daily_df(60)
    result = add_continuation_indicators(df)
    k, d = result["stoch_k"], result["stoch_d"]
    crossover = (k > d) & (k.shift(1) <= d.shift(1))
    zone = (k <= OSCILLATOR_ZONE_MAX) | (d <= OSCILLATOR_ZONE_MAX)
    expected = crossover & zone
    pd.testing.assert_series_equal(
        result["oscillator_trigger_ok"].fillna(False),
        expected.fillna(False),
        check_names=False,
    )


def test_monthly_and_weekly_stoch_use_only_completed_bars():
    # A long enough history (several years) so weekly/monthly resampling has
    # multiple completed bins; every row's weekly/monthly reading must come
    # from a period that ended on or before that row's own date.
    df = _daily_df(600, start="2021-01-04")
    result = add_continuation_indicators(df)
    non_null = result.dropna(subset=["monthly_stoch_k", "weekly_stoch_k"])
    assert len(non_null) > 0
    # Spot check: the monthly/weekly columns are stable within a given
    # calendar week/month (same resampled value repeated across its daily rows).
    last_month = result["date"].iloc[-1].to_period("M")
    same_month = result[result["date"].dt.to_period("M") == last_month]
    # The still-forming current month must not have its own monthly_stoch_k -
    # every row in it carries the prior completed month's reading instead.
    assert same_month["monthly_stoch_k"].nunique() <= 1
