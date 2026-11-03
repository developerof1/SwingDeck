"""Central engine-status -> UI mapping (docs/SwingDeck_V2_Refactoring_Spec.md
Section 6). Kept separate from rules_engine.py (which stays pure eligibility math)
and out of app.py (so the mapping isn't scattered through render code) - this is
the one place a status decides its UI group, dot color, and reason text.
"""

from src.rules_engine import (
    BADGE_EXTENDED_FROM_EMA20,
    BADGE_SHALLOW_PULLBACK,
    STATUS_CANDIDATE,
    STATUS_DATA_INVALID,
    STATUS_DEVELOPING,
    STATUS_INELIGIBLE_PULLBACK,
    STATUS_INELIGIBLE_TREND,
)

GROUP_QUALIFIED = "qualified"
GROUP_DEVELOPING = "developing"
GROUP_OTHER = "other"
GROUP_UNKNOWN = "unknown"

STATUS_TO_GROUP = {
    STATUS_CANDIDATE: GROUP_QUALIFIED,
    STATUS_DEVELOPING: GROUP_DEVELOPING,
    STATUS_INELIGIBLE_TREND: GROUP_OTHER,
    STATUS_INELIGIBLE_PULLBACK: GROUP_OTHER,
    STATUS_DATA_INVALID: GROUP_OTHER,
}

# Dot colors: green/amber/neutral only. V1's WATCH-blue has no equivalent here -
# there's no Trend-failure-but-close reclassification in the new engine.
DOT_QUALIFIED = "\U0001f7e2"  # green
DOT_DEVELOPING = "\U0001f7e1"  # amber
DOT_OTHER = "⚪"  # neutral/white

GROUP_DOT = {
    GROUP_QUALIFIED: DOT_QUALIFIED,
    GROUP_DEVELOPING: DOT_DEVELOPING,
    GROUP_OTHER: DOT_OTHER,
    GROUP_UNKNOWN: DOT_OTHER,
}

GROUP_LABEL = {
    GROUP_QUALIFIED: "Qualified",
    GROUP_DEVELOPING: "Developing",
    GROUP_OTHER: "Other monitored",
    GROUP_UNKNOWN: "Unknown status",
}

QUALIFIED_REASON = "Trend + pullback criteria met."

BADGE_LABEL = {
    BADGE_SHALLOW_PULLBACK: "SHALLOW PULLBACK",
    BADGE_EXTENDED_FROM_EMA20: "EXTENDED FROM EMA20",
}


def ui_group_for_status(status: str) -> str:
    """Unrecognized status -> GROUP_UNKNOWN, never silently Qualified/Developing."""
    return STATUS_TO_GROUP.get(status, GROUP_UNKNOWN)


def badge_text(badge: dict) -> str:
    """badge: {"code", "measured", "threshold"} (as serialized into results.json).
    e.g. "SHALLOW PULLBACK | 0.8 ATR · minimum 1.0" /
    "EXTENDED FROM EMA20 | 0.7 ATR · maximum 0.5"."""
    label = BADGE_LABEL.get(badge["code"], badge["code"])
    comparator = "minimum" if badge["code"] == BADGE_SHALLOW_PULLBACK else "maximum"
    return f"{label} | {badge['measured']:.1f} ATR · {comparator} {badge['threshold']:.1f}"


def other_monitored_reason(result: dict) -> tuple[str, bool]:
    """Returns (reason_text, is_data_invalid) for an Other-monitored row - the
    Data Invalid case gets its own icon/text treatment per the refactoring spec
    (§6/§7), distinct from a plain trend/pullback ineligibility, even though both
    share the neutral dot and GROUP_OTHER bucket."""
    status = result.get("status")
    if status == STATUS_DATA_INVALID:
        return f"Data unavailable/invalid — {result.get('data_invalid_reason') or 'reason unknown'}", True

    trend_detail = result.get("trend_detail") or {}
    if status == STATUS_INELIGIBLE_TREND:
        if trend_detail.get("sma50_gt_sma200") is False:
            return "Trend: 50-SMA below 200-SMA", False
        if trend_detail.get("close_gt_sma200") is False:
            return "Trend: price below 200-SMA", False
        if trend_detail.get("sma50_rising") is False:
            return "Trend: 50-SMA not rising vs. 20 sessions ago", False
        return "Trend criteria not met", False

    pullback_detail = result.get("pullback_detail") or {}
    if status == STATUS_INELIGIBLE_PULLBACK:
        if pullback_detail.get("decline") is False:
            return "Pullback: no recent decline (close not below 3-session-ago close)", False
        depth = pullback_detail.get("depth_atr")
        ema_dist = pullback_detail.get("ema_dist_atr")
        if depth is not None and depth > 3.0:
            return f"Pullback: too deep | {depth:.1f} ATR · maximum 3.0", False
        if ema_dist is not None and ema_dist < -1.0:
            return f"Pullback: too far below EMA20 | {ema_dist:.1f} ATR · minimum -1.0", False
        return "Pullback criteria not met", False

    return "Status not recognized", False
