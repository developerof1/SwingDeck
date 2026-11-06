"""Acceptance tests for src/rules_engine.py, following docs/Momentum Continuation
V2 — Logic.md: a 3-gate pipeline (Trend hard-veto, Location grace-window, Oscillator
grace-window) over a 4-status model (DATA_INVALID / INELIGIBLE_TREND / DEVELOPING /
CANDIDATE).

Builds minimal DataFrames with directly-set indicator columns (rather than deriving
them from raw OHLCV) so each test controls exactly the condition(s) under
examination - data_engine's indicator math is covered separately in
test_data_engine.py. `overrides` lets a test touch specific sessions within the
5-session grace window, keyed by offset-from-today (0 = today, 5 = oldest session
in the window).
"""

import pandas as pd
import pytest

from src.rules_engine import (
    BADGE_LOCATION_PENDING,
    BADGE_OSCILLATOR_PENDING,
    DATA_VALID_MIN_SESSIONS,
    GRACE_WINDOW_SESSIONS,
    STATUS_CANDIDATE,
    STATUS_DATA_INVALID,
    STATUS_DEVELOPING,
    STATUS_INELIGIBLE_TREND,
    evaluate_stock,
)
from src.status_mapping import GROUP_QUALIFIED, GROUP_UNKNOWN, ui_group_for_status

# A row that cleanly passes data-validity, Trend, Location, and Oscillator - the
# baseline every test perturbs from.
BASELINE_ROW = {
    "close": 100.0,
    "sma_50": 95.0,
    "sma_200": 90.0,
    "sma_50_prev20": 94.0,  # sma_50 (95) > sma_50_prev20 (94) -> rising
    "monthly_stoch_k": 50.0,
    "monthly_stoch_d": 40.0,  # k > d -> monthly_pass
    "monthly_stoch_k_prev": 45.0,
    "weekly_stoch_k": 25.0,  # rising vs prev and >= 20 -> weekly_pass
    "weekly_stoch_d": 15.0,  # not read by weekly_pass, but required for data_valid
    "weekly_stoch_k_prev": 20.0,
    "ema_8": 100.0,
    "ema_21": 100.0,
    "ema_ref": 100.0,
    "location_dpct": 1.0,  # <= 3.0 -> location_ok
    "location_ok": True,
    "stoch_k": 25.0,
    "stoch_d": 20.0,
    "oscillator_trigger_ok": True,
    "prev_session_high": 97.0,  # close (100) > prev_session_high -> recovery True
}


def _df(n: int = DATA_VALID_MIN_SESSIONS + 5, overrides: dict[int, dict] | None = None) -> pd.DataFrame:
    """n rows of baseline-passing filler history, plus optional per-session
    overrides applied by offset-from-the-end (0 = today)."""
    base = {col: [val] * n for col, val in BASELINE_ROW.items()}
    df = pd.DataFrame(base)
    df["date"] = pd.date_range("2024-01-01", periods=n, freq="B")
    if overrides:
        for offset, cols in overrides.items():
            idx = n - 1 - offset
            for col, val in cols.items():
                df.loc[idx, col] = val
    return df


