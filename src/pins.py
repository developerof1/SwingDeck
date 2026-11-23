"""Pinned wall logic - pure doc -> doc functions over the `pins.json` document, so they can be
handed straight to UserStore.update() (which may re-run them on a fresh document after a
conflict) and unit-tested without any storage.

pins.json (global, not per watchlist - the wall is a view-filter over it):
    {"schema": 1,
     "pins": [{"setup": "momentum_continuation", "ticker": "CPB",
               "pinned_at": "2026-11-23T14:05Z", "pinned_group": "developing"}]}

A pin is identified by (setup, ticker). `pinned_group` is the shared UI group at pin time
(src/status_groups.py), used only to show "Pinned as ..." when the current group differs.
`pinned_at` is stored for later use but deliberately not displayed. List order = pin order.
"""

PINS_PATH = "pins.json"
SCHEMA_VERSION = 1


def empty_doc() -> dict:
    return {"schema": SCHEMA_VERSION, "pins": []}


def is_pinned(doc: dict, setup_id: str, ticker: str) -> bool:
    return any(p["setup"] == setup_id and p["ticker"] == ticker for p in doc.get("pins", []))


def add_pin(doc: dict, setup_id: str, ticker: str, group: str, pinned_at: str) -> dict:
    """Idempotent: pinning an already-pinned (setup, ticker) changes nothing."""
    if is_pinned(doc, setup_id, ticker):
        return doc
    pins = list(doc.get("pins", []))
    pins.append({"setup": setup_id, "ticker": ticker, "pinned_at": pinned_at, "pinned_group": group})
    return {**doc, "pins": pins}


def remove_pin(doc: dict, setup_id: str, ticker: str) -> dict:
    """Idempotent: unpinning something not pinned changes nothing."""
    pins = [p for p in doc.get("pins", []) if not (p["setup"] == setup_id and p["ticker"] == ticker)]
    return {**doc, "pins": pins}


def prune_orphans(doc: dict, known_tickers: set[str]) -> dict:
    """Drops pins whose ticker is in no watchlist at all. An empty `known_tickers` is treated
    as "couldn't determine the universe" and prunes nothing, so a missing/unreadable
    watchlists directory can never wipe the wall."""
    if not known_tickers:
        return doc
    pins = [p for p in doc.get("pins", []) if p["ticker"] in known_tickers]
    if len(pins) == len(doc.get("pins", [])):
        return doc
    return {**doc, "pins": pins}


def pins_in_watchlist(doc: dict, watchlist_tickers: set[str]) -> list[dict]:
    """The wall for one watchlist: pins (in pin order) whose ticker belongs to it."""
    return [p for p in doc.get("pins", []) if p["ticker"] in watchlist_tickers]
