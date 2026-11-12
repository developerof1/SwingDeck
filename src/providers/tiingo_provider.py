"""Tiingo REST client for daily OHLCV history. Sole data source for v1 (see docs/SPEC.md Section 4)."""

import os

import requests

TIINGO_BASE_URL = "https://api.tiingo.com/tiingo/daily"
REQUEST_TIMEOUT_SECONDS = 30


class TiingoError(Exception):
    pass


class TiingoRateLimitError(TiingoError):
    """Raised specifically for HTTP 429 - distinguished from other TiingoErrors so callers
    can retry/back off instead of treating it as a dead ticker (see refresh_job.py)."""


def fetch_daily_prices(ticker: str, start_date: str, api_key: str | None = None) -> list[dict]:
    """Fetch daily OHLCV history for `ticker` from `start_date` (YYYY-MM-DD) to today.

    Uses Tiingo's adjusted close/volume fields so splits/dividends don't distort the
    moving averages and stochastic calculations.
    """
    api_key = api_key or os.environ.get("TIINGO_API_KEY")
    if not api_key:
        raise TiingoError("TIINGO_API_KEY is not set")

    url = f"{TIINGO_BASE_URL}/{ticker}/prices"
    params = {"startDate": start_date, "format": "json", "token": api_key}

    try:
        response = requests.get(url, params=params, timeout=REQUEST_TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        raise TiingoError(f"{ticker}: request failed ({exc})") from exc

    if response.status_code == 404:
        raise TiingoError(f"{ticker}: not found on Tiingo")
    if response.status_code == 401:
        raise TiingoError("Tiingo API key rejected (401) - check TIINGO_API_KEY")
    if response.status_code == 429:
        raise TiingoRateLimitError(f"{ticker}: Tiingo returned HTTP 429")
    if not response.ok:
        raise TiingoError(f"{ticker}: Tiingo returned HTTP {response.status_code}")

    data = response.json()
    if not data:
        raise TiingoError(f"{ticker}: no price data returned for the requested range")
    return data


def fetch_ticker_meta(ticker: str, api_key: str | None = None) -> dict | None:
    """Fetch Tiingo's ticker metadata (company name, etc.) for display purposes only.

    Unlike fetch_daily_prices, this never raises - a company-name lookup failing
    (missing key, bad symbol, network error, unexpected response shape) should only
    blank a display field, never abort a ticker's evaluation. Returns None on any
    failure.
    """
    api_key = api_key or os.environ.get("TIINGO_API_KEY")
    if not api_key:
        return None

    url = f"{TIINGO_BASE_URL}/{ticker}"
    try:
        response = requests.get(url, params={"token": api_key}, timeout=REQUEST_TIMEOUT_SECONDS)
    except requests.RequestException:
        return None

    if not response.ok:
        return None

    try:
        data = response.json()
    except ValueError:
        return None

    return data if isinstance(data, dict) else None
