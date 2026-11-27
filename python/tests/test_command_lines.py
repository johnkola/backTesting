"""The Python command lines: what each takes, what it says and what it exits with.

0 it ran; 1 it ran and found something wrong (a model that differs, a feed
that failed, a repair that was not applied); 2 it was refused, or was asked
for nothing it does. argparse's own errors are 2 as well. The work behind each
has its own tests; here it is stubbed, so what is checked is the command line:
which function each option reaches with which arguments, and the exit code.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from .dbfixtures import requires_db


class FakeConn:
    """A connection, a pool and a cursor at once: the stubs never query it."""

    closed = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def connection(self):
        return self

    def cursor(self):
        return self

    def commit(self):
        pass

    def close(self):
        self.closed = True


@pytest.fixture
def conn(monkeypatch):
    """Every way a command line connects, answered by one fake connection."""
    from loader import db
    from nn import feature_build, walkforward

    c = FakeConn()
    monkeypatch.setattr(feature_build, "connect", lambda: c)
    monkeypatch.setattr(walkforward, "connect", lambda: c)
    monkeypatch.setattr(db, "pool", lambda: c)
    monkeypatch.setattr(db, "close_pool", lambda: None)
    return c


def usage_error(main, *argv: str) -> None:
    with pytest.raises(SystemExit) as exc:
        main(list(argv))
    assert exc.value.code == 2


# --------------------------------------------------------------------------
# nn.staleness
# --------------------------------------------------------------------------


def test_staleness_prints_the_stages_or_backfills_spans(conn, monkeypatch, capsys):
    from nn import staleness

    asked = []
    monkeypatch.setattr(staleness, "status", lambda cur, tf: asked.append(tf) or {
        "data": {"state": "passed"}, "features": {"state": "stale"}})
    assert staleness.main(["--timeframe", "H1"]) == 0
    assert asked == ["H1"]
    assert json.loads(capsys.readouterr().out) == {
        "data": {"state": "passed"}, "features": {"state": "stale"}}

    dry = []
    monkeypatch.setattr(staleness, "backfill", lambda cur, c, dry_run: dry.append(dry_run) or 3)
    assert staleness.main(["--backfill", "--dry-run"]) == 0
    assert capsys.readouterr().out == "3 run(s) would get a span\n"
    assert staleness.main(["--backfill"]) == 0
    assert capsys.readouterr().out == "3 run(s) given a span\n"
    assert dry == [True, False]
    usage_error(staleness.main, "--timeframe")


# --------------------------------------------------------------------------
# nn.recalculate
# --------------------------------------------------------------------------


def test_recalculate_passes_its_options_and_says_what_it_refused(conn, monkeypatch, capsys):
    from nn import recalculate

    usage_error(recalculate.main)                    # --run is required
    usage_error(recalculate.main, "--run", "seven")

    calls, shown = [], []
    monkeypatch.setattr(recalculate, "recalculate", lambda c, run, sid, dry: calls.append(
        (c, run, sid, dry)) or "outcome")
    monkeypatch.setattr(recalculate, "print_outcome", shown.append)
    assert recalculate.main(["--run", "7", "--strategy-id", "3", "--dry-run"]) == 0
    assert calls == [(conn, 7, 3, True)] and shown == ["outcome"] and conn.closed

    def refuse(c, run, sid, dry):
        raise recalculate.Refused(["run 7 scored the holdout", "and another reason"])

    monkeypatch.setattr(recalculate, "recalculate", refuse)
    assert recalculate.main(["--run", "7"]) == 2
    assert capsys.readouterr().out == ("run 7 refused:\n  - run 7 scored the holdout\n"
                                       "  - and another reason\n")


@requires_db
def test_recalculate_refuses_a_run_that_does_not_exist(capsys):
    from nn import recalculate

    assert recalculate.main(["--run", "2000000000"]) == 2
    assert capsys.readouterr().out == ("run 2000000000 refused:\n"
                                       "  - no walk-forward run with id 2000000000\n")


# --------------------------------------------------------------------------
# nn.fold_models
# --------------------------------------------------------------------------


def test_fold_models_lists_prunes_and_checks(conn, monkeypatch, capsys):
    from nn import fold_models

    # Asked for nothing it does: the help, and 2. --dry-run alone is nothing too.
    assert fold_models.main([]) == 2
    assert "--check RUN_ID" in capsys.readouterr().out
    assert fold_models.main(["--dry-run"]) == 2
    capsys.readouterr()

    monkeypatch.setattr(fold_models, "rows", lambda cur, run: [
        {"fold": 1, "engine": "xgboost", "bytes": 2048, "file_hash": "ab" * 32,
         "path": f"{run}/1-xgboost.json"}])
    assert fold_models.main(["--list", "9"]) == 0
    assert capsys.readouterr().out == (
        "fold  1  xgboost        2048 bytes  abababababab  9/1-xgboost.json\n")

    pruned = []
    monkeypatch.setattr(fold_models, "prune", lambda cur, dry_run: pruned.append(dry_run) or [
        Path("runs/41"), Path("runs/42")][: 2 if dry_run else 1])
    assert fold_models.main(["--prune", "--dry-run"]) == 0
    assert capsys.readouterr().out == ("would remove runs/41\nwould remove runs/42\n"
                                       "2 run directories to remove\n")
    assert fold_models.main(["--prune"]) == 0
    assert capsys.readouterr().out == "removed runs/41\n1 run directory removed\n"
    assert pruned == [True, False]

    def fold(n, ok, worst=1e-7, problem=None):
        return SimpleNamespace(fold=n, engine="mlp", bars=250, ok=ok, worst=worst, problem=problem)

    answers = iter([([], "there is no run 9"),
                    ([fold(1, True), fold(2, False, worst=0.31)], None),
                    ([fold(1, True), fold(2, True)], None),
                    ([fold(1, False, problem="its file is gone")], None)])
    monkeypatch.setattr(fold_models, "check", lambda cur, run: next(answers))
    assert fold_models.main(["--check", "9"]) == 2
    assert capsys.readouterr().out == "not checked: there is no run 9\n"
    assert fold_models.main(["--check", "9"]) == 1
    out = capsys.readouterr().out
    assert "fold  2  mlp         250 bars  largest difference 0.31  DIFFERENT" in out
    assert out.endswith("1 of 2 kept models give the signals their fold stored\n")
    assert fold_models.main(["--check", "9"]) == 0
    assert fold_models.main(["--check", "9"]) == 1
    assert "fold  1  mlp         250 bars  NOT CHECKED: its file is gone" in capsys.readouterr().out


# --------------------------------------------------------------------------
# nn.model_windows
# --------------------------------------------------------------------------


def test_model_windows_backfills_only_when_asked(conn, monkeypatch, capsys):
    from nn import model_windows, nn_api

    usage_error(model_windows.main)                  # --backfill is required
    usage_error(model_windows.main, "--dry-run")

    calls = []
    monkeypatch.setattr(nn_api, "_registry", lambda: "registry")
    monkeypatch.setattr(model_windows, "backfill", lambda c, reg, dry_run: calls.append(
        (c, reg, dry_run)) or 4)
    assert model_windows.main(["--backfill", "--dry-run"]) == 0
    assert capsys.readouterr().out == "4 result(s) checked, nothing written\n"
    assert model_windows.main(["--backfill"]) == 0
    assert capsys.readouterr().out == "4 result(s) recorded\n"
    assert calls == [(conn, "registry", True), (conn, "registry", False)] and conn.closed


# --------------------------------------------------------------------------
# nn.feature_catalog
# --------------------------------------------------------------------------


def test_feature_catalog_prints_the_rows_and_writes_only_with_sync(conn, monkeypatch, capsys):
    from nn import feature_catalog, feature_config

    def no_writes(c, cfg=None):
        raise AssertionError("the catalogue was written without --sync")

    monkeypatch.setattr(feature_catalog, "sync", no_writes)
    assert feature_catalog.main([]) == 0
    out = capsys.readouterr().out
    rows = feature_catalog.rows_from_config(feature_config.load())
    assert out.startswith(f"{len(rows)} features in {feature_config.DEFAULT_PATH}:\n")
    assert out.endswith("\nNothing written. Pass --sync to write.\n")
    assert all(f"  {r['name']:<24} " in out for r in rows)

    monkeypatch.setattr(feature_catalog, "sync", lambda c, cfg: {"upserted": len(rows)})
    assert feature_catalog.main(["--sync"]) == 0
    assert capsys.readouterr().out == f"synced: {{'upserted': {len(rows)}}}\n"
    usage_error(feature_catalog.main, "--write")


# --------------------------------------------------------------------------
# loader.repair
# --------------------------------------------------------------------------


def test_repair_proposes_chooses_and_refuses_what_it_lacks(conn, monkeypatch, capsys):
    from loader import repair

    assert repair.main(["--propose", "--symbol", "QQQ"]) == 2
    assert capsys.readouterr().out == "--propose needs --symbol and --source\n"
    monkeypatch.setattr(repair, "propose", lambda c, sym, src, tf: {
        "bars": 2, "proposed": 3, "applied": 1, "held": 2})
    assert repair.main(["--propose", "--symbol", "QQQ", "--source", "yahoo-adj"]) == 0
    assert capsys.readouterr().out == ("QQQ/yahoo-adj D1: 2 flagged bar(s), 3 repair row(s); "
                                       "1 applied automatically, 2 held for a choice\n")

    usage_error(repair.main, "--choose", "guess")
    # The reason is checked before anything is read.
    assert repair.main(["--choose", "calculated", "--id", "5"]) == 2
    assert capsys.readouterr().out == ("refused: a repair needs a reason: it is recorded in "
                                       "the repair log\n")
    chosen = []
    monkeypatch.setattr(repair, "choose", lambda c, which, reason, **kw: chosen.append(
        (which, reason, kw)) or 3)
    assert repair.main(["--choose", "second_source", "--id", "5", "--id", "6",
                        "--reason", " checked the tape "]) == 0
    assert capsys.readouterr().out == ("applied 3 repair(s) from the second source value: "
                                       "checked the tape\n")
    assert chosen[0][0] == "second_source" and chosen[0][2]["ids"] == [5, 6]


def test_repair_revert_exits_1_for_a_row_it_could_not_take_back(conn, monkeypatch, capsys):
    from loader import repair

    assert repair.main(["--revert", "--id", "5"]) == 2
    assert capsys.readouterr().out == "--revert needs --id and --reason\n"
    monkeypatch.setattr(repair, "revert", lambda c, i, reason: i == 5)
    assert repair.main(["--revert", "--id", "5", "--reason", "wrong source"]) == 0
    assert capsys.readouterr().out == "reverted 1 repair(s): [5]\n"
    # Row 6 is not applied. It said "reverted 0" and exited 0, where the route
    # answers 404.
    assert repair.main(["--revert", "--id", "5", "--id", "6", "--reason", "wrong source"]) == 1
    assert capsys.readouterr().out == ("reverted 1 repair(s): [5]\n"
                                       "not applied, so not reverted: [6]\n")


def test_repair_lists_the_log_by_default(conn, monkeypatch, capsys):
    from loader import repair

    seen = []

    def log(cur, symbol, source, status):
        seen.append((symbol, source, status))
        return [{"id": i, "symbol": "QQQ", "source": "yahoo-adj",
                 "barTime": "2026-11-20T00:00:00Z", "field": "high", "original": 99.0,
                 "problem": "below the low", "secondSource": 101.0, "calculated": 100.5,
                 "status": "held", "applied": None, "appliedFrom": None} for i in range(42)]

    monkeypatch.setattr(repair, "log", log)
    monkeypatch.setattr(repair, "summary", lambda cur: [
        {"symbol": "QQQ", "source": "yahoo-adj", "timeframe": "D1", "held": 42,
         "heldWithCalculated": 40, "heldWithSecondSource": 42, "applied": 3}])
    assert repair.main([]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == ("QQQ/yahoo-adj D1: 42 held (40 with a calculated value, 42 with a second "
                      "source), 3 applied")
    assert out[1] == ("  #0 QQQ/yahoo-adj 2026-11-20 high: 99, below the low; second source "
                      "101.0, calculated 100.5; held")
    assert len(out) == 1 + 40 + 1 and out[-1] == "  ... and 2 more"
    assert repair.main(["--list", "--all", "--symbol", "QQQ"]) == 0
    assert seen == [(None, None, "held"), ("QQQ", None, None)]


# --------------------------------------------------------------------------
# loader.missing
# --------------------------------------------------------------------------


def test_missing_scans_recalculates_and_refuses(conn, monkeypatch, capsys):
    from loader import missing

    series = ["--symbol", "QQQ", "--source", "yahoo-adj"]
    usage_error(missing.main, "--scan", "--symbol", "QQQ")        # --source is required
    usage_error(missing.main, *series, "--recalculate", "--method", "guess")
    usage_error(missing.main, *series, "--recalculate", "--method", "carry", "--day", "11/20")
    assert missing.main(series) == 2                              # neither --scan nor --recalculate
    capsys.readouterr()
    assert missing.main([*series, "--recalculate"]) == 2
    assert capsys.readouterr().out == "--recalculate needs --method\n"

    monkeypatch.setattr(missing, "scan", lambda c, sym, src, tf: {
        "missing": 3, "applied": 1, "held": 2})
    assert missing.main([*series, "--scan"]) == 0
    assert capsys.readouterr().out == ("QQQ/yahoo-adj D1: 3 missing bar(s); 1 filled by the "
                                       "rule, 2 held\n")

    asked = []
    monkeypatch.setattr(missing, "recalculate", lambda c, sym, src, method, **kw: asked.append(
        (method, kw)) or {"recalculated": 1, "skipped": [
            {"day": "2026-11-21", "why": "SPY has no close that day"}]})
    assert missing.main([*series, "--recalculate", "--method", "related", "--day",
                         "2026-11-20", "--related-symbol", "SPY",
                         "--related-source", "yahoo-adj"]) == 0
    assert capsys.readouterr().out == ("recalculated 1 bar(s) by related\n"
                                       "  2026-11-21: SPY has no close that day\n")
    assert asked == [("related", {"day": dt.date(2026, 11, 20),
                                  "related": ("SPY", "yahoo-adj"), "timeframe": "D1"})]

    def unknown(c, sym, src, tf):
        raise LookupError("no D1 candles for QQQ (yahoo-adj)")

    monkeypatch.setattr(missing, "scan", unknown)
    assert missing.main([*series, "--scan"]) == 2
    assert capsys.readouterr().out == "refused: no D1 candles for QQQ (yahoo-adj)\n"


# --------------------------------------------------------------------------
# loader.feeds
# --------------------------------------------------------------------------


@pytest.fixture
def feeds(conn, monkeypatch):
    """The runner's run and its record, as the test says."""
    from loader.feeds import runner

    state = {"runs": [], "records": [], "result": {"feeds": [{"status": "extended"}]}}

    def run(trigger, keys, **kw):
        state["runs"].append((trigger, keys, kw))
        if isinstance(state["result"], Exception):
            raise state["result"]
        return state["result"]

    monkeypatch.setattr(runner, "run", run)
    monkeypatch.setattr(runner, "record_start", lambda c, trigger, keys, apply_key: state[
        "records"].append(("start", trigger, keys, apply_key)) or 77)
    monkeypatch.setattr(runner, "record_end", lambda c, run_id, status, result, error: state[
        "records"].append(("end", run_id, status, error)))
    return state


