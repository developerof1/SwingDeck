"""End-to-end UI tests for the pin feature, driving app.py through Streamlit's AppTest against
the real committed results/ and watchlists/ files and an in-memory user-data store (no token,
no network). Rows are picked from whatever the current results hold, so these assert behavior,
not specific tickers."""

import json
import os

import pytest
import requests
from streamlit.testing.v1 import AppTest

from src import pins, user_store
from src.user_store import MemoryBackend, StoreError

ROOT = os.path.dirname(os.path.dirname(__file__))
APP = os.path.join(ROOT, "app.py")
SETUP = "momentum_continuation"
MASTER = "master_watchlist"
NOW = "2026-11-23T14:05Z"


def _results(watchlist_id=MASTER):
    with open(os.path.join(ROOT, "results", f"{watchlist_id}.json")) as f:
        return json.load(f)["setups"][SETUP]["results"]


def _tickers(watchlist_id):
    with open(os.path.join(ROOT, "watchlists", f"{watchlist_id}.json")) as f:
        return {e["ticker"] for e in json.load(f)["tickers"]}


def _group_tickers(group):
    return [r["ticker"] for r in _results() if r["ui_group"] == group]


@pytest.fixture(autouse=True)
def no_real_network(monkeypatch):
    # app.py's catch-up refresh trigger may POST to GitHub when a dispatch token exists locally.
    monkeypatch.setattr(requests, "post", lambda *args, **kwargs: None)


@pytest.fixture
def store(monkeypatch):
    mem = MemoryBackend()
    monkeypatch.setattr(user_store, "TEST_BACKEND", mem)
    return mem


def seed(mem, *pin_args):
    doc = pins.empty_doc()
    for ticker, group in pin_args:
        doc = pins.add_pin(doc, SETUP, ticker, group, NOW)
    mem.files[pins.PINS_PATH] = (doc, 1)


def stored_tickers(mem):
    doc, _ = mem.files.get(pins.PINS_PATH, ({"pins": []}, 0))
    return [p["ticker"] for p in doc["pins"]]


def run_app(watchlist_id=MASTER):
    at = AppTest.from_file(APP, default_timeout=60)
    at.session_state["authenticated"] = True
    at.session_state["selected_watchlist_id"] = watchlist_id
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def pin_keys(at):
    return [b.key for b in at.button if b.key and b.key.startswith("sd_pin")]


def html_text(at):
    return "\n".join(str(h.value) for h in at.html)


def developing_ticker():
    tickers = _group_tickers("developing") + _group_tickers("qualified")
    if not tickers:
        pytest.skip("no Qualified/Developing rows in the current results")
    return tickers[0]


# ---- no token: the app is exactly as before --------------------------------------------


def test_without_a_token_there_are_no_pin_controls_and_no_wall(monkeypatch):
    monkeypatch.setattr(user_store, "TEST_BACKEND", None)
    monkeypatch.setattr(user_store, "TOKEN_SECRET_NAME", "NO_SUCH_SECRET_FOR_TESTS")
    at = run_app()
    assert pin_keys(at) == []
    assert "Pinned Wall" not in "".join(str(m.value) for m in at.markdown)
    # ...and it says why, naming the secret, instead of failing silently.
    assert any("Pins unavailable" in c.value and "NO_SUCH_SECRET_FOR_TESTS" in c.value for c in at.caption)


# ---- pin / unpin ------------------------------------------------------------------------


def test_pin_buttons_only_on_qualified_and_developing_rows(store):
    at = run_app()
    keys = pin_keys(at)
    expected = len(_group_tickers("developing")) + len(_group_tickers("qualified"))
    assert len(keys) == expected  # no wall yet, so every key is a row button
    assert all(k.startswith("sd_pinoff_row_") for k in keys)
    monitored = _group_tickers("other")[0]
    assert not any(k.endswith(f"_{monitored}") for k in keys)


