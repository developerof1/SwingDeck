"""Covers the setup registry (src/setups/__init__.py, src/setups/base.py) and the
Momentum Continuation adapter (src/setups/momentum_continuation/__init__.py) - a pure
wrap of rules_engine.evaluate_stock/gates.add_continuation_indicators plus the
serialize()/reason_for()/badge_for() logic. The underlying gate math itself is already
covered by test_momentum_continuation_gates.py/test_momentum_continuation_rules_engine.py;
these tests only check the wrap is faithful.
"""

import pytest

from src.setups import get_setup, list_setups
from src.setups import momentum_continuation
from src.setups.momentum_continuation.gates import add_continuation_indicators
from src.setups.momentum_continuation.rules_engine import (
    EvalResult,
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
from src.status_groups import GROUP_DEVELOPING, GROUP_OTHER, GROUP_QUALIFIED


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


def _qualified_result() -> EvalResult:
    return EvalResult(
        ticker="NVDA",
        status=STATUS_QUALIFIED,
        evaluated_session="2026-11-06",
        l1_state=L1_PASS,
        l2_state=L2_ARMED,
        l2_age=0,
        armed=True,
        l3_score=5,
        l3_detail={"stoch_pts": 2, "rsi_pts": 2, "macd_pts": 1},
        stoch_flattening=False,
        diagnostics={"close": 101.2},
    )


def test_serialize_round_trips_every_eval_result_field_reason_for_needs():
    result = _qualified_result()
    row = momentum_continuation.serialize(result)

    assert row["status"] == STATUS_QUALIFIED
    assert row["ui_group"] == GROUP_QUALIFIED
    assert row["evaluated_session"] == "2026-11-06"
    assert row["l1_state"] == L1_PASS
    assert row["l2_state"] == L2_ARMED
    assert row["armed"] is True
    assert row["l3_score"] == 5
    assert row["l3_detail"] == result.l3_detail
    assert row["stoch_flattening"] is False


def test_reason_for_qualified_is_just_the_score():
    row = momentum_continuation.serialize(_qualified_result())
    text, is_invalid = momentum_continuation.reason_for(row)

    assert is_invalid is False
    assert text == "5/5"


def test_reason_for_developing_awaiting_daily_signal():
    result = EvalResult(
        ticker="AMD",
        status=STATUS_DEVELOPING,
        l1_state=L1_PASS,
        l2_state=L2_NOT_ARMED,
        armed=False,
        l3_score=2,
        l3_detail={"stoch_pts": 0, "rsi_pts": 2, "macd_pts": 0},
    )
    row = momentum_continuation.serialize(result)
    assert row["ui_group"] == GROUP_DEVELOPING
    text, is_invalid = momentum_continuation.reason_for(row)

    assert is_invalid is False
    assert "Daily" in text and "Weekly" in text
    assert "to confirm" not in text


def test_reason_for_monitored_l1_fail_is_not_marked_invalid():
    result = EvalResult(ticker="XYZ", status=STATUS_MONITORED, l1_state=L1_FAIL, l2_state=L2_NOT_ARMED, armed=False)
    row = momentum_continuation.serialize(result)
    assert row["ui_group"] == GROUP_OTHER
    text, is_invalid = momentum_continuation.reason_for(row)
    assert is_invalid is False
    assert text == "Trend <span class=\"sd-check-fail\">✗</span>"


def test_reason_for_monitored_not_evaluable_is_not_marked_invalid():
    result = EvalResult(ticker="NEWCO", status=STATUS_MONITORED, l1_state=L1_NOT_EVALUABLE, l2_state=L2_NOT_EVALUABLE)
    row = momentum_continuation.serialize(result)
    text, is_invalid = momentum_continuation.reason_for(row)

    assert is_invalid is False
    assert "not enough history yet" in text


def test_badge_for_returns_flattening_pill_only_on_developing_rows_with_the_flag():
    flattening = EvalResult(ticker="SWK", status=STATUS_DEVELOPING, l1_state=L1_PASS, stoch_flattening=True)
    not_flattening = EvalResult(ticker="ROKU", status=STATUS_DEVELOPING, l1_state=L1_PASS, stoch_flattening=False)
    qualified = EvalResult(ticker="AVGO", status=STATUS_QUALIFIED, l1_state=L1_PASS, stoch_flattening=True)

    assert momentum_continuation.badge_for(momentum_continuation.serialize(flattening)) == "STOCH FLATTENING"
    assert momentum_continuation.badge_for(momentum_continuation.serialize(not_flattening)) is None
    assert momentum_continuation.badge_for(momentum_continuation.serialize(qualified)) is None
