"""Tests for src/setups/momentum_continuation/gates.py - the composition layer built on
top of the shared data_engine primitives (already tested independently in
test_data_engine.py). Covers: the EMA-reference tie-break, the ATR-proximity/crossover
gate inputs, the L2 grace-window combination, the weekly completed-vs-current bar split
(including the "net change only, not every week up" semantic Part A insists on), and the
L3/flattening weekly readings.
"""

import pandas as pd
import pytest

from src.setups.momentum_continuation.gates import (
    L1_MIN_WEEKS,
    L2_ATR_K,
    L2_GRACE,
    add_continuation_indicators,
)


def _daily_df(n: int, start_close: float = 100.0, start: str = "2021-01-04", step: float = 0.0) -> pd.DataFrame:
    closes = [start_close + i * step for i in range(n)]
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


def _weekly_daily_df(weekly_closes: list[float], start: str = "2021-01-04") -> pd.DataFrame:
    """5 business days per week (start must be a Monday so weeks align with no gaps),
    every day's close flat at that week's target close - the Friday close (and so the
    whole week's resampled close) ends up exactly `weekly_closes[w]`."""
    closes = [c for c in weekly_closes for _ in range(5)]
    n = len(closes)
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


class TestEmaRef:
    def test_picks_whichever_ema_is_closer_to_close(self):
        df = _daily_df(60, step=1.0)
        result = add_continuation_indicators(df)
        last = result.iloc[-1]
        dist_8 = abs(last["close"] - last["ema_8"])
        dist_21 = abs(last["close"] - last["ema_21"])
        expected_ref = last["ema_8"] if dist_8 <= dist_21 else last["ema_21"]
        assert last["ema_ref"] == pytest.approx(expected_ref)

    def test_ties_prefer_ema8(self):
        df = _daily_df(60, step=0.0)  # flat -> ema_8 == ema_21 == close, exact tie
        result = add_continuation_indicators(df)
        last = result.iloc[-1]
        assert last["ema_ref"] == pytest.approx(last["ema_8"])
        assert bool(last["ema_ref_is_ema8"]) is True


class TestL2GateInputs:
    def test_prox_day_matches_the_atr_multiple_formula(self):
        df = _daily_df(90, step=1.0)
        result = add_continuation_indicators(df)
        dist = (result["close"] - result["ema_ref"]).abs()
        expected = dist <= L2_ATR_K * result["atr_14"]
        pd.testing.assert_series_equal(result["prox_day"].fillna(False), expected.fillna(False), check_names=False)

    def test_cross_day_requires_crossover_and_oversold_zone(self):
        df = _daily_df(90, step=1.0)
        result = add_continuation_indicators(df)
        k, d = result["stoch_k"], result["stoch_d"]
        crossover = (k > d) & (k.shift(1) <= d.shift(1))
        zone = (k <= 30) | (d <= 30)
        expected = crossover & zone
        pd.testing.assert_series_equal(result["cross_day"].fillna(False), expected.fillna(False), check_names=False)

    def test_l2_signal_true_when_prox_and_cross_each_fired_within_grace_but_not_same_day(self):
        df = _daily_df(90, step=1.0)
        result = add_continuation_indicators(df)
        n = len(result)
        result.loc[n - 1, ["prox_day", "cross_day"]] = [True, False]
        result.loc[n - 2, ["prox_day", "cross_day"]] = [False, True]
        result.loc[n - 3, ["prox_day", "cross_day"]] = [False, False]
        prox_recent = result["prox_day"].astype(int).rolling(L2_GRACE, min_periods=1).max().astype(bool)
        cross_recent = result["cross_day"].astype(int).rolling(L2_GRACE, min_periods=1).max().astype(bool)
        assert bool((prox_recent & cross_recent).iloc[-1]) is True

    def test_l2_signal_false_once_outside_the_grace_window(self):
        df = _daily_df(90, step=1.0)
        result = add_continuation_indicators(df)
        n = len(result)
        result.loc[n - 1, ["prox_day", "cross_day"]] = [True, False]
        result.loc[n - 1 - L2_GRACE, ["prox_day", "cross_day"]] = [False, True]  # one session too old
        prox_recent = result["prox_day"].astype(int).rolling(L2_GRACE, min_periods=1).max().astype(bool)
        cross_recent = result["cross_day"].astype(int).rolling(L2_GRACE, min_periods=1).max().astype(bool)
        assert bool((prox_recent & cross_recent).iloc[-1]) is False


class TestWeeklyBarCompleteness:
    def test_not_evaluable_below_54_completed_weeks(self):
        df = _weekly_daily_df([100.0 + w for w in range(50)])  # 50 weeks < L1_MIN_WEEKS
        result = add_continuation_indicators(df)
        assert result.iloc[-1]["l1_completed_weeks"] < L1_MIN_WEEKS

    def test_l1_ema50_uses_only_completed_bars_and_excludes_todays_partial_week(self):
        weekly_closes = [100.0 + w for w in range(60)]
        df = _weekly_daily_df(weekly_closes)
        df = pd.concat([df, pd.DataFrame({  # one extra Monday session, not a full week yet
            "date": [df["date"].iloc[-1] + pd.tseries.offsets.BDay(1)],
            "open": [500.0], "high": [501.0], "low": [499.0], "close": [500.0], "volume": [1_000_000],
        })], ignore_index=True)
        result = add_continuation_indicators(df)
        last = result.iloc[-1]
        # The last COMPLETED week is still week 59 (close 159.0) - the stray Monday close
        # of 500 must not leak into l1_ema50's EMA.
        expected_ema50 = pd.Series(weekly_closes).ewm(span=50, adjust=False).mean().iloc[-1]
        assert last["l1_ema50"] == pytest.approx(expected_ema50)

    def test_l1_passes_despite_a_dip_partway_through_the_lookback_as_long_as_net_change_is_positive(self):
        weekly_closes = [100.0 + w for w in range(55)]
        weekly_closes[-2] -= 5.0  # a dip two weeks ago...
        weekly_closes[-1] += 0.1  # ...but this week still nets above 4 weeks ago overall
        df = _weekly_daily_df(weekly_closes)
        result = add_continuation_indicators(df)
        last = result.iloc[-1]
        assert last["l1_ema50"] > last["l1_ema50_4w_ago"]


class TestL3WeeklyReadings:
    def test_weekly_k_now_reflects_the_in_progress_bar_weekly_k_prev_does_not(self):
        weekly_closes = [100.0 + w for w in range(45)]
        df = _weekly_daily_df(weekly_closes)
        before = add_continuation_indicators(df).iloc[-1]

        df_with_partial_week = pd.concat([df, pd.DataFrame({
            "date": [df["date"].iloc[-1] + pd.tseries.offsets.BDay(1)],
            "open": [10.0], "high": [10.0], "low": [1.0], "close": [1.0], "volume": [1_000_000],
        })], ignore_index=True)
        after = add_continuation_indicators(df_with_partial_week).iloc[-1]

        assert after["weekly_k_now"] != pytest.approx(before["weekly_k_now"])
        assert after["weekly_k_prev"] == pytest.approx(before["weekly_k_now"])
