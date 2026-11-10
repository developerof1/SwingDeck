"""Entrypoint for the hourly GitHub Actions refresh (see .github/workflows/refresh_data.yml).

Tiingo's free tier caps requests at ~50/hour, and the unique-ticker count across all
watchlists now exceeds that, so this no longer fetches everyone in one pass. Instead it
drains a persisted queue (state/refresh_queue.json) MAX_TICKERS_PER_RUN tickers at a time,
once per invocation - the workflow fires this hourly, and however many firings it takes to
drain tonight's queue falls out of the actual ticker count at runtime, not a hardcoded
schedule. Once the queue is empty, further firings that night are fast no-ops.

Each batch fetches every registered trading setup's own indicators+evaluation for its tickers
(see evaluate_unique_tickers(), src/setups/) and merges the results into every affected
watchlist's results/<id>.json, nested by setup id - preserving rows for tickers not in this
batch rather than overwriting the whole file (see merge_watchlist_results()). A ticker listed
in multiple watchlists is still fetched and evaluated exactly once per batch (per setup),
fanned out into every watchlist's own sector/thesis (see build_row()), same as before. Adding
a new trading setup to src/setups/__init__.py's REGISTRY is the only change needed here - this
file loops over whatever is registered, it never hardcodes which setups exist.

Night boundary: if today's queue is exhausted or doesn't exist yet when a run starts, this
is a new night - any unfinished stragglers from the last queue are abandoned (not carried
forward) and their outcome is snapshotted into state/refresh_status.json (the dashboard's
job-health indicator) before a fresh queue is built for tonight. The fresh queue's starting
position rotates each night (see build_fresh_queue()) so a chronically-incomplete drain
doesn't always strand the same tail-end tickers.

Per-ticker fetch failures (bad symbol, insufficient history, Tiingo outage) are caught and
recorded under every registered setup's "errors" list for the affected watchlist(s) rather
than aborting the batch; if *every* ticker in a batch fails to even fetch (likely systemic -
bad API key, Tiingo outage - not bad symbols), the queue is left untouched so the same batch
retries next run instead of silently "completing" by recording everything as an error.
Insufficient *completed-session* history is a different thing - that's a DATA_INVALID status
from the engine itself (a real Other Monitored row with a reason), not a fetch error.

A second kind of failure exists once there's more than one setup: the fetch succeeds, but one
particular setup's own add_indicators/evaluate/serialize raises. That's caught separately (see
evaluate_ticker()) so it only blanks *that* setup's row for *that* ticker - it must not look
like a fetch failure (which would wrongly blank every other setup too) and must not feed the
all-tickers-failed retry check above, which stays scoped to fetch success only.

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

from src.data_engine import to_dataframe
from src.providers.tiingo_provider import TiingoError, fetch_daily_prices, fetch_ticker_meta
from src.setups import list_setups
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
        for setup_doc in prior.get("setups", {}).values():
            for r in setup_doc.get("results", []):
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
    """Fetch once, then run every registered setup's own add_indicators/evaluate/serialize
    against that same raw data - independent of which watchlist(s) this ticker belongs to
    (sector/thesis, which vary per watchlist, are attached later by build_row()).

    Returns {"ticker", "name", "by_setup": {setup_id: row_dict}, "setup_errors": {setup_id: msg}} -
    by_setup holds only the setups that succeeded for this ticker; a setup whose own
    add_indicators/evaluate/serialize raised lands in setup_errors instead, without blocking
    any other setup's result for the same ticker. setup.add_indicators is always called
    against the same pristine base_df, never a previous setup's enriched df - see the
    Setup.add_indicators docstring in src/setups/base.py for why that matters.
    """
    raw = fetch_daily_prices(ticker, start_date)  # TiingoError propagates to caller
    base_df = to_dataframe(raw)

    name = prior_names.get(ticker)
    if not name and meta_budget[0] > 0:
        meta_budget[0] -= 1
        meta = fetch_ticker_meta(ticker)
        name = (meta or {}).get("name")

    by_setup: dict[str, dict] = {}
    setup_errors: dict[str, str] = {}
    for setup in list_setups():
        try:
            enriched = setup.add_indicators(base_df)
            result = setup.evaluate(ticker, enriched)
            by_setup[setup.id] = setup.serialize(result)
        except Exception as exc:  # noqa: BLE001 - one setup's bug must not block the others
            setup_errors[setup.id] = str(exc)

    return {"ticker": ticker, "name": name, "by_setup": by_setup, "setup_errors": setup_errors}


def evaluate_unique_tickers(
    tickers: set[str], start_date: str, prior_names: dict[str, str]
) -> tuple[dict[str, dict], dict[str, dict]]:
    """Fetches and evaluates every ticker in `tickers` exactly once (across all registered
    setups), regardless of how many watchlists reference it - this is the Tiingo-call dedup.

    Returns (ticker -> evaluation dict from evaluate_ticker(), ticker -> {"fetch_error",
    "setup_errors"}) - a ticker only appears in `errors` if its fetch failed (fetch_error set,
    evaluations has no entry for it) or at least one setup failed after a successful fetch
    (fetch_error is None, evaluations[ticker] still holds whatever setups DID succeed)."""
    meta_budget = [MAX_META_FETCHES_PER_RUN]
    evaluations: dict[str, dict] = {}
    errors: dict[str, dict] = {}
    for ticker in sorted(tickers):
        try:
            evaluation = evaluate_ticker(ticker, start_date, prior_names, meta_budget)
        except TiingoError as exc:
            errors[ticker] = {"fetch_error": str(exc), "setup_errors": {}}
            continue
        evaluations[ticker] = evaluation
        if evaluation["setup_errors"]:
            errors[ticker] = {"fetch_error": None, "setup_errors": evaluation["setup_errors"]}
    return evaluations, errors


def build_row(entry: dict, evaluation: dict, setup_id: str) -> dict:
    """Merges one watchlist entry's own sector/thesis with this ticker's cached result for
    one setup - the same ticker can carry different sector/thesis text in different
    watchlists, and a different result per setup."""
    return {
        "ticker": evaluation["ticker"],
        "name": evaluation["name"],
        "sector": entry.get("sector"),
        "thesis": entry.get("thesis"),
        **evaluation["by_setup"][setup_id],
    }


def error_message_for(ticker: str, setup_id: str, errors: dict[str, dict]) -> str | None:
    """A fetch failure applies to every setup equally; a setup-specific failure applies only
    to that setup. None means this ticker had no problem under this setup."""
    err = errors.get(ticker)
    if not err:
        return None
    return err.get("fetch_error") or (err.get("setup_errors") or {}).get(setup_id)


def print_debug_summary(
    watchlist_id: str, entries: list[dict], evaluations: dict[str, dict], errors: dict[str, dict]
) -> None:
    print(f"[debug run, not written] {watchlist_id}:")
    for setup in list_setups():
        print(f"  Setup: {setup.label}")
        for entry in entries:
            ticker = entry["ticker"]
            evaluation = evaluations.get(ticker)
            if evaluation and setup.id in evaluation["by_setup"]:
                row = evaluation["by_setup"][setup.id]
                extra = f" ({row['data_invalid_reason']})" if row["status"] == "DATA_INVALID" else ""
                print(f"    {ticker} ({evaluation['name'] or 'no name'}): {row['status']}{extra}")
            else:
                message = error_message_for(ticker, setup.id, errors) or "unknown error"
                print(f"    ERROR {ticker}: {message}")


def merge_watchlist_results(
    watchlist_id: str, by_setup_results: dict[str, list[dict]], by_setup_errors: dict[str, list[dict]]
) -> None:
    """Merges this batch's rows into the watchlist's existing results/<id>.json, nested by
    setup_id - one read + one write per watchlist per run, not one round-trip per setup. Rows
    for a (setup, ticker) pair not touched this batch are left exactly as they were. A ticker
    that succeeds a setup this batch clears any stale error recorded for it under that same
    setup previously."""
    path = results_path(watchlist_id)
    prior: dict = {}
    if os.path.exists(path):
        try:
            with open(path) as f:
                prior = json.load(f)
        except (json.JSONDecodeError, OSError):
            prior = {}

    setups_doc: dict = prior.get("setups", {})
    touched_setup_ids = set(by_setup_results) | set(by_setup_errors)

    for setup_id in touched_setup_ids:
        setup_doc = setups_doc.get(setup_id, {})
        new_results = by_setup_results.get(setup_id, [])
        new_errors = by_setup_errors.get(setup_id, [])

        results_by_ticker = {r["ticker"]: r for r in setup_doc.get("results", [])}
        for r in new_results:
            results_by_ticker[r["ticker"]] = r

        errors_by_ticker = {e["ticker"]: e for e in setup_doc.get("errors", [])}
        for e in new_errors:
            errors_by_ticker[e["ticker"]] = e
        for r in new_results:
            errors_by_ticker.pop(r["ticker"], None)

        merged_results = list(results_by_ticker.values())
        merged_errors = list(errors_by_ticker.values())

        session_counts = Counter(r["evaluated_session"] for r in merged_results if r.get("evaluated_session"))
        evaluated_session = session_counts.most_common(1)[0][0] if session_counts else None

        setups_doc[setup_id] = {
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="minutes"),
            "evaluated_session": evaluated_session,
            "results": merged_results,
            "errors": merged_errors,
        }

    output = {"setups": setups_doc}

    os.makedirs(RESULTS_DIR, exist_ok=True)
    with open(path, "w") as f:
        json.dump(output, f, indent=2)

    total_results = sum(len(v) for v in by_setup_results.values())
    total_errors = sum(len(v) for v in by_setup_errors.values())
    print(f"Merged {total_results} result(s) and {total_errors} error(s) into {path}")
    for setup_id, errs in by_setup_errors.items():
        for err in errs:
            print(f"  ERROR [{setup_id}] {err['ticker']}: {err['error']}")


def _by_setup_rows_and_errors(
    entries: list[dict], evaluations: dict[str, dict], errors: dict[str, dict]
) -> tuple[dict[str, list[dict]], dict[str, list[dict]]]:
    """Builds this watchlist's results/errors lists for every registered setup, for the given
    entries (already filtered to tickers in the current batch/override)."""
    by_setup_results: dict[str, list[dict]] = {}
    by_setup_errors: dict[str, list[dict]] = {}
    for setup in list_setups():
        results = []
        error_rows = []
        for entry in entries:
            ticker = entry["ticker"]
            evaluation = evaluations.get(ticker)
            if evaluation and setup.id in evaluation["by_setup"]:
                results.append(build_row(entry, evaluation, setup.id))
                continue
            message = error_message_for(ticker, setup.id, errors)
            if message:
                error_rows.append({"ticker": ticker, "error": message})
        if results:
            by_setup_results[setup.id] = results
        if error_rows:
            by_setup_errors[setup.id] = error_rows
    return by_setup_results, by_setup_errors


def run_debug_override(
    watchlists: list[dict], all_tickers: set[str], override: str, start_date: str, prior_names: dict[str, str]
) -> None:
    wanted = {t.strip().upper() for t in override.split(",") if t.strip()}
    tickers_to_evaluate = {t for t in all_tickers if t.upper() in wanted}
    if not tickers_to_evaluate:
        print(f"No tickers in any watchlist matched {TICKERS_OVERRIDE_ENV}={override!r}")
        return

    evaluations, errors = evaluate_unique_tickers(tickers_to_evaluate, start_date, prior_names)

    for watchlist in watchlists:
        entries = [e for e in watchlist["tickers"] if e["ticker"].upper() in wanted]
        if not entries:
            continue  # this watchlist has none of the debug-overridden tickers
        print_debug_summary(watchlist["id"], entries, evaluations, errors)


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
    evaluations, errors = evaluate_unique_tickers(batch_set, start_date, prior_names)

    if not evaluations:
        # Every ticker in this batch failed to even fetch - almost certainly systemic (bad
        # API key, Tiingo outage) rather than N coincidentally-bad symbols at once. Leave the
        # queue untouched so the exact same batch retries next run instead of "completing" by
        # recording a wall of errors. (A ticker that fetched fine but had every setup's own
        # evaluate/serialize fail still has an entry in `evaluations` - just an empty
        # by_setup - so this check only fires on total fetch failure.)
        print(f"All {len(batch)} ticker(s) in this batch failed to fetch - leaving queue untouched for retry. Errors:")
        for ticker, err in errors.items():
            if err.get("fetch_error"):
                print(f"  ERROR {ticker}: {err['fetch_error']}")
        return

    for watchlist in watchlists:
        entries = [e for e in watchlist["tickers"] if e["ticker"] in batch_set]
        if not entries:
            continue  # this watchlist has none of tonight's current batch
        by_setup_results, by_setup_errors = _by_setup_rows_and_errors(entries, evaluations, errors)
        merge_watchlist_results(watchlist["id"], by_setup_results, by_setup_errors)

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
