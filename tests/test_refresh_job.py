"""Covers the multi-watchlist dedup-and-fan-out orchestration in scripts/refresh_job.py,
plus the hourly batching queue (build_fresh_queue, finalize_night, merge_watchlist_results)
that lets a run drain only MAX_TICKERS_PER_RUN tickers at a time instead of fetching
everyone in one pass. evaluate_ticker (the actual Tiingo fetch + indicator/rules-engine
call) is mocked out throughout - test_data_engine.py / test_rules_engine.py already cover
that math. These tests exercise: fetching each unique ticker once per batch regardless of
how many watchlists reference it, fanning a ticker's single evaluation out into each
watchlist's own sector/thesis, distributing fetch failures into every watchlist's errors
list that references the failed ticker, merging a batch into existing results instead of
overwriting, leaving the queue untouched (for retry) when an entire batch fails, and the
night-boundary reset that abandons stragglers and snapshots refresh_status.json.
"""

import json
from datetime import date

import pytest

import scripts.refresh_job as refresh_job
import src.watchlist_store as watchlist_store
from src.providers.tiingo_provider import TiingoError, TiingoRateLimitError

# The one setup currently in src.setups.REGISTRY - evaluate_ticker() is mocked throughout
# this file (the real add_indicators/evaluate/serialize math is covered by
# test_data_engine.py/test_rules_engine.py), but refresh_job still calls the real
# list_setups()/_by_setup_rows_and_errors()/merge_watchlist_results(), so fakes need to key
# their results under this same setup id for those to find them.
SETUP_ID = "momentum_continuation"


def _write_watchlist(dir_path, filename, name, tickers):
    (dir_path / filename).write_text(json.dumps({"name": name, "tickers": tickers}))


def _fake_row(ticker: str) -> dict:
    """One setup's serialized result for one ticker - the shape setup.serialize() produces,
    nested under evaluation["by_setup"][SETUP_ID]."""
    return {
        "status": "CANDIDATE",
        "ui_group": "qualified",
        "evaluated_session": "2026-11-06",
        "data_valid": True,
        "recovery_observed": False,
        "developing_badges": [],
        "trend_detail": {},
        "location_detail": {},
        "oscillator_detail": {},
        "data_invalid_reason": None,
        "diagnostics": {},
    }


def _fake_evaluation(ticker: str) -> dict:
    """The shape evaluate_ticker() returns - ticker/name generic, by_setup holding every
    registered setup's own row (just the one setup here), setup_errors empty (no setup
    failed)."""
    return {
        "ticker": ticker,
        "name": f"{ticker} Inc",
        "by_setup": {SETUP_ID: _fake_row(ticker)},
        "setup_errors": {},
    }


def _make_fake_evaluate_ticker(fail_tickers=frozenset(), rate_limited_tickers=frozenset()):
    calls = []

    def fake(ticker, start_date, prior_names, meta_budget):
        calls.append(ticker)
        if ticker in rate_limited_tickers:
            raise TiingoRateLimitError(f"{ticker}: Tiingo returned HTTP 429")
        if ticker in fail_tickers:
            raise TiingoError(f"{ticker}: boom")
        return _fake_evaluation(ticker)

    fake.calls = calls
    return fake


def _run_main(monkeypatch, watchlists_dir, results_dir, fake_evaluate_ticker, override="", state_dir=None):
    monkeypatch.setattr(refresh_job, "RESULTS_DIR", str(results_dir))
    monkeypatch.setattr(refresh_job, "STATE_DIR", str(state_dir) if state_dir else str(results_dir.parent / "state"))
    monkeypatch.setattr(refresh_job, "discover_watchlists", lambda: watchlist_store.discover_watchlists(str(watchlists_dir)))
    monkeypatch.setattr(refresh_job, "load_watchlist", lambda wid: watchlist_store.load_watchlist(wid, str(watchlists_dir)))
    monkeypatch.setattr(refresh_job, "evaluate_ticker", fake_evaluate_ticker)
    if override:
        monkeypatch.setenv(refresh_job.TICKERS_OVERRIDE_ENV, override)
    else:
        monkeypatch.delenv(refresh_job.TICKERS_OVERRIDE_ENV, raising=False)
    refresh_job.main()


def test_build_row_merges_cached_evaluation_with_entry_metadata():
    entry = {"ticker": "NVDA", "sector": "Chips", "thesis": "AI beta leader"}
    evaluation = _fake_evaluation("NVDA")

    row = refresh_job.build_row(entry, evaluation, SETUP_ID)

    assert row["ticker"] == "NVDA"
    assert row["name"] == "NVDA Inc"
    assert row["sector"] == "Chips"
    assert row["thesis"] == "AI beta leader"
    assert row["status"] == "CANDIDATE"


