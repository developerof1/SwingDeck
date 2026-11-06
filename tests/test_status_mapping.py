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


def test_badge_text_location_pending_never_says_the_word_zone():
    # "zone" is ambiguous - Location and Oscillator each have their own,
    # unrelated "zone" condition - so the message must spell out the actual
    # measured condition instead of naming it.
    location = {"code": "LOCATION_PENDING", "measured": 4.2, "threshold": 3.0, "direction": "closing_in", "ema_label": "EMA21"}
    text = badge_text(location)
    assert "zone" not in text.lower()
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
    assert "already oversold" in text
    assert "drifting away" not in text


def test_badge_text_oscillator_pending_direction_variants_all_resolve():
    base = {"code": "OSCILLATOR_PENDING", "measured": 56.8, "threshold": 30.0, "in_zone": False}
    closing_in = badge_text({**base, "direction": "closing_in"})
    drifting_away = badge_text({**base, "direction": "drifting_away"})
    holding_steady = badge_text({**base, "direction": "holding_steady"})
    unknown = badge_text({**base, "direction": None})

    assert "closing in" in closing_in
    assert "drifting away" in drifting_away
    assert "holding flat" in holding_steady
    # Flat and unknown must read differently - both used to collapse into the
    # same silent "no direction shown" message.
    assert holding_steady != unknown
    for text in (closing_in, drifting_away, holding_steady, unknown):
        assert "56.8" in text and "oversold trigger" in text


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


def test_qualified_reason_never_says_the_word_zone_and_always_shows_magnitude():
    result = {
        "location_detail": {"pass": True, "sessions_ago": 0, "dpct": 1.4, "ema_ref_is_ema8": True},
        "oscillator_detail": {"pass": True, "sessions_ago": 0, "stoch_k": 22.4},
    }
    reason = qualified_reason(result)
    assert reason == "Trend OK → Price 1.4% from EMA8 (within 3.0%), today → Stochastic crossed up from oversold (%K 22.4), today."
    assert "zone" not in reason.lower()


def test_qualified_reason_flags_grace_about_to_expire():
    result = {
        "location_detail": {"pass": True, "sessions_ago": GRACE_WINDOW_SESSIONS, "dpct": 2.9, "ema_ref_is_ema8": False},
        "oscillator_detail": {"pass": True, "sessions_ago": 0, "stoch_k": 18.0},
    }
    reason = qualified_reason(result)
    assert "5 sessions ago" in reason
    assert "grace expires next session" in reason


def test_developing_reason_shows_direction_on_a_pending_oscillator_leg():
    result = {
        "location_detail": {"pass": True, "sessions_ago": 2, "dpct": 1.8, "ema_ref_is_ema8": True},
        "oscillator_detail": {"pass": False, "stoch_k": 56.8, "stoch_k_prev": 60.0, "in_zone": False},
    }
    reason = developing_reason(result)
    assert reason.startswith("Trend OK → Price 1.8% from EMA8 (within 3.0%), triggered 2 sessions ago")
    assert "56.8" in reason
    assert "closing in" in reason  # 56.8 < 60.0 prev -> moving toward the trigger
    assert "zone" not in reason.lower()


def test_developing_reason_flat_oscillator_reading_says_so_explicitly():
    result = {
        "location_detail": {"pass": True, "sessions_ago": 0, "dpct": 1.0, "ema_ref_is_ema8": True},
        "oscillator_detail": {"pass": False, "stoch_k": 56.8, "stoch_k_prev": 56.8, "in_zone": False},
    }
    reason = developing_reason(result)
    assert "holding flat" in reason


def test_developing_reason_both_legs_pending():
    result = {
        "location_detail": {"pass": False, "dpct": 4.2, "dpct_prev": 5.0, "ema_ref_is_ema8": True},
        "oscillator_detail": {"pass": False, "stoch_k": 56.8, "stoch_k_prev": 50.0, "in_zone": False},
    }
    reason = developing_reason(result)
    assert "Price 4.2% from EMA8, needs ≤3.0%" in reason
    assert "closing in" in reason  # location improving (5.0 -> 4.2)
    assert "above the ≤30 oversold trigger" in reason
    assert "drifting away" in reason  # oscillator worsening (50.0 -> 56.8)


def test_developing_reason_oscillator_already_in_zone_says_awaiting_cross():
    result = {
        "location_detail": {"pass": True, "sessions_ago": 0, "dpct": 1.0, "ema_ref_is_ema8": True},
        "oscillator_detail": {"pass": False, "stoch_k": 27.0, "stoch_d": 29.0, "in_zone": True},
    }
    reason = developing_reason(result)
    assert "already oversold" in reason and "awaiting the bullish cross" in reason


def test_has_recovery_tag_reflects_recovery_observed():
    assert has_recovery_tag({"recovery_observed": True}) is True
    assert has_recovery_tag({"recovery_observed": False}) is False
    assert has_recovery_tag({}) is False
