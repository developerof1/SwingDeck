"""Entrypoint for the hourly GitHub Actions refresh (see .github/workflows/refresh_data.yml).

Tiingo's free tier caps requests at ~50/hour, and the unique-ticker count across all
watchlists now exceeds that, so this no longer fetches everyone in one pass. Instead it
drains a persisted queue (state/refresh_queue.json) MAX_TICKERS_PER_RUN tickers at a time,
once per invocation - the workflow fires this hourly, and however many firings it takes to
drain tonight's queue falls out of the actual ticker count at runtime, not a hardcoded
schedule. Once the queue is empty, further firings that night are fast no-ops.

Each batch fetches+evaluates just its own tickers (see evaluate_unique_tickers()) and merges
the results into every affected watchlist's results/<id>.json - preserving rows for tickers
not in this batch rather than overwriting the whole file (see merge_watchlist_results()). A
ticker listed in multiple watchlists is still fetched and evaluated exactly once per batch,
fanned out into every watchlist's own sector/thesis (see build_row()), same as before.

Night boundary: if today's queue is exhausted or doesn't exist yet when a run starts, this
is a new night - any unfinished stragglers from the last queue are abandoned (not carried
forward) and their outcome is snapshotted into state/refresh_status.json (the dashboard's
job-health indicator) before a fresh queue is built for tonight. The fresh queue's starting
position rotates each night (see build_fresh_queue()) so a chronically-incomplete drain
doesn't always strand the same tail-end tickers.

Per-ticker fetch failures (bad symbol, insufficient history, Tiingo outage) are caught and
recorded in every affected watchlist's "errors" list rather than aborting the batch; if
*every* ticker in a batch fails (likely systemic - bad API key, Tiingo outage - not bad
symbols), the queue is left untouched so the same batch retries next run instead of silently
"completing" by recording everything as an error. Insufficient *completed-session* history is
a different thing - that's a DATA_INVALID status from the engine itself (a real Other
Monitored row with a reason), not a fetch error.

The debug override path (TICKERS_OVERRIDE) is unaffected by any of this - it still runs an
ad hoc one-shot evaluation of just the named tickers, prints a summary, and never touches the
queue, state, or results files.
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
STATE_DIR = os.path.join(REPO_ROOT, "state")
HISTORY_BUFFER_DAYS = 750  # calendar days requested, comfortably covers the 420+ trading
# sessions rules_engine.DATA_VALID_MIN_SESSIONS now requires for Monthly Stochastic warm-up
TICKERS_OVERRIDE_ENV = "TICKERS_OVERRIDE"  # comma-separated subset for cheap manual debugging runs

# Tiingo's free tier allows ~50 requests/hour, shared across price fetches AND metadata
# fetches on the same API key - so a single batch's total real Tiingo calls must stay
# comfortably under 50, not 50 price fetches plus a separate metadata allowance on top.
MAX_TICKERS_PER_RUN = 45
MAX_META_FETCHES_PER_RUN = 5  # company names barely ever change, so metadata is only
# fetched for tickers missing a cached name (new additions to any watchlist); capped per
# batch so a large backfill can't blow the shared hourly budget - it just completes
# gradually over a few runs.


def results_path(watchlist_id: str) -> str:
    return os.path.join(RESULTS_DIR, f"{watchlist_id}.json")


def queue_path() -> str:
    return os.path.join(STATE_DIR, "refresh_queue.json")


def status_path() -> str:
    return os.path.join(STATE_DIR, "refresh_status.json")


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


def load_queue() -> dict | None:
    path = queue_path()
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def save_queue(queue: dict) -> None:
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(queue_path(), "w") as f:
        json.dump(queue, f, indent=2)


def save_status(status: dict) -> None:
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(status_path(), "w") as f:
        json.dump(status, f, indent=2)


def build_fresh_queue(all_tickers: set[str], rotation_cursor: int) -> dict:
    """Starts tonight's queue at a rotated offset into the stable sorted ticker list, so a
    night that doesn't fully drain strands a different tail of tickers each time rather than
    always the same alphabetically-last ones. rotation_cursor is advanced by one batch width
    and carried in the returned queue for next time a fresh queue is built."""
    ordered = sorted(all_tickers)
    n = len(ordered)
    if n == 0:
        return {"night_key": date.today().isoformat(), "remaining": [], "total": 0, "rotation_cursor": 0}

    cursor = rotation_cursor % n
    rotated = ordered[cursor:] + ordered[:cursor]
    next_cursor = (cursor + MAX_TICKERS_PER_RUN) % n
    return {
        "night_key": date.today().isoformat(),
        "remaining": rotated,
        "total": n,
        "rotation_cursor": next_cursor,
    }


def finalize_night(queue: dict) -> None:
    """Snapshots how far last night's queue got into state/refresh_status.json - the
    dashboard's job-health indicator - before it's replaced by a fresh queue. This is a
    verdict on a *finished* night (abandon-stragglers-and-restart), not a live progress
    counter, so the indicator doesn't read as alarming while a queue is still legitimately
    mid-drain."""
    total = queue.get("total", 0)
    remaining = queue.get("remaining", [])
    completed = total - len(remaining)
    status = {
        "night_key": queue.get("night_key"),
        "total": total,
        "completed": completed,
        "status": "complete" if not remaining else "incomplete",
        "finished_at": datetime.now(timezone.utc).isoformat(timespec="minutes"),
    }
    save_status(status)
    print(f"Night {status['night_key']} finished: {status['status']} ({completed}/{total} tickers).")


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


def merge_watchlist_results(watchlist_id: str, new_results: list[dict], new_errors: list[dict]) -> None:
    """Merges this batch's rows into the watchlist's existing results/<id>.json instead of
    overwriting it - rows for tickers not in this batch are left exactly as they were. A
    ticker that succeeds this batch clears any stale error recorded for it previously."""
    path = results_path(watchlist_id)
    prior: dict = {}
    if os.path.exists(path):
        try:
            with open(path) as f:
                prior = json.load(f)
        except (json.JSONDecodeError, OSError):
            prior = {}

    results_by_ticker = {r["ticker"]: r for r in prior.get("results", [])}
    for r in new_results:
        results_by_ticker[r["ticker"]] = r

    errors_by_ticker = {e["ticker"]: e for e in prior.get("errors", [])}
    for e in new_errors:
        errors_by_ticker[e["ticker"]] = e
    for r in new_results:
        errors_by_ticker.pop(r["ticker"], None)

    merged_results = list(results_by_ticker.values())
    merged_errors = list(errors_by_ticker.values())

    session_counts = Counter(r["evaluated_session"] for r in merged_results if r.get("evaluated_session"))
    evaluated_session = session_counts.most_common(1)[0][0] if session_counts else None

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="minutes"),
        "evaluated_session": evaluated_session,
        "results": merged_results,
        "errors": merged_errors,
    }

    os.makedirs(RESULTS_DIR, exist_ok=True)
    with open(path, "w") as f:
        json.dump(output, f, indent=2)

    print(f"Merged {len(new_results)} result(s) and {len(new_errors)} error(s) into {path}")
    for err in new_errors:
        print(f"  ERROR {err['ticker']}: {err['error']}")


def run_debug_override(
    watchlists: list[dict], all_tickers: set[str], override: str, start_date: str, prior_names: dict[str, str]
) -> None:
    wanted = {t.strip().upper() for t in override.split(",") if t.strip()}
    tickers_to_evaluate = {t for t in all_tickers if t.upper() in wanted}
    if not tickers_to_evaluate:
        print(f"No tickers in any watchlist matched {TICKERS_OVERRIDE_ENV}={override!r}")
        return

    evaluations, fetch_errors = evaluate_unique_tickers(tickers_to_evaluate, start_date, prior_names)

    for watchlist in watchlists:
        entries = [e for e in watchlist["tickers"] if e["ticker"].upper() in wanted]
        if not entries:
            continue  # this watchlist has none of the debug-overridden tickers
        results = [build_row(e, evaluations[e["ticker"]]) for e in entries if e["ticker"] in evaluations]
        errors = [
            {"ticker": e["ticker"], "error": fetch_errors[e["ticker"]]} for e in entries if e["ticker"] in fetch_errors
        ]
        print_debug_summary(watchlist["id"], results, errors)


def run_scheduled_batch(watchlists: list[dict], all_tickers: set[str], start_date: str, prior_names: dict[str, str]) -> None:
    today_key = date.today().isoformat()
    queue = load_queue()

    if queue is None or queue.get("night_key") != today_key:
        if queue is not None:
            finalize_night(queue)
        queue = build_fresh_queue(all_tickers, (queue or {}).get("rotation_cursor", 0))
        print(f"Starting fresh queue for {queue['night_key']}: {queue['total']} unique ticker(s).")

    batch = queue["remaining"][:MAX_TICKERS_PER_RUN]
    if not batch:
        print(f"Tonight's queue ({queue['night_key']}) is already drained - nothing to do.")
        save_queue(queue)
        return

    batch_set = set(batch)
    evaluations, fetch_errors = evaluate_unique_tickers(batch_set, start_date, prior_names)

    if not evaluations:
        # Every ticker in this batch failed - almost certainly systemic (bad API key,
        # Tiingo outage) rather than N coincidentally-bad symbols at once. Leave the queue
        # untouched so the exact same batch retries next run instead of "completing" by
        # recording a wall of errors.
        print(f"All {len(batch)} ticker(s) in this batch failed - leaving queue untouched for retry. Errors:")
        for ticker, err in fetch_errors.items():
            print(f"  ERROR {ticker}: {err}")
        return

    for watchlist in watchlists:
        entries = [e for e in watchlist["tickers"] if e["ticker"] in batch_set]
        if not entries:
            continue  # this watchlist has none of tonight's current batch
        results = [build_row(e, evaluations[e["ticker"]]) for e in entries if e["ticker"] in evaluations]
        errors = [
            {"ticker": e["ticker"], "error": fetch_errors[e["ticker"]]} for e in entries if e["ticker"] in fetch_errors
        ]
        merge_watchlist_results(watchlist["id"], results, errors)

    queue["remaining"] = [t for t in queue["remaining"] if t not in batch_set]
    save_queue(queue)
    print(f"Batch done: {len(batch)} ticker(s) processed, {len(queue['remaining'])} remaining tonight ({queue['night_key']}).")


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
    all_tickers = {entry["ticker"] for w in watchlists for entry in w["tickers"]}

    override = os.environ.get(TICKERS_OVERRIDE_ENV, "").strip()
    if override:
        run_debug_override(watchlists, all_tickers, override, start_date, prior_names)
        return

    run_scheduled_batch(watchlists, all_tickers, start_date, prior_names)


if __name__ == "__main__":
    main()
