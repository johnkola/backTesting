"""What produced a number: the code, the libraries and the inputs (DESIGN-GAPS G3).

backTesting-04-Architecture.md §1: "A feature or model run is identified by code version plus
input snapshot, so it can be recomputed and compared." This module is the one
definition of each of those, so the feature build, the walk-forward run and the
check that compares them later cannot disagree about what a hash covers.

HASHES ARE OF VALUES, NOT OF ROWS' METADATA. Candles carry no `updated_at`, and
a roll-up never came from an imported file, so "which rows, by timestamp" cannot
describe an input on its own. What can is the values as read: every series is
hashed from a canonical text of its (date, value) pairs, and a matrix from its
names, dates and float bytes. Two reads hash equal exactly when they would
train the same model, which is the property a snapshot is for.

CANONICAL MEANS STABLE ACROSS MACHINES. Floats go through `repr` (shortest
round-trip form) for series and through little-endian float32 bytes for the
matrix, which is the dtype `read_matrix` returns. NaN is written as the text
`nan` rather than its bit pattern, which varies.

WHAT IS NOT HERE: storing old values. A snapshot proves whether the inputs are
still what a run saw; it cannot bring back ones a rebuild replaced. That needs
versioned feature sets, which G3 deliberately left as a later decision.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np

#: Source trees whose code decides a feature value or a run's result.
_PACKAGE_ROOT = Path(__file__).resolve().parent.parent
_CODE_DIRS = ("nn", "loader")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _num(v: float | None) -> str:
    if v is None:
        return "null"
    f = float(v)
    return "nan" if f != f else repr(f)


@lru_cache(maxsize=1)
def code_hash() -> str:
    """SHA-256 over every `.py` file under `nn/` and `loader/`, path and bytes.

    The containers have the source but no `.git`, so this is the code version
    that is always available. Paths are relative and sorted, so the hash does
    not depend on where the package is installed. Tests are excluded: they
    cannot change a stored value.
    """
    h = hashlib.sha256()
    for d in _CODE_DIRS:
        for p in sorted((_PACKAGE_ROOT / d).rglob("*.py")):
            if "__pycache__" in p.parts:
                continue
            h.update(p.relative_to(_PACKAGE_ROOT).as_posix().encode())
            h.update(b"\0")
            h.update(p.read_bytes())
            h.update(b"\0")
    return h.hexdigest()


#: Where the loader image keeps the commit it was built from (G45). The image
#: has neither `.git` nor a `git` binary, so `python/Dockerfile` resolves HEAD
#: at build time, from the `.git` compose passes as a second build context,
#: and writes it here. A build from uncommitted changes records the commit it
#: started from; `code_hash` is what pins the exact code.
BAKED_SHA_FILE = _PACKAGE_ROOT / "GIT_SHA"

_SHA_RE = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")


def _as_sha(text: str) -> str | None:
    text = text.strip()
    return text[:40] if _SHA_RE.fullmatch(text) else None


def head_sha(gitdir: Path) -> str | None:
    """The commit HEAD names, read from a `.git` directory's files alone.

    Covers a detached HEAD, a branch's loose ref and a ref only in
    `packed-refs`. None when the directory is missing or says nothing usable:
    the build that calls this must not fail for want of a sha.
    """
    try:
        head = (gitdir / "HEAD").read_text(encoding="utf-8").strip()
        if not head.startswith("ref:"):
            return _as_sha(head)
        ref = head[len("ref:"):].strip()
        loose = gitdir / ref
        if loose.is_file():
            return _as_sha(loose.read_text(encoding="utf-8"))
        packed = gitdir / "packed-refs"
        if packed.is_file():
            for line in packed.read_text(encoding="utf-8").splitlines():
                parts = line.split()
                if len(parts) == 2 and parts[1] == ref and not line.startswith(("#", "^")):
                    return _as_sha(parts[0])
    except (OSError, UnicodeDecodeError):
        return None
    return None


@lru_cache(maxsize=1)
def git_sha() -> str | None:
    """The commit this code came from: `$GIT_SHA`, else the sha baked into the
    image (`BAKED_SHA_FILE`), else the checkout's HEAD, else None."""
    env = os.environ.get("GIT_SHA", "").strip()
    if env:
        return env[:40]
    with contextlib.suppress(OSError, UnicodeDecodeError):
        baked = _as_sha(BAKED_SHA_FILE.read_text(encoding="utf-8"))
        if baked:
            return baked
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=_PACKAGE_ROOT, capture_output=True,
            text=True, timeout=5, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    sha = out.stdout.strip()
    return sha[:40] if out.returncode == 0 and sha else None


