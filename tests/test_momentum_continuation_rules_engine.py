"""Acceptance tests for src/setups/momentum_continuation/rules_engine.py, following
docs/Momentum Continuation - V2 Logic Improvement Specs.md Part A §4-7 / Part B's
Combine table: L1 structural-bull gate, L2 daily-signal (independent of L1, with an
"armed" recency window), L3 weekly-confirmation score (direction-of-change only), and
the resulting Qualified/Developing/Monitored status.

Builds minimal DataFrames with directly-set, already-derived gate-input columns (rather
than deriving them from raw OHLCV) so each test controls exactly the condition(s) under
examination - gates.py's own indicator math is covered separately in
test_momentum_continuation_gates.py. `overrides` lets a test touch specific sessions
within L2's lookback, keyed by offset-from-today (0 = today).
"""

import pandas as pd
import pytest

from src.setups.momentum_continuation.gates import ARMED_MAX_AGE, L1_R1_OVERBOUGHT, L2_MIN_SESSIONS, L3_QUALIFY_MIN
from src.setups.momentum_continuation.rules_engine import (
    L1_FAIL,
    L1_NOT_EVALUABLE,
    L1_PASS,
    L2_ARMED,
    L2_NOT_ARMED,
    L2_NOT_EVALUABLE,
    STATUS_DEVELOPING,
    STATUS_MONITORED,
    STATUS_QUALIFIED,
    evaluate_stock,
)

# A row that cleanly passes L1, has L2 armed today (age 0), and scores 4/4 on L3.
# weekly_hist_t1/t2/t3 (all finite, non-negative here) deliberately keep the
# MACD_HIST_STEADY_DECLINE badge false on the baseline - it's an informational flag, not
# part of the score, and its own tests set these explicitly.
BASELINE_ROW = {
    "atr_14": 2.0,
    "ema_ref": 100.0,
    "close": 100.0,
    "stoch_k": 25.0,
    "stoch_d": 20.0,
    "stoch_k_prev": 30.0,
    "stoch_d_prev": 35.0,
    "l2_signal": True,
    "l1_completed_weeks": 60,
    "l1_ema50": 105.0,
    "l1_ema50_4w_ago": 100.0,  # rising -> L1 PASS
    "weekly_k_now": 25.0,
    "weekly_k_prev": 15.0,  # rising from <=20 floor -> stoch_pts
    "weekly_k_prevprev": 40.0,
    "weekly_rsi_now": 55.0,
    "weekly_rsi_prev": 50.0,  # rising -> rsi_pts
    "weekly_hist_now": 0.5,
    "weekly_hist_t1": 0.2,
    "weekly_hist_t2": 0.1,
    "weekly_hist_t3": 0.05,
    # Round 1: L3 points are computed per session in gates.py (RSI band, 25-session turn
    # memory) and only summed here. Baseline = both lit this week (so memory-valid too).
    "stoch_pt_now": 2,
    "rsi_pt_now": 2,
    "stoch_pts_mem": 2,
    "rsi_pts_mem": 2,
    # L1-R1/R2 inputs (weekly close vs EMA21, monthly stoch) - baseline is a clean, rising,
    # not-rolled-over name: only the original slope test is what passes it.
    "l1_wclose": 110.0,
    "wk_ema21": 108.0,
    "wk_ema21_prior": 109.0,  # EMA21 falling -> R2 route off, slope test is the only pass
    "l1_below21_2w": False,
    "mo_K": 60.0,
    "mo_D": 55.0,
    "mo_Kmax4": 70.0,
    "l2_knife": False,
}


def _df(n: int = L2_MIN_SESSIONS + 10, overrides: dict[int, dict] | None = None) -> pd.DataFrame:
    base = {col: [val] * n for col, val in BASELINE_ROW.items()}
    df = pd.DataFrame(base)
    df["date"] = pd.date_range("2024-01-01", periods=n, freq="B")
    if overrides:
        for offset, cols in overrides.items():
            idx = n - 1 - offset
            for col, val in cols.items():
                df.loc[idx, col] = val
    return df


def _not_armed(**today) -> dict:
    """Overrides that clear the whole armed window, plus any column overrides for today."""
    overrides = {offset: {"l2_signal": False} for offset in range(ARMED_MAX_AGE + 1)}
    overrides[0] = {"l2_signal": False, **today}
    return overrides


