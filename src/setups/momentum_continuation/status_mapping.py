"""Momentum-Continuation-owned status -> UI mapping (docs/Momentum Continuation - V2
Logic Improvement Specs.md, Part C "Dashboard Row Copy"). Maps this setup's own
l1_state/l2_state/l3_score into the shared status_groups.py taxonomy, and builds the
six row-copy templates verbatim from the spec. No setup other than this one should ever
need to read this file.

Design brief (Part C): the one decision a row needs to drive is "do I go look at this
chart right now or not." No raw indicator values, no L1/L2/L3 jargon on screen - those
stay in the spec and this file. ✓/✗ are literal glyphs, colored muted teal-green/muted
orange (never red/green, never an alarm color) - including on NOT_EVALUABLE rows, which
is why `reason_for` always reports is_invalid=False now; the old alarm-badge styling for
"data invalid" doesn't exist in this model at all.
"""

from src.setups.momentum_continuation.gates import ARMED_MAX_AGE
from src.setups.momentum_continuation.rules_engine import (
    L1_NOT_EVALUABLE,
    L1_PASS,
    L2_ARMED,
    L2_NOT_EVALUABLE,
    STATUS_DEVELOPING,
    STATUS_MONITORED,
    STATUS_QUALIFIED,
)
from src.status_groups import GROUP_DEVELOPING, GROUP_OTHER, GROUP_QUALIFIED

STATUS_TO_GROUP = {
    STATUS_QUALIFIED: GROUP_QUALIFIED,
    STATUS_DEVELOPING: GROUP_DEVELOPING,
    STATUS_MONITORED: GROUP_OTHER,
}

FLATTENING_BADGE_TEXT = "STOCH FLATTENING"

_PASS_CLASS = "sd-check-pass"
_FAIL_CLASS = "sd-check-fail"


def _check(ok: bool) -> str:
    cls = _PASS_CLASS if ok else _FAIL_CLASS
    glyph = "✓" if ok else "✗"
    return f'<span class="{cls}">{glyph}</span>'


NOT_ENOUGH_HISTORY = "not enough history yet"


def _weekly_clause(result: dict) -> str:
    detail = result.get("l3_detail") or {}
    rsi_ok = _check(bool(detail.get("rsi_pts")))
    macd_ok = _check(bool(detail.get("macd_pts")))
    stoch_ok = _check(bool(detail.get("stoch_pts")))
    return f"Weekly (RSI {rsi_ok}, MACD {macd_ok}, Stoch {stoch_ok})"


def row_text(result: dict) -> str:
    """The subtext line for one row, per Part C's six templates."""
    l1_state = result.get("l1_state")
    l2_state = result.get("l2_state")
    armed = bool(result.get("armed"))
    status = result.get("status")

    if l1_state != L1_PASS:
        if l1_state == L1_NOT_EVALUABLE:
            daily = f"({NOT_ENOUGH_HISTORY})" if l2_state == L2_NOT_EVALUABLE else _check(l2_state == L2_ARMED)
            return f"Trend ({NOT_ENOUGH_HISTORY}) ; Daily {daily}"
        # l1_state == L1_FAIL
        if armed:
            return f"Trend {_check(False)} ; Daily {_check(True)} {result.get('l2_age')}d ago"
        return f"Trend {_check(False)}"

    # l1_state == L1_PASS
    if status == STATUS_QUALIFIED:
        return f"{result.get('l3_score')}/5"
    if armed:
        remaining = ARMED_MAX_AGE - (result.get("l2_age") or 0)
        return f"Daily {_check(True)} ; {_weekly_clause(result)} ; {remaining}d to confirm"
    return f"Daily {_check(False)} ; {_weekly_clause(result)}"


def flattening_badge_text(result: dict) -> str | None:
    """Right-aligned, muted/neutral pill - shown only on Developing rows where the
    weekly Stoch is flattening after a fall (Part A §8). Never changes status."""
    if result.get("status") != STATUS_DEVELOPING:
        return None
    return FLATTENING_BADGE_TEXT if result.get("stoch_flattening") else None
