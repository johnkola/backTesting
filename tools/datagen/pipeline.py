#!/usr/bin/env python3
"""Re-runnable pipeline: generate intraday CSVs, import them, train, backtest.

The point of this script is that you can run it again. Every step is either
idempotent or explicitly forced, and the two places where a second run behaves
differently from the first are handled rather than left to surprise you (see
RE-RUN NOTES below).

    python pipeline.py                                  # generate only
    python pipeline.py --load                           # + import into the app
    python pipeline.py --load --run sma-crossover       # + backtest each series
    python pipeline.py --load --train --run nn-feedforward
    python pipeline.py -s AAPL -t M30 --seed 7 --load --force

Defaults cover every `*_daily.csv` in ../../test-data at M30 and H1, imported
under the source name `synthetic`. Standard library only; the app is reached
over HTTP, never by importing its code.

RE-RUN NOTES -- what changes on the second run:

  * Same seed and settings produce a byte-identical CSV, so the importer sees
    the same hash and reports `skipped`. Re-running costs nothing and changes
    nothing. This is the normal case.

  * A CHANGED file (different --seed, --session, --wick, ...) collides with
    the archived slice from last time and the whole upload is rejected with
    409 `conflict` unless you pass --force. The script says so explicitly
    rather than just printing the error.

  * --force overwrites candles in chunks that TimescaleDB has already
    compressed. That works on this stack (verified against yearly chunks from
    2020-2023), but if your TimescaleDB ever refuses it, the loader answers
    409 `compressed_chunk` with a decompress_chunk hint -- the script
    surfaces that hint instead of swallowing it.

  * NN TRAINING IS KEY-SENSITIVE. The model cache key includes a fingerprint
    of the exact candle window the caller asked for. The engine derives that
    window from Ta4j BAR END times -- for a 09:30 M30 bar it sends
    since=10:00 -- so training over the whole series (the obvious thing, and
    what a bare curl does) lands under a different key and the backtest then
    fails with 409 "no cached model" even though a perfectly good model for
    that symbol is sitting on disk. This script computes the engine's bounds
    from the generated bars and trains under the key `run` will actually look
    for.

  * NN LABEL THRESHOLDS ARE TIMEFRAME-SENSITIVE. The default +/-2% over 5
    bars means "2% in 5 days" on D1, but "2% in 2.5 hours" on M30, which
    almost never happens: ~99% of bars label HOLD and the network scores 98%
    "accuracy" by always predicting HOLD. --train scales the threshold by
    1/sqrt(bars per day), which reproduces the D1 label balance at any
    timeframe (M30: 23/53/23 BUY/HOLD/SELL, same as D1 at 2%).
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import urllib.error
import urllib.request
import uuid
from datetime import timedelta
from pathlib import Path

from generate_intraday import (
    TF_MINUTES,
    default_output,
    read_daily,
    resolve_session,
    synthesize,
    verify,
    write_csv,
)

HERE = Path(__file__).resolve().parent
DEFAULT_INPUT_DIR = HERE.parent.parent / "test-data"

# D1 reference: the app's default label threshold is +/-2% over 5 bars, which
# is calibrated for daily bars. Volatility scales with the square root of the
# horizon, so the equivalent threshold at a finer timeframe is this divided by
# sqrt(bars per day) -- matched against measured 5-bar return quantiles on the
# generated data (M30 p95 1.23% vs D1 4.76%, a ratio of 0.26 ~= 1/sqrt(13)).
D1_THRESHOLD = 0.02


# ---------------------------------------------------------------------------
# HTTP (stdlib only -- no requests, no curl)
# ---------------------------------------------------------------------------


def post_json(url: str, payload: dict, timeout: int = 900) -> tuple[int, dict]:
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    return _send(req, timeout)


def post_multipart(url: str, fields: dict[str, str], file_path: Path, timeout: int = 900):
    boundary = uuid.uuid4().hex
    parts = bytearray()
    for name, value in fields.items():
        parts += (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'
        ).encode()
    parts += (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{file_path.name}"\r\n'
        f"Content-Type: text/csv\r\n\r\n"
    ).encode()
    parts += file_path.read_bytes() + b"\r\n"
    parts += f"--{boundary}--\r\n".encode()

    req = urllib.request.Request(
        url,
        data=bytes(parts),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    return _send(req, timeout)


def _send(req, timeout: int) -> tuple[int, dict]:
    """Returns (status, parsed body). HTTP errors come back as values rather
    than exceptions -- a 409 is an expected outcome here, not a crash."""
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            return exc.code, json.loads(raw or b"{}")
        except json.JSONDecodeError:
            return exc.code, {"error": raw.decode(errors="replace")[:500]}
    except urllib.error.URLError as exc:
        return 0, {"error": f"cannot reach {req.full_url}: {exc.reason}"}


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------


def step_generate(source_csv: Path, timeframe: str, args) -> tuple[Path, int, list[str]]:
    """Generate one CSV. Deterministic: same inputs, same bytes."""
    start_min, end_min = resolve_session(args.session, None, None)
    daily = read_daily(source_csv, args.since, args.until)
    if not daily:
        return Path(), 0, [f"no daily rows in {source_csv.name}"]

    bars = synthesize(
        daily,
        timeframe=timeframe,
        session_start=start_min,
        session_end=end_min,
        seed=args.seed,
        wick=args.wick,
        volume_profile=args.volume_profile,
    )
    out = default_output(source_csv, timeframe)
    write_csv(out, bars)
    return out, len(bars), verify(daily, bars)


def step_import(api: str, csv_path: Path, symbol: str, timeframe: str, args):
    """Upload one CSV. Returns (label, detail) for the summary line."""
    fields = {
        "symbol": symbol,
        "type": args.type,
        "timeframe": timeframe,
        "source": args.source,
    }
    if args.force:
        fields["force"] = "true"

    status, body = post_multipart(f"{api}/api/imports", fields, csv_path)

    if status == 200:
        outcomes = [i.get("status", "?") for i in body.get("imports", [])]
        tally = {o: outcomes.count(o) for o in sorted(set(outcomes))}
        cohesion = body.get("cohesion", {})
        detail = ", ".join(f"{n} {o}" for o, n in tally.items())
        if not cohesion.get("ok", True):
            detail += f" | cohesion: {cohesion.get('totalIssues')} issues"
        return "ok", detail

    error = str(body.get("error") or body.get("message") or body)
    if status == 409 and "conflict" in error.lower():
        return "conflict", "content changed since last import -- re-run with --force"
    if status == 409 and "compress" in error.lower():
        return "compressed", f"{error[:200]} (decompress the chunk, then retry)"
    return "failed", f"HTTP {status}: {error[:200]}"


def engine_window(csv_path: Path, timeframe: str) -> tuple[str, str]:
    """The candle window the ENGINE will ask the loader for.

    Ta4j bar end times, not the stored bar-open timestamps: `since` is the
    first bar's end and `until` is the last bar's end plus a millisecond. Get
    this wrong and the NN model lands under a cache key `run` never looks up.
    """
    from generate_intraday import read_written

    bars = read_written(csv_path)
    step = timedelta(minutes=TF_MINUTES[timeframe])
    first_end = bars[0][0] + step
    last_end = bars[-1][0] + step
    return (
        first_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        (last_end + timedelta(milliseconds=1)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")[:-4] + "Z",
    )


def scaled_threshold(timeframe: str, session_minutes: int) -> float:
    """The D1 label threshold rescaled to this timeframe (see module docstring)."""
    bars_per_day = math.ceil(session_minutes / TF_MINUTES[timeframe])
    return D1_THRESHOLD / math.sqrt(bars_per_day)


def step_train(api_loader: str, csv_path: Path, symbol: str, timeframe: str, args):
    since, until = engine_window(csv_path, timeframe)
    start_min, end_min = resolve_session(args.session, None, None)
    thr = round(scaled_threshold(timeframe, end_min - start_min), 5)

    payload = {
        "symbol": symbol,
        "source": args.source,
        "timeframe": timeframe,
        "mode": "force" if args.retrain else "auto",
        "since": since,
        "until": until,
        "buy_threshold": thr,
        "sell_threshold": -thr,
    }
    status, body = post_json(f"{api_loader}/api/nn/train", payload)
    if status != 200:
        return "failed", f"HTTP {status}: {str(body.get('error', body))[:200]}", None

    labels = body.get("labelDistribution", {})
    total = sum(labels.values()) or 1
    mix = "/".join(f"{100 * labels.get(k, 0) / total:.0f}" for k in ("BUY", "HOLD", "SELL"))
    detail = (
        f"thr +/-{thr:.4f} | labels {mix} BUY/HOLD/SELL | "
        f"val acc {body.get('finalValAcc', 0):.3f} | v{body.get('versionId')}"
    )
    return "ok", detail, thr


def step_run(api: str, symbol: str, timeframe: str, strategy: str, args, threshold=None):
    payload = {
        "instrument": symbol,
        "strategy": strategy,
        "timeframe": timeframe,
        "source": args.source,
    }
    if threshold is not None:
        # Must match what the model was trained with, or the cache key differs.
        payload["parameters"] = {
            "buyThreshold": str(threshold),
            "sellThreshold": str(-threshold),
        }

    status, body = post_json(f"{api}/api/run", payload)
    if status == 409:
        return "no-model", "no cached model for this key -- add --train"
    if status != 200:
        return "failed", f"HTTP {status}: {str(body.get('error', body))[:200]}"

    m = body.get("metrics", {})
    return "ok", (
        f"{m.get('totalTrades', 0)} trades | "
        f"return {m.get('totalReturnPct', 0):+.2f}% | "
        f"B&H {m.get('buyAndHoldReturnPct', 0):+.2f}% | "
        f"maxDD {m.get('maxDrawdownPct', 0):.1f}%"
    )


# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="pipeline.py",
        description="Generate intraday test data, optionally import it, train and backtest.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Re-running is safe: identical output is skipped by the importer. "
        "Change --seed and you need --force.",
    )
    p.add_argument("-s", "--symbols", default=None,
                   help="comma-separated symbols (default: every *_daily.csv in test-data)")
    p.add_argument("-t", "--timeframes", default="M30,H1", help="comma-separated (default: M30,H1)")
    p.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT_DIR,
                   help=f"where the daily CSVs live (default: {DEFAULT_INPUT_DIR})")
    p.add_argument("--session", default="nyse", choices=["nyse", "24h"])
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--wick", type=float, default=0.35)
    p.add_argument("--volume-profile", type=float, default=0.5)
    p.add_argument("--since", default=None, help="skip source days before YYYY-MM-DD")
    p.add_argument("--until", default=None, help="skip source days after YYYY-MM-DD")

    p.add_argument("--load", action="store_true", help="import the generated CSVs into the app")
    p.add_argument("--force", action="store_true",
                   help="overwrite existing imports (needed after changing --seed etc.)")
    p.add_argument("--source", default="synthetic", help="data-source name to import under")
    p.add_argument("--type", default="STOCK", help="instrument type (default: STOCK)")

    p.add_argument("--train", action="store_true",
                   help="train nn-feedforward at the key `run` uses, with a scaled threshold")
    p.add_argument("--retrain", action="store_true", help="force a fresh train (mode=force)")
    p.add_argument("--run", default=None, metavar="STRATEGY",
                   help="backtest each series with this strategy after loading")

    p.add_argument("--api", default="http://localhost:8001", help="API origin (default: :8001)")
    p.add_argument("--loader", default="http://localhost:8003",
                   help="loader origin, for training (default: :8003)")
    args = p.parse_args(argv)

    if not args.input_dir.is_dir():
        p.error(f"no such directory: {args.input_dir}")
    timeframes = [t.strip().upper() for t in args.timeframes.split(",") if t.strip()]
    for tf in timeframes:
        if tf not in TF_MINUTES:
            p.error(f"unknown timeframe {tf}; pick from {', '.join(sorted(TF_MINUTES))}")

    if args.symbols:
        wanted = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
        sources = []
        for sym in wanted:
            matches = sorted(args.input_dir.glob(f"{sym}_*.csv"))
            matches = [m for m in matches if "_daily" in m.stem or m.stem.endswith("_D1")]
            if not matches:
                print(f"! no daily CSV for {sym} in {args.input_dir}", file=sys.stderr)
            sources.extend(matches[:1])
    else:
        sources = sorted(args.input_dir.glob("*_daily.csv"))

    if not sources:
        print("Nothing to do: no daily CSVs matched.", file=sys.stderr)
        return 1

    failures = 0
    for src in sources:
        symbol = src.stem.replace("_daily", "").replace("_D1", "").upper()
        for tf in timeframes:
            print(f"\n=== {symbol} {tf} " + "=" * (48 - len(symbol) - len(tf)))

            out, count, issues = step_generate(src, tf, args)
            if issues:
                failures += 1
                print(f"  generate  FAILED  round-trip broke on {len(issues)} check(s)")
                for line in issues[:3]:
                    print(f"            {line}")
                continue
            print(f"  generate  ok      {count:,} bars -> {out.relative_to(HERE)}")

            if not args.load:
                continue

            label, detail = step_import(args.api, out, symbol, tf, args)
            print(f"  import    {label:<7} {detail}")
            if label != "ok":
                failures += 1
                continue

            threshold = None
            if args.train:
                label, detail, threshold = step_train(args.loader, out, symbol, tf, args)
                print(f"  train     {label:<7} {detail}")
                if label != "ok":
                    failures += 1
                    continue

            if args.run:
                label, detail = step_run(args.api, symbol, tf, args.run, args, threshold)
                print(f"  run       {label:<7} {detail}")
                if label != "ok":
                    failures += 1

    print(f"\n{'FAILED: ' + str(failures) + ' step(s)' if failures else 'All steps OK'}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
