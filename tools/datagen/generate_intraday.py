#!/usr/bin/env python3
"""Generate intraday test candles (M30, H1, ...) from a daily OHLCV CSV.

A standalone data-generation tool. It reads one CSV and writes another: no
database, no network, no imports from the backtesting app, nothing but the
Python standard library. Nothing in the app imports it either. It is a
stopgap for having no intraday feed, so intraday code paths have something
to run on.

The generated bars are FICTION. Use them to exercise the pipeline and shake
out intraday bugs, never to conclude anything about how a strategy would
behave on real intraday data.

    python generate_intraday.py ../../test-data/SYNTH_daily.csv -t M30
    python generate_intraday.py input/AAPL_daily.csv -t H1 --session 24h
    python generate_intraday.py input/SPY_daily.csv -t M30 --since 2020-01-01

Output lands in ./output/ beside this script (override with -o), as a
`Date,Open,High,Low,Close,Volume` CSV with `YYYY-MM-DD HH:MM:SS` timestamps.
That is the shape the app's importer reads, so a generated file can go in
through the normal path whenever you want it to:

    curl -F file=@output/AAPL_M30.csv -F symbol=AAPL -F type=STOCK \
         -F timeframe=M30 -F source=synthetic http://localhost:8001/api/imports

Importing under a distinct `source` name is worth the keystrokes: it keeps
invented bars from mixing with real ones in the candles table.

WHAT MAKES THE OUTPUT USABLE rather than noise: aggregating it back up to D1
reproduces the source file exactly. Per day, the first open is the daily
open, the last close is the daily close, the highest high is the daily high,
the lowest low is the daily low, and the volumes sum to the daily volume. So
a D1 backtest and an M30 backtest over the same range see the same market;
the M30 one just also sees an invented path between those anchors. The tool
re-checks that property against the bytes it actually wrote, on every run.

How a day gets filled in:

  1. Lay one slot per bar across the session (`session_slots`).
  2. Walk a Brownian *bridge* from the daily open to the daily close, so the
     path wanders but is pinned at both ends. Amplitude scales off the daily
     range; anything overshooting is clamped into [low, high].
  3. Each bar takes a path segment as its open/close, plus a random wick.
     Then the bar that came closest to the daily high is forced to *be* the
     high (same for the low). Forcing is what makes the round-trip exact, and
     the clamping in step 2 is what keeps it legal: no other bar can poke
     above the high that is about to be assigned.
  4. Split the daily volume on a U-shaped profile (busy at the open and the
     close, quiet midday) by largest remainder, so the parts sum to the whole.

Every random draw comes from a seed derived from (seed, timeframe, day), so
runs are reproducible and a given day's shape does not shift when you change
the date range around it.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import math
import random
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

# (timestamp, open, high, low, close, volume). Timestamps stay naive
# throughout; the importer reads a bare `YYYY-MM-DD HH:MM:SS` as UTC.
Bar = tuple[datetime, float, float, float, float, float]

HEADER = ["Date", "Open", "High", "Low", "Close", "Volume"]

# Generated files go here, beside this script -- deliberately not the app's
# test-data/ directory, so invented candles never sit next to real ones.
OUTPUT_DIR = Path(__file__).resolve().parent / "output"

# Named session windows as (start, end) wall-clock. "nyse" is the US cash
# session; "24h" covers the whole day, for forex and crypto. Neither models a
# real exchange timezone or calendar -- they only decide where inside the day
# the generated bars sit. Only days present in the source file are filled, so
# weekends and holidays never appear.
SESSION_PRESETS: dict[str, tuple[str, str]] = {
    "nyse": ("09:30", "16:00"),
    "24h": ("00:00", "24:00"),
}

# Minutes per bar, for every timeframe finer than D1. D1 and coarser are
# absent on purpose: this only ever generates downward.
TF_MINUTES: dict[str, int] = {
    "M1": 1,
    "M5": 5,
    "M15": 15,
    "M30": 30,
    "H1": 60,
    "H4": 240,
}

# Wick length as a fraction of a bar's own open-to-close span. At 0 every bar
# is a body with no shadows, which reads as obviously fake.
DEFAULT_WICK = 0.35

# Depth of the U in the volume profile. 0 spreads volume flat.
DEFAULT_VOLUME_PROFILE = 0.5

# Decimals kept when writing prices. Prices are clamped into the daily range
# before rounding, and rounding is monotonic, so a rounded high can never
# exceed the rounded daily high: the round-trip survives formatting.
PRICE_DECIMALS = 6


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


def parse_hhmm(value: str) -> int:
    """`"09:30"` -> minutes since midnight. `"24:00"` (1440) is the
    end-of-day sentinel, so a full day is written 00:00-24:00."""
    parts = value.strip().split(":")
    if len(parts) != 2:
        raise ValueError(f"expected HH:MM, got {value!r}")
    hours, minutes = int(parts[0]), int(parts[1])
    total = hours * 60 + minutes
    if not 0 <= total <= 1440 or not 0 <= minutes < 60:
        raise ValueError(f"time out of range: {value!r}")
    return total


def resolve_session(session: str, start: str | None, end: str | None) -> tuple[int, int]:
    """(start, end) minutes since midnight, from a preset plus optional
    explicit overrides."""
    if session not in SESSION_PRESETS:
        known = ", ".join(sorted(SESSION_PRESETS))
        raise ValueError(f"unknown session {session!r}; known: {known}")
    preset_start, preset_end = SESSION_PRESETS[session]
    start_min = parse_hhmm(start or preset_start)
    end_min = parse_hhmm(end or preset_end)
    if end_min <= start_min:
        raise ValueError(f"session end must be after its start: {start_min}..{end_min}")
    return start_min, end_min


def session_slots(day: date, timeframe: str, start_min: int, end_min: int) -> list[datetime]:
    """Bar-open timestamps for one day.

    Slots are anchored to the session start and spaced by the timeframe. When
    the session is not a whole number of bars long, the last bar is SHORT
    rather than running past the close -- H1 over 09:30-16:00 gives seven
    bars, the last covering only 15:30-16:00. That keeps every timestamp
    inside the day it belongs to (so rolling back up to D1 lands on the same
    bucket) and keeps the spacing between consecutive bars uniform (so the
    app's cohesion gap check, which measures spacing within a date, stays
    quiet about the generated file)."""
    minutes = TF_MINUTES[timeframe]
    count = math.ceil((end_min - start_min) / minutes)
    midnight = datetime(day.year, day.month, day.day)
    return [midnight + timedelta(minutes=start_min + i * minutes) for i in range(count)]


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------


def rng_for(seed: int, timeframe: str, day: date) -> random.Random:
    """A per-day RNG. Keyed by the day rather than by position in the series,
    so a bar's shape does not change when the requested range changes, and
    hashed rather than `hash()`ed so it is stable across processes."""
    key = f"{seed}|{timeframe}|{day.isoformat()}".encode()
    return random.Random(int.from_bytes(hashlib.blake2b(key, digest_size=8).digest(), "big"))


def brownian_bridge(rng: random.Random, n: int) -> list[float]:
    """n points of a discrete Brownian bridge, ending pinned at 0.
    Unit-variance steps; the caller scales."""
    steps = [rng.gauss(0.0, 1.0) for _ in range(n)]
    total = sum(steps)
    out: list[float] = []
    running = 0.0
    for i, step in enumerate(steps, start=1):
        running += step
        out.append(running - (i / n) * total)
    return out


def distribute(total: float, weights: list[float]) -> list[float]:
    """Split `total` across `weights` so the parts sum back to the whole.

    An integral total (the usual case -- volume is a count) is split by the
    largest-remainder method: every part is a whole number and the sum is
    exact. A non-integral total is split proportionally with the residual
    parked on the last part."""
    n = len(weights)
    if n == 0:
        return []
    weight_sum = sum(weights)
    if total <= 0 or weight_sum <= 0:
        return [0.0] * n

    raw = [total * w / weight_sum for w in weights]
    if not float(total).is_integer():
        raw[-1] = total - math.fsum(raw[:-1])
        return raw

    floors = [math.floor(x) for x in raw]
    shortfall = int(round(total - sum(floors)))
    ranked = sorted(range(n), key=lambda i: raw[i] - floors[i], reverse=True)
    for i in ranked[:shortfall]:
        floors[i] += 1
    return [float(x) for x in floors]


def volume_weights(rng: random.Random, n: int, profile: float) -> list[float]:
    """U-shaped intraday volume curve with mild jitter: heavy at the open and
    the close, light in the middle."""
    return [
        max(0.01, 1.0 + profile * math.cos(2.0 * math.pi * (i + 0.5) / n)) * rng.uniform(0.85, 1.15)
        for i in range(n)
    ]


def synthesize_day(
    bar: Bar,
    slots: list[datetime],
    rng: random.Random,
    *,
    wick: float = DEFAULT_WICK,
    volume_profile: float = DEFAULT_VOLUME_PROFILE,
) -> list[Bar]:
    """Fill one daily bar with intraday bars, one per slot.

    Exactly (not approximately): first open == daily open, last close ==
    daily close, max high == daily high, min low == daily low, volumes sum to
    the daily volume. Every bar is individually well-formed -- high is the
    largest of the four prices, low the smallest."""
    _, d_open, d_high, d_low, d_close, d_volume = bar
    n = len(slots)
    if n == 0:
        return []
    if n == 1:
        return [(slots[0], d_open, d_high, d_low, d_close, d_volume)]

    # The close-to-close path, pinned at the daily open and close. The bridge
    # amplitude is a heuristic: enough wandering to fill a good part of the
    # day's range without constantly slamming into it. Measured over 300 AAPL
    # days at M30, this divisor puts the close path across ~62% of the daily
    # range with ~6% of closes clamped flat against an extreme; doubling the
    # amplitude buys 10 more points of coverage at 3x the flat spots.
    # Overshoot is clamped and undershoot is repaired by the forced extremes
    # below, so exactness never depends on this constant being right.
    span = d_high - d_low
    amplitude = span / (2.0 * math.sqrt(n))
    drift = (d_close - d_open) / n
    bridge = brownian_bridge(rng, n)

    prices = [d_open]
    for i in range(n):
        raw = d_open + drift * (i + 1) + amplitude * bridge[i]
        prices.append(min(d_high, max(d_low, raw)))
    prices[-1] = d_close  # the bridge ends at zero, but say so exactly

    # Bars, with wicks, all inside [low, high]. The `floor` term keeps flat
    # days from producing zero-length wicks, i.e. a column of dojis.
    floor = span * 0.02
    rows: list[list[float]] = []
    for i in range(n):
        bar_open, bar_close = prices[i], prices[i + 1]
        top, bottom = max(bar_open, bar_close), min(bar_open, bar_close)
        reach = max(top - bottom, floor) * wick
        rows.append(
            [
                bar_open,
                min(d_high, top + reach * rng.random()),
                max(d_low, bottom - reach * rng.random()),
                bar_close,
            ]
        )

    # Pin the daily extremes onto whichever bar came closest, so the high
    # lands where the path actually peaked rather than somewhere arbitrary.
    # Legal on any bar, since every price above is clamped into [low, high].
    rows[max(range(n), key=lambda i: rows[i][1])][1] = d_high
    rows[min(range(n), key=lambda i: rows[i][2])][2] = d_low

    volumes = distribute(d_volume, volume_weights(rng, n, volume_profile))
    return [(slots[i], *rows[i], volumes[i]) for i in range(n)]


def synthesize(
    daily: list[Bar],
    *,
    timeframe: str,
    session_start: int,
    session_end: int,
    seed: int = 42,
    wick: float = DEFAULT_WICK,
    volume_profile: float = DEFAULT_VOLUME_PROFILE,
) -> list[Bar]:
    """Generate `timeframe` bars for a whole series of daily bars."""
    out: list[Bar] = []
    for bar in daily:
        day = bar[0].date()
        slots = session_slots(day, timeframe, session_start, session_end)
        rng = rng_for(seed, timeframe, day)
        out.extend(synthesize_day(bar, slots, rng, wick=wick, volume_profile=volume_profile))
    return out


def verify(daily: list[Bar], generated: list[Bar], *, tol: float = 1e-9) -> list[str]:
    """Roll the generated bars back up per day and compare to the source.

    This is the property the whole tool exists to hold, so it runs on every
    invocation rather than only in tests -- one pass over bars already in
    memory. Returns one line per mismatch; empty means the round-trip is
    exact."""
    by_day: dict[date, list[Bar]] = {}
    for bar in generated:
        by_day.setdefault(bar[0].date(), []).append(bar)

    issues: list[str] = []
    for src in daily:
        group = by_day.get(src[0].date())
        if not group:
            issues.append(f"{src[0].date()}: no bars generated")
            continue
        group.sort(key=lambda b: b[0])
        for name, got, want in (
            ("open", group[0][1], src[1]),
            ("high", max(b[2] for b in group), src[2]),
            ("low", min(b[3] for b in group), src[3]),
            ("close", group[-1][4], src[4]),
            ("volume", math.fsum(b[5] for b in group), src[5]),
        ):
            if abs(got - want) > tol * max(1.0, abs(want)):
                issues.append(f"{src[0].date()}: {name} {got} != daily {want}")
    return issues


# ---------------------------------------------------------------------------
# CSV in / CSV out
# ---------------------------------------------------------------------------


def read_daily(path: Path, since: str | None = None, until: str | None = None) -> list[Bar]:
    """Read a `Date,Open,High,Low,Close,Volume` daily CSV. Rows that do not
    parse are skipped, matching the importer's leniency. `since`/`until` are
    inclusive `YYYY-MM-DD` bounds."""
    out: list[Bar] = []
    with path.open(newline="", encoding="utf-8-sig") as fh:
        for parts in csv.reader(fh):
            if len(parts) < 6:
                continue
            stamp = parts[0].strip()
            if since and stamp[:10] < since:
                continue
            if until and stamp[:10] > until:
                continue
            try:
                ts = datetime.strptime(stamp[:10], "%Y-%m-%d")
                values = [float(p) for p in parts[1:6]]
            except ValueError:
                continue  # header line, or a malformed row
            out.append((ts, *values))
    out.sort(key=lambda b: b[0])
    return out


def format_bar(bar: Bar) -> list[str]:
    ts, o, h, low, c, v = bar
    prices = [f"{p:.{PRICE_DECIMALS}f}".rstrip("0").rstrip(".") for p in (o, h, low, c)]
    volume = str(int(v)) if float(v).is_integer() else f"{v:.6f}".rstrip("0").rstrip(".")
    return [ts.strftime("%Y-%m-%d %H:%M:%S"), *prices, volume]


def write_csv(path: Path, bars: list[Bar]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(HEADER)
        writer.writerows(format_bar(b) for b in bars)


def read_written(path: Path) -> list[Bar]:
    """Read back a generated intraday CSV, timestamps included."""
    out: list[Bar] = []
    with path.open(newline="", encoding="utf-8") as fh:
        for parts in csv.reader(fh):
            if len(parts) < 6:
                continue
            try:
                ts = datetime.strptime(parts[0].strip(), "%Y-%m-%d %H:%M:%S")
                values = [float(p) for p in parts[1:6]]
            except ValueError:
                continue  # header
            out.append((ts, *values))
    return out


def default_output(source: Path, timeframe: str, out_dir: Path = OUTPUT_DIR) -> Path:
    """`AAPL_daily.csv` + M30 -> `<out_dir>/AAPL_M30.csv`. The default
    directory is this tool's own output/, never the input's directory."""
    stem = source.stem
    for suffix in ("_daily", "_D1", "_d1"):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    return out_dir / f"{stem}_{timeframe}.csv"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="generate_intraday.py",
        description="Generate intraday test candles (M30, H1, ...) from a daily CSV.",
    )
    parser.add_argument("source", type=Path, help="daily CSV: Date,Open,High,Low,Close,Volume")
    parser.add_argument(
        "-t",
        "--timeframe",
        default="M30",
        choices=sorted(TF_MINUTES, key=lambda tf: TF_MINUTES[tf]),
        help="target timeframe (default: M30)",
    )
    parser.add_argument(
        "-o",
        "--out",
        type=Path,
        default=None,
        help="output CSV path (default: output/<SYMBOL>_<TF>.csv beside this script)",
    )
    parser.add_argument(
        "--session",
        default="nyse",
        choices=sorted(SESSION_PRESETS),
        help="session window the generated bars fill (default: nyse, 09:30-16:00)",
    )
    parser.add_argument("--session-start", default=None, help="override session start, HH:MM")
    parser.add_argument("--session-end", default=None, help="override session end, HH:MM")
    parser.add_argument("--since", default=None, help="skip days before this YYYY-MM-DD")
    parser.add_argument("--until", default=None, help="skip days after this YYYY-MM-DD")
    parser.add_argument("--seed", type=int, default=42, help="RNG seed (default: 42)")
    parser.add_argument(
        "--wick",
        type=float,
        default=DEFAULT_WICK,
        help=f"wick length as a fraction of each bar's body (default: {DEFAULT_WICK})",
    )
    parser.add_argument(
        "--volume-profile",
        type=float,
        default=DEFAULT_VOLUME_PROFILE,
        help=f"depth of the U-shaped volume curve, 0 = flat (default: {DEFAULT_VOLUME_PROFILE})",
    )
    args = parser.parse_args(argv)

    if not args.source.is_file():
        parser.error(f"no such file: {args.source}")
    try:
        start_min, end_min = resolve_session(args.session, args.session_start, args.session_end)
    except ValueError as exc:
        parser.error(str(exc))

    daily = read_daily(args.source, args.since, args.until)
    if not daily:
        print(f"No daily rows read from {args.source}", file=sys.stderr)
        return 1

    bars = synthesize(
        daily,
        timeframe=args.timeframe,
        session_start=start_min,
        session_end=end_min,
        seed=args.seed,
        wick=args.wick,
        volume_profile=args.volume_profile,
    )
    out_path = args.out or default_output(args.source, args.timeframe)
    write_csv(out_path, bars)

    # Verify the bytes actually written, not the in-memory bars: the check is
    # only worth something if it covers the rounding done on the way out.
    issues = verify(daily, read_written(out_path))
    per_day = math.ceil((end_min - start_min) / TF_MINUTES[args.timeframe])
    print(
        f"{out_path}: {len(bars)} {args.timeframe} bars from {len(daily)} daily bars "
        f"({per_day}/day, {daily[0][0].date()}..{daily[-1][0].date()})"
    )
    if issues:
        print(f"Round-trip to D1 FAILED on {len(issues)} check(s):", file=sys.stderr)
        for line in issues[:10]:
            print(f"  {line}", file=sys.stderr)
        return 1
    print(f"Round-trip to D1: OK ({len(daily)} days match open/high/low/close/volume)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
