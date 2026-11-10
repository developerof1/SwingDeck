"""Setup registry: lets scripts/refresh_job.py and app.py loop over every registered
trading setup without knowing which setups exist. Adding a new setup is: write a module
exposing the same shape as momentum_continuation.py (ID, LABEL, add_indicators, evaluate,
serialize, reason_for, and a module-level SETUP built from them), then append that SETUP
to REGISTRY below - no change needed anywhere else.
"""

from src.setups import momentum_continuation
from src.setups.base import Setup

REGISTRY: list[Setup] = [
    momentum_continuation.SETUP,
]

_ids = [setup.id for setup in REGISTRY]
if len(_ids) != len(set(_ids)):
    raise ValueError(f"Duplicate setup id(s) in REGISTRY: {_ids}")


def list_setups() -> list[Setup]:
    return list(REGISTRY)


def get_setup(setup_id: str) -> Setup:
    for setup in REGISTRY:
        if setup.id == setup_id:
            return setup
    raise KeyError(f"No registered setup with id {setup_id!r}")
