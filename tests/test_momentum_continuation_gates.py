"""Tests for src/setups/momentum_continuation/gates.py - the composition layer built on
top of the shared data_engine primitives (already tested independently in
test_data_engine.py). Covers: the EMA-reference tie-break, the ATR-proximity/crossover
gate inputs, the L2 grace-window combination, the weekly completed-vs-current bar split
(including the "net change only, not every week up" semantic Part A insists on), and the
L3/flattening weekly readings.
"""

import pandas as pd
import pytest

from src.data_engine import resample_ohlcv
from src.setups.momentum_continuation.gates import (
    L1_MIN_WEEKS,
    L2_ATR_K,
    L2_GRACE,
    L2_KNIFE_ATR_K,
    L3_TURN_MEMORY_SESSIONS,
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


# --- Round 1 (change note 2026-11-18) ---


def _wiggly_daily_df(n: int = 400, seed: int = 7) -> pd.DataFrame:
    import numpy as np

    rng = np.random.default_rng(seed)
    closes = 100 + np.cumsum(rng.normal(0, 1.5, n))
    return pd.DataFrame(
        {
            "date": pd.date_range("2021-01-04", periods=n, freq="B"),
            "open": closes,
            "high": closes + 1.0,
            "low": closes - 1.0,
            "close": closes,
            "volume": [1_000_000] * n,
        }
    )


class TestL2KnifeGuard:
    def test_knife_flag_matches_the_formula(self):
        result = add_continuation_indicators(_wiggly_daily_df())
        expected = result["close"] < result["ema_21"] - L2_KNIFE_ATR_K * result["atr_14"]
        pd.testing.assert_series_equal(result["l2_knife"], expected, check_names=False)

    def test_a_knife_day_voids_an_otherwise_valid_signal(self):
        result = add_continuation_indicators(_wiggly_daily_df())
        prox = result["prox_day"].astype(int).rolling(L2_GRACE, min_periods=1).max().astype(bool)
        cross = result["cross_day"].astype(int).rolling(L2_GRACE, min_periods=1).max().astype(bool)
        raw = prox & cross
        assert (raw & result["l2_knife"]).any(), "fixture must contain a signal that the guard actually voids"
        pd.testing.assert_series_equal(result["l2_signal"], raw & ~result["l2_knife"], check_names=False)
        assert not (result["l2_signal"] & result["l2_knife"]).any()


class TestL3Points:
    @staticmethod
    def _points(monkeypatch, k_prev, k_now, rsi_prev, rsi_now):
        from src.setups.momentum_continuation import gates

        weekly = pd.DataFrame({"close": [1.0, 1.0, 1.0, 1.0]})
        monkeypatch.setattr(gates, "stochastic", lambda *_a, **_k: (pd.Series([50, 50, k_prev, k_now]), None))
        monkeypatch.setattr(gates, "rsi", lambda *_a, **_k: pd.Series([50, 50, rsi_prev, rsi_now]))
        return gates._l3_points(weekly)

    @pytest.mark.parametrize(
        "k_prev,k_now,expected",
        [
            (15.0, 25.0, 2),  # rising from below the floor
            (35.0, 45.0, 0),  # rising but prev above the floor
            (20.0, 30.0, 2),  # floor is inclusive
            (15.0, 10.0, 0),  # prev below floor but not rising
        ],
    )
    def test_stoch_point_needs_rising_from_at_or_below_the_floor(self, monkeypatch, k_prev, k_now, expected):
        assert self._points(monkeypatch, k_prev, k_now, 50.0, 40.0)[0] == expected

    @pytest.mark.parametrize(
        "rsi_prev,rsi_now,expected",
        [
            (39.2, 52.8, 2),  # ASO 2026-09-09: early-zone turn
            (45.0, 59.0, 2),  # ASO 2026-09-11
            (35.0, 40.0, 2),  # lower band edge inclusive
            (55.0, 60.0, 2),  # upper band edge inclusive
            (65.0, 73.3, 0),  # ASO 2026-11-13: rising but above the band -> no point (L3-R1)
            (69.0, 69.5, 0),  # BJ latest
            (30.0, 39.9, 0),  # rising but still below the band
            (60.0, 55.0, 0),  # inside the band but falling
            (50.0, 50.0, 0),  # flat is not rising
        ],
    )
    def test_rsi_point_needs_rising_and_inside_the_40_60_band(self, monkeypatch, rsi_prev, rsi_now, expected):
        assert self._points(monkeypatch, 50.0, 40.0, rsi_prev, rsi_now)[1] == expected

    def test_short_weekly_history_scores_zero(self):
        from src.setups.momentum_continuation import gates

        assert gates._l3_points(pd.DataFrame({"close": [1.0, 2.0]})) == (0, 0)


class TestL3TurnMemory:
    @staticmethod
    def _scripted(monkeypatch, per_session):
        """per_session: list of (stoch_pt, rsi_pt) oldest -> newest, one per replayed session."""
        from src.setups.momentum_continuation import gates

        calls = iter(per_session)
        monkeypatch.setattr(gates, "_l3_points", lambda _weekly: next(calls))
        return gates

    def test_a_point_stays_valid_for_25_sessions_today_included(self, monkeypatch):
        df = _daily_df(100, step=1.0)
        points = [(0, 0)] * L3_TURN_MEMORY_SESSIONS
        points[0] = (2, 2)  # the oldest of the replayed sessions: still inside the window
        gates = self._scripted(monkeypatch, points)
        cols = gates._l3_turn_columns(df, gates._weekly_series(df))
        assert cols["stoch_pts_mem"] == 2 and cols["rsi_pts_mem"] == 2
        assert cols["stoch_pt_now"] == 0 and cols["rsi_pt_now"] == 0  # today's own reading

    def test_only_the_last_25_sessions_are_replayed(self, monkeypatch):
        from src.setups.momentum_continuation import gates

        df = _daily_df(100, step=1.0)
        replayed = []
        monkeypatch.setattr(gates, "_l3_points", lambda _weekly: replayed.append(1) or (0, 0))
        cols = gates._l3_turn_columns(df, gates._weekly_series(df))
        assert len(replayed) == L3_TURN_MEMORY_SESSIONS  # a point from session 26 back is never seen
        assert cols["stoch_pts_mem"] == 0 and cols["rsi_pts_mem"] == 0

    def test_today_counts_and_is_reported_as_now(self, monkeypatch):
        df = _daily_df(100, step=1.0)
        points = [(0, 0)] * (L3_TURN_MEMORY_SESSIONS - 1) + [(2, 0)]
        gates = self._scripted(monkeypatch, points)
        cols = gates._l3_turn_columns(df, gates._weekly_series(df))
        assert cols["stoch_pt_now"] == 2 and cols["stoch_pts_mem"] == 2
        assert cols["rsi_pt_now"] == 0 and cols["rsi_pts_mem"] == 0

    def test_each_session_is_replayed_point_in_time_not_from_the_full_history(self, monkeypatch):
        from src.setups.momentum_continuation import gates

        df = _daily_df(100, step=1.0)
        seen_closes = []
        monkeypatch.setattr(gates, "_l3_points", lambda weekly: seen_closes.append(weekly["close"].iloc[-1]) or (0, 0))
        gates._l3_turn_columns(df, gates._weekly_series(df))
        # the weekly bar at each replayed session closes at that session's own close
        assert seen_closes == list(df["close"].iloc[-L3_TURN_MEMORY_SESSIONS:])

    def test_engine_inputs_exist_on_the_real_pipeline(self):
        result = add_continuation_indicators(_wiggly_daily_df()).iloc[-1]
        for col in ("stoch_pt_now", "rsi_pt_now", "stoch_pts_mem", "rsi_pts_mem"):
            assert result[col] in (0, 2)
        assert result["stoch_pts_mem"] >= result["stoch_pt_now"]
        assert result["rsi_pts_mem"] >= result["rsi_pt_now"]


class TestL1R1R2Columns:
    def test_below_ema21_needs_both_of_the_last_two_completed_weeks(self):
        both = [100.0 + w for w in range(58)] + [60.0, 55.0]
        one = [100.0 + w for w in range(59)] + [55.0]
        assert bool(add_continuation_indicators(_weekly_daily_df(both)).iloc[-1]["l1_below21_2w"]) is True
        assert bool(add_continuation_indicators(_weekly_daily_df(one)).iloc[-1]["l1_below21_2w"]) is False

    def test_r2_columns_are_the_completed_week_ema21_and_its_value_two_weeks_ago(self):
        weekly_closes = [100.0 + w for w in range(60)]
        last = add_continuation_indicators(_weekly_daily_df(weekly_closes)).iloc[-1]
        ema21 = pd.Series(weekly_closes).ewm(span=21, adjust=False).mean()
        assert last["l1_wclose"] == pytest.approx(weekly_closes[-1])
        assert last["wk_ema21"] == pytest.approx(ema21.iloc[-1])
        assert last["wk_ema21_prior"] == pytest.approx(ema21.iloc[-3])

    def test_r2_columns_use_completed_weeks_only(self):
        weekly_closes = [100.0 + w for w in range(60)]
        df = _weekly_daily_df(weekly_closes)
        df = pd.concat([df, pd.DataFrame({
            "date": [df["date"].iloc[-1] + pd.tseries.offsets.BDay(1)],
            "open": [500.0], "high": [501.0], "low": [499.0], "close": [500.0], "volume": [1_000_000],
        })], ignore_index=True)
        last = add_continuation_indicators(df).iloc[-1]
        assert last["l1_wclose"] == pytest.approx(weekly_closes[-1])

    def test_monthly_stoch_is_none_with_too_little_history(self):
        last = add_continuation_indicators(_weekly_daily_df([100.0 + w for w in range(60)])).iloc[-1]  # ~14 months
        assert last["l1_completed_weeks"] >= L1_MIN_WEEKS
        assert pd.isna(last["mo_K"]) and pd.isna(last["mo_D"]) and pd.isna(last["mo_Kmax4"])

    def test_monthly_stoch_is_computed_with_enough_history(self):
        last = add_continuation_indicators(_wiggly_daily_df(600)).iloc[-1]
        assert pd.notna(last["mo_K"]) and pd.notna(last["mo_D"])
        assert last["mo_Kmax4"] >= last["mo_K"]

    def test_monthly_stoch_ignores_the_month_of_the_last_completed_weekly_bar(self):
        df = _wiggly_daily_df(600)
        before = add_continuation_indicators(df).iloc[-1]
        weekly = resample_ohlcv(df, "W-FRI")
        completed_end = weekly["date"].iloc[-1] if df["date"].iloc[-1].weekday() == 4 else weekly["date"].iloc[-2]
        month_start = completed_end.replace(day=1).normalize()
        # Wreck every session from that month onward: the monthly stoch may only read months
        # that ended before the month of the last completed weekly bar.
        tampered = df.copy()
        mask = tampered["date"] >= month_start
        tampered.loc[mask, ["open", "high", "low", "close"]] = tampered.loc[mask, ["open", "high", "low", "close"]] * 3
        after = add_continuation_indicators(tampered).iloc[-1]
        for col in ("mo_K", "mo_D", "mo_Kmax4"):
            assert after[col] == pytest.approx(before[col])
