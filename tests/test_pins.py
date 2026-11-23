from src import pins

NOW = "2026-11-23T14:05Z"
MC = "momentum_continuation"


def test_empty_doc_has_schema_and_no_pins():
    assert pins.empty_doc() == {"schema": 1, "pins": []}


def test_add_pin_records_setup_ticker_time_and_group():
    doc = pins.add_pin(pins.empty_doc(), MC, "CPB", "developing", NOW)
    assert doc["pins"] == [{"setup": MC, "ticker": "CPB", "pinned_at": NOW, "pinned_group": "developing"}]
    assert pins.is_pinned(doc, MC, "CPB")


def test_add_pin_is_idempotent_and_keeps_the_original_pin_time_and_group():
    doc = pins.add_pin(pins.empty_doc(), MC, "CPB", "developing", NOW)
    again = pins.add_pin(doc, MC, "CPB", "qualified", "2026-11-24T09:00Z")
    assert again == doc


def test_pin_is_keyed_by_setup_and_ticker():
    doc = pins.add_pin(pins.empty_doc(), MC, "CPB", "developing", NOW)
    doc = pins.add_pin(doc, "other_setup", "CPB", "qualified", NOW)
    assert [p["setup"] for p in doc["pins"]] == [MC, "other_setup"]
    doc = pins.remove_pin(doc, MC, "CPB")
    assert pins.is_pinned(doc, "other_setup", "CPB") and not pins.is_pinned(doc, MC, "CPB")


def test_remove_pin_is_idempotent():
    doc = pins.empty_doc()
    assert pins.remove_pin(doc, MC, "CPB") == doc


def test_pin_order_is_pin_order():
    doc = pins.empty_doc()
    for ticker in ["CMG", "CPB", "CELH"]:
        doc = pins.add_pin(doc, MC, ticker, "developing", NOW)
    assert [p["ticker"] for p in pins.pins_in_watchlist(doc, {"CPB", "CMG", "CELH"})] == ["CMG", "CPB", "CELH"]


def test_wall_is_filtered_to_the_watchlist_but_storage_is_not():
    doc = pins.empty_doc()
    for ticker in ["CMG", "NVDA"]:
        doc = pins.add_pin(doc, MC, ticker, "developing", NOW)
    assert [p["ticker"] for p in pins.pins_in_watchlist(doc, {"NVDA"})] == ["NVDA"]
    assert len(doc["pins"]) == 2


def test_prune_drops_only_tickers_in_no_watchlist():
    doc = pins.empty_doc()
    for ticker in ["CMG", "GONE"]:
        doc = pins.add_pin(doc, MC, ticker, "developing", NOW)
    pruned = pins.prune_orphans(doc, {"CMG", "NVDA"})
    assert [p["ticker"] for p in pruned["pins"]] == ["CMG"]


def test_prune_with_unknown_universe_never_wipes_the_wall():
    doc = pins.add_pin(pins.empty_doc(), MC, "CMG", "developing", NOW)
    assert pins.prune_orphans(doc, set()) == doc


def test_prune_returns_the_same_doc_when_nothing_to_drop():
    doc = pins.add_pin(pins.empty_doc(), MC, "CMG", "developing", NOW)
    assert pins.prune_orphans(doc, {"CMG"}) is doc
