"""Discovers and loads watchlist definitions from watchlists/*.json.

Each file is self-describing ({"name": ..., "tickers": [{"ticker", "sector", "thesis"}, ...]}),
so adding a watchlist is just dropping a new file in the directory - no separate registry
to keep in sync. The filename stem (e.g. "ai_trade_names" for ai_trade_names.json) is the
stable id used everywhere else (results filename, UI selection, session state).
"""

import json
import os

WATCHLISTS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "watchlists")


def _watchlist_id(path: str) -> str:
    return os.path.splitext(os.path.basename(path))[0]


def discover_watchlists(watchlists_dir: str = WATCHLISTS_DIR) -> list[dict]:
    """Returns [{id, name, ticker_count, path}, ...] for every watchlists/*.json file,
    sorted by name. Cheap - these files are small - safe to call on every app rerun."""
    if not os.path.isdir(watchlists_dir):
        return []

    entries = []
    for filename in sorted(os.listdir(watchlists_dir)):
        if not filename.endswith(".json"):
            continue
        path = os.path.join(watchlists_dir, filename)
        with open(path) as f:
            data = json.load(f)
        entries.append(
            {
                "id": _watchlist_id(path),
                "name": data.get("name") or _watchlist_id(path),
                "ticker_count": len(data.get("tickers", [])),
                "path": path,
            }
        )
    return sorted(entries, key=lambda e: e["name"])


def load_watchlist(watchlist_id: str, watchlists_dir: str = WATCHLISTS_DIR) -> dict:
    """Returns the full {"name": ..., "tickers": [...]} for one watchlist id."""
    path = os.path.join(watchlists_dir, f"{watchlist_id}.json")
    with open(path) as f:
        return json.load(f)
