"""Momentum Continuation, wrapped behind the Setup interface (src/setups/base.py).

Pure adapter, no behavior change: add_indicators/evaluate are the exact, unmodified functions
from src/data_engine.py and src/rules_engine.py. serialize()/reason_for() are moved - not
rewritten - from what used to be inline in scripts/refresh_job.py's evaluate_ticker() and
app.py's render_row() respectively, back when there was only ever one setup to hardcode against.

rules_engine.py/data_engine.py/status_mapping.py stay byte-for-byte untouched by this refactor
(see test_rules_engine.py::test_momentum_continuation_source_never_references_pullback_value) -
this module only wraps them.
"""

from src.data_engine import add_continuation_indicators
from src.rules_engine import EvalResult, evaluate_stock
from src.setups.base import Setup
from src.status_mapping import (
    GROUP_DEVELOPING,
    GROUP_OTHER,
    GROUP_QUALIFIED,
    RECOVERY_TAG,
    developing_reason,
    has_recovery_tag,
    other_monitored_reason,
    qualified_reason,
    ui_group_for_status,
)

ID = "momentum_continuation"
LABEL = "Momentum Continuation"

add_indicators = add_continuation_indicators
evaluate = evaluate_stock


def serialize(result: EvalResult) -> dict:
    """The per-ticker row fields this setup owns - ticker/name are generic and attached by
    refresh_job.py itself, not included here."""
    return {
        "status": result.status,
        "ui_group": ui_group_for_status(result.status),
        "evaluated_session": result.evaluated_session,
        "data_valid": result.data_valid,
        "recovery_observed": result.recovery_observed,
        "developing_badges": [
            {
                "code": b.code,
                "measured": b.measured,
                "threshold": b.threshold,
                "direction": b.direction,
                "in_zone": b.in_zone,
                "ema_label": b.ema_label,
            }
            for b in result.developing_badges
        ],
        "trend_detail": result.trend_detail,
        "location_detail": result.location_detail,
        "oscillator_detail": result.oscillator_detail,
        "data_invalid_reason": result.data_invalid_reason,
        "diagnostics": result.diagnostics,
    }


def reason_for(result: dict) -> tuple[str, bool]:
    """(display text, is_invalid_badge) for one serialized row - moved verbatim from
    app.py's render_row(), which used to dispatch on result["ui_group"] directly against
    this setup's own reason builders. A future setup's statuses/groups never flow through
    here - each setup owns its own reason_for."""
    group = result["ui_group"]
    recovery_suffix = f" | {RECOVERY_TAG}" if has_recovery_tag(result) else ""

    if group == GROUP_QUALIFIED:
        return f"{qualified_reason(result)}{recovery_suffix}", False
    if group == GROUP_DEVELOPING:
        return f"{developing_reason(result)}{recovery_suffix}", False
    if group == GROUP_OTHER:
        return other_monitored_reason(result)
    return f"Unrecognized status: {result.get('status')}", False


SETUP = Setup(
    id=ID,
    label=LABEL,
    add_indicators=add_indicators,
    evaluate=evaluate,
    serialize=serialize,
    reason_for=reason_for,
)
