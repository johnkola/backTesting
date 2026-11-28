"""The Bank of Canada's Valet API (public, no key), as degenerate bars like
FRED's: one number a day, open = high = low = close, volume 0. Used by
`scripts/fetch_boc_series.py` and by the feeds."""

from __future__ import annotations

import urllib.parse
import urllib.request

from loader.feeds.answers import get_json, quote
from loader.feeds.core import Bar, ProviderError

VALET_URL = "https://www.bankofcanada.ca/valet/observations/{series}/json"


def fetch(series: str, start: str | None = None, timeout: int = 60) -> dict:
    url = VALET_URL.format(series=urllib.parse.quote(series))
    if start:
        url += "?" + urllib.parse.urlencode({"start_date": start})
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    return get_json(req, f"the Bank of Canada's answer for '{series}'", timeout)


def to_bars(payload: dict, series: str,
            start: str | None, end: str | None) -> tuple[list[Bar], int]:
    """Valet observations as bars, plus the number of blank observations
    skipped (a blank is no print, not a zero)."""
    if not isinstance(payload, dict):
        raise ProviderError(f"the Bank of Canada's answer for '{series}' is not an object: "
                            f"{quote(payload)}")
    if payload.get("message") and not payload.get("observations"):
        raise ProviderError(f"the Bank of Canada refused '{series}': {payload['message']}")
    bars: list[Bar] = []
    skipped = 0
    for obs in payload.get("observations") or []:
        # An observation is {"d": date, series: {"v": value}}. Without its date
        # it became a bar dated None, which failed later as a TypeError.
        date = obs.get("d") if isinstance(obs, dict) else None
        cell = obs.get(series) if date else None
        if not date or (cell is not None and not isinstance(cell, dict)):
            raise ProviderError(f"the Bank of Canada's answer for '{series}' has a malformed "
                                f"observation: {quote(obs)}")
        raw = (cell or {}).get("v")
        if raw in (None, ""):
            skipped += 1
            continue
        if (start and date < start) or (end and date > end):
            continue
        try:
            value = float(raw)
        except ValueError:
            skipped += 1
            continue
        bars.append((date, value, value, value, value, 0))
    if not bars:
        raise ProviderError(f"No observations for '{series}' in the requested range.")
    return bars, skipped