class TestTrendGate:
    def test_candidate_passes_all_gates_on_same_bar(self):
        result = evaluate_stock("TEST", _df())
        assert result.status == STATUS_CANDIDATE
        assert result.trend_pass is True
        assert result.location_pass is True
        assert result.oscillator_pass is True
        assert result.location_detail["sessions_ago"] == 0
        assert result.oscillator_detail["sessions_ago"] == 0

    def test_symbol_below_sma50_can_still_qualify(self):
        # close (100) below sma_50 (102); layer1 only checks sma_50 vs
        # sma_200/sma_50_prev20 and close vs sma_200 - price vs sma_50 is not
        # a trend input at all.
        result = evaluate_stock("TEST", _df(overrides={0: {"sma_50": 102.0, "sma_50_prev20": 101.0}}))
        assert result.trend_pass is True
        assert result.status == STATUS_CANDIDATE

    def test_trend_boundary_is_strict_not_inclusive(self):
        result = evaluate_stock("TEST", _df(overrides={0: {"close": 90.0, "sma_200": 90.0}}))
        assert result.trend_detail["close_gt_sma200"] is False
        assert result.trend_pass is False
        assert result.status == STATUS_INELIGIBLE_TREND

    def test_trend_fails_on_monthly_mtf_layer(self):
        # k < d and k < k_prev -> monthly_pass False, even though layer1 and
        # weekly both still pass.
        result = evaluate_stock(
            "TEST", _df(overrides={0: {"monthly_stoch_k": 30.0, "monthly_stoch_d": 40.0, "monthly_stoch_k_prev": 35.0}})
        )
        assert result.trend_detail["monthly_pass"] is False
        assert result.status == STATUS_INELIGIBLE_TREND

    def test_trend_fails_on_weekly_mtf_layer_below_20_floor(self):
        result = evaluate_stock("TEST", _df(overrides={0: {"weekly_stoch_k": 15.0, "weekly_stoch_k_prev": 10.0}}))
        assert result.trend_detail["weekly_pass"] is False
        assert result.status == STATUS_INELIGIBLE_TREND

    def test_trend_fails_on_weekly_mtf_layer_not_rising(self):
        result = evaluate_stock("TEST", _df(overrides={0: {"weekly_stoch_k": 25.0, "weekly_stoch_k_prev": 30.0}}))
        assert result.trend_detail["weekly_pass"] is False
        assert result.status == STATUS_INELIGIBLE_TREND


class TestLocationGate:
    def test_grace_window_rescues_a_pass_from_3_sessions_ago(self):
        overrides = {i: {"location_ok": False} for i in range(GRACE_WINDOW_SESSIONS + 1)}
        overrides[3] = {"location_ok": True}
        result = evaluate_stock("TEST", _df(overrides=overrides))
        assert result.location_pass is True
        assert result.location_detail["pass_today"] is False
        assert result.location_detail["sessions_ago"] == 3
        assert result.status == STATUS_CANDIDATE  # oscillator still baseline-passing

    def test_grace_window_exhausted_is_developing_not_ineligible(self):
        overrides = {i: {"location_ok": False} for i in range(GRACE_WINDOW_SESSIONS + 1)}
        result = evaluate_stock("TEST", _df(overrides=overrides))
        assert result.location_pass is False
        assert result.location_detail["sessions_ago"] is None
        assert result.status == STATUS_DEVELOPING
        codes = {b.code for b in result.developing_badges}
        assert BADGE_LOCATION_PENDING in codes
        assert BADGE_OSCILLATOR_PENDING not in codes

    def test_location_pending_badge_measured_and_threshold(self):
        overrides = {i: {"location_ok": False} for i in range(GRACE_WINDOW_SESSIONS + 1)}
        overrides[0]["location_dpct"] = 4.2
        result = evaluate_stock("TEST", _df(overrides=overrides))
        badge = next(b for b in result.developing_badges if b.code == BADGE_LOCATION_PENDING)
        assert badge.measured == pytest.approx(4.2)
        assert badge.threshold == pytest.approx(3.0)