def lib_versions() -> dict[str, str]:
    """The versions §6.1 asks a run to record: Python and the numeric stack."""
    out = {"python": platform.python_version(), "numpy": np.__version__}
    try:
        import torch  # noqa: PLC0415 - optional at import time, required to train
        out["torch"] = torch.__version__
    except ImportError:
        pass
    try:
        import psycopg  # noqa: PLC0415
        out["psycopg"] = psycopg.__version__
    except ImportError:
        pass
    # The tree engines' libraries (B9), when installed.
    for name in ("xgboost", "lightgbm"):
        with contextlib.suppress(ImportError):
            out[name] = __import__(name).__version__
    return out


def series_hash(pairs: Iterable[tuple[date, Any]]) -> str:
    """Hash of a series as `date,value` lines, in date order.

    `value` may be a float or a tuple of floats (an OHLCV bar)."""
    lines = []
    for d, v in sorted(pairs, key=lambda p: p[0]):
        vals = v if isinstance(v, (tuple, list)) else (v,)
        lines.append(d.isoformat() + "," + ",".join(_num(x) for x in vals))
    return _sha("\n".join(lines))


def matrix_hash(dates: Sequence[date], names: Sequence[str], values: np.ndarray) -> str:
    """Hash of a feature matrix: its column names, its dates and its float32 values."""
    h = hashlib.sha256()
    h.update(json.dumps(list(names)).encode())
    h.update(b"\0")
    h.update("\n".join(d.isoformat() for d in dates).encode())
    h.update(b"\0")
    arr = np.ascontiguousarray(values, dtype="<f4")
    if np.isnan(arr).any():
        arr = arr.copy()
        # One fixed NaN, so NaN payload bits cannot change the hash.
        arr[np.isnan(arr)] = np.float32("nan")
    h.update(arr.tobytes())
    return h.hexdigest()


def combine(parts: Mapping[str, str]) -> str:
    """One id for several part hashes; order-independent by key."""
    return _sha(json.dumps(dict(parts), sort_keys=True))


@dataclass
class InputLog:
    """Every candle series a feature build read, with a hash of what it read.

    Keyed by (symbol, source, timeframe, field): the same close series read
    for three computed columns is one input, not three. A series read twice
    with different values inside one build is impossible here, because a build
    reads in one transaction; if it ever happened the later read wins and its
    hash is what was used.
    """

    entries: dict[tuple[str, str, str, str], dict[str, Any]] = field(default_factory=dict)

    def record(self, symbol: str, source: str, timeframe: str, field_name: str,
               pairs: Mapping[date, Any]) -> None:
        days = sorted(pairs)
        self.entries[(symbol, source, timeframe, field_name)] = {
            "symbol": symbol, "source": source, "timeframe": timeframe,
            "field": field_name, "bars": len(days),
            "first": days[0].isoformat() if days else None,
            "last": days[-1].isoformat() if days else None,
            "hash": series_hash(pairs.items()),
        }

    def to_list(self) -> list[dict[str, Any]]:
        return [self.entries[k] for k in sorted(self.entries)]


def changed_inputs(then: Sequence[Mapping[str, Any]],
                   now: Sequence[Mapping[str, Any]]) -> list[str]:
    """Which input series differ between two builds' `inputs_json`, as readable lines."""
    def key(e: Mapping[str, Any]) -> tuple:
        return (e["symbol"], e["source"], e["timeframe"], e["field"])

    a = {key(e): e for e in then}
    b = {key(e): e for e in now}
    out: list[str] = []
    for k in sorted(set(a) | set(b)):
        name = f"{k[0]} ({k[1]}, {k[2]}) {k[3]}"
        if k not in b:
            out.append(f"{name}: no longer read")
        elif k not in a:
            out.append(f"{name}: newly read")
        elif a[k]["hash"] != b[k]["hash"]:
            out.append(
                f"{name}: values changed ({a[k]['bars']} bars {a[k]['first']}..{a[k]['last']}"
                f" then, {b[k]['bars']} bars {b[k]['first']}..{b[k]['last']} now)"
            )
    return out


def main(argv: Sequence[str] | None = None) -> int:
    """`python -m nn.lineage --head DIR`: print the commit HEAD names in the
    `.git` directory DIR, or nothing. Always exits 0, so a build without a
    usable `.git` still succeeds and records no sha (`python/Dockerfile`)."""
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2 or args[0] != "--head":
        print("usage: python -m nn.lineage --head GITDIR", file=sys.stderr)
        return 2
    sha = head_sha(Path(args[1]))
    if sha:
        print(sha)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