class TestL1Gate:
    def test_passes_when_ema50_net_change_over_4_weeks_is_positive(self):
        result = evaluate_stock("TEST", _df())
        assert result.l1_state == L1_PASS

    def test_fails_when_ema50_net_change_is_negative(self):
        df = _df(overrides={0: {"l1_ema50": 95.0, "l1_ema50_4w_ago": 100.0}})
        result = evaluate_stock("TEST", df)
        assert result.l1_state == L1_FAIL
        assert result.status == STATUS_MONITORED

    def test_boundary_is_strict_not_inclusive(self):
        df = _df(overrides={0: {"l1_ema50": 100.0, "l1_ema50_4w_ago": 100.0}})
        result = evaluate_stock("TEST", df)
        assert result.l1_state == L1_FAIL

    def test_not_evaluable_below_54_completed_weeks(self):
        df = _df(overrides={0: {"l1_completed_weeks": 53}})
        result = evaluate_stock("TEST", df)
        assert result.l1_state == L1_NOT_EVALUABLE
        assert result.status == STATUS_MONITORED

    def test_not_evaluable_when_ema50_values_missing_despite_enough_weeks(self):
        df = _df(overrides={0: {"l1_ema50_4w_ago": float("nan")}})
        result = evaluate_stock("TEST", df)
        assert result.l1_state == L1_NOT_EVALUABLE


class TestL2Gate:
    def test_armed_today_has_age_zero(self):
        result = evaluate_stock("TEST", _df())
        assert result.l2_state == L2_ARMED
        assert result.armed is True
        assert result.l2_age == 0

    @pytest.mark.parametrize("age", [1, 3, 5])
    def test_armed_within_5_sessions_reports_correct_age(self, age):
        # Clear the whole lookback window first (baseline defaults l2_signal True on every
        # row), then set only the target offset - otherwise an untouched baseline-True row
        # closer to today would mask the age this test is trying to pin down.
        overrides = {offset: {"l2_signal": False} for offset in range(ARMED_MAX_AGE + 1)}
        overrides[age] = {"l2_signal": True}
        df = _df(overrides=overrides)
        result = evaluate_stock("TEST", df)
        assert result.l2_state == L2_ARMED
        assert result.armed is True
        assert result.l2_age == age

    def test_not_armed_once_signal_ages_past_5_sessions(self):
        overrides = {offset: {"l2_signal": False} for offset in range(ARMED_MAX_AGE + 1)}
        overrides[ARMED_MAX_AGE + 1] = {"l2_signal": True}
        df = _df(overrides=overrides)
        result = evaluate_stock("TEST", df)
        assert result.l2_state == L2_NOT_ARMED
        assert result.armed is False
        assert result.l2_age is None

    def test_not_evaluable_below_min_sessions(self):
        df = _df(n=L2_MIN_SESSIONS - 1)
        result = evaluate_stock("TEST", df)
        assert result.l2_state == L2_NOT_EVALUABLE
        assert result.armed is False

    def test_not_evaluable_when_a_required_field_is_missing(self):
        df = _df(overrides={0: {"atr_14": float("nan")}})
        result = evaluate_stock("TEST", df)
        assert result.l2_state == L2_NOT_EVALUABLE

    def test_resolves_independently_of_l1_fail(self):
        df = _df(overrides={0: {"l1_ema50": 95.0, "l1_ema50_4w_ago": 100.0}})
        result = evaluate_stock("TEST", df)
        assert result.l1_state == L1_FAIL
        assert result.l2_state == L2_ARMED
        assert result.armed is True

    def test_resolves_independently_of_l1_not_evaluable(self):
        df = _df(overrides={0: {"l1_completed_weeks": 10}})
        result = evaluate_stock("TEST", df)
        assert result.l1_state == L1_NOT_EVALUABLE
        assert result.l2_state == L2_ARMED
        assert result.armed is True


