"""Entrypoint for the nightly GitHub Actions refresh (see .github/workflows/refresh_data.yml).

Fetches fresh data for every ticker in watchlist.json, runs the Momentum
Continuation engine, and writes results.json - the only thing app.py reads at
runtime. Per-ticker fetch failures (bad symbol, insufficient history read from
Tiingo, Tiingo outage) are caught and recorded in the "errors" list rather than
aborting the whole run; insufficient *completed-session* history is a different
thing - that's a DATA_INVALID status from the engine itself (a real Other
Monitored row with a reason), not a fetch error - see evaluate_ticker() below.
"""

import json
import os
import sys
from collections import Counter
from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.data_engine import add_continuation_indicators, to_dataframe
from src.providers.tiingo_provider import TiingoError, fetch_daily_prices, fetch_ticker_meta
from src.rules_engine import evaluate_stock
from src.status_mapping import ui_group_for_status

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..")
WATCHLIST_PATH = os.path.join(REPO_ROOT, "watchlist.json")
RESULTS_PATH = os.path.join(REPO_ROOT, "results.json")
HISTORY_BUFFER_DAYS = 500  # calendar days requested, comfortably covers 252+ trading days
TICKERS_OVERRIDE_ENV = "TICKERS_OVERRIDE"  # comma-separated subset for cheap manual debugging runs


def load_watchlist() -> list[dict]:
    with open(WATCHLIST_PATH) as f:
        return json.load(f)


def load_prior_names() -> dict[str, str]:
    """Best-effort read of yesterday's results.json purely as a resilience
    fallback - if tonight's Tiingo metadata call fails, fall back to the last
    name we successfully fetched rather than showing a blank. Not state in the
    V1 sense (no grace windows/regimes survive here); a missing or corrupt prior
    file just means every ticker falls back to no cached name, never fatal.
    """
    if not os.path.exists(RESULTS_PATH):
        return {}
    try:
        with open(RESULTS_PATH) as f:
            prior = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}
    return {r["ticker"]: r["name"] for r in prior.get("results", []) if r.get("name")}


def evaluate_ticker(entry: dict, start_date: str, prior_names: dict[str, str]) -> dict:
    ticker = entry["ticker"]
    raw = fetch_daily_prices(ticker, start_date)  # TiingoError propagates to main()'s except
    df = add_continuation_indicators(to_dataframe(raw))
    result = evaluate_stock(ticker, df)

    meta = fetch_ticker_meta(ticker)
    name = (meta or {}).get("name") or prior_names.get(ticker)

    return {
        "ticker": ticker,
        "name": name,
        "sector": entry.get("sector"),
        "thesis": entry.get("thesis"),
        "status": result.status,
        "ui_group": ui_group_for_status(result.status),
        "evaluated_session": result.evaluated_session,
        "data_valid": result.data_valid,
        "recovery_observed": result.recovery_observed,
        "developing_badges": [
            {"code": b.code, "measured": b.measured, "threshold": b.threshold} for b in result.developing_badges
        ],
        "trend_detail": result.trend_detail,
        "pullback_detail": result.pullback_detail,
        "data_invalid_reason": result.data_invalid_reason,
        "diagnostics": result.diagnostics,
    }


def main() -> None:
    watchlist = load_watchlist()
    prior_names = load_prior_names()
    today = date.today().isoformat()

    override = os.environ.get(TICKERS_OVERRIDE_ENV, "").strip()
    debug_mode = bool(override)
    if debug_mode:
        wanted = {t.strip().upper() for t in override.split(",") if t.strip()}
        watchlist = [e for e in watchlist if e["ticker"].upper() in wanted]
        if not watchlist:
            print(f"No tickers in watchlist.json matched {TICKERS_OVERRIDE_ENV}={override!r}")
            return

    start_date = (date.today() - timedelta(days=HISTORY_BUFFER_DAYS)).isoformat()

    results = []
    errors = []

    for entry in watchlist:
        ticker = entry["ticker"]
        try:
            results.append(evaluate_ticker(entry, start_date, prior_names))
        except TiingoError as exc:
            errors.append({"ticker": ticker, "error": str(exc)})

    if debug_mode:
        # Debug/override runs never touch results.json - a partial result set would
        # otherwise wipe out the other tickers' real data for everyone reading the app.
        print(f"[debug run, not written to {RESULTS_PATH}] {len(results)} results, {len(errors)} errors")
        for r in results:
            extra = f" ({r['data_invalid_reason']})" if r["status"] == "DATA_INVALID" else ""
            print(f"  {r['ticker']} ({r['name'] or 'no name'}): {r['status']}{extra}")
        for err in errors:
            print(f"  ERROR {err['ticker']}: {err['error']}")
        return

    session_counts = Counter(r["evaluated_session"] for r in results if r["evaluated_session"])
    evaluated_session = session_counts.most_common(1)[0][0] if session_counts else None

    output = {
        "generated_at": today,
        "evaluated_session": evaluated_session,
        "results": results,
        "errors": errors,
    }

    with open(RESULTS_PATH, "w") as f:
        json.dump(output, f, indent=2)

    print(f"Wrote {len(results)} results and {len(errors)} errors to {RESULTS_PATH}")
    for err in errors:
        print(f"  ERROR {err['ticker']}: {err['error']}")


if __name__ == "__main__":
    main()
