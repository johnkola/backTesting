"""The Bank of Canada's Valet API (public, no key), as degenerate bars like
FRED's: one number a day, open = high = low = close, volume 0. Used by
`fetch_boc_series.py` and by the feeds."""

from __future__ import annotations

import json
import urllib.parse
import urllib.request

from loader.feeds.core import Bar, ProviderError

VALET_URL = "https://www.bankofcanada.ca/valet/observations/{series}/json"


def fetch(series: str, start: str | None = None, timeout: int = 60) -> dict:
    url = VALET_URL.format(series=urllib.parse.quote(series))
    if start:
        url += "?" + urllib.parse.urlencode({"start_date": start})
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def to_bars(payload: dict, series: str,
            start: str | None, end: str | None) -> tuple[list[Bar], int]:
    """Valet observations as bars, plus the number of blank observations
    skipped (a blank is no print, not a zero)."""
    bars: list[Bar] = []
    skipped = 0
    for obs in payload.get("observations", []):
        date = obs.get("d")
        raw = (obs.get(series) or {}).get("v")
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