class TestL3Score:
    """The engine only sums the per-session points gates.py hands it (point *logic* - stoch
    floor, RSI band, turn memory - is tested in test_momentum_continuation_gates.py)."""

    def test_full_score_from_both_components_turning(self):
        result = evaluate_stock("TEST", _df())
        assert result.l3_score == 4
        assert result.l3_score_now == 4
        assert result.status == STATUS_QUALIFIED

    def test_score_is_the_sum_of_the_memory_valid_points(self):
        df = _df(overrides={0: {"stoch_pts_mem": 2, "rsi_pts_mem": 0}})
        result = evaluate_stock("TEST", df)
        assert result.l3_score == 2
        assert result.l3_detail["stoch_pts"] == 2
        assert result.l3_detail["rsi_pts"] == 0

    def test_qualified_can_use_a_remembered_point_that_is_not_lit_this_week(self):
        # L3-R2: the RSI point lit a few weeks ago (memory-valid) but not today - score is
        # still 4 for Qualified purposes, while score_now only has today's stoch point.
        df = _df(overrides={0: {"rsi_pt_now": 0, "rsi_pts_mem": 2}})
        result = evaluate_stock("TEST", df)
        assert result.l3_score == 4
        assert result.l3_score_now == 2
        assert result.status == STATUS_QUALIFIED

    def test_missing_point_columns_count_as_zero(self):
        df = _df(overrides={0: {"stoch_pts_mem": float("nan"), "rsi_pt_now": float("nan")}})
        result = evaluate_stock("TEST", df)
        assert result.l3_detail["stoch_pts"] == 0
        assert result.l3_score_now == 2


class TestStochFlattening:
    def test_flattening_true_matches_spec_11s_anet_case(self):
        # ANET weekly 2026-11-06 (spec §11): K 22.19, K prev 26.2, K prevprev 52.6.
        df = _df(overrides={0: {"weekly_k_now": 22.19, "weekly_k_prev": 26.2, "weekly_k_prevprev": 52.6}})
        result = evaluate_stock("TEST", df)
        assert result.stoch_flattening is True

    def test_false_when_still_falling_not_flattening(self):
        df = _df(overrides={0: {"weekly_k_now": 20.0, "weekly_k_prev": 28.0, "weekly_k_prevprev": 45.0}})
        result = evaluate_stock("TEST", df)
        assert result.stoch_flattening is False

    def test_badge_never_shown_on_a_qualified_row(self):
        # K readings that satisfy both L3's stoch_pts (rising, prior <= floor) and the
        # flattening shape (<=30, small move, big prior fall) at once, so the row stays
        # Qualified (4/4) while stoch_flattening is still true underneath.
        df = _df(overrides={0: {"weekly_k_now": 20.0, "weekly_k_prev": 18.0, "weekly_k_prevprev": 40.0}})
        result = evaluate_stock("TEST", df)
        assert result.status == STATUS_QUALIFIED
        # stoch_flattening is still just a fact about the data - whether it's *shown* is
        # status_mapping's job (see test_momentum_continuation_status_mapping.py).
        assert result.stoch_flattening is True


class TestMacdTurning:
    def test_true_when_three_completed_bars_shrink_in_the_red_and_today_continues(self):
        df = _df(overrides={0: {
            "weekly_hist_now": -0.05,
            "weekly_hist_t1": -0.1, "weekly_hist_t2": -0.2, "weekly_hist_t3": -0.3,
        }})
        result = evaluate_stock("TEST", df)
        assert result.macd_turning is True

    def test_false_when_still_in_the_red_but_not_monotonically_shrinking(self):
        df = _df(overrides={0: {
            "weekly_hist_now": -0.05,
            "weekly_hist_t1": -0.1, "weekly_hist_t2": -0.05, "weekly_hist_t3": -0.3,
        }})
        result = evaluate_stock("TEST", df)
        assert result.macd_turning is False

    def test_false_when_one_of_the_three_completed_bars_is_not_negative(self):
        df = _df(overrides={0: {
            "weekly_hist_now": 0.1,
            "weekly_hist_t1": 0.05, "weekly_hist_t2": -0.2, "weekly_hist_t3": -0.3,
        }})
        result = evaluate_stock("TEST", df)
        assert result.macd_turning is False

    def test_false_when_today_does_not_continue_the_rise(self):
        df = _df(overrides={0: {
            "weekly_hist_now": -0.15,
            "weekly_hist_t1": -0.1, "weekly_hist_t2": -0.2, "weekly_hist_t3": -0.3,
        }})
        result = evaluate_stock("TEST", df)
        assert result.macd_turning is False

    def test_true_when_today_is_the_first_cross_to_positive(self):
        df = _df(overrides={0: {
            "weekly_hist_now": 0.02,
            "weekly_hist_t1": -0.1, "weekly_hist_t2": -0.2, "weekly_hist_t3": -0.3,
        }})
        result = evaluate_stock("TEST", df)
        assert result.macd_turning is True

    def test_badge_is_informational_only_never_changes_score_or_status(self):
        # Same turning histogram as above, laid on top of the baseline's full L3 score -
        # still Qualified, score still 4, the flag is purely a "go look" signal.
        df = _df(overrides={0: {
            "weekly_hist_now": -0.05,
            "weekly_hist_t1": -0.1, "weekly_hist_t2": -0.2, "weekly_hist_t3": -0.3,
        }})
        result = evaluate_stock("TEST", df)
        assert result.macd_turning is True
        assert result.l3_score == 4
        assert result.status == STATUS_QUALIFIED


