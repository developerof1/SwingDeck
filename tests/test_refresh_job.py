"""Covers the multi-watchlist dedup-and-fan-out orchestration in scripts/refresh_job.py.
evaluate_ticker (the actual Tiingo fetch + indicator/rules-engine call) is mocked out
throughout - test_data_engine.py / test_rules_engine.py already cover that math. These
tests only exercise: fetching each unique ticker once regardless of how many watchlists
reference it, fanning a ticker's single evaluation out into each watchlist's own
sector/thesis, distributing fetch failures into every watchlist's errors list that
references the failed ticker, and never overwriting a watchlist whose tickers all failed.
"""

import json

import scripts.refresh_job as refresh_job
import src.watchlist_store as watchlist_store
from src.providers.tiingo_provider import TiingoError


def _write_watchlist(dir_path, filename, name, tickers):
    (dir_path / filename).write_text(json.dumps({"name": name, "tickers": tickers}))


def _fake_evaluation(ticker: str) -> dict:
    return {
        "ticker": ticker,
        "name": f"{ticker} Inc",
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


def _make_fake_evaluate_ticker(fail_tickers=frozenset()):
    calls = []

    def fake(ticker, start_date, prior_names, meta_budget):
        calls.append(ticker)
        if ticker in fail_tickers:
            raise TiingoError(f"{ticker}: boom")
        return _fake_evaluation(ticker)

    fake.calls = calls
    return fake


def _run_main(monkeypatch, watchlists_dir, results_dir, fake_evaluate_ticker, override=""):
    monkeypatch.setattr(refresh_job, "RESULTS_DIR", str(results_dir))
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

    row = refresh_job.build_row(entry, evaluation)

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

    nvda_in_a = next(r for r in list_a["results"] if r["ticker"] == "NVDA")
    nvda_in_b = next(r for r in list_b["results"] if r["ticker"] == "NVDA")
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

    assert [e["ticker"] for e in list_a["errors"]] == ["BADTICKER"]
    assert [e["ticker"] for e in list_b["errors"]] == ["BADTICKER"]
    assert [r["ticker"] for r in list_a["results"]] == ["AAPL"]


def test_watchlist_with_every_ticker_failing_is_left_untouched(tmp_path, monkeypatch):
    watchlists_dir = tmp_path / "watchlists"
    results_dir = tmp_path / "results"
    watchlists_dir.mkdir()
    results_dir.mkdir()

    _write_watchlist(watchlists_dir, "all_bad.json", "All Bad", [{"ticker": "BADTICKER", "sector": None, "thesis": None}])
    _write_watchlist(watchlists_dir, "fine.json", "Fine", [{"ticker": "AAPL", "sector": None, "thesis": None}])

    prior_content = json.dumps({"generated_at": "2026-11-01T00:00+00:00", "evaluated_session": "2026-10-31", "results": [], "errors": []})
    (results_dir / "all_bad.json").write_text(prior_content)

    fake = _make_fake_evaluate_ticker(fail_tickers={"BADTICKER"})
    _run_main(monkeypatch, watchlists_dir, results_dir, fake)

    assert (results_dir / "all_bad.json").read_text() == prior_content  # untouched
    assert (results_dir / "fine.json").exists()


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
    (results_dir / "list_a.json").write_text(json.dumps({"results": [{"ticker": "NVDA", "name": "Nvidia Corp"}]}))
    (results_dir / "list_b.json").write_text(json.dumps({"results": [{"ticker": "AMD", "name": "Advanced Micro Devices"}]}))

    monkeypatch.setattr(refresh_job, "RESULTS_DIR", str(results_dir))

    assert refresh_job.load_prior_names() == {"NVDA": "Nvidia Corp", "AMD": "Advanced Micro Devices"}