def test_shared_ticker_is_fetched_once_across_watchlists(tmp_path, monkeypatch):
    watchlists_dir = tmp_path / "watchlists"
    results_dir = tmp_path / "results"
    watchlists_dir.mkdir()

    _write_watchlist(
        watchlists_dir,
        "list_a.json",
        "List A",
        [{"ticker": "NVDA", "sector": "Chips", "thesis": "A's thesis"}, {"ticker": "AMD", "sector": "Chips", "thesis": "only in A"}],
    )
    _write_watchlist(
        watchlists_dir,
        "list_b.json",
        "List B",
        [{"ticker": "NVDA", "sector": "AI", "thesis": "B's thesis"}],
    )

    fake = _make_fake_evaluate_ticker()
    _run_main(monkeypatch, watchlists_dir, results_dir, fake)

    assert sorted(fake.calls) == ["AMD", "NVDA"]  # NVDA fetched once despite appearing in both lists

    list_a = json.loads((results_dir / "list_a.json").read_text())
    list_b = json.loads((results_dir / "list_b.json").read_text())

    nvda_in_a = next(r for r in list_a["setups"][SETUP_ID]["results"] if r["ticker"] == "NVDA")
    nvda_in_b = next(r for r in list_b["setups"][SETUP_ID]["results"] if r["ticker"] == "NVDA")
    assert nvda_in_a["thesis"] == "A's thesis"
    assert nvda_in_b["thesis"] == "B's thesis"
    assert nvda_in_a["status"] == nvda_in_b["status"] == "CANDIDATE"


def test_fetch_failure_recorded_in_every_watchlist_that_references_it(tmp_path, monkeypatch):
    watchlists_dir = tmp_path / "watchlists"
    results_dir = tmp_path / "results"
    watchlists_dir.mkdir()

    _write_watchlist(watchlists_dir, "list_a.json", "List A", [{"ticker": "BADTICKER", "sector": None, "thesis": None}, {"ticker": "AAPL", "sector": None, "thesis": None}])
    # list_b also has a passing ticker (MSFT) so it isn't an "every ticker failed" case -
    # that scenario (nothing written at all) is covered separately below.
    _write_watchlist(watchlists_dir, "list_b.json", "List B", [{"ticker": "BADTICKER", "sector": None, "thesis": None}, {"ticker": "MSFT", "sector": None, "thesis": None}])

    fake = _make_fake_evaluate_ticker(fail_tickers={"BADTICKER"})
    _run_main(monkeypatch, watchlists_dir, results_dir, fake)

    list_a = json.loads((results_dir / "list_a.json").read_text())
    list_b = json.loads((results_dir / "list_b.json").read_text())

    assert [e["ticker"] for e in list_a["setups"][SETUP_ID]["errors"]] == ["BADTICKER"]
    assert [e["ticker"] for e in list_b["setups"][SETUP_ID]["errors"]] == ["BADTICKER"]
    assert [r["ticker"] for r in list_a["setups"][SETUP_ID]["results"]] == ["AAPL"]


def test_batch_where_every_ticker_fails_leaves_queue_untouched_for_retry(tmp_path, monkeypatch):
    # Unlike a single bad symbol alongside otherwise-healthy tickers, every ticker in the
    # whole batch failing is almost certainly systemic (bad API key, Tiingo outage) - the
    # queue must not advance (nor any results file be written), so the exact same batch
    # retries next run instead of "completing" by recording a wall of errors.
    watchlists_dir = tmp_path / "watchlists"
    results_dir = tmp_path / "results"
    watchlists_dir.mkdir()
    results_dir.mkdir()

    _write_watchlist(watchlists_dir, "all_bad.json", "All Bad", [{"ticker": "BADTICKER", "sector": None, "thesis": None}])

    fake = _make_fake_evaluate_ticker(fail_tickers={"BADTICKER"})
    state_dir = tmp_path / "state"
    _run_main(monkeypatch, watchlists_dir, results_dir, fake, state_dir=state_dir)

    assert not (results_dir / "all_bad.json").exists()
    assert not (state_dir / "refresh_queue.json").exists()  # never persisted, so next run rebuilds + retries the same batch


def test_batch_where_every_ticker_is_rate_limited_leaves_queue_untouched(tmp_path, monkeypatch):
    # Same "leave it alone" treatment as every-ticker-hard-fails, but for the all-rate-limited
    # case - this is the expected, self-healing shape (next hour's quota resets), not a sign
    # of anything broken.
    watchlists_dir = tmp_path / "watchlists"
    results_dir = tmp_path / "results"
    watchlists_dir.mkdir()

    _write_watchlist(watchlists_dir, "all_limited.json", "All Limited", [{"ticker": "AAPL", "sector": None, "thesis": None}])

    fake = _make_fake_evaluate_ticker(rate_limited_tickers={"AAPL"})
    state_dir = tmp_path / "state"
    _run_main(monkeypatch, watchlists_dir, results_dir, fake, state_dir=state_dir)

    assert not (results_dir / "all_limited.json").exists()
    assert not (state_dir / "refresh_queue.json").exists()