class TestCombine:
    """Part B's combine table, as amended by S-R1 (Developing needs a live trigger)."""

    def test_l1_pass_armed_score_above_4_is_qualified(self):
        assert evaluate_stock("TEST", _df()).status == STATUS_QUALIFIED

    def test_l1_pass_armed_score_below_4_is_developing(self):
        df = _df(overrides={0: {"rsi_pt_now": 0, "rsi_pts_mem": 0}})  # drops rsi_pts -> score 2
        result = evaluate_stock("TEST", df)
        assert result.l3_score < L3_QUALIFY_MIN
        assert result.status == STATUS_DEVELOPING

    def test_l1_fail_armed_is_monitored(self):
        df = _df(overrides={0: {"l1_ema50": 95.0, "l1_ema50_4w_ago": 100.0, "l1_wclose": 90.0}})
        result = evaluate_stock("TEST", df)
        assert result.status == STATUS_MONITORED
        assert result.armed is True

    def test_l1_fail_not_armed_is_monitored(self):
        overrides = _not_armed(l1_ema50=95.0, l1_ema50_4w_ago=100.0, l1_wclose=90.0)
        result = evaluate_stock("TEST", _df(overrides=overrides))
        assert result.status == STATUS_MONITORED
        assert result.armed is False

    def test_l1_not_evaluable_is_monitored_regardless_of_l2(self):
        df = _df(overrides={0: {"l1_completed_weeks": 10}})
        result = evaluate_stock("TEST", df)
        assert result.status == STATUS_MONITORED


class TestLiveTriggerSR1:
    def test_not_armed_with_a_point_lit_this_week_is_developing(self):
        df = _df(overrides=_not_armed(stoch_pt_now=0, rsi_pt_now=2))
        result = evaluate_stock("TEST", df)
        assert result.armed is False
        assert result.l3_score_now == 2
        assert result.status == STATUS_DEVELOPING
        assert result.flags == []

    def test_armed_with_no_weekly_points_at_all_is_developing(self):
        df = _df(overrides={0: {
            "stoch_pt_now": 0, "rsi_pt_now": 0, "stoch_pts_mem": 0, "rsi_pts_mem": 0,
        }})
        result = evaluate_stock("TEST", df)
        assert result.armed is True
        assert result.l3_score == 0
        assert result.status == STATUS_DEVELOPING

    def test_not_armed_and_nothing_lit_this_week_is_monitored_no_live_trigger(self):
        df = _df(overrides=_not_armed(stoch_pt_now=0, rsi_pt_now=0, stoch_pts_mem=0, rsi_pts_mem=0))
        result = evaluate_stock("TEST", df)
        assert result.status == STATUS_MONITORED
        assert result.l1_state == L1_PASS
        assert result.flags == ["NO_LIVE_TRIGGER"]
        assert result.diagnostics["no_live_trigger"] is True

    def test_remembered_points_alone_are_not_a_live_trigger(self):
        # Both points are memory-valid (lit weeks ago) but neither is lit this week and
        # Daily isn't armed: the row is Monitored even though the Weekly checks show ticks.
        df = _df(overrides=_not_armed(stoch_pt_now=0, rsi_pt_now=0))
        result = evaluate_stock("TEST", df)
        assert result.l3_score == 4
        assert result.l3_score_now == 0
        assert result.status == STATUS_MONITORED
        assert result.flags == ["NO_LIVE_TRIGGER"]

    def test_l1_fail_never_gets_the_no_live_trigger_flag(self):
        df = _df(overrides=_not_armed(
            l1_ema50=95.0, l1_ema50_4w_ago=100.0, l1_wclose=90.0, stoch_pt_now=0, rsi_pt_now=0,
        ))
        result = evaluate_stock("TEST", df)
        assert result.status == STATUS_MONITORED
        assert "NO_LIVE_TRIGGER" not in result.flags
        assert result.diagnostics["no_live_trigger"] is False


