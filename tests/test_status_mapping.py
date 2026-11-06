from src.rules_engine import (
    STATUS_CANDIDATE,
    STATUS_DATA_INVALID,
    STATUS_DEVELOPING,
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
    assert ui_group_for_status(STATUS_DATA_INVALID) == GROUP_OTHER


def test_unrecognized_status_never_defaults_to_qualified_or_developing():
    group = ui_group_for_status("NOT_A_REAL_STATUS")
    assert group == GROUP_UNKNOWN
    assert group not in (GROUP_QUALIFIED, GROUP_DEVELOPING)


def test_badge_text_formats_location_and_oscillator_pending():
    location = {"code": "LOCATION_PENDING", "measured": 4.2, "threshold": 3.0}
    assert badge_text(location) == "LOCATION PENDING | 4.2% from EMA · max 3.0%"

    oscillator = {"code": "OSCILLATOR_PENDING", "measured": 38.1, "threshold": 30.0}
    assert badge_text(oscillator) == "OSCILLATOR PENDING | %K 38.1 · zone ≤30"


def test_data_invalid_is_flagged_distinctly_from_plain_ineligibility():
    data_invalid_result = {"status": STATUS_DATA_INVALID, "data_invalid_reason": "only 10 sessions, need 420"}
    reason, is_invalid = other_monitored_reason(data_invalid_result)
    assert is_invalid is True
    assert "Data unavailable/invalid" in reason

    ineligible_result = {
        "status": STATUS_INELIGIBLE_TREND,
        "trend_detail": {
            "close_gt_sma200": False,
            "sma50_gt_sma200": True,
            "sma50_rising": True,
            "monthly_pass": True,
            "weekly_pass": True,
        },
    }
    reason2, is_invalid2 = other_monitored_reason(ineligible_result)
    assert is_invalid2 is False
    assert "Data unavailable" not in reason2


def test_other_monitored_reason_surfaces_mtf_sub_reasons():
    monthly_fail = {
        "status": STATUS_INELIGIBLE_TREND,
        "trend_detail": {
            "close_gt_sma200": True,
            "sma50_gt_sma200": True,
            "sma50_rising": True,
            "monthly_pass": False,
            "weekly_pass": True,
        },
    }
    reason, _ = other_monitored_reason(monthly_fail)
    assert "monthly" in reason.lower()

    weekly_fail = {
        "status": STATUS_INELIGIBLE_TREND,
        "trend_detail": {
            "close_gt_sma200": True,
            "sma50_gt_sma200": True,
            "sma50_rising": True,
            "monthly_pass": True,
            "weekly_pass": False,
        },
    }
    reason2, _ = other_monitored_reason(weekly_fail)
    assert "weekly" in reason2.lower()
