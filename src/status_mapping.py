"""Central engine-status -> UI mapping (docs/SwingDeck_V2_Refactoring_Spec.md
Section 6). Kept separate from rules_engine.py (which stays pure eligibility math)
and out of app.py (so the mapping isn't scattered through render code) - this is
the one place a status decides its UI group, dot color, and reason text.

Every message built here is held to the same bar (see swingdeck-message-overhaul-
todo memory): it must carry magnitude (the actual measured number, not just a
pass/fail bit), direction (closing in on the threshold vs. drifting away from it,
wherever a prior-session reading exists to compare against), and recency (how
many sessions ago a grace-window gate actually triggered, and how much of the
grace window is left) - so a row is self-explanatory without opening a detail
panel.
"""

from src.rules_engine import (
    BADGE_LOCATION_PENDING,
    BADGE_OSCILLATOR_PENDING,
    GRACE_WINDOW_SESSIONS,
    STATUS_CANDIDATE,
    STATUS_DATA_INVALID,
    STATUS_DEVELOPING,
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

BADGE_LABEL = {
    BADGE_LOCATION_PENDING: "LOCATION PENDING",
    BADGE_OSCILLATOR_PENDING: "OSCILLATOR PENDING",
}

# badge["direction"] -> a short clause, or "" (direction unknown/not applicable)
# so callers can drop the clause entirely rather than print "unknown".
_DIRECTION_PHRASE = {
    "closing_in": "closing in",
    "drifting_away": "drifting away",
    "holding_steady": "holding steady",
}

RECOVERY_TAG = "↑ Follow-through"


def ui_group_for_status(status: str) -> str:
    """Unrecognized status -> GROUP_UNKNOWN, never silently Qualified/Developing."""
    return STATUS_TO_GROUP.get(status, GROUP_UNKNOWN)


def has_recovery_tag(result: dict) -> bool:
    """True when today's close cleared the prior session's high - a non-gating
    follow-through signal (rules_engine.EvalResult.recovery_observed) that used
    to be computed and then never shown anywhere in the UI."""
    return bool(result.get("recovery_observed"))


def _location_progress_phrase(badge: dict) -> str:
    """Magnitude + direction for a still-pending Location gate, e.g.
    "4.2% from EMA8 (max 3.0%), closing in"."""
    ema_label = badge.get("ema_label") or "EMA"
    direction = _DIRECTION_PHRASE.get(badge.get("direction") or "")
    base = f"{badge['measured']:.1f}% from {ema_label} (max {badge['threshold']:.1f}%)"
    return f"{base}, {direction}" if direction else base


def _oscillator_progress_phrase(badge: dict) -> str:
    """Magnitude + direction + in_zone for a still-pending Oscillator gate.
    in_zone (already <=30 today, just no crossover yet) takes priority over
    direction - it's the more decision-relevant fact of the two."""
    stoch_k, threshold = badge["measured"], badge["threshold"]
    if badge.get("in_zone"):
        return f"%K {stoch_k:.1f}, in the ≤{threshold:.0f} zone, awaiting bullish cross"
    direction = badge.get("direction")
    if direction == "closing_in":
        return f"%K {stoch_k:.1f}, falling toward ≤{threshold:.0f}"
    if direction == "drifting_away":
        return f"%K {stoch_k:.1f}, moving away from ≤{threshold:.0f}"
    return f"%K {stoch_k:.1f}, not yet ≤{threshold:.0f}"


def badge_text(badge: dict) -> str:
    """badge: a serialized rules_engine.Badge (as stored in results.json).
    e.g. "LOCATION PENDING | 4.2% from EMA8 (max 3.0%), closing in" /
    "OSCILLATOR PENDING | %K 38.1, falling toward ≤30"."""
    code = badge["code"]
    label = BADGE_LABEL.get(code, code)
    if code == BADGE_LOCATION_PENDING:
        return f"{label} | {_location_progress_phrase(badge)}"
    if code == BADGE_OSCILLATOR_PENDING:
        return f"{label} | {_oscillator_progress_phrase(badge)}"
    return f"{label} | {badge['measured']:.1f} · {badge['threshold']:.1f}"


def _recency_phrase(verb_phrase: str, detail: dict) -> str:
    """e.g. verb_phrase="reached zone" + sessions_ago=3 -> "reached zone 3
    sessions ago - grace expires next session". Flags the last 1 session of
    grace remaining so a Candidate that's about to lapse back to Developing
    on the next refresh doesn't do so without warning."""
    sessions_ago = detail.get("sessions_ago")
    if sessions_ago is None:
        return f"{verb_phrase} status unknown"
    if sessions_ago == 0:
        return f"{verb_phrase} today"
    remaining = GRACE_WINDOW_SESSIONS - sessions_ago
    plural = "" if sessions_ago == 1 else "s"
    phrase = f"{verb_phrase} {sessions_ago} session{plural} ago"
    if remaining <= 1:
        phrase += " — grace expires next session"
    return phrase


def _location_clause(result: dict) -> str:
    """The Location leg of the pipeline chain, whichever state it's in."""
    detail = result.get("location_detail") or {}
    if detail.get("pass"):
        return f"Location {_recency_phrase('reached zone', detail)}"
    badge = next(
        (b for b in result.get("developing_badges") or [] if b.get("code") == BADGE_LOCATION_PENDING), None
    )
    return f"Location pending — {_location_progress_phrase(badge)}" if badge else "Location pending"


def _oscillator_clause(result: dict) -> str:
    """The Oscillator leg of the pipeline chain, whichever state it's in."""
    detail = result.get("oscillator_detail") or {}
    if detail.get("pass"):
        return f"Oscillator {_recency_phrase('crossed', detail)}"
    badge = next(
        (b for b in result.get("developing_badges") or [] if b.get("code") == BADGE_OSCILLATOR_PENDING), None
    )
    return f"Oscillator pending — {_oscillator_progress_phrase(badge)}" if badge else "Oscillator pending"


def qualified_reason(result: dict) -> str:
    """Candidate row text - always a Trend -> Location -> Oscillator chain, so a
    Qualified row reads as confirmation of the same pipeline a Developing row
    is still working through, not a disconnected static sentence."""
    return f"Trend OK → {_location_clause(result)} → {_oscillator_clause(result)}."


def developing_reason(result: dict) -> str:
    """Developing row text - same chain template as qualified_reason(), except
    at least one leg is still pending. Replaces the old side-by-side badge
    pair: reading top-down tells you not just *what's* pending but *where in
    the pipeline* it's pending, which the two independent pills never did."""
    return f"Trend OK → {_location_clause(result)} → {_oscillator_clause(result)}."


def _pct_diff(a: float, b: float) -> float:
    return abs(a - b) / b * 100


# trend_detail boolean fields, in the same order as the Trend Gate spec
# (docs/Momentum Continuation V2 — Logic.md §3): Layer 1 structure, then
# Layer 2 MTF. The first False in this order is the headline reason.
_TREND_CONDITION_FIELDS = ("close_gt_sma200", "sma50_gt_sma200", "sma50_rising", "monthly_pass", "weekly_pass")


def _trend_failure_headline(trend_detail: dict) -> str | None:
    close, sma_50, sma_200 = trend_detail.get("close"), trend_detail.get("sma_50"), trend_detail.get("sma_200")
    sma_50_prev20 = trend_detail.get("sma_50_prev20")

    if trend_detail.get("close_gt_sma200") is False:
        pct = _pct_diff(close, sma_200)
        return f"Trend: price ${close:.2f} is {pct:.1f}% below 200-SMA (${sma_200:.2f})"
    if trend_detail.get("sma50_gt_sma200") is False:
        pct = _pct_diff(sma_50, sma_200)
        return f"Trend: 50-SMA (${sma_50:.2f}) is {pct:.1f}% below 200-SMA (${sma_200:.2f})"
    if trend_detail.get("sma50_rising") is False:
        pct = _pct_diff(sma_50, sma_50_prev20)
        verb = "down" if sma_50 < sma_50_prev20 else "flat"
        return f"Trend: 50-SMA {verb} {pct:.1f}% vs. 20 sessions ago (${sma_50:.2f} vs ${sma_50_prev20:.2f})"
    if trend_detail.get("monthly_pass") is False:
        mk, md = trend_detail.get("monthly_stoch_k"), trend_detail.get("monthly_stoch_d")
        mk_prev = trend_detail.get("monthly_stoch_k_prev")
        return f"Trend: monthly %K {mk:.1f} below %D {md:.1f} and below last month's {mk_prev:.1f}"
    if trend_detail.get("weekly_pass") is False:
        wk, wk_prev = trend_detail.get("weekly_stoch_k"), trend_detail.get("weekly_stoch_k_prev")
        if wk < 20:
            return f"Trend: weekly %K {wk:.1f} below the 20 floor"
        return f"Trend: weekly %K {wk:.1f} not rising (was {wk_prev:.1f})"
    return None


def other_monitored_reason(result: dict) -> tuple[str, bool]:
    """Returns (reason_text, is_data_invalid) for an Other-monitored row - the
    Data Invalid case gets its own icon/text treatment per the refactoring spec
    (§6/§7), distinct from a plain trend ineligibility, even though both share
    the neutral dot and GROUP_OTHER bucket."""
    status = result.get("status")
    if status == STATUS_DATA_INVALID:
        return f"Data unavailable/invalid — {result.get('data_invalid_reason') or 'reason unknown'}", True

    trend_detail = result.get("trend_detail") or {}
    if status == STATUS_INELIGIBLE_TREND:
        headline = _trend_failure_headline(trend_detail)
        if headline is None:
            return "Trend criteria not met", False
        conditions = [trend_detail.get(f) for f in _TREND_CONDITION_FIELDS]
        evaluated = [c for c in conditions if c is not None]
        failing = sum(1 for c in evaluated if c is False)
        if failing > 1:
            headline += f"  ({failing} of {len(evaluated)} trend conditions failing)"
        return headline, False

    return "Status not recognized", False
