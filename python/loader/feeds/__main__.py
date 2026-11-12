"""Command line for the data feeds (run it in the loader container).

    python -m loader.feeds list                        what is fed, from where
    python -m loader.feeds refresh --dry-run           what a refresh would do; writes nothing
    python -m loader.feeds refresh                     refresh every series, then rebuild features
    python -m loader.feeds refresh --feed QQQ/yahoo-adj --no-rebuild
    python -m loader.feeds apply QQQ/yahoo-adj         accept a held series' changes

A real run is recorded in `feed_runs` like a scheduled one (trigger `cli`).
"""

from __future__ import annotations

import argparse
import json
import sys

from loader.feeds import registry, runner


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m loader.feeds",
                                description="Refresh stored series from their providers.")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="list the fed series")
    r = sub.add_parser("refresh", help="refresh series from their providers")
    r.add_argument("--feed", action="append", metavar="SYMBOL/SOURCE",
                   help="only this series (repeatable); default every series")
    r.add_argument("--dry-run", action="store_true", help="fetch and compare, write nothing")
    r.add_argument("--no-rebuild", action="store_true",
                   help="do not rebuild the feature table afterwards")
    r.add_argument("--json", action="store_true", help="print the full result as JSON")
    a = sub.add_parser("apply", help="accept a held series' changes (full re-download, force)")
    a.add_argument("feed", metavar="SYMBOL/SOURCE")
    args = p.parse_args(argv)

    if args.cmd == "list":
        for f in registry.FEEDS:
            print(f"{f.key:22} {f.group:8} {f.provider:10} {f.ref}")
        for (sym, src), how in sorted(registry.MANUAL.items()):
            print(f"{sym + '/' + src:22} manual   {how}")
        return 0

    keys = args.feed if args.cmd == "refresh" else None
    apply_key = args.feed if args.cmd == "apply" else None
    dry = args.cmd == "refresh" and args.dry_run
    try:
        registry.select(keys) if keys else None
        if apply_key and apply_key not in registry.BY_KEY:
            raise KeyError(apply_key)
    except KeyError as exc:
        print(f"unknown feed {exc.args[0]} (see `python -m loader.feeds list`)", file=sys.stderr)
        return 2

    from loader.db import pool

    run_id = None
    if not dry:
        with pool().connection() as conn:
            run_id = runner.record_start(conn, "cli", keys, apply_key)
    try:
        result = runner.run("cli", keys, apply_key=apply_key, dry_run=dry,
                            rebuild=not (args.cmd == "refresh" and args.no_rebuild))
    except Exception as exc:
        if run_id is not None:
            with pool().connection() as conn:
                runner.record_end(conn, run_id, "failed", None, f"{type(exc).__name__}: {exc}")
        print(f"feeds: {exc}", file=sys.stderr)
        return 1
    errors = [r for r in result["feeds"] if r.get("status") == "error"]
    if run_id is not None:
        with pool().connection() as conn:
            runner.record_end(conn, run_id, "done-with-errors" if errors else "done", result, None)
        print(f"recorded feed_runs id={run_id}")
    if args.cmd == "refresh" and args.json:
        print(json.dumps(result, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
