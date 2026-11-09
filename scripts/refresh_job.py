"""Entrypoint for the nightly GitHub Actions refresh (see .github/workflows/refresh_data.yml).

Fetches fresh data once per unique ticker across every watchlist in watchlists/, runs the
Momentum Continuation engine, and writes one results/<watchlist_id>.json per watchlist - the
only thing app.py reads at runtime. A ticker listed in multiple watchlists is fetched and
evaluated exactly once per run (see evaluate_unique_tickers()) and the same evaluation is
fanned out into every watchlist's output row, each carrying that watchlist's own sector/thesis
for the ticker (see build_row()). Per-ticker fetch failures (bad symbol, insufficient history,
Tiingo outage) are caught and recorded in every affected watchlist's "errors" list rather than
aborting the whole run; insufficient *completed-session* history is a different thing - that's
a DATA_INVALID status from the engine itself (a real Other Monitored row with a reason), not a
fetch error.
"""

import json
import os
import sys
from collections import Counter
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.data_engine import add_continuation_indicators, to_dataframe
from src.providers.tiingo_provider import TiingoError, fetch_daily_prices, fetch_ticker_meta
from src.rules_engine import evaluate_stock
from src.status_mapping import ui_group_for_status
from src.watchlist_store import discover_watchlists, load_watchlist

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..")
RESULTS_DIR = os.path.join(REPO_ROOT, "results")
HISTORY_BUFFER_DAYS = 750  # calendar days requested, comfortably covers the 420+ trading
# sessions rules_engine.DATA_VALID_MIN_SESSIONS now requires for Monthly Stochastic warm-up
TICKERS_OVERRIDE_ENV = "TICKERS_OVERRIDE"  # comma-separated subset for cheap manual debugging runs

# Tiingo's free tier allows ~50 requests/hour. A full run already spends one request per
# *unique* ticker on fetch_daily_prices (price) - deduped across every watchlist a ticker
# appears in (see evaluate_unique_tickers) - and a nightly metadata call for every ticker too
# would roughly double that and blow the budget on its own - confirmed by hitting HTTP 429
# partway through a real run. Company names barely ever change, so metadata is only fetched
# for tickers missing a cached name (new additions to any watchlist), capped per run so a
# large backfill can't exceed the hourly quota either - it just completes gradually over a
# few nights.
MAX_META_FETCHES_PER_RUN = 10


def results_path(watchlist_id: str) -> str:
    return os.path.join(RESULTS_DIR, f"{watchlist_id}.json")


def load_prior_names() -> dict[str, str]:
    """Best-effort read of every watchlist's last results/<id>.json purely as a resilience
    fallback - if tonight's Tiingo metadata call fails, fall back to the last name we
    successfully fetched rather than showing a blank. Not state in the V1 sense (no grace
    windows/regimes survive here); a missing, corrupt, or absent prior file just means those
    tickers fall back to no cached name, never fatal."""
    names: dict[str, str] = {}
    if not os.path.isdir(RESULTS_DIR):
        return names
    for filename in os.listdir(RESULTS_DIR):
        if not filename.endswith(".json"):
            continue
        try:
            with open(os.path.join(RESULTS_DIR, filename)) as f:
                prior = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue
        for r in prior.get("results", []):
            if r.get("name"):
                names.setdefault(r["ticker"], r["name"])
    return names