def test_pinning_writes_to_the_store_and_shows_a_tile_with_a_filled_pin(store):
    ticker = developing_ticker()
    at = run_app()
    at.button(key=f"sd_pinoff_row_{SETUP}_{ticker}").click().run()
    assert not at.exception

    assert stored_tickers(store) == [ticker]
    assert store.writes[-1][1] == f"[user-data] pin {ticker} ({SETUP})"
    assert any("Pinned Wall" in str(m.value) for m in at.markdown)
    assert f"sd_pinon_tile_{SETUP}_{ticker}" in pin_keys(at)
    assert f"sd_pinon_row_{SETUP}_{ticker}" in pin_keys(at)  # the row's own pin is filled in place
    assert f"sd_pinoff_row_{SETUP}_{ticker}" not in pin_keys(at)


def test_pinned_name_stays_in_its_list_and_counts_do_not_change(store):
    ticker = developing_ticker()
    before = html_text(run_app())
    at = run_app()
    at.button(key=f"sd_pinoff_row_{SETUP}_{ticker}").click().run()
    after = html_text(at)
    assert after.count(f">{ticker}<") == 2  # the row and the tile
    assert before.count(f">{ticker}<") == 1
    assert "Developing" in after and f"{len(_group_tickers('developing'))} Developing" in after


def test_unpin_from_the_tile_removes_the_pin_and_hides_the_empty_wall(store):
    ticker = developing_ticker()
    seed(store, (ticker, "developing"))
    at = run_app()
    at.button(key=f"sd_pinon_tile_{SETUP}_{ticker}").click().run()
    assert not at.exception
    assert stored_tickers(store) == []
    assert not any("Pinned Wall" in str(m.value) for m in at.markdown)
    assert f"sd_pinoff_row_{SETUP}_{ticker}" in pin_keys(at)


def test_tiles_run_in_pin_order(store):
    tickers = (_group_tickers("developing") + _group_tickers("qualified"))[:3]
    if len(tickers) < 3:
        pytest.skip("fewer than 3 pinnable rows")
    pinned_order = [tickers[2], tickers[0], tickers[1]]
    seed(store, *[(t, "developing") for t in pinned_order])
    at = run_app()
    tile_order = [k.rsplit("_", 1)[1] for k in pin_keys(at) if k.startswith("sd_pinon_tile_")]
    assert tile_order == pinned_order


# ---- the second line ---------------------------------------------------------------------


def test_second_line_blank_when_status_unchanged_and_present_when_changed(store):
    ticker = _group_tickers("developing")[0] if _group_tickers("developing") else developing_ticker()
    current = next(r["ui_group"] for r in _results() if r["ticker"] == ticker)

    seed(store, (ticker, current))
    unchanged = html_text(run_app())
    assert "Pinned as" not in unchanged
    assert 'class="sd-tile-change"' in unchanged  # the line's height is reserved either way

    changed_from = "qualified" if current != "qualified" else "developing"
    seed(store, (ticker, changed_from))
    changed = html_text(run_app())
    label = {"qualified": "Qualified", "developing": "Developing"}[changed_from]
    assert f"Pinned as" in changed and label in changed


def test_tile_spells_out_the_current_status(store):
    ticker = _group_tickers("developing")[0] if _group_tickers("developing") else developing_ticker()
    seed(store, (ticker, "developing"))
    html = html_text(run_app())
    assert 'class="sd-tile-status"' in html
    assert "Developing</span>" in html  # label next to the dot, regardless of whether it changed
    # badges live in the tile's reserved bottom row, never in the top row
    assert 'class="sd-tile-bottom"' in html and 'class="sd-tile-badges"' in html


def test_pinned_tile_survives_dropping_to_monitored(store):
    ticker = _group_tickers("other")[0]
    seed(store, (ticker, "developing"))
    at = run_app()
    html = html_text(at)
    assert html.count(f">{ticker}<") == 1  # on the wall; Monitored rows are not rendered until toggled
    assert "Pinned as" in html and "Developing" in html
    assert f"sd_pinon_tile_{SETUP}_{ticker}" in pin_keys(at)


# ---- scope: global storage, wall filtered to the selected watchlist -----------------------


