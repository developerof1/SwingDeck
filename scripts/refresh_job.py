"""Entrypoint for the nightly GitHub Actions refresh (see .github/workflows/refresh_data.yml).

Fetches fresh data for every ticker in watchlist.json, runs the rules engine with
default thresholds, and writes results.json - the only thing app.py reads at runtime.
Per-ticker failures (bad symbol, insufficient history, Tiingo outage) are caught and
recorded in the "errors" list rather than aborting the whole run.
"""

import json
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.data_engine import MIN_HISTORY_DAYS, add_indicators, snapshot_tail, to_dataframe
from src.providers.tiingo_provider import TiingoError, fetch_daily_prices
from src.rules_engine import evaluate_stock

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..")
WATCHLIST_PATH = os.path.join(REPO_ROOT, "watchlist.json")
RESULTS_PATH = os.path.join(REPO_ROOT, "results.json")
HISTORY_BUFFER_DAYS = 500  # calendar days requested, comfortably covers 300 trading days
TICKERS_OVERRIDE_ENV = "TICKERS_OVERRIDE"  # comma-separated subset for cheap manual debugging runs


def load_watchlist() -> list[dict]:
    with open(WATCHLIST_PATH) as f:
        return json.load(f)


def evaluate_ticker(entry: dict, start_date: str) -> dict:
    ticker = entry["ticker"]
    raw = fetch_daily_prices(ticker, start_date)
    df = to_dataframe(raw)
    if len(df) < MIN_HISTORY_DAYS:
        raise ValueError(f"only {len(df)} trading days returned, need at least {MIN_HISTORY_DAYS}")

    df = add_indicators(df)
    result = evaluate_stock(ticker, df)

    return {
        "ticker": ticker,
        "sector": entry.get("sector"),
        "thesis": entry.get("thesis"),
        "default_eval": {
            "status": result.status,
            "regime": result.regime,
            "current_price": result.current_price,
            "dist_to_ema21_pct": result.dist_to_ema21_pct,
            "stoch_k": result.stoch_k,
            "stoch_d": result.stoch_d,
            "rvol": result.rvol,
            "entry": result.entry,
            "stop": result.stop,
            "target": result.target,
            "rr": result.rr,
            "reason": result.reason,
        },
        "snapshot": snapshot_tail(df, rows=5),
    }


def main() -> None:
    watchlist = load_watchlist()

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
            results.append(evaluate_ticker(entry, start_date))
        except (TiingoError, ValueError) as exc:
            errors.append({"ticker": ticker, "error": str(exc)})

    if debug_mode:
        # Debug/override runs never touch results.json - a partial result set would
        # otherwise wipe out the other tickers' real data for everyone reading the app.
        print(f"[debug run, not written to {RESULTS_PATH}] {len(results)} results, {len(errors)} errors")
        for r in results:
            print(f"  {r['ticker']}: {r['default_eval']['status']} | {r['default_eval']['reason']}")
        for err in errors:
            print(f"  ERROR {err['ticker']}: {err['error']}")
        return

    output = {
        "generated_at": date.today().isoformat(),
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
