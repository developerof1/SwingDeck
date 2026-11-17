"""Momentum Continuation, wrapped behind the Setup interface (src/setups/base.py).

Everything this setup needs lives in this package: gates.py (indicator/gate-input
columns), rules_engine.py (pure evaluation logic), status_mapping.py (this setup's own
status->group mapping and all of Part C's row copy). Nothing outside this package
should ever need to import from gates/rules_engine/status_mapping directly - only
app.py/refresh_job.py's generic Setup/list_setups() plumbing, and this adapter's own
serialize()/reason_for()/badge_for(), touch this setup's internals.
"""

from src.setups.base import Setup
from src.setups.momentum_continuation.gates import add_continuation_indicators
from src.setups.momentum_continuation.rules_engine import EvalResult, evaluate_stock
from src.setups.momentum_continuation.status_mapping import (
    STATUS_TO_GROUP,
    flattening_badge_text,
    macd_turning_badge_text,
    row_text,
)
from src.status_groups import group_for_status

ID = "momentum_continuation"
LABEL = "Momentum Continuation"

add_indicators = add_continuation_indicators
evaluate = evaluate_stock


def serialize(result: EvalResult) -> dict:
    """The per-ticker row fields this setup owns - ticker/name are generic and attached
    by refresh_job.py itself, not included here."""
    return {
        "status": result.status,
        "ui_group": group_for_status(result.status, STATUS_TO_GROUP),
        "evaluated_session": result.evaluated_session,
        "l1_state": result.l1_state,
        "l2_state": result.l2_state,
        "l2_age": result.l2_age,
        "armed": result.armed,
        "l3_score": result.l3_score,
        "l3_detail": result.l3_detail,
        "stoch_flattening": result.stoch_flattening,
        "macd_turning": result.macd_turning,
        "diagnostics": result.diagnostics,
    }


def reason_for(result: dict) -> tuple[str, bool]:
    """(display text, is_invalid_badge) for one serialized row. is_invalid_badge is
    always False now - Part C's design brief asks for muted/neutral styling everywhere,
    with no alarm-color treatment even for a not-enough-history row, so the old
    "data invalid" badge styling has no equivalent in this model."""
    return row_text(result), False


def badge_for(result: dict) -> list[tuple[str, str]]:
    """The row-level informational badges (Part A §8): STOCH FLATTENING and MACD
    TURNING, right-aligned, muted/neutral, shown only on Developing rows where each
    applies. Each is paired with its own CSS class so they render in different colors;
    neither ever changes score or status."""
    badges = []
    flattening_text = flattening_badge_text(result)
    if flattening_text:
        badges.append((flattening_text, "sd-row-flattening-badge"))
    macd_text = macd_turning_badge_text(result)
    if macd_text:
        badges.append((macd_text, "sd-row-macd-badge"))
    return badges


SETUP = Setup(
    id=ID,
    label=LABEL,
    add_indicators=add_indicators,
    evaluate=evaluate,
    serialize=serialize,
    reason_for=reason_for,
    badge_for=badge_for,
)