class TestL1R1BreakdownCut:
    ROLLED = {"mo_K": 76.55, "mo_D": 79.24, "mo_Kmax4": 89.15, "l1_below21_2w": True}

    def test_asml_case_cut_fails_l1_even_though_the_slope_test_alone_passes(self):
        # ASML weekly 2026-11-13 (change note): close 1495.16 < EMA21 1633.17, EMA50 1501.23
        # still rising; mK 76.55 < mD 79.24 and max K over 4 months 89.15 -> cut.
        df = _df(overrides={0: {
            "l1_ema50": 1501.23, "l1_ema50_4w_ago": 1480.0,
            "l1_wclose": 1495.16, "wk_ema21": 1633.17, "wk_ema21_prior": 1650.0, **self.ROLLED,
        }})
        result = evaluate_stock("TEST", df)
        assert result.diagnostics["l1_slope_pass"] is True
        assert result.diagnostics["l1_mroll"] is True
        assert result.diagnostics["l1_below21_2w"] is True
        assert result.diagnostics["l1_r1_cut"] is True
        assert result.l1_state == L1_FAIL
        assert result.status == STATUS_MONITORED
        assert "L1_BREAKDOWN" in result.flags and "L1_FAILED" in result.flags

    def test_adm_case_slope_passes_by_a_hair_but_the_cut_still_fails_l1(self):
        # ADM weekly 2026-11-13: EMA50 73.61 vs 73.40 four weeks earlier (+0.29%), close 72.90
        # under EMA21 78.14, mK 74.50 < mD 77.60, max K 83.31.
        df = _df(overrides={0: {
            "l1_ema50": 73.61, "l1_ema50_4w_ago": 73.40, "l1_wclose": 72.90,
            "wk_ema21": 78.14, "wk_ema21_prior": 79.0,
            "mo_K": 74.50, "mo_D": 77.60, "mo_Kmax4": 83.31, "l1_below21_2w": True,
        }})
        result = evaluate_stock("TEST", df)
        assert result.diagnostics["l1_slope_pass"] is True
        assert result.l1_state == L1_FAIL

    def test_releases_when_price_is_no_longer_below_the_ema_for_two_weeks(self):
        df = _df(overrides={0: {**self.ROLLED, "l1_below21_2w": False}})
        result = evaluate_stock("TEST", df)
        assert result.diagnostics["l1_mroll"] is True
        assert result.diagnostics["l1_r1_cut"] is False
        assert result.l1_state == L1_PASS

    def test_releases_when_the_monthly_stoch_has_not_rolled_over(self):
        df = _df(overrides={0: {"mo_K": 85.0, "mo_D": 79.0, "mo_Kmax4": 89.15, "l1_below21_2w": True}})
        result = evaluate_stock("TEST", df)
        assert result.diagnostics["l1_mroll"] is False
        assert result.l1_state == L1_PASS

    def test_needs_the_stoch_to_have_been_overbought_recently(self):
        df = _df(overrides={0: {
            "mo_K": 60.0, "mo_D": 65.0, "mo_Kmax4": L1_R1_OVERBOUGHT - 0.1, "l1_below21_2w": True,
        }})
        assert evaluate_stock("TEST", df).diagnostics["l1_mroll"] is False

    def test_overbought_boundary_is_inclusive(self):
        df = _df(overrides={0: {
            "mo_K": 60.0, "mo_D": 65.0, "mo_Kmax4": float(L1_R1_OVERBOUGHT), "l1_below21_2w": True,
        }})
        assert evaluate_stock("TEST", df).diagnostics["l1_mroll"] is True

    def test_no_cut_when_the_monthly_stoch_is_not_computable(self):
        nan = float("nan")
        df = _df(overrides={0: {"mo_K": nan, "mo_D": nan, "mo_Kmax4": nan, "l1_below21_2w": True}})
        result = evaluate_stock("TEST", df)
        assert result.diagnostics["l1_mroll"] is False
        assert result.diagnostics["l1_r1_cut"] is False
        assert result.l1_state == L1_PASS

    def test_cut_also_overrides_the_r2_route(self):
        df = _df(overrides={0: {
            "l1_ema50": 95.0, "l1_ema50_4w_ago": 100.0,  # slope fails
            "l1_wclose": 120.0, "wk_ema21": 110.0, "wk_ema21_prior": 105.0,  # r2 passes
            **self.ROLLED,
        }})
        result = evaluate_stock("TEST", df)
        assert result.diagnostics["l1_r2_pass"] is True
        assert result.l1_state == L1_FAIL


