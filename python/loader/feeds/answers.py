"""A provider's answer over HTTP, read so that a bad one says what it was.

Two kinds of answer used to fail a series without the reason:

  * An HTTP error whose body explains it. Yahoo answers an unknown or delisted
    symbol with a 404 whose JSON says "No data found, symbol may be delisted";
    FRED answers a bad key or series with a 400 and an `error_message`; the
    Bank of Canada answers an unknown series with a 404 and a `message`.
    `urlopen` raises on the status before the body is read, so the run
    recorded only "HTTP Error 404: Not Found".
  * A body that is not JSON: a rate limit or an outage page in HTML, which
    `json.loads` reports as "Expecting value: line 1 column 1 (char 0)".

Both are a `ProviderError` here, with the provider, the series and what came
back. A failure to reach the provider at all stays an `OSError`, as before.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from loader.feeds.core import ProviderError

#: How much of an unexpected body a reason quotes.
QUOTE_CHARS = 80


def get_json(req: urllib.request.Request, what: str, timeout: float) -> Any:
    """The JSON `req` answers with. `what` names the answer in a reason
    ("Yahoo's answer for 'QQQ'"); it must not hold the URL, which can carry a
    key."""
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
    except urllib.error.HTTPError as exc:
        why = explanation(exc.read() if exc.fp is not None else b"")
        if why is None:
            raise
        raise ProviderError(f"{what}: HTTP {exc.code}: {why}") from None
    return parse(body, what)


def parse(body: bytes, what: str) -> Any:
    """`body` as JSON, or a ProviderError quoting how it begins."""
    try:
        return json.loads(body.decode("utf-8"))
    except ValueError as exc:  # JSONDecodeError and UnicodeDecodeError both are
        raise ProviderError(f"{what} is not JSON ({exc}); it begins {quote(body)}") from None


def explanation(body: bytes) -> str | None:
    """The reason an error answer gives in its body: Yahoo's chart error,
    FRED's `error_message`, the Bank of Canada's `message`. None when the body
    gives none."""
    try:
        payload = json.loads(body.decode("utf-8"))
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    chart = payload.get("chart")
    if isinstance(chart, dict) and isinstance(chart.get("error"), dict):
        err = chart["error"]
        return str(err.get("description") or err.get("code") or err)
    for key in ("error_message", "message"):
        if payload.get(key):
            return str(payload[key])
    return None


def quote(body: bytes | str | object) -> str:
    """The start of an unexpected answer, for a reason."""
    text = body.decode("utf-8", "replace") if isinstance(body, bytes) else str(body)
    text = " ".join(text.split())
    return repr(text[:QUOTE_CHARS] + ("..." if len(text) > QUOTE_CHARS else ""))
