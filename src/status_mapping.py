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

from src.data_engine import LOCATION_PROXIMITY_PCT, OSCILLATOR_ZONE_MAX
from src.rules_engine import (
    BADGE_LOCATION_PENDING,
    BADGE_OSCILLATOR_PENDING,
    GRACE_WINDOW_SESSIONS,
    STATUS_CANDIDATE,
    STATUS_DATA_INVALID,
    STATUS_DEVELOPING,
    STATUS_INELIGIBLE_TREND,
    direction_of,
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

# direction_of(...) -> a short clause, or "" (direction unknown/not applicable)
# so callers can drop the clause entirely rather than print "unknown". Every
# branch (including the flat case) must resolve to *something* - "not yet in
# zone" swallowing "holding flat" was a real gap, not a cosmetic one, since a
# reading that's dead flat vs. one that's actively worsening read as the same
# message otherwise.
_DIRECTION_PHRASE = {
    "closing_in": "closing in",
    "drifting_away": "drifting away",
    "holding_steady": "holding flat",
}

RECOVERY_TAG = "↑ Closed above prior high"


def ui_group_for_status(status: str) -> str:
    """Unrecognized status -> GROUP_UNKNOWN, never silently Qualified/Developing."""
    return STATUS_TO_GROUP.get(status, GROUP_UNKNOWN)


def has_recovery_tag(result: dict) -> bool:
    """True when today's close cleared the prior session's high - a non-gating
    follow-through signal (rules_engine.EvalResult.recovery_observed) that used
    to be computed and then never shown anywhere in the UI."""
    return bool(result.get("recovery_observed"))


def _ema_label(ema_ref_is_ema8) -> str:
    if ema_ref_is_ema8 is None:
        return "EMA"
    return "EMA8" if ema_ref_is_ema8 else "EMA21"


def _recency_suffix(detail: dict) -> str:
    """"" (gate hasn't passed, no recency to report) / "today" / "triggered 3
    sessions ago - grace expires next session". Flags the last 1 session of
    grace remaining so a Candidate about to lapse back to Developing on the
    next refresh doesn't do so without warning."""
    sessions_ago = detail.get("sessions_ago")
    if sessions_ago is None:
        return ""
    if sessions_ago == 0:
        return "today"
    remaining = GRACE_WINDOW_SESSIONS - sessions_ago
    plural = "" if sessions_ago == 1 else "s"
    phrase = f"triggered {sessions_ago} session{plural} ago"
    if remaining <= 1:
        phrase += " — grace expires next session"
    return phrase


def _location_pending_phrase(dpct: float, direction: str | None, ema_label: str) -> str:
    """Spells out the actual condition ("needs <=3.0% from its EMA") rather
    than naming it "the zone" - that noun meant nothing on its own, and this
    gate and the Oscillator gate below each have their own unrelated zone, so
    reusing the word between them was actively misleading, not just vague.
    `direction` is an already-resolved direction_of(...) code, not raw
    readings - callers compute it themselves since the two call sites
    (the live clause and the archived Badge) get it from different places."""
    phrase = _DIRECTION_PHRASE.get(direction or "")
    base = f"Price {dpct:.1f}% from {ema_label}, needs ≤{LOCATION_PROXIMITY_PCT:.1f}%"
    return f"{base} — {phrase}" if phrase else base


def _oscillator_pending_phrase(stoch_k: float, direction: str | None, in_zone) -> str:
    """in_zone (already <=30 today, just no crossover yet) takes priority over
    direction - it's the more decision-relevant fact of the two, and "waiting
    on a cross" vs. "still falling toward oversold" are genuinely different
    situations a direction clause alone can't distinguish."""
    if in_zone:
        return f"%K {stoch_k:.1f} — already oversold (≤{OSCILLATOR_ZONE_MAX:.0f}), awaiting the bullish cross"
    phrase = _DIRECTION_PHRASE.get(direction or "")
    base = f"%K {stoch_k:.1f}, above the ≤{OSCILLATOR_ZONE_MAX:.0f} oversold trigger"
    return f"{base} — {phrase}" if phrase else base


def badge_text(badge: dict) -> str:
    """badge: a serialized rules_engine.Badge (as stored in results.json).
    Not currently rendered by app.py (the pipeline-chain sentence below
    absorbed this) - kept for a future per-gate detail view."""
    code = badge["code"]
    label = BADGE_LABEL.get(code, code)
    if code == BADGE_LOCATION_PENDING:
        phrase = _location_pending_phrase(badge["measured"], badge.get("direction"), badge.get("ema_label") or "EMA")
        return f"{label} | {phrase}"
    if code == BADGE_OSCILLATOR_PENDING:
        phrase = _oscillator_pending_phrase(badge["measured"], badge.get("direction"), badge.get("in_zone"))
        return f"{label} | {phrase}"
    return f"{label} | {badge['measured']:.1f} · {badge['threshold']:.1f}"


def _location_clause(result: dict) -> str:
    """The Location leg of the pipeline chain: always states what's actually
    being measured (price's % distance from its reference EMA) and how it's
    moving - whether or not the gate has passed - instead of a content-free
    "reached zone" that read as a pass/fail bit with no number attached."""
    detail = result.get("location_detail") or {}
    dpct = detail.get("dpct")
    if dpct is None:
        return "Price-to-EMA reading unavailable"
    ema_label = _ema_label(detail.get("ema_ref_is_ema8"))
    if detail.get("pass"):
        base = f"Price {dpct:.1f}% from {ema_label} (within {LOCATION_PROXIMITY_PCT:.1f}%)"
        recency = _recency_suffix(detail)
        return f"{base}, {recency}" if recency else base
    direction = direction_of(dpct, detail.get("dpct_prev"))
    return _location_pending_phrase(dpct, direction, ema_label)


def _oscillator_clause(result: dict) -> str:
    """The Oscillator leg of the pipeline chain - same always-show-the-number
    treatment as _location_clause above."""
    detail = result.get("oscillator_detail") or {}
    stoch_k = detail.get("stoch_k")
    if stoch_k is None:
        return "Stochastic reading unavailable"
    if detail.get("pass"):
        base = f"Stochastic crossed up from oversold (%K {stoch_k:.1f})"
        recency = _recency_suffix(detail)
        return f"{base}, {recency}" if recency else base
    direction = direction_of(stoch_k, detail.get("stoch_k_prev"))
    return _oscillator_pending_phrase(stoch_k, direction, detail.get("in_zone"))


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
