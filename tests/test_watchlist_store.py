import json

from src.watchlist_store import discover_watchlists, load_watchlist


def _write(path, name, tickers):
    path.write_text(json.dumps({"name": name, "tickers": tickers}))


def test_discover_watchlists_empty_dir(tmp_path):
    assert discover_watchlists(str(tmp_path)) == []


def test_discover_watchlists_missing_dir(tmp_path):
    assert discover_watchlists(str(tmp_path / "does_not_exist")) == []


def test_discover_watchlists_reads_name_and_count(tmp_path):
    _write(tmp_path / "tech.json", "Tech & Growth Leaders", [{"ticker": "NVDA"}, {"ticker": "AMD"}])
    _write(tmp_path / "core.json", "Core Swing", [{"ticker": "AAPL"}])

    entries = discover_watchlists(str(tmp_path))

    assert entries == [
        {"id": "core", "name": "Core Swing", "ticker_count": 1, "path": str(tmp_path / "core.json")},
        {"id": "tech", "name": "Tech & Growth Leaders", "ticker_count": 2, "path": str(tmp_path / "tech.json")},
    ]


def test_discover_watchlists_sorted_by_name_not_filename(tmp_path):
    _write(tmp_path / "zzz_first_alphabetically.json", "AAA comes last by filename", [])
    _write(tmp_path / "aaa_last_alphabetically.json", "ZZZ comes first by filename", [])

    entries = discover_watchlists(str(tmp_path))

    assert [e["name"] for e in entries] == ["AAA comes last by filename", "ZZZ comes first by filename"]


def test_discover_watchlists_ignores_non_json_files(tmp_path):
    _write(tmp_path / "tech.json", "Tech", [])
    (tmp_path / "README.md").write_text("not a watchlist")

    entries = discover_watchlists(str(tmp_path))

    assert [e["id"] for e in entries] == ["tech"]


def test_discover_watchlists_falls_back_to_id_when_name_missing(tmp_path):
    (tmp_path / "no_name.json").write_text(json.dumps({"tickers": []}))

    entries = discover_watchlists(str(tmp_path))

    assert entries[0]["name"] == "no_name"


def test_load_watchlist_returns_full_contents(tmp_path):
    _write(tmp_path / "tech.json", "Tech", [{"ticker": "NVDA", "sector": "Chips", "thesis": "AI beta"}])

    data = load_watchlist("tech", str(tmp_path))

    assert data == {"name": "Tech", "tickers": [{"ticker": "NVDA", "sector": "Chips", "thesis": "AI beta"}]}
