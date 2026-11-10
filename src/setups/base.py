"""The Setup interface every trading-setup module implements (see src/setups/__init__.py
for the registry that collects them).

Split into its own module, separate from __init__.py, purely to avoid a circular import:
each setup module (e.g. momentum_continuation.py) builds its own `Setup` instance at import
time, and __init__.py imports both `Setup` and every setup module - if `Setup` lived in
__init__.py itself, a setup module importing it back would run into the registry package
still being mid-import.
"""

from dataclasses import dataclass
from typing import Any, Callable

import pandas as pd


@dataclass(frozen=True)
class Setup:
    """One trading setup, wrapped so refresh_job.py and app.py can loop over every
    registered setup without knowing which ones exist.

    add_indicators must always be called against a pristine per-ticker base DataFrame,
    never chained setup-to-setup - add_continuation_indicators (and the archived V1
    add_indicators, both in src/data_engine.py) write overlapping column names (sma_50,
    ema_8, stoch_k, ...) with different semantics, so threading an already-enriched df
    from one setup into another's add_indicators would silently produce wrong values,
    not a crash.
    """

    id: str
    label: str
    add_indicators: Callable[[pd.DataFrame], pd.DataFrame]
    evaluate: Callable[[str, pd.DataFrame], Any]
    # result object (whatever `evaluate` returned) -> the row fields this setup owns
    # (status, ui_group, and its own detail/diagnostic fields) - ticker/name are NOT
    # included here, they're generic and attached by refresh_job.py itself.
    serialize: Callable[[Any], dict]
    # serialized row -> (display text, is_invalid_badge) - replaces a hardcoded
    # qualified/developing/other dispatch in app.py with a setup-owned one.
    reason_for: Callable[[dict], tuple[str, bool]]
