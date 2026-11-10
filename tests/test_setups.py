"""Covers the setup registry (src/setups/__init__.py, src/setups/base.py) and the
Momentum Continuation adapter (src/setups/momentum_continuation.py) - a pure wrap of
rules_engine.evaluate_stock/data_engine.add_continuation_indicators plus the
serialize()/reason_for() logic that used to be inline in refresh_job.py/app.py. The
underlying gate math itself is already covered by test_rules_engine.py/test_data_engine.py;
these tests only check the wrap is faithful.
"""

import pytest

from src.data_engine import add_continuation_indicators
from src.rules_engine import Badge, EvalResult, STATUS_CANDIDATE, STATUS_DATA_INVALID, STATUS_DEVELOPING, STATUS_INELIGIBLE_TREND, evaluate_stock
from src.setups import get_setup, list_setups
from src.setups import momentum_continuation
from src.status_mapping import GROUP_DEVELOPING, GROUP_OTHER, GROUP_QUALIFIED


def test_registry_lists_momentum_continuation():
    setups = list_setups()
    assert [s.id for s in setups] == ["momentum_continuation"]
    assert setups[0].label == "Momentum Continuation"


def test_get_setup_returns_the_same_registered_instance():
    assert get_setup("momentum_continuation") is momentum_continuation.SETUP


def test_get_setup_raises_for_an_unregistered_id():
    with pytest.raises(KeyError):
        get_setup("not_a_real_setup")


def test_momentum_continuation_wraps_the_real_indicator_and_eval_functions():
    # Must be the actual functions, not reimplementations - a future setup's indicators must
    # never be computed by anything other than its own module's math.
    assert momentum_continuation.add_indicators is add_continuation_indicators
    assert momentum_continuation.evaluate is evaluate_stock


def _candidate_result() -> EvalResult:
    return EvalResult(
        ticker="NVDA",
        status=STATUS_CANDIDATE,
        evaluated_session="2026-11-06",
        data_valid=True,
        trend_pass=True,
        location_pass=True,
        oscillator_pass=True,
        location_detail={"dpct": 1.2, "dpct_prev": 1.5, "ema_ref": 100.0, "ema_ref_is_ema8": True, "pass_today": True, "pass": True, "sessions_ago": 0},
        oscillator_detail={"stoch_k": 22.0, "stoch_d": 18.0, "stoch_k_prev": 19.0, "in_zone": True, "pass_today": True, "pass": True, "sessions_ago": 0},
        recovery_observed=True,
        diagnostics={"close": 101.2},
    )


def test_serialize_round_trips_every_eval_result_field_reason_for_needs():
    result = _candidate_result()
    row = momentum_continuation.serialize(result)

    assert row["status"] == STATUS_CANDIDATE
    assert row["ui_group"] == GROUP_QUALIFIED
    assert row["evaluated_session"] == "2026-11-06"
    assert row["data_valid"] is True
    assert row["recovery_observed"] is True
    assert row["location_detail"] == result.location_detail
    assert row["oscillator_detail"] == result.oscillator_detail
    assert row["developing_badges"] == []


def test_serialize_flattens_developing_badges_to_plain_dicts():
    result = EvalResult(
        ticker="AMD",
        status=STATUS_DEVELOPING,
        trend_pass=True,
        location_pass=False,
        oscillator_pass=True,
        developing_badges=[Badge(code="LOCATION_PENDING", measured=4.2, threshold=3.0, direction="drifting_away", ema_label="EMA8")],
        location_detail={"dpct": 4.2, "dpct_prev": 4.0, "pass": False},
        oscillator_detail={"stoch_k": 25.0, "pass": True, "sessions_ago": 0},
    )
    row = momentum_continuation.serialize(result)

    assert row["ui_group"] == GROUP_DEVELOPING
    assert row["developing_badges"] == [
        {"code": "LOCATION_PENDING", "measured": 4.2, "threshold": 3.0, "direction": "drifting_away", "in_zone": None, "ema_label": "EMA8"}
    ]


def test_reason_for_qualified_includes_recovery_tag_when_observed():
    row = momentum_continuation.serialize(_candidate_result())
    text, is_invalid = momentum_continuation.reason_for(row)

    assert is_invalid is False
    assert "Trend OK" in text
    assert "Closed above prior high" in text


def test_reason_for_developing_has_no_recovery_tag_when_not_observed():
    result = EvalResult(
        ticker="AMD",
        status=STATUS_DEVELOPING,
        trend_pass=True,
        location_pass=False,
        oscillator_pass=True,
        recovery_observed=False,
        location_detail={"dpct": 4.2, "dpct_prev": 4.0, "pass": False},
        oscillator_detail={"stoch_k": 25.0, "pass": True, "sessions_ago": 0},
    )
    row = momentum_continuation.serialize(result)
    text, is_invalid = momentum_continuation.reason_for(row)

    assert is_invalid is False
    assert "Closed above prior high" not in text


def test_reason_for_data_invalid_is_marked_as_invalid_badge():
    result = EvalResult(ticker="NEWCO", status=STATUS_DATA_INVALID, data_invalid_reason="Only 50 completed sessions available, need at least 420")
    row = momentum_continuation.serialize(result)
    text, is_invalid = momentum_continuation.reason_for(row)

    assert is_invalid is True
    assert "Only 50 completed sessions" in text


def test_reason_for_ineligible_trend_is_not_marked_invalid():
    result = EvalResult(
        ticker="XYZ",
        status=STATUS_INELIGIBLE_TREND,
        trend_pass=False,
        trend_detail={"close_gt_sma200": False, "close": 90.0, "sma_200": 100.0},
    )
    row = momentum_continuation.serialize(result)
    assert row["ui_group"] == GROUP_OTHER
    text, is_invalid = momentum_continuation.reason_for(row)
    assert is_invalid is False


def test_reason_for_unrecognized_group_has_a_generic_fallback():
    text, is_invalid = momentum_continuation.reason_for({"ui_group": "some_future_group", "status": "WHATEVER"})
    assert is_invalid is False
    assert "Unrecognized status" in text