def evaluate_ticker(ticker: str, start_date: str, prior_names: dict[str, str], meta_budget: list[int]) -> dict:
    """Fetch + evaluate one ticker, independent of which watchlist(s) it belongs to -
    sector/thesis (which vary per watchlist) are attached later by build_row()."""
    raw = fetch_daily_prices(ticker, start_date)  # TiingoError propagates to caller
    df = add_continuation_indicators(to_dataframe(raw))
    result = evaluate_stock(ticker, df)

    name = prior_names.get(ticker)
    if not name and meta_budget[0] > 0:
        meta_budget[0] -= 1
        meta = fetch_ticker_meta(ticker)
        name = (meta or {}).get("name")

    return {
        "ticker": ticker,
        "name": name,
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


def evaluate_unique_tickers(
    tickers: set[str], start_date: str, prior_names: dict[str, str]
) -> tuple[dict[str, dict], dict[str, str]]:
    """Fetches and evaluates every ticker in `tickers` exactly once, regardless of how many
    watchlists reference it - this is the Tiingo-call dedup. Returns
    (ticker -> evaluation dict, ticker -> error message); exactly one of the two holds per
    ticker."""
    meta_budget = [MAX_META_FETCHES_PER_RUN]
    evaluations: dict[str, dict] = {}
    errors: dict[str, str] = {}
    for ticker in sorted(tickers):
        try:
            evaluations[ticker] = evaluate_ticker(ticker, start_date, prior_names, meta_budget)
        except TiingoError as exc:
            errors[ticker] = str(exc)
    return evaluations, errors


def build_row(entry: dict, evaluation: dict) -> dict:
    """Merges a cached per-ticker evaluation with one watchlist entry's own sector/thesis -
    the same ticker can carry different sector/thesis text in different watchlists."""
    return {
        "ticker": evaluation["ticker"],
        "name": evaluation["name"],
        "sector": entry.get("sector"),
        "thesis": entry.get("thesis"),
        **{k: v for k, v in evaluation.items() if k not in ("ticker", "name")},
    }


def print_debug_summary(watchlist_id: str, results: list[dict], errors: list[dict]) -> None:
    print(f"[debug run, not written] {watchlist_id}: {len(results)} results, {len(errors)} errors")
    for r in results:
        extra = f" ({r['data_invalid_reason']})" if r["status"] == "DATA_INVALID" else ""
        print(f"  {r['ticker']} ({r['name'] or 'no name'}): {r['status']}{extra}")
    for err in errors:
        print(f"  ERROR {err['ticker']}: {err['error']}")


def write_watchlist_results(watchlist_id: str, results: list[dict], errors: list[dict]) -> None:
    session_counts = Counter(r["evaluated_session"] for r in results if r["evaluated_session"])
    evaluated_session = session_counts.most_common(1)[0][0] if session_counts else None

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="minutes"),
        "evaluated_session": evaluated_session,
        "results": results,
        "errors": errors,
    }

    os.makedirs(RESULTS_DIR, exist_ok=True)
    path = results_path(watchlist_id)
    with open(path, "w") as f:
        json.dump(output, f, indent=2)

    print(f"Wrote {len(results)} results and {len(errors)} errors to {path}")
    for err in errors:
        print(f"  ERROR {err['ticker']}: {err['error']}")


def main() -> None:
    watchlists = []
    for w in discover_watchlists():
        data = load_watchlist(w["id"])
        watchlists.append({"id": w["id"], "name": data.get("name") or w["id"], "tickers": data.get("tickers", [])})

    if not watchlists:
        print("No watchlists found in watchlists/ - nothing to do.")
        return

    prior_names = load_prior_names()
    start_date = (date.today() - timedelta(days=HISTORY_BUFFER_DAYS)).isoformat()

    override = os.environ.get(TICKERS_OVERRIDE_ENV, "").strip()
    debug_mode = bool(override)
    wanted = {t.strip().upper() for t in override.split(",") if t.strip()} if debug_mode else None

    all_tickers = {entry["ticker"] for w in watchlists for entry in w["tickers"]}
    tickers_to_evaluate = {t for t in all_tickers if t.upper() in wanted} if debug_mode else all_tickers
    if debug_mode and not tickers_to_evaluate:
        print(f"No tickers in any watchlist matched {TICKERS_OVERRIDE_ENV}={override!r}")
        return

    evaluations, fetch_errors = evaluate_unique_tickers(tickers_to_evaluate, start_date, prior_names)

    for watchlist in watchlists:
        watchlist_id, watchlist_name = watchlist["id"], watchlist["name"]
        entries = (
            [e for e in watchlist["tickers"] if e["ticker"].upper() in wanted] if debug_mode else watchlist["tickers"]
        )
        if debug_mode and not entries:
            continue  # this watchlist has none of the debug-overridden tickers

        results = [build_row(e, evaluations[e["ticker"]]) for e in entries if e["ticker"] in evaluations]
        errors = [
            {"ticker": e["ticker"], "error": fetch_errors[e["ticker"]]} for e in entries if e["ticker"] in fetch_errors
        ]

        if debug_mode:
            print_debug_summary(watchlist_id, results, errors)
            continue

        if entries and not results:
            # Every ticker in this watchlist failed (e.g. Tiingo outage, rate limit, bad API
            # key) - never overwrite this watchlist's last good results with an empty one.
            # The live app would otherwise show "no qualified/developing stocks" instead of
            # the true, if stale, prior scan. Leave the file untouched; the next scheduled
            # run tries again.
            print(f"All tickers failed for '{watchlist_name}' ({watchlist_id}) - leaving {results_path(watchlist_id)} untouched. Errors:")
            for err in errors:
                print(f"  ERROR {err['ticker']}: {err['error']}")
            continue

        write_watchlist_results(watchlist_id, results, errors)


if __name__ == "__main__":
    main()
