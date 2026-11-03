from src.rules_engine import (
    STATUS_CANDIDATE,
    STATUS_DATA_INVALID,
    STATUS_DEVELOPING,
    STATUS_INELIGIBLE_PULLBACK,
    STATUS_INELIGIBLE_TREND,
)
from src.status_mapping import (
    GROUP_DEVELOPING,
    GROUP_OTHER,
    GROUP_QUALIFIED,
    GROUP_UNKNOWN,
    badge_text,
    other_monitored_reason,
    ui_group_for_status,
)


def test_each_status_maps_to_its_documented_group():
    assert ui_group_for_status(STATUS_CANDIDATE) == GROUP_QUALIFIED
    assert ui_group_for_status(STATUS_DEVELOPING) == GROUP_DEVELOPING
    assert ui_group_for_status(STATUS_INELIGIBLE_TREND) == GROUP_OTHER
    assert ui_group_for_status(STATUS_INELIGIBLE_PULLBACK) == GROUP_OTHER
    assert ui_group_for_status(STATUS_DATA_INVALID) == GROUP_OTHER


def test_unrecognized_status_never_defaults_to_qualified_or_developing():
    group = ui_group_for_status("NOT_A_REAL_STATUS")
    assert group == GROUP_UNKNOWN
    assert group not in (GROUP_QUALIFIED, GROUP_DEVELOPING)


def test_badge_text_matches_mockup_format():
    shallow = {"code": "SHALLOW_PULLBACK", "measured": 0.8, "threshold": 1.0}
    assert badge_text(shallow) == "SHALLOW PULLBACK | 0.8 ATR · minimum 1.0"

    extended = {"code": "EXTENDED_FROM_EMA20", "measured": 0.7, "threshold": 0.5}
    assert badge_text(extended) == "EXTENDED FROM EMA20 | 0.7 ATR · maximum 0.5"


def test_data_invalid_is_flagged_distinctly_from_plain_ineligibility():
    data_invalid_result = {"status": STATUS_DATA_INVALID, "data_invalid_reason": "only 10 sessions, need 252"}
    reason, is_invalid = other_monitored_reason(data_invalid_result)
    assert is_invalid is True
    assert "Data unavailable/invalid" in reason

    ineligible_result = {
        "status": STATUS_INELIGIBLE_TREND,
        "trend_detail": {"close_gt_sma200": False, "sma50_gt_sma200": True, "sma50_rising": True},
    }
    reason2, is_invalid2 = other_monitored_reason(ineligible_result)
    assert is_invalid2 is False
    assert "Data unavailable" not in reason2
