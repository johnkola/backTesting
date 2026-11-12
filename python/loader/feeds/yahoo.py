"""Yahoo Finance's chart API: daily OHLCV, raw or dividend-adjusted.

Unofficial and keyless (query1.finance.yahoo.com/v8/finance/chart). It has
served this project's prices, indexes and futures since the start, and it can
rate-limit or change without notice; a paid feed is the safer source if the
app becomes a signal provider. Used by `fetch_historical_data.py` and by the
feeds, so both store the same numbers.
"""

from __future__ import annotations

import datetime as dt
import json
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

from loader.feeds.core import Bar, ProviderError

CHART_URL = (
    "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    "?period1={period1}&period2={period2}&interval={interval}"
)

# Bare volatility/index tickers need a caret on Yahoo (^VIX, ^VXN, ^GSPC …).
KNOWN_INDICES = {"VIX", "VXN", "VVIX", "GSPC", "IXIC", "DJI", "RUT", "NDX"}


def yahoo_symbol(symbol: str) -> str:
    """Prefix known index tickers with a caret; leave ETFs/stocks untouched."""
    s = symbol.upper().lstrip("^")
    return f"^{s}" if s in KNOWN_INDICES else s


def epoch(day: dt.date) -> int:
    return int(dt.datetime(day.year, day.month, day.day, tzinfo=dt.UTC).timestamp())


def exchange_tz(meta: dict) -> dt.tzinfo:
    """The exchange's timezone from the chart's meta, UTC when it is missing."""
    name = meta.get("exchangeTimezoneName")
    if name:
        try:
            return ZoneInfo(name)
        except (KeyError, ValueError):
            pass
    return dt.UTC


def live_quote_index(timestamps: list[int], tz: dt.tzinfo) -> int | None:
    """The index of a trailing live quote, or None.

    Yahoo stamps every regular daily bar at one local time per exchange (09:30
    for US equities, midnight for CME futures and ICE's dollar index). After a
    futures session closes it appends the next session's running quote,
    stamped with the time it was fetched. Dated by that stamp it can land on
    today, beside today's real bar, whose close Yahoo may not have filled yet:
    stored, it would be the next session's partial price under today's date.
    So a last bar stamped at a different local time than the one before it is
    a quote, not a bar, and is dropped.
    """
    if len(timestamps) < 2:
        return None

    def local_time(ts: int) -> dt.time:
        return dt.datetime.fromtimestamp(ts, tz).time()

    return len(timestamps) - 1 if local_time(timestamps[-1]) != local_time(timestamps[-2]) else None


def fetch(symbol: str, period1: int, period2: int, interval: str = "1d",
          timeout: int = 30) -> dict:
    # Explicit epoch bounds (not range=max) — range=max silently downsamples a
    # multi-decade daily request to monthly bars; period1/period2 stays daily.
    # urlencode the symbol so the caret in index tickers (^VIX) survives.
    url = CHART_URL.format(symbol=urllib.parse.quote(yahoo_symbol(symbol)),
                           period1=period1, period2=period2, interval=interval)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def to_bars(payload: dict, symbol: str, adjusted: bool = False) -> tuple[list[Bar], int]:
    """Reshape Yahoo's columnar JSON into bars, plus how many were dropped.

    With `adjusted`, the bars are the dividend-adjusted (total-return) series
    instead of the raw prints. Yahoo's `close` is already split-adjusted;
    `adjclose` is split *and* dividend adjusted, so `adjclose / close` is the
    dividend factor for that bar. All four prices are scaled by it — the same
    positive factor on each, so OHLC stays internally consistent — and volume
    is left alone, since it is already split-adjusted.

    Why it matters: on raw prices, buy-and-hold looks worse than it was,
    because the cash paid out as dividends simply vanishes from the series.
    On SPY that is roughly 1.7 points of annual return, which is the
    difference between a strategy beating the index and losing to it.
    """
    chart = payload.get("chart", {})
    if chart.get("error"):
        raise ProviderError(f"Yahoo error for '{symbol}': {chart['error']}")
    results = chart.get("result") or []
    if not results:
        raise ProviderError(f"No data returned for '{symbol}'.")

    result = results[0]
    timestamps = result.get("timestamp") or []
    quote = (result.get("indicators", {}).get("quote") or [{}])[0]
    opens, highs = quote.get("open", []), quote.get("high", [])
    lows, closes = quote.get("low", []), quote.get("close", [])
    volumes = quote.get("volume", [])
    if not timestamps:
        raise ProviderError(f"No candles for '{symbol}' (check symbol / range).")

    adjcloses = []
    if adjusted:
        adjcloses = (result.get("indicators", {}).get("adjclose") or [{}])[0].get("adjclose", [])
        if not adjcloses:
            raise ProviderError(
                f"'{symbol}' returned no adjclose series — Yahoo has no "
                f"dividend-adjusted data for it (indices don't pay dividends)."
            )

    tz = exchange_tz(result.get("meta") or {})
    live = live_quote_index(timestamps, tz)
    bars: list[Bar] = []
    seen: set[str] = set()
    dropped = 0
    for i, ts in enumerate(timestamps):
        if i == live:
            dropped += 1
            continue
        o, h, lo, c = opens[i], highs[i], lows[i], closes[i]
        v = volumes[i] if i < len(volumes) else None
        # Yahoo emits nulls for gaps and an in-progress current bar — skip them.
        if None in (o, h, lo, c):
            dropped += 1
            continue
        if adjusted:
            adj = adjcloses[i] if i < len(adjcloses) else None
            if adj is None or not c:
                dropped += 1
                continue
            factor = adj / c
            o, h, lo, c = o * factor, h * factor, lo * factor, adj
        date = dt.datetime.fromtimestamp(ts, dt.UTC).strftime("%Y-%m-%d")
        if date in seen:
            # Two bars on one date: keep the first, the regular daily bar.
            dropped += 1
            continue
        seen.add(date)
        bars.append((date, round(o, 6), round(h, 6), round(lo, 6),
                     round(c, 6), int(v) if v is not None else 0))

    if not bars:
        raise ProviderError(f"All bars for '{symbol}' were empty/null.")
    return bars, dropped


def fetch_closes(symbol: str, period1: int, period2: int, timeout: int = 30) -> dict[str, float]:
    """Date -> split-adjusted close for one symbol: what breadth is built from."""
    payload = fetch(symbol, period1, period2, timeout=timeout)
    chart = payload.get("chart", {})
    if chart.get("error"):
        raise ProviderError(chart["error"].get("description") or str(chart["error"]))
    results = chart.get("result") or []
    if not results:
        raise ProviderError("no result")
    result = results[0]
    stamps = result.get("timestamp") or []
    closes = (result.get("indicators", {}).get("quote") or [{}])[0].get("close", [])
    out: dict[str, float] = {}
    for i, ts in enumerate(stamps):
        c = closes[i] if i < len(closes) else None
        if c is None:
            continue
        out[dt.datetime.fromtimestamp(ts, dt.UTC).strftime("%Y-%m-%d")] = float(c)
    return out
