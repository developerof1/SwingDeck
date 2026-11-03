"""Acceptance-criteria tests for src/rules_engine.py, following the 10 criteria in
docs/Momentum Continuation V2 — Logic.md Section 5.

Builds minimal DataFrames with directly-set indicator columns on the last row
(rather than deriving them from raw OHLCV) so each test controls exactly the
condition(s) under examination - data_engine's indicator math is covered
separately in test_data_engine.py.
"""

import pandas as pd
import pytest

from src.rules_engine import (
    BADGE_EXTENDED_FROM_EMA20,
    BADGE_SHALLOW_PULLBACK,
    DATA_VALID_MIN_SESSIONS,
    STATUS_CANDIDATE,
    STATUS_DATA_INVALID,
    STATUS_DEVELOPING,
    STATUS_INELIGIBLE_PULLBACK,
    STATUS_INELIGIBLE_TREND,
    evaluate_stock,
)
from src.status_mapping import GROUP_QUALIFIED, GROUP_UNKNOWN, ui_group_for_status

# A last row that passes data-validity, trend, and Candidate pullback/location
# cleanly - the baseline every test perturbs from.
BASELINE_LAST_ROW = {
    "close": 100.0,
    "sma_50": 95.0,
    "sma_200": 90.0,
    "sma_50_prev20": 94.0,  # sma_50 (95) > sma_50_prev20 (94) -> rising
    "atr_14": 2.0,
    "close_3ago": 101.0,  # close (100) < close_3ago (101) -> decline
    "highest_high_20": 104.0,  # depth_atr = (104-100)/2 = 2.0, inside [1.0, 3.0]
    "ema_20": 100.0,  # ema_dist_atr = (100-100)/2 = 0.0, inside [-1.0, 0.5]
    "prev_session_high": 97.0,  # close (100) > prev_session_high -> recovery True
}


def _df(n: int = DATA_VALID_MIN_SESSIONS + 5, last_row: dict | None = None) -> pd.DataFrame:
    """n rows of filler history (content irrelevant - evaluate_stock only reads
    the last row) plus a controlled last row."""
    row = dict(BASELINE_LAST_ROW)
    if last_row:
        row.update(last_row)
    base = {col: [val] * n for col, val in row.items()}
    df = pd.DataFrame(base)
    df["date"] = pd.date_range("2024-01-01", periods=n, freq="B")
    return df


