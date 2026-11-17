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

from src.setups.momentum_continuation.gates import ARMED_MAX_AGE, L2_MIN_SESSIONS, L3_QUALIFY_MIN
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
    def test_full_score_from_both_components_turning(self):
        result = evaluate_stock("TEST", _df())
        assert result.l3_score == 4
        assert result.status == STATUS_QUALIFIED

    @pytest.mark.parametrize(
        "k_now,k_prev,expected_pts",
        [
            (25.0, 15.0, 2),  # rising from below the floor -> points
            (25.0, 35.0, 0),  # prev above the floor -> no points even though rising
            (10.0, 15.0, 0),  # not rising -> no points even though prev is below the floor
        ],
    )
    def test_stoch_points_require_both_rising_and_prior_below_floor(self, k_now, k_prev, expected_pts):
        df = _df(overrides={0: {"weekly_k_now": k_now, "weekly_k_prev": k_prev}})
        result = evaluate_stock("TEST", df)
        assert result.l3_detail["stoch_pts"] == expected_pts

    def test_no_partial_credit_for_a_big_jump_still_above_the_floor(self):
        # K_prev=50 (above the L3_STOCH_FLOOR of 20) even though K_now rises a lot - no points.
        df = _df(overrides={0: {"weekly_k_now": 90.0, "weekly_k_prev": 50.0}})
        result = evaluate_stock("TEST", df)
        assert result.l3_detail["stoch_pts"] == 0

    def test_no_level_condition_rsi_above_50_but_falling_scores_zero(self):
        df = _df(overrides={0: {"weekly_rsi_now": 55.0, "weekly_rsi_prev": 60.0}})
        result = evaluate_stock("TEST", df)
        assert result.l3_detail["rsi_pts"] == 0

    @pytest.mark.parametrize(
        "k_now,k_prev,rsi_now,rsi_prev,expected",
        [
            (16.4, 9.9, 47.5, 35.2, 4),  # AVGO weekly 2025-04-11 (spec §11), MACD no longer scored
            (22.5, 18.3, 47.1, 42.5, 4),  # AVGO weekly 2026-04-03 (spec §11), MACD no longer scored
            (48.0, 65.0, 48.6, 61.6, 0),  # AVGO weekly 2024-09-06 (spec §11)
        ],
    )
    def test_acceptance_cases_from_spec_11(self, k_now, k_prev, rsi_now, rsi_prev, expected):
        df = _df(
            overrides={
                0: {
                    "weekly_k_now": k_now, "weekly_k_prev": k_prev,
                    "weekly_rsi_now": rsi_now, "weekly_rsi_prev": rsi_prev,
                }
            }
        )
        result = evaluate_stock("TEST", df)
        assert result.l3_score == expected


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
    """The 6-row table from Part B."""

    def test_l1_pass_armed_score_above_4_is_qualified(self):
        assert evaluate_stock("TEST", _df()).status == STATUS_QUALIFIED

    def test_l1_pass_armed_score_below_4_is_developing(self):
        df = _df(overrides={0: {"weekly_rsi_now": 40.0, "weekly_rsi_prev": 50.0}})  # drops rsi_pts -> score 2
        result = evaluate_stock("TEST", df)
        assert result.l3_score < L3_QUALIFY_MIN
        assert result.status == STATUS_DEVELOPING

    def test_l1_pass_not_armed_is_developing(self):
        overrides = {offset: {"l2_signal": False} for offset in range(ARMED_MAX_AGE + 1)}
        df = _df(overrides=overrides)
        result = evaluate_stock("TEST", df)
        assert result.armed is False
        assert result.status == STATUS_DEVELOPING

    def test_l1_fail_armed_is_monitored(self):
        df = _df(overrides={0: {"l1_ema50": 95.0, "l1_ema50_4w_ago": 100.0}})
        result = evaluate_stock("TEST", df)
        assert result.status == STATUS_MONITORED
        assert result.armed is True

    def test_l1_fail_not_armed_is_monitored(self):
        overrides = {offset: {"l2_signal": False} for offset in range(ARMED_MAX_AGE + 1)}
        overrides[0] = {"l1_ema50": 95.0, "l1_ema50_4w_ago": 100.0, "l2_signal": False}
        df = _df(overrides=overrides)
        result = evaluate_stock("TEST", df)
        assert result.status == STATUS_MONITORED
        assert result.armed is False

    def test_l1_not_evaluable_is_monitored_regardless_of_l2(self):
        df = _df(overrides={0: {"l1_completed_weeks": 10}})
        result = evaluate_stock("TEST", df)
        assert result.status == STATUS_MONITORED

    def test_l1_pass_not_armed_and_zero_l3_score_is_monitored(self):
        # Zero signal on both other layers - nothing is actually "developing", so this
        # moves to Monitored even though Trend itself is fine.
        overrides = {offset: {"l2_signal": False} for offset in range(ARMED_MAX_AGE + 1)}
        overrides[0] = {
            "l2_signal": False,
            "weekly_k_now": 15.0, "weekly_k_prev": 25.0,  # falling -> stoch_pts 0
            "weekly_rsi_now": 45.0, "weekly_rsi_prev": 50.0,  # falling -> rsi_pts 0
        }
        df = _df(overrides=overrides)
        result = evaluate_stock("TEST", df)
        assert result.armed is False
        assert result.l3_score == 0
        assert result.status == STATUS_MONITORED

    def test_l1_pass_not_armed_but_nonzero_l3_score_stays_developing(self):
        # Only the "not armed" axis is zero here - L3 still has a point, so this is not
        # the zero-signal case and must stay Developing, not fall into Monitored.
        overrides = {offset: {"l2_signal": False} for offset in range(ARMED_MAX_AGE + 1)}
        overrides[0] = {
            "l2_signal": False,
            "weekly_k_now": 15.0, "weekly_k_prev": 25.0,  # falling -> stoch_pts 0
            "weekly_rsi_now": 55.0, "weekly_rsi_prev": 50.0,  # rising -> rsi_pts 2
        }
        df = _df(overrides=overrides)
        result = evaluate_stock("TEST", df)
        assert result.armed is False
        assert result.l3_score == 2
        assert result.status == STATUS_DEVELOPING