class TestOscillatorGate:
    def test_grace_window_rescues_a_trigger_from_5_sessions_ago(self):
        overrides = {i: {"oscillator_trigger_ok": False} for i in range(GRACE_WINDOW_SESSIONS + 1)}
        overrides[GRACE_WINDOW_SESSIONS] = {"oscillator_trigger_ok": True}
        result = evaluate_stock("TEST", _df(overrides=overrides))
        assert result.oscillator_pass is True
        assert result.oscillator_detail["sessions_ago"] == GRACE_WINDOW_SESSIONS
        assert result.status == STATUS_CANDIDATE  # location still baseline-passing

    def test_grace_window_exhausted_is_developing_not_ineligible(self):
        overrides = {i: {"oscillator_trigger_ok": False} for i in range(GRACE_WINDOW_SESSIONS + 1)}
        result = evaluate_stock("TEST", _df(overrides=overrides))
        assert result.oscillator_pass is False
        assert result.status == STATUS_DEVELOPING
        codes = {b.code for b in result.developing_badges}
        assert BADGE_OSCILLATOR_PENDING in codes
        assert BADGE_LOCATION_PENDING not in codes

    def test_oscillator_pending_badge_measured_and_threshold(self):
        overrides = {i: {"oscillator_trigger_ok": False} for i in range(GRACE_WINDOW_SESSIONS + 1)}
        overrides[0]["stoch_k"] = 38.1
        result = evaluate_stock("TEST", _df(overrides=overrides))
        badge = next(b for b in result.developing_badges if b.code == BADGE_OSCILLATOR_PENDING)
        assert badge.measured == pytest.approx(38.1)
        assert badge.threshold == pytest.approx(30.0)


class TestDevelopingBothPending:
    def test_both_badges_appear_together(self):
        overrides = {i: {"location_ok": False, "oscillator_trigger_ok": False} for i in range(GRACE_WINDOW_SESSIONS + 1)}
        result = evaluate_stock("TEST", _df(overrides=overrides))
        assert result.status == STATUS_DEVELOPING
        codes = {b.code for b in result.developing_badges}
        assert codes == {BADGE_LOCATION_PENDING, BADGE_OSCILLATOR_PENDING}

    def test_candidate_carries_no_badges(self):
        result = evaluate_stock("TEST", _df())
        assert result.status == STATUS_CANDIDATE
        assert result.developing_badges == []


class TestDataValidityAndAnnotations:
    def test_data_invalid_below_min_sessions(self):
        short = evaluate_stock("TEST", _df(n=DATA_VALID_MIN_SESSIONS - 1))
        assert short.status == STATUS_DATA_INVALID
        assert short.data_valid is False

        enough = evaluate_stock("TEST", _df(n=DATA_VALID_MIN_SESSIONS))
        assert enough.status != STATUS_DATA_INVALID
        assert enough.data_valid is True

    def test_unfinished_current_session_bar_cannot_alter_result(self):
        df = _df()
        full = evaluate_stock("TEST", df)
        trimmed = evaluate_stock("TEST", df.iloc[:-1])
        assert full.evaluated_session == df.iloc[-1]["date"].strftime("%Y-%m-%d")
        assert trimmed.evaluated_session == df.iloc[-2]["date"].strftime("%Y-%m-%d")
        assert full.evaluated_session != trimmed.evaluated_session

    def test_recovery_observed_is_non_gating(self):
        recovery_true = evaluate_stock("TEST", _df(overrides={0: {"prev_session_high": 50.0}}))
        recovery_false = evaluate_stock("TEST", _df(overrides={0: {"prev_session_high": 150.0}}))
        assert recovery_true.recovery_observed is True
        assert recovery_false.recovery_observed is False
        assert recovery_true.status == recovery_false.status == STATUS_CANDIDATE

    def test_missing_recovery_input_does_not_change_eligibility(self):
        baseline = evaluate_stock("TEST", _df())
        no_recovery_input = evaluate_stock("TEST", _df(overrides={0: {"prev_session_high": float("nan")}}))
        assert no_recovery_input.status == baseline.status
        assert no_recovery_input.recovery_observed is None

    def test_status_matches_ui_group_mapping(self):
        result = evaluate_stock("TEST", _df())
        assert ui_group_for_status(result.status) == GROUP_QUALIFIED
        assert ui_group_for_status("SOME_UNRECOGNIZED_STATUS") == GROUP_UNKNOWN

    def test_momentum_continuation_source_never_references_pullback_value(self):
        import src.rules_engine as engine_module

        source = open(engine_module.__file__).read()
        assert "Pullback Value" not in source
        assert "PULLBACK_VALUE" not in source
