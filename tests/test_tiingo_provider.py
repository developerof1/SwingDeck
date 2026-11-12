"""fetch_ticker_meta's degrade-to-None paths, plus fetch_daily_prices's error-raising paths -
mocked, no real network calls."""

import pytest
import requests

from src.providers.tiingo_provider import TiingoError, TiingoRateLimitError, fetch_daily_prices, fetch_ticker_meta


class _FakeResponse:
    def __init__(self, ok=True, status_code=200, json_data=None, raise_on_json=False):
        self.ok = ok
        self.status_code = status_code
        self._json_data = json_data
        self._raise_on_json = raise_on_json

    def json(self):
        if self._raise_on_json:
            raise ValueError("invalid json")
        return self._json_data


def test_no_api_key_returns_none(monkeypatch):
    monkeypatch.delenv("TIINGO_API_KEY", raising=False)
    assert fetch_ticker_meta("AAPL", api_key=None) is None


def test_bad_status_returns_none(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResponse(ok=False, status_code=404))
    assert fetch_ticker_meta("NOTATICKER", api_key="fake-key") is None


def test_malformed_json_returns_none(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResponse(ok=True, raise_on_json=True))
    assert fetch_ticker_meta("AAPL", api_key="fake-key") is None


def test_non_dict_json_returns_none(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResponse(ok=True, json_data=[1, 2, 3]))
    assert fetch_ticker_meta("AAPL", api_key="fake-key") is None


def test_network_error_returns_none(monkeypatch):
    def _raise(*a, **k):
        raise requests.RequestException("connection failed")

    monkeypatch.setattr(requests, "get", _raise)
    assert fetch_ticker_meta("AAPL", api_key="fake-key") is None


def test_successful_response_returns_dict(monkeypatch):
    monkeypatch.setattr(
        requests, "get", lambda *a, **k: _FakeResponse(ok=True, json_data={"ticker": "AAPL", "name": "Apple Inc"})
    )
    result = fetch_ticker_meta("AAPL", api_key="fake-key")
    assert result == {"ticker": "AAPL", "name": "Apple Inc"}


def test_fetch_daily_prices_429_raises_rate_limit_error(monkeypatch):
    # Distinguished from other TiingoErrors - refresh_job.py relies on this subclass to know
    # a 429 means "retry/back off" rather than "this ticker is dead."
    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResponse(ok=False, status_code=429))
    with pytest.raises(TiingoRateLimitError):
        fetch_daily_prices("AAPL", "2024-01-01", api_key="fake-key")


def test_fetch_daily_prices_other_bad_status_raises_plain_tiingo_error(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResponse(ok=False, status_code=500))
    with pytest.raises(TiingoError) as exc_info:
        fetch_daily_prices("AAPL", "2024-01-01", api_key="fake-key")
    assert not isinstance(exc_info.value, TiingoRateLimitError)