def test_partial_rate_limit_defers_remaining_tickers_without_recording_errors(tmp_path, monkeypatch):
    # The actual bug being fixed: a 429 partway through a batch used to let every ticker after
    # it also 429, then write all of them up as persistent "errors" AND drop them from the
    # queue as done. They must instead be left in the queue, untouched, for the next run - and
    # never show up as errors at all.
    watchlists_dir = tmp_path / "watchlists"
    results_dir = tmp_path / "results"
    state_dir = tmp_path / "state"
    watchlists_dir.mkdir()

    _write_watchlist(
        watchlists_dir,
        "list_a.json",
        "List A",
        [{"ticker": t, "sector": None, "thesis": None} for t in ("AAPL", "MSFT", "NVDA", "ZEBRA")],
    )

    fake = _make_fake_evaluate_ticker(rate_limited_tickers={"NVDA"})
    _run_main(monkeypatch, watchlists_dir, results_dir, fake, state_dir=state_dir)

    # Sorted order is AAPL, MSFT, NVDA, ZEBRA - the batch stops the moment NVDA 429s, so
    # ZEBRA (sorted after it) is never even attempted.
    assert fake.calls == ["AAPL", "MSFT", "NVDA"]

    merged = json.loads((results_dir / "list_a.json").read_text())
    assert {r["ticker"] for r in merged["setups"][SETUP_ID]["results"]} == {"AAPL", "MSFT"}
    assert merged["setups"][SETUP_ID]["errors"] == []  # rate-limited tickers are not errors

    queue = json.loads((state_dir / "refresh_queue.json").read_text())
    assert set(queue["remaining"]) == {"NVDA", "ZEBRA"}  # deferred, not dropped as done


def test_fetch_daily_prices_with_retry_recovers_from_transient_429(monkeypatch):
    monkeypatch.setattr(refresh_job.time, "sleep", lambda seconds: None)
    attempts = []

    def flaky(ticker, start_date):
        attempts.append(ticker)
        if len(attempts) < 3:
            raise TiingoRateLimitError(f"{ticker}: Tiingo returned HTTP 429")
        return [{"date": "2026-11-06", "close": 100}]

    monkeypatch.setattr(refresh_job, "fetch_daily_prices", flaky)

    result = refresh_job.fetch_daily_prices_with_retry("AAPL", "2024-01-01")

    assert len(attempts) == 3  # two 429s absorbed by backoff, third attempt succeeds
    assert result == [{"date": "2026-11-06", "close": 100}]


def test_fetch_daily_prices_with_retry_gives_up_after_max_retries(monkeypatch):
    monkeypatch.setattr(refresh_job.time, "sleep", lambda seconds: None)

    def always_limited(ticker, start_date):
        raise TiingoRateLimitError(f"{ticker}: Tiingo returned HTTP 429")

    monkeypatch.setattr(refresh_job, "fetch_daily_prices", always_limited)

    with pytest.raises(TiingoRateLimitError):
        refresh_job.fetch_daily_prices_with_retry("AAPL", "2024-01-01")


def test_merge_preserves_prior_rows_and_clears_stale_error_on_success(tmp_path, monkeypatch):
    results_dir = tmp_path / "results"
    results_dir.mkdir()
    monkeypatch.setattr(refresh_job, "RESULTS_DIR", str(results_dir))

    prior = {
        "setups": {
            SETUP_ID: {
                "generated_at": "2026-11-01T00:00+00:00",
                "evaluated_session": "2026-10-31",
                "results": [
                    {"ticker": "KEEPME", "name": "Keep Me Inc", "status": "CANDIDATE", "evaluated_session": "2026-10-31"}
                ],
                "errors": [{"ticker": "AAPL", "error": "AAPL: Tiingo returned HTTP 429"}],
            }
        }
    }
    (results_dir / "watch.json").write_text(json.dumps(prior))

    entry = {"ticker": "AAPL", "sector": None, "thesis": None}
    aapl_row = refresh_job.build_row(entry, _fake_evaluation("AAPL"), SETUP_ID)

    # AAPL now succeeds - its stale error should be cleared - while KEEPME (not in this
    # batch at all) is left exactly as it was.
    refresh_job.merge_watchlist_results("watch", {SETUP_ID: [aapl_row]}, {})

    merged = json.loads((results_dir / "watch.json").read_text())
    tickers = {r["ticker"] for r in merged["setups"][SETUP_ID]["results"]}
    assert tickers == {"KEEPME", "AAPL"}
    assert merged["setups"][SETUP_ID]["errors"] == []