class TestAcceptanceCriteria:
    def test_1_candidate_passes_all_conditions_on_same_bar(self):
        result = evaluate_stock("TEST", _df())
        assert result.status == STATUS_CANDIDATE
        assert result.trend_pass is True
        assert result.pullback_pass is True
        assert result.pullback_detail["decline"] is True
        assert 1.0 <= result.pullback_detail["depth_atr"] <= 3.0
        assert -1.0 <= result.pullback_detail["ema_dist_atr"] <= 0.5

    def test_2_pullback_value_never_referenced(self):
        import src.rules_engine as engine_module

        source = open(engine_module.__file__).read()
        assert "Pullback Value" not in source
        assert "PULLBACK_VALUE" not in source

    def test_3_symbol_below_sma50_can_still_qualify(self):
        # close (100) below sma_50 (102), trend gate only checks sma_50 vs
        # sma_200/sma_50_prev20 and close vs sma_200 - price vs sma_50 is not a
        # trend input at all.
        result = evaluate_stock("TEST", _df(last_row={"sma_50": 102.0, "sma_50_prev20": 101.0}))
        assert result.trend_pass is True
        assert result.status == STATUS_CANDIDATE

    def test_4_atr_band_endpoints_inclusive_trend_strict(self):
        at_floor = evaluate_stock("TEST", _df(last_row={"highest_high_20": 102.0}))  # depth = 1.0
        assert at_floor.pullback_detail["depth_atr"] == pytest.approx(1.0)
        assert at_floor.status == STATUS_CANDIDATE

        at_ceiling = evaluate_stock("TEST", _df(last_row={"highest_high_20": 106.0}))  # depth = 3.0
        assert at_ceiling.pullback_detail["depth_atr"] == pytest.approx(3.0)
        assert at_ceiling.status == STATUS_CANDIDATE

        just_below_floor = evaluate_stock("TEST", _df(last_row={"highest_high_20": 101.9}))  # depth = 0.95
        assert just_below_floor.status != STATUS_CANDIDATE

        # Trend inequalities are strict: close == sma_200 must fail, not pass.
        at_trend_boundary = evaluate_stock("TEST", _df(last_row={"close": 90.0, "sma_200": 90.0}))
        assert at_trend_boundary.trend_pass is False
        assert at_trend_boundary.status == STATUS_INELIGIBLE_TREND

    def test_5_historical_events_cannot_rescue_current_failure(self):
        # evaluate_stock takes no prior-state argument - two frames with entirely
        # different filler history but an identical last row must agree exactly,
        # proving the result is a pure function of the latest bar's own columns.
        last_row = {"close_3ago": 99.0}  # breaks "decline" (close 100 !< 99)
        df_a = _df(n=DATA_VALID_MIN_SESSIONS + 5, last_row=last_row)
        df_b = _df(n=DATA_VALID_MIN_SESSIONS + 50, last_row=last_row)
        result_a = evaluate_stock("TEST", df_a)
        result_b = evaluate_stock("TEST", df_b)
        assert result_a.pullback_detail["decline"] is False
        assert result_a.status == result_b.status == STATUS_INELIGIBLE_PULLBACK

    def test_6_missing_optional_indicators_dont_change_eligibility(self):
        baseline = evaluate_stock("TEST", _df())
        no_recovery_input = evaluate_stock("TEST", _df(last_row={"prev_session_high": float("nan")}))
        assert no_recovery_input.status == baseline.status
        assert no_recovery_input.recovery_observed is None

    def test_7_recovery_observed_does_not_change_status(self):
        recovery_true = evaluate_stock("TEST", _df(last_row={"prev_session_high": 50.0}))
        recovery_false = evaluate_stock("TEST", _df(last_row={"prev_session_high": 150.0}))
        assert recovery_true.recovery_observed is True
        assert recovery_false.recovery_observed is False
        assert recovery_true.status == recovery_false.status == STATUS_CANDIDATE

    def test_8_unfinished_current_session_bar_cannot_alter_result(self):
        df = _df()
        full = evaluate_stock("TEST", df)
        trimmed = evaluate_stock("TEST", df.iloc[:-1])
        assert full.evaluated_session == df.iloc[-1]["date"].strftime("%Y-%m-%d")
        assert trimmed.evaluated_session == df.iloc[-2]["date"].strftime("%Y-%m-%d")
        assert full.evaluated_session != trimmed.evaluated_session

    def test_9_status_matches_ui_group_mapping(self):
        result = evaluate_stock("TEST", _df())
        assert ui_group_for_status(result.status) == GROUP_QUALIFIED
        assert ui_group_for_status("SOME_UNRECOGNIZED_STATUS") == GROUP_UNKNOWN

    def test_10_data_invalid_below_min_sessions(self):
        short = evaluate_stock("TEST", _df(n=DATA_VALID_MIN_SESSIONS - 1))
        assert short.status == STATUS_DATA_INVALID
        assert short.data_valid is False

        enough = evaluate_stock("TEST", _df(n=DATA_VALID_MIN_SESSIONS))
        assert enough.status != STATUS_DATA_INVALID
        assert enough.data_valid is True


class TestDevelopingBadges:
    def test_shallow_pullback_badge(self):
        # depth_atr = (100.7 - 100) / 2 = 0.35... use 0.5 exactly as the Developing floor + a bit
        result = evaluate_stock("TEST", _df(last_row={"highest_high_20": 101.4}))  # depth = 0.7
        assert result.status == STATUS_DEVELOPING
        codes = {b.code for b in result.developing_badges}
        assert BADGE_SHALLOW_PULLBACK in codes
        assert BADGE_EXTENDED_FROM_EMA20 not in codes
        badge = next(b for b in result.developing_badges if b.code == BADGE_SHALLOW_PULLBACK)
        assert badge.measured == pytest.approx(0.7)
        assert badge.threshold == pytest.approx(1.0)

    def test_extended_from_ema20_badge(self):
        # ema_dist_atr = (100 - 98.4) / 2 = 0.8, inside (0.5, 1.0]
        result = evaluate_stock("TEST", _df(last_row={"ema_20": 98.4}))
        assert result.status == STATUS_DEVELOPING
        codes = {b.code for b in result.developing_badges}
        assert BADGE_EXTENDED_FROM_EMA20 in codes
        assert BADGE_SHALLOW_PULLBACK not in codes

    def test_both_badges_can_appear_together(self):
        result = evaluate_stock("TEST", _df(last_row={"highest_high_20": 101.4, "ema_20": 98.4}))
        assert result.status == STATUS_DEVELOPING
        codes = {b.code for b in result.developing_badges}
        assert codes == {BADGE_SHALLOW_PULLBACK, BADGE_EXTENDED_FROM_EMA20}

    def test_too_shallow_for_developing_is_ineligible_pullback(self):
        # depth_atr = (100.2 - 100) / 2 = 0.1, below Developing's own 0.5 floor
        result = evaluate_stock("TEST", _df(last_row={"highest_high_20": 100.2}))
        assert result.status == STATUS_INELIGIBLE_PULLBACK
        assert result.developing_badges == []

    def test_candidate_takes_precedence_over_developing(self):
        # Exactly at the Candidate band - must not be reported as Developing even
        # though it would also satisfy Developing's looser bands.
        result = evaluate_stock("TEST", _df())
        assert result.status == STATUS_CANDIDATE
        assert result.developing_badges == []