class TestL1R2SecondPassRoute:
    def test_aso_case_passes_via_r2_while_the_ema50_slope_still_fails(self):
        # ASO weekly 2026-10-09: close 53.49 > EMA50 50.44 and EMA21 49.62, EMA21 rising.
        df = _df(overrides={0: {
            "l1_ema50": 50.44, "l1_ema50_4w_ago": 50.60,  # slope still negative
            "l1_wclose": 53.49, "wk_ema21": 49.62, "wk_ema21_prior": 48.9,
        }})
        result = evaluate_stock("TEST", df)
        assert result.diagnostics["l1_slope_pass"] is False
        assert result.diagnostics["l1_r2_pass"] is True
        assert result.l1_state == L1_PASS

    @pytest.mark.parametrize(
        "wclose,ema21,ema21_prior",
        [
            (50.0, 49.62, 48.9),    # not above EMA50 (50.44)
            (53.49, 54.0, 53.0),    # not above EMA21
            (53.49, 49.62, 49.62),  # EMA21 flat, not strictly higher
            (53.49, 49.62, 50.0),   # EMA21 falling
        ],
    )
    def test_each_condition_is_required(self, wclose, ema21, ema21_prior):
        df = _df(overrides={0: {
            "l1_ema50": 50.44, "l1_ema50_4w_ago": 50.60,
            "l1_wclose": wclose, "wk_ema21": ema21, "wk_ema21_prior": ema21_prior,
        }})
        result = evaluate_stock("TEST", df)
        assert result.diagnostics["l1_r2_pass"] is False
        assert result.l1_state == L1_FAIL

    def test_r2_inputs_missing_just_means_the_route_is_off(self):
        df = _df(overrides={0: {
            "l1_ema50": 50.44, "l1_ema50_4w_ago": 50.60, "wk_ema21": float("nan"),
        }})
        result = evaluate_stock("TEST", df)
        assert result.diagnostics["l1_r2_pass"] is False
        assert result.l1_state == L1_FAIL


class TestAfrmFallingKnife:
    def test_knife_today_with_no_weekly_trigger_is_monitored_no_live_trigger(self):
        # AFRM daily 2026-11-18: close 58.91 far below EMA21 ~67.8 (ATR ~3-3.5) -> knife, so
        # gates.py leaves l2_signal False; weekly score 0 -> no live trigger -> Monitored.
        df = _df(overrides=_not_armed(
            l2_knife=True, close=58.91, stoch_pt_now=0, rsi_pt_now=0, stoch_pts_mem=0, rsi_pts_mem=0,
        ))
        result = evaluate_stock("TEST", df)
        assert result.diagnostics["l2_knife"] is True
        assert result.armed is False
        assert result.status == STATUS_MONITORED
        assert result.flags == ["NO_LIVE_TRIGGER"]


class TestLogColumns:
    def test_diagnostics_carry_every_round_1_log_column_json_safely(self):
        import json

        result = evaluate_stock("TEST", _df())
        expected = {
            "l1_slope_pass", "l1_r2_pass", "l1_mroll", "l1_below21_2w", "l1_r1_cut", "wk_ema21",
            "mo_K", "mo_D", "mo_Kmax4", "l2_knife", "stoch_pt_now", "rsi_pt_now", "score_now",
            "no_live_trigger",
        }
        assert expected <= set(result.diagnostics)
        json.dumps(result.diagnostics)  # no numpy scalars left in there