def test_queue_drains_across_multiple_runs_within_one_night(tmp_path, monkeypatch):
    watchlists_dir = tmp_path / "watchlists"
    results_dir = tmp_path / "results"
    state_dir = tmp_path / "state"
    watchlists_dir.mkdir()

    _write_watchlist(
        watchlists_dir,
        "list_a.json",
        "List A",
        [{"ticker": t, "sector": None, "thesis": None} for t in ("AAPL", "MSFT", "GOOG", "AMZN")],
    )
    monkeypatch.setattr(refresh_job, "MAX_TICKERS_PER_RUN", 2)

    fake = _make_fake_evaluate_ticker()
    _run_main(monkeypatch, watchlists_dir, results_dir, fake, state_dir=state_dir)
    assert len(fake.calls) == 2
    queue_after_first = json.loads((state_dir / "refresh_queue.json").read_text())
    assert len(queue_after_first["remaining"]) == 2

    _run_main(monkeypatch, watchlists_dir, results_dir, fake, state_dir=state_dir)
    assert len(fake.calls) == 4  # second batch of 2 more, same night
    queue_after_second = json.loads((state_dir / "refresh_queue.json").read_text())
    assert queue_after_second["remaining"] == []

    merged = json.loads((results_dir / "list_a.json").read_text())
    assert {r["ticker"] for r in merged["setups"][SETUP_ID]["results"]} == {"AAPL", "MSFT", "GOOG", "AMZN"}


def test_night_boundary_abandons_stragglers_and_writes_status(tmp_path, monkeypatch):
    watchlists_dir = tmp_path / "watchlists"
    results_dir = tmp_path / "results"
    state_dir = tmp_path / "state"
    watchlists_dir.mkdir()
    state_dir.mkdir()

    _write_watchlist(watchlists_dir, "list_a.json", "List A", [{"ticker": "AAPL", "sector": None, "thesis": None}])

    # A stale queue from a previous (long-gone) night that never finished draining.
    (state_dir / "refresh_queue.json").write_text(
        json.dumps({"night_key": "2020-01-01", "remaining": ["OLDTICKER"], "total": 5, "rotation_cursor": 10})
    )

    fake = _make_fake_evaluate_ticker()
    _run_main(monkeypatch, watchlists_dir, results_dir, fake, state_dir=state_dir)

    status = json.loads((state_dir / "refresh_status.json").read_text())
    assert status == {
        "night_key": "2020-01-01",
        "total": 5,
        "completed": 4,
        "status": "incomplete",
        "finished_at": status["finished_at"],
    }

    new_queue = json.loads((state_dir / "refresh_queue.json").read_text())
    assert new_queue["night_key"] == date.today().isoformat()
    assert "AAPL" not in new_queue["remaining"]  # today's only ticker, already processed this run


def test_build_fresh_queue_rotates_starting_position_across_nights(monkeypatch):
    monkeypatch.setattr(refresh_job, "MAX_TICKERS_PER_RUN", 2)
    tickers = {"AAA", "BBB", "CCC", "DDD", "EEE"}

    first = refresh_job.build_fresh_queue(tickers, rotation_cursor=0)
    second = refresh_job.build_fresh_queue(tickers, rotation_cursor=first["rotation_cursor"])

    assert first["remaining"][0] != second["remaining"][0]  # different tail gets stranded if a night doesn't finish


def test_debug_override_never_writes_results(tmp_path, monkeypatch, capsys):
    watchlists_dir = tmp_path / "watchlists"
    results_dir = tmp_path / "results"
    watchlists_dir.mkdir()

    _write_watchlist(watchlists_dir, "list_a.json", "List A", [{"ticker": "NVDA", "sector": None, "thesis": None}, {"ticker": "AMD", "sector": None, "thesis": None}])

    fake = _make_fake_evaluate_ticker()
    _run_main(monkeypatch, watchlists_dir, results_dir, fake, override="nvda")

    assert fake.calls == ["NVDA"]  # only the overridden ticker was evaluated
    assert not results_dir.exists() or list(results_dir.iterdir()) == []


def test_load_prior_names_merges_across_all_watchlist_result_files(tmp_path, monkeypatch):
    results_dir = tmp_path / "results"
    results_dir.mkdir()
    (results_dir / "list_a.json").write_text(
        json.dumps({"setups": {SETUP_ID: {"results": [{"ticker": "NVDA", "name": "Nvidia Corp"}]}}})
    )
    (results_dir / "list_b.json").write_text(
        json.dumps({"setups": {SETUP_ID: {"results": [{"ticker": "AMD", "name": "Advanced Micro Devices"}]}}})
    )

    monkeypatch.setattr(refresh_job, "RESULTS_DIR", str(results_dir))

    assert refresh_job.load_prior_names() == {"NVDA": "Nvidia Corp", "AMD": "Advanced Micro Devices"}
