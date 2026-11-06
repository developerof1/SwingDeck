from src.rules_engine import (
    BADGE_LOCATION_PENDING,
    BADGE_OSCILLATOR_PENDING,
    GRACE_WINDOW_SESSIONS,
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
    developing_reason,
    has_recovery_tag,
    other_monitored_reason,
    qualified_reason,
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


def test_badge_text_location_pending_carries_magnitude_direction_and_ema_label():
    location = {
        "code": "LOCATION_PENDING",
        "measured": 4.2,
        "threshold": 3.0,
        "direction": "closing_in",
        "ema_label": "EMA21",
    }
    text = badge_text(location)
    assert "4.2%" in text and "EMA21" in text and "3.0%" in text and "closing in" in text


def test_badge_text_oscillator_pending_in_zone_beats_direction():
    oscillator = {
        "code": "OSCILLATOR_PENDING",
        "measured": 28.0,
        "threshold": 30.0,
        "direction": "drifting_away",
        "in_zone": True,
    }
    text = badge_text(oscillator)
    assert "in the ≤30 zone" in text
    assert "drifting away" not in text


def test_badge_text_oscillator_pending_falling_toward_zone():
    oscillator = {"code": "OSCILLATOR_PENDING", "measured": 38.1, "threshold": 30.0, "direction": "closing_in"}
    text = badge_text(oscillator)
    assert "38.1" in text and "falling toward ≤30" in text


def test_badge_text_oscillator_pending_moving_away():
    oscillator = {"code": "OSCILLATOR_PENDING", "measured": 56.8, "threshold": 30.0, "direction": "drifting_away"}
    text = badge_text(oscillator)
    assert "moving away from ≤30" in text


def test_badge_text_oscillator_pending_unknown_direction_falls_back_to_plain_reading():
    oscillator = {"code": "OSCILLATOR_PENDING", "measured": 56.8, "threshold": 30.0, "direction": None}
    text = badge_text(oscillator)
    assert "56.8" in text and "not yet ≤30" in text


def test_data_invalid_is_flagged_distinctly_from_plain_ineligibility():
    data_invalid_result = {"status": STATUS_DATA_INVALID, "data_invalid_reason": "only 10 sessions, need 420"}
    reason, is_invalid = other_monitored_reason(data_invalid_result)
    assert is_invalid is True
    assert "Data unavailable/invalid" in reason

    ineligible_result = {
        "status": STATUS_INELIGIBLE_TREND,
        "trend_detail": {
            "close": 100.0,
            "sma_200": 110.0,
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


def test_other_monitored_trend_reason_carries_magnitude():
    result = {
        "status": STATUS_INELIGIBLE_TREND,
        "trend_detail": {
            "close": 96.1,
            "sma_200": 100.0,
            "close_gt_sma200": False,
            "sma50_gt_sma200": True,
            "sma50_rising": True,
            "monthly_pass": True,
            "weekly_pass": True,
        },
    }
    reason, _ = other_monitored_reason(result)
    assert "96.1" in reason and "100.0" in reason and "3.9%" in reason


def test_other_monitored_reason_surfaces_mtf_sub_reasons_with_readings():
    monthly_fail = {
        "status": STATUS_INELIGIBLE_TREND,
        "trend_detail": {
            "close_gt_sma200": True,
            "sma50_gt_sma200": True,
            "sma50_rising": True,
            "monthly_pass": False,
            "monthly_stoch_k": 22.1,
            "monthly_stoch_d": 24.6,
            "monthly_stoch_k_prev": 23.0,
            "weekly_pass": True,
        },
    }
    reason, _ = other_monitored_reason(monthly_fail)
    assert "monthly" in reason.lower()
    assert "22.1" in reason and "24.6" in reason and "23.0" in reason

    weekly_fail = {
        "status": STATUS_INELIGIBLE_TREND,
        "trend_detail": {
            "close_gt_sma200": True,
            "sma50_gt_sma200": True,
            "sma50_rising": True,
            "monthly_pass": True,
            "weekly_pass": False,
            "weekly_stoch_k": 14.2,
            "weekly_stoch_k_prev": 16.8,
        },
    }
    reason2, _ = other_monitored_reason(weekly_fail)
    assert "weekly" in reason2.lower() and "14.2" in reason2


def test_other_monitored_reason_reports_compound_failure_count():
    result = {
        "status": STATUS_INELIGIBLE_TREND,
        "trend_detail": {
            "close": 90.0,
            "sma_200": 100.0,
            "close_gt_sma200": False,
            "sma50_gt_sma200": False,
            "sma50_rising": True,
            "monthly_pass": True,
            "weekly_pass": False,
            "weekly_stoch_k": 14.0,
            "weekly_stoch_k_prev": 16.0,
            "sma_50": 92.0,
        },
    }
    reason, _ = other_monitored_reason(result)
    assert "3 of 5 trend conditions failing" in reason


def test_qualified_reason_both_gates_triggered_today():
    result = {
        "location_detail": {"pass": True, "sessions_ago": 0},
        "oscillator_detail": {"pass": True, "sessions_ago": 0},
    }
    reason = qualified_reason(result)
    assert reason == "Trend OK → Location reached zone today → Oscillator crossed today."


def test_qualified_reason_flags_grace_about_to_expire():
    result = {
        "location_detail": {"pass": True, "sessions_ago": GRACE_WINDOW_SESSIONS},
        "oscillator_detail": {"pass": True, "sessions_ago": 0},
    }
    reason = qualified_reason(result)
    assert "5 sessions ago" in reason
    assert "grace expires next session" in reason


def test_developing_reason_combines_passed_and_pending_legs_in_pipeline_order():
    result = {
        "location_detail": {"pass": True, "sessions_ago": 2},
        "oscillator_detail": {"pass": False},
        "developing_badges": [
            {
                "code": BADGE_OSCILLATOR_PENDING,
                "measured": 38.1,
                "threshold": 30.0,
                "direction": "closing_in",
                "in_zone": False,
            }
        ],
    }
    reason = developing_reason(result)
    assert reason.startswith("Trend OK → Location reached zone 2 sessions ago → Oscillator pending")
    assert "38.1" in reason and "falling toward ≤30" in reason


def test_developing_reason_both_legs_pending():
    result = {
        "location_detail": {"pass": False},
        "oscillator_detail": {"pass": False},
        "developing_badges": [
            {"code": BADGE_LOCATION_PENDING, "measured": 4.2, "threshold": 3.0, "direction": None, "ema_label": "EMA8"},
            {
                "code": BADGE_OSCILLATOR_PENDING,
                "measured": 56.8,
                "threshold": 30.0,
                "direction": "drifting_away",
                "in_zone": False,
            },
        ],
    }
    reason = developing_reason(result)
    assert "Location pending" in reason and "4.2%" in reason
    assert "Oscillator pending" in reason and "moving away from ≤30" in reason


def test_has_recovery_tag_reflects_recovery_observed():
    assert has_recovery_tag({"recovery_observed": True}) is True
    assert has_recovery_tag({"recovery_observed": False}) is False
    assert has_recovery_tag({}) is False