def test_feeds_lists_and_refuses_a_feed_it_does_not_have(feeds, capsys):
    from loader.feeds import registry
    from loader.feeds.__main__ import main

    assert main(["list"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert len(out) == len(registry.FEEDS) + len(registry.MANUAL)
    assert out[0].split() == [registry.FEEDS[0].key, registry.FEEDS[0].group,
                              registry.FEEDS[0].provider, registry.FEEDS[0].ref]

    usage_error(main)                                  # a command is required
    capsys.readouterr()
    for argv in (["refresh", "--feed", "NOPE/zz"], ["apply", "NOPE/zz"]):
        assert main(argv) == 2
        assert capsys.readouterr().err == ("unknown feed NOPE/zz (see `python -m loader.feeds "
                                           "list`)\n")
    assert feeds["runs"] == [] and feeds["records"] == []


def test_feeds_refresh_records_its_run_and_exits_1_on_a_failed_series(feeds, capsys):
    from loader.feeds import registry
    from loader.feeds.__main__ import main

    key = registry.FEEDS[0].key
    # A dry run fetches and compares, and records nothing.
    assert main(["refresh", "--dry-run"]) == 0
    assert feeds["runs"][-1] == ("cli", None, {"apply_key": None, "dry_run": True,
                                               "rebuild": True, "override_reason": None})
    assert feeds["records"] == []

    feeds["result"] = {"feeds": [{"status": "extended"}, {"status": "error", "error": "x"}]}
    assert main(["refresh", "--feed", key, "--no-rebuild", "--json",
                 "--override-checks", "vendor tick"]) == 1
    assert feeds["runs"][-1] == ("cli", [key], {"apply_key": None, "dry_run": False,
                                                "rebuild": False,
                                                "override_reason": "vendor tick"})
    assert feeds["records"] == [("start", "cli", [key], None),
                                ("end", 77, "done-with-errors", None)]
    out = capsys.readouterr().out
    assert out.startswith("recorded feed_runs id=77\n")
    assert json.loads(out.split("\n", 1)[1]) == feeds["result"]


def test_feeds_apply_and_a_run_that_fails(feeds, capsys):
    from loader.feeds import registry, runner
    from loader.feeds.__main__ import main

    key = registry.FEEDS[0].key
    assert main(["apply", key]) == 0
    assert feeds["runs"][-1] == ("cli", None, {"apply_key": key, "dry_run": False,
                                               "rebuild": True, "override_reason": None})
    assert feeds["records"][-1] == ("end", 77, "done", None)

    feeds["result"] = runner.Busy("another feeds run is in progress")
    assert main(["refresh"]) == 1
    assert feeds["records"][-1] == ("end", 77, "failed",
                                    "Busy: another feeds run is in progress")
    assert capsys.readouterr().err == "feeds: another feeds run is in progress\n"
