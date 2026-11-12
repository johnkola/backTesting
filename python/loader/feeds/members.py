"""Keeps the breadth members' daily closes current: one JSON file per member,
date -> split-adjusted close, under `NDX_CONSTITUENTS_DIR` (the same cache
`build_ndx_breadth.py` reads). The member list is the set of files there.

Each member is extended from OVERLAP_DAYS before its newest cached close. The
overlap must agree with the cache to within float noise; if it does, the new
days are appended. If it does not, the provider has rewritten that name's
history (a split rescales every close), so the full history is downloaded and
replaces the cache, which leaves breadth unchanged since every breadth metric
is scale-free.

A full download is refused when it is SHORTER than the cache: on 2026-11-11
Yahoo returned one stray bar for WBD instead of its history from 2005, and
taking it would have moved ~5,000 past days of all four breadth series. The
cached history is kept and the member reported, so a delisted name simply stops
voting after its last real close.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from loader.feeds.core import OVERLAP_DAYS

DEFAULT_DIR = "data/ndx-constituents"
#: Members' closes agree when they differ by less than this, relatively.
CLOSE_REL_TOL = 1e-6
#: A full download may hold up to this many fewer days than the cache before it
#: counts as shorter (a vendor can drop a stray bar or two).
SHRINK_SLACK = 5


def cache_dir() -> Path:
    return Path(os.environ.get("NDX_CONSTITUENTS_DIR", DEFAULT_DIR))


@dataclass
class MemberResult:
    symbol: str
    status: str  # new | extended | up-to-date | replaced | kept-shorter | no-new-data | error
    added: int = 0
    note: str = ""

    def to_dict(self) -> dict[str, object]:
        return {"symbol": self.symbol, "status": self.status, "added": self.added,
                "note": self.note}


def load(path: Path) -> dict[str, float]:
    with open(path) as f:
        return json.load(f)


def save(path: Path, closes: dict[str, float]) -> None:
    """Write atomically: a crash mid-write must not leave a truncated cache,
    which would read as a member with a short history."""
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w") as f:
        json.dump(closes, f)
    os.replace(tmp, path)


def _cut(closes: dict[str, float], cutoff: str, start: str = "") -> dict[str, float]:
    return {d: v for d, v in closes.items() if start <= d <= cutoff}


def refresh_member(
    symbol: str,
    cached: dict[str, float],
    *,
    cutoff: dt.date,
    fetch_since: Callable[[str, dt.date | None], dict[str, float]],
) -> tuple[dict[str, float], MemberResult]:
    """The member's closes after this refresh, and what happened.

    `fetch_since(symbol, start)` returns date -> close from `start` on (the
    full history when start is None)."""
    hi = cutoff.isoformat()
    if not cached:
        full = _cut(fetch_since(symbol, None), hi)
        return full, MemberResult(symbol, "new", len(full))

    last = max(cached)
    start = dt.date.fromisoformat(last) - dt.timedelta(days=OVERLAP_DAYS)
    recent = _cut(fetch_since(symbol, start), hi, start.isoformat())
    if not recent:
        return cached, MemberResult(symbol, "no-new-data", 0,
                                    f"no closes since {start}; delisted or renamed?")

    overlap = [d for d in recent if d in cached]
    agrees = bool(overlap) and all(
        abs(recent[d] - cached[d]) <= CLOSE_REL_TOL * max(abs(cached[d]), 1e-12)
        for d in overlap
    )
    if agrees:
        new = {d: v for d, v in recent.items() if d > last}
        if not new:
            return cached, MemberResult(symbol, "up-to-date")
        return {**cached, **new}, MemberResult(symbol, "extended", len(new))

    # The overlap disagrees (or there is none): re-download everything.
    full = _cut(fetch_since(symbol, None), hi)
    upto_last = [d for d in full if d <= last]
    if not full or min(full) > min(cached) or len(upto_last) < len(cached) - SHRINK_SLACK:
        first = min(full) if full else "-"
        return cached, MemberResult(
            symbol, "kept-shorter", 0,
            f"provider history is shorter ({len(full)} closes from {first}) than the "
            f"cache ({len(cached)} from {min(cached)}); kept the cache",
        )
    added = sum(1 for d in full if d > last)
    return full, MemberResult(symbol, "replaced", added,
                              "overlap disagreed (a split?); full history re-downloaded")


def refresh_all(
    directory: Path,
    *,
    cutoff: dt.date,
    fetch_since: Callable[[str, dt.date | None], dict[str, float]],
    save_changes: bool = True,
    delay: float = 0.3,
) -> tuple[dict[str, dict[str, float]], list[MemberResult]]:
    """Every cached member, refreshed. Returns all members' closes (refreshed
    or, on error, as cached) and one result per member."""
    out: dict[str, dict[str, float]] = {}
    results: list[MemberResult] = []
    files = sorted(directory.glob("*.json"))
    for n, path in enumerate(files):
        symbol = path.stem
        cached = load(path)
        if n and delay:
            time.sleep(delay)
        try:
            closes, result = refresh_member(symbol, cached, cutoff=cutoff, fetch_since=fetch_since)
        except Exception as exc:  # noqa: BLE001 — one name must not stop the other 100
            closes, result = cached, MemberResult(symbol, "error", 0, str(exc)[:200])
        if save_changes and closes is not cached:
            save(path, closes)
        out[symbol] = closes
        results.append(result)
    return out, results
