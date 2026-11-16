"""Tests for src/setups/momentum_continuation/status_mapping.py - the six Part C
row-copy templates (docs/Momentum Continuation - V2 Logic Improvement Specs.md) and the
STATUS_TO_GROUP mapping, in isolation from the adapter/engine.
"""

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
)
from src.setups.momentum_continuation.status_mapping import (
    STATUS_TO_GROUP,
    flattening_badge_text,
    row_text,
)
from src.status_groups import GROUP_DEVELOPING, GROUP_OTHER, GROUP_QUALIFIED


def test_a_row_from_the_pre_rebuild_engine_is_not_misread_as_l1_fail():
    # Old-schema row: has "status"/"ui_group" but no "l1_state" at all (that field is
    # new). Must not silently fall through to the L1_FAIL branch and claim a Trend
    # failure that was never actually decided.
    old_row = {"status": "DEVELOPING", "ui_group": "developing"}
    assert row_text(old_row) == "(awaiting re-evaluation)"


def test_status_to_group_covers_every_status():
    assert STATUS_TO_GROUP == {
        STATUS_QUALIFIED: GROUP_QUALIFIED,
        STATUS_DEVELOPING: GROUP_DEVELOPING,
        STATUS_MONITORED: GROUP_OTHER,
    }


def test_qualified_row_shows_the_daily_weekly_breakdown_alongside_the_score():
    result = {
        "status": STATUS_QUALIFIED,
        "l1_state": L1_PASS,
        "armed": True,
        "l2_age": 2,
        "l3_score": 4,
        "l3_detail": {"stoch_pts": 2, "rsi_pts": 2, "macd_pts": 0},
    }
    text = row_text(result)
    assert "Daily <span class=\"sd-check-pass\">✓</span> 2d ago" in text
    assert "Weekly (RSI <span class=\"sd-check-pass\">✓</span>" in text
    assert "MACD <span class=\"sd-check-fail\">✗</span>" in text
    assert "Stoch <span class=\"sd-check-pass\">✓</span>" in text
    assert text.endswith("Confidence: 4/5")


def test_developing_daily_in_awaiting_weekly_confirmation():
    result = {
        "status": STATUS_DEVELOPING,
        "l1_state": L1_PASS,
        "armed": True,
        "l2_age": 2,
        "l3_score": 2,
        "l3_detail": {"stoch_pts": 0, "rsi_pts": 2, "macd_pts": 0},
    }
    text = row_text(result)
    assert "Daily <span class=\"sd-check-pass\">✓</span>" in text
    assert "RSI <span class=\"sd-check-pass\">✓</span>" in text
    assert "MACD <span class=\"sd-check-fail\">✗</span>" in text
    assert "Stoch <span class=\"sd-check-fail\">✗</span>" in text
    assert "3d to confirm" in text  # ARMED_MAX_AGE(5) - l2_age(2)


def test_developing_awaiting_daily_signal_has_no_confirm_clause():
    result = {
        "status": STATUS_DEVELOPING,
        "l1_state": L1_PASS,
        "armed": False,
        "l3_score": 0,
        "l3_detail": {"stoch_pts": 0, "rsi_pts": 0, "macd_pts": 0},
    }
    text = row_text(result)
    assert "Daily <span class=\"sd-check-fail\">✗</span>" in text
    assert "Weekly" in text
    assert "to confirm" not in text


def test_monitored_l1_failed_only():
    result = {"status": STATUS_MONITORED, "l1_state": L1_FAIL, "armed": False}
    assert row_text(result) == 'Trend <span class="sd-check-fail">✗</span>'


def test_monitored_l1_failed_plus_daily_signal_in():
    result = {"status": STATUS_MONITORED, "l1_state": L1_FAIL, "armed": True, "l2_age": 0}
    text = row_text(result)
    assert text == 'Trend <span class="sd-check-fail">✗</span> ; Daily <span class="sd-check-pass">✓</span> 0d ago'


def test_monitored_not_evaluable_daily_also_not_evaluable():
    result = {"status": STATUS_MONITORED, "l1_state": L1_NOT_EVALUABLE, "l2_state": L2_NOT_EVALUABLE, "armed": False}
    text = row_text(result)
    assert text == "Trend (not enough history yet) ; Daily (not enough history yet)"


def test_monitored_not_evaluable_daily_already_resolved():
    armed = {"status": STATUS_MONITORED, "l1_state": L1_NOT_EVALUABLE, "l2_state": L2_ARMED, "armed": True}
    not_armed = {"status": STATUS_MONITORED, "l1_state": L1_NOT_EVALUABLE, "l2_state": L2_NOT_ARMED, "armed": False}
    assert row_text(armed) == 'Trend (not enough history yet) ; Daily <span class="sd-check-pass">✓</span>'
    assert row_text(not_armed) == 'Trend (not enough history yet) ; Daily <span class="sd-check-fail">✗</span>'


def test_flattening_badge_only_on_developing_rows_with_the_flag():
    assert flattening_badge_text({"status": STATUS_DEVELOPING, "stoch_flattening": True}) == "STOCH FLATTENING"
    assert flattening_badge_text({"status": STATUS_DEVELOPING, "stoch_flattening": False}) is None
    assert flattening_badge_text({"status": STATUS_QUALIFIED, "stoch_flattening": True}) is None
    assert flattening_badge_text({"status": STATUS_MONITORED, "stoch_flattening": True}) is None