def test_wall_shows_only_pins_in_the_selected_watchlist(store):
    ai_only = sorted(_tickers("ai_trade_names") - _tickers(MASTER))
    in_master = developing_ticker()
    seed(store, (ai_only[0], "developing"), (in_master, "developing"))

    master_keys = pin_keys(run_app(MASTER))
    assert f"sd_pinon_tile_{SETUP}_{in_master}" in master_keys
    assert not any(k.endswith(f"_{ai_only[0]}") for k in master_keys)
    assert stored_tickers(store) == [ai_only[0], in_master]  # still stored, just not on this wall

    ai_tiles = [k for k in pin_keys(run_app("ai_trade_names")) if k.startswith("sd_pinon_tile_")]
    assert f"sd_pinon_tile_{SETUP}_{ai_only[0]}" in ai_tiles
    assert (f"sd_pinon_tile_{SETUP}_{in_master}" in ai_tiles) == (in_master in _tickers("ai_trade_names"))


# ---- cleanup and failure handling ---------------------------------------------------------


def test_pin_for_a_ticker_in_no_watchlist_is_cleaned_up_on_load(store):
    ticker = developing_ticker()
    seed(store, ("ZZZZ_NOT_A_TICKER", "developing"), (ticker, "developing"))
    run_app()
    assert stored_tickers(store) == [ticker]
    assert store.writes[-1][1] == "[user-data] prune pins for tickers in no watchlist"


def test_no_orphans_means_no_cleanup_commit(store):
    ticker = developing_ticker()
    seed(store, (ticker, "developing"))
    run_app()
    assert store.writes == []


def test_failed_save_leaves_pins_unchanged_and_shows_a_toast(store):
    ticker = developing_ticker()
    at = run_app()
    store.fail_next_write = StoreError("GitHub unreachable: boom")
    at.button(key=f"sd_pinoff_row_{SETUP}_{ticker}").click().run()
    assert not at.exception
    assert stored_tickers(store) == []
    assert f"sd_pinoff_row_{SETUP}_{ticker}" in pin_keys(at)  # still hollow
    assert any("Couldn't save the pin change" in t.value for t in at.toast)


def test_unreadable_store_hides_pin_controls_and_says_why(store):
    store.fail_reads = StoreError("Branch 'user-data' does not exist in developerof1/SwingDeck.")
    at = run_app()
    assert pin_keys(at) == []
    assert any("Pins unavailable" in c.value and "user-data" in c.value for c in at.caption)


# ---- load failures are visible and not final -----------------------------------------------


def test_unexpected_exception_while_loading_is_shown_not_swallowed(store):
    # Not a StoreError: before, this raised on the first run only and every later rerun
    # silently skipped the load (no pins, no caption).
    store.fail_reads = KeyError("content")
    at = run_app()
    assert pin_keys(at) == []
    assert any("Pins unavailable" in c.value and "KeyError" in c.value for c in at.caption)
    at.run()  # a later rerun inside the retry window must still explain itself
    assert not at.exception
    assert any("Pins unavailable" in c.value and "KeyError" in c.value for c in at.caption)


def test_status_line_reports_loaded_pins(store):
    seed(store, (developing_ticker(), "developing"))
    at = run_app()
    assert any(c.value.startswith("Pins: loaded (1 pinned") for c in at.caption)


def test_failed_load_is_retried_after_the_window_and_recovers(store, monkeypatch):
    import time as time_module

    offset = [0.0]
    real_monotonic = time_module.monotonic
    monkeypatch.setattr(time_module, "monotonic", lambda: real_monotonic() + offset[0])

    store.fail_reads = StoreError("GitHub unreachable: timed out")
    at = run_app()
    assert pin_keys(at) == []

    store.fail_reads = None  # GitHub is back
    at.run()  # too soon: still inside the retry window, no new attempt
    assert pin_keys(at) == []

    offset[0] = 31.0
    at.run()
    assert not at.exception
    assert len(pin_keys(at)) > 0
    assert any(c.value.startswith("Pins: loaded") for c in at.caption)
    assert not any("Pins unavailable" in c.value for c in at.caption)
