"""An import always lands; its errors are flagged rows that hold what reads
them (G75; R12, R65; the author 2026-11-22).

The gate and the upload route are tested without a database. The flagged rows
themselves need Postgres: they are a table.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import json

import pytest
from fastapi.testclient import TestClient

from loader import flags, imports_api, ingest
from loader.cohesion import Flagged, check_candles, gate
from loader.main import app

from .test_walkforward_store import db  # noqa: F401

HEADER = "Date,Open,High,Low,Close,Volume\n"
GOOD = HEADER + "2023-01-03,100,101,99,100.5,1000\n2023-01-04,100.5,102,100,101.5,1000\n"
#: The second bar's high is below its low: it cannot be true.
BROKEN = HEADER + "2023-01-03,100,101,99,100.5,1000\n2023-01-04,100.5,99,101,100.5,1000\n"


def _report(csv_rows):
    return check_candles(csv_rows, "D1")


BAD_BAR = [("2023-01-03T00:00:00Z", 100.0, 99.0, 101.0, 100.5, 1000.0)]
OK_BAR = [("2023-01-03T00:00:00Z", 100.0, 101.0, 99.0, 100.5, 1000.0)]


class TestGate:
    def test_a_clean_report_flags_nothing_and_keeps_no_reason(self):
        clean = gate(_report(OK_BAR))
        assert clean.errors == 0 and not clean.held
        assert clean.bar_times == ("2023-01-03T00:00:00Z",)      # so an old flag can be cleared
        assert gate(_report(OK_BAR), "not needed").reason is None, "nothing was accepted"

    @pytest.mark.parametrize("blank", [None, "", "   "])
    def test_an_error_never_refuses_the_file_and_holds_until_accepted(self, blank):
        flagged = gate(_report(BAD_BAR), blank)
        assert flagged.errors == 1 and flagged.held and flagged.reason is None
        f = flagged.findings[0]
        assert (f.category, f.timestamp) == ("ohlc", "2023-01-03T00:00:00Z")
        assert "high 99.0 < low 101.0" in f.detail

    def test_a_reason_accepts_the_flags_as_the_file_goes_in(self):
        flagged = gate(_report(BAD_BAR), " vendor tick, close is right ")
        assert flagged.reason == "vendor tick, close is right"
        assert flagged.errors == 1 and not flagged.held

    def test_every_error_is_kept_not_only_the_examples(self):
        many = [(f"2023-01-{d:02d}T00:00:00Z", 100.0, 99.0, 101.0, 100.5, 1000.0)
                for d in range(1, 29)]
        report = _report(many)
        assert report.errors == 28 and len(gate(report).findings) == 28
        assert len([e for e in report.examples if e.severity == "error"]) < 28

    def test_a_warning_is_not_a_flag(self):
        gap = [OK_BAR[0], ("2023-01-12T00:00:00Z", 100.5, 102.0, 100.0, 101.5, 1000.0)]
        report = _report(gap)
        assert report.warnings == 1 and gate(report).errors == 0


class TestDropFolder:
    def test_a_file_with_a_broken_bar_is_not_refused(self, tmp_path, capsys):
        f = tmp_path / "yahoo__QQQ__D1.csv"
        f.write_text(BROKEN, encoding="utf-8")
        assert ingest._process_one(f, force=False, dry_run=True) != "check-failed"
        out = capsys.readouterr().out
        assert "FLAG" in out and "1 bar(s) with an error" in out
        assert "The file is stored" in out and "held until the rows are accepted" in out

    def test_a_reason_on_the_run_accepts_them(self, tmp_path, capsys):
        f = tmp_path / "yahoo__QQQ__D1.csv"
        f.write_text(BROKEN, encoding="utf-8")
        ingest._process_one(f, force=False, dry_run=True, override_reason="vendor tick")
        out = capsys.readouterr().out
        assert "1 flagged row(s) accepted: vendor tick" in out and "FLAG" not in out

    def test_the_report_is_written_beside_the_file(self, tmp_path):
        done = tmp_path / "processed" / "yahoo__QQQ__D1.csv"
        done.parent.mkdir()
        done.write_text(BROKEN, encoding="utf-8")
        out = ingest._write_report(done, _report(BAD_BAR))
        assert out.name == "yahoo__QQQ__D1.csv.checks.json"
        body = json.loads(out.read_text())
        assert body["refused"] is False and body["flagged"] == 1
        assert body["errors"][0].startswith("ohlc at 2023-01-03")


class _NoQueries:
    """A pool whose connection may be opened; the commit is replaced."""

    @contextlib.contextmanager
    def connection(self):
        yield object()


@pytest.fixture
def upload(monkeypatch):
    seen = {}
    monkeypatch.setattr(imports_api, "pool", lambda: _NoQueries())
    monkeypatch.setattr(imports_api, "find_loaded_file", lambda *a, **k: None)
    monkeypatch.setattr(imports_api, "plan_slices", lambda *a, **k: [])
    monkeypatch.setattr(imports_api, "write_archive_files", lambda slices: None)

    def commit(conn, slices, source_file, **kw):
        seen.update(kw)
        flagged = kw["override"]
        return [], {"id": 1, "flagged": {"found": flagged.errors, "new": flagged.errors,
                                         "cleared": 0, "accepted": 0,
                                         "open": flagged.errors if flagged.held else 0}}

    monkeypatch.setattr(imports_api, "commit_file", commit)
    client = TestClient(app)

    def post(csv: str, **extra):
        return client.post(
            "/api/imports",
            data={"symbol": "QQQ", "type": "STOCK", "timeframe": "D1", "source": "yahoo",
                  **extra},
            files={"file": ("qqq.csv", csv.encode("utf-8"), "text/csv")},
        )
    return post, seen


class TestUpload:
    def test_a_file_with_a_broken_bar_lands_and_says_what_it_left(self, upload):
        post, seen = upload
        r = post(BROKEN)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "completed"
        assert isinstance(seen["override"], Flagged) and seen["override"].held
        assert body["flagged"]["errors"] == 1 and body["flagged"]["held"] is True
        assert body["flagged"]["open"] == 1
        assert body["flagged"]["lines"] == [
            "ohlc at 2023-01-04T00:00:00Z: high 99.0 < low 101.0; "
            "high 99.0 below open/close (100.5/100.5); low 101.0 above open/close (100.5/100.5)"]
        assert "feature build and training" in body["flagged"]["hint"]
        assert "checkOverride" not in body and body["cohesion"]["blocked"] is True

    def test_a_blank_reason_accepts_nothing(self, upload):
        post, _ = upload
        body = post(BROKEN, override_reason="  ").json()
        assert body["flagged"]["held"] is True and "checkOverride" not in body

    def test_a_reason_accepts_the_flags_with_the_upload(self, upload):
        post, seen = upload
        r = post(BROKEN, override_reason="vendor tick, close is right")
        assert r.status_code == 200, r.text
        assert seen["override"].reason == "vendor tick, close is right"
        body = r.json()
        assert body["checkOverride"] == {"reason": "vendor tick, close is right", "errors": 1}
        assert body["flagged"]["held"] is False and body["flagged"]["hint"] is None

    def test_a_clean_file_has_no_flagged_block(self, upload):
        post, seen = upload
        body = post(GOOD).json()
        assert "flagged" not in body and seen["override"].errors == 0


def test_accepting_over_http_needs_the_series_and_a_reason():
    client = TestClient(app)
    assert client.post("/api/checks/accept", json={"reason": "x"}).status_code == 400
    r = client.post("/api/checks/accept", json={"symbol": "QQQ", "source": "yahoo"})
    assert r.status_code == 400 and "reason is required" in r.json()["error"]


# --------------------------------------------------------------------------
# The flagged rows, against a real database
# --------------------------------------------------------------------------

SYMBOL, SOURCE = "ZZFLAG", "zz-flag-src"
DAY = "2023-01-0{}T00:00:00Z"


def bars(*broken: int, days=(3, 4, 5)):
    """Daily bars; the days in `broken` have their high under their low."""
    return [(DAY.format(d), 100.0, 99.0 if d in broken else 101.0,
             101.0 if d in broken else 99.0, 100.5, 1000.0) for d in days]


@pytest.fixture
def series(db):  # noqa: F811
    with db.cursor() as cur:
        cur.execute("DELETE FROM instruments WHERE symbol = %s", (SYMBOL,))
        cur.execute("DELETE FROM data_sources WHERE name = %s", (SOURCE,))
        cur.execute("INSERT INTO instruments (symbol, name, type) VALUES (%s, 'pytest flags', "
                    "'STOCK') RETURNING id", (SYMBOL,))
        inst = int(cur.fetchone()[0])
        cur.execute("INSERT INTO data_sources (name) VALUES (%s) RETURNING id", (SOURCE,))
        src = int(cur.fetchone()[0])
    db.commit()
    try:
        yield inst, src
    finally:
        db.rollback()
        with db.cursor() as cur:
            cur.execute("DELETE FROM data_checks WHERE instrument_id = %s", (inst,))
            cur.execute("DELETE FROM instruments WHERE id = %s", (inst,))
            cur.execute("DELETE FROM data_sources WHERE id = %s", (src,))
        db.commit()


def land(db, series, candles, reason=None):  # noqa: F811
    out = flags.record(db, *series, "D1", gate(check_candles(candles, "D1"), reason))
    db.commit()
    return out


class TestFlaggedRows:
    def test_an_import_s_errors_become_rows_that_hold_the_series(self, db, series):  # noqa: F811
        out = land(db, series, bars(4, 5))
        assert out == {"found": 2, "new": 2, "cleared": 0, "accepted": 0, "open": 2}
        with db.cursor() as cur:
            held = flags.open_flags(cur, [(SYMBOL, SOURCE, "D1"), ("QQQ", "yahoo-adj", "D1")])
            assert [(h["symbol"], h["open"], h["from"], h["to"]) for h in held] == [
                (SYMBOL, 2, "2023-01-04", "2023-01-05")]
            why = flags.refusal(cur, [(SYMBOL, SOURCE, "D1")], "the feature build")
            assert why.startswith(f"the feature build is held: {SYMBOL}/{SOURCE} D1 has 2 "
                                  "flagged row(s)")
            assert "accept them with a reason" in why
            # Another timeframe of the same series is another series.
            assert flags.refusal(cur, [(SYMBOL, SOURCE, "H1")], "training") is None
            rows = flags.rows(cur, SYMBOL, SOURCE)
            assert [r["barTime"][:10] for r in rows] == ["2023-01-05", "2023-01-04"]
            assert rows[0]["category"] == "ohlc" and rows[0]["acceptedAt"] is None

    def test_a_corrected_file_clears_the_flag_it_no_longer_finds(self, db, series):  # noqa: F811
        land(db, series, bars(4, 5))
        out = land(db, series, bars(5))               # day 4 came back right
        assert (out["cleared"], out["open"]) == (1, 1)
        # A file that does not cover a bar says nothing about it.
        out = land(db, series, bars(days=(3,)))
        assert out["cleared"] == 0
        with db.cursor() as cur:
            assert flags.open_flags(cur, [(SYMBOL, SOURCE, "D1")])[0]["open"] == 1

    def test_accepting_records_who_and_why_and_lets_the_series_be_read(self, db, series):  # noqa: F811
        land(db, series, bars(4, 5))
        with pytest.raises(ValueError, match="needs a reason"):
            flags.accept(db, "  ", SYMBOL, SOURCE)
        assert flags.accept(db, " exchange print, checked against the tape ", SYMBOL, SOURCE) == 2
        with db.cursor() as cur:
            assert flags.refusal(cur, [(SYMBOL, SOURCE, "D1")], "training") is None
            row = flags.rows(cur, SYMBOL, SOURCE, only_open=False)[0]
            assert row["acceptedReason"] == "exchange print, checked against the tape"
            assert row["acceptedBy"] and row["acceptedAt"]
            s = [x for x in flags.summary(cur) if x["symbol"] == SYMBOL][0]
            assert (s["open"], s["accepted"]) == (0, 2)
        assert flags.accept(db, "again", SYMBOL, SOURCE) == 0      # nothing left to accept

    def test_the_same_finding_found_again_keeps_its_acceptance(self, db, series):  # noqa: F811
        land(db, series, bars(4))
        flags.accept(db, "known bad print", SYMBOL, SOURCE)
        out = land(db, series, bars(4, 5))            # the file again, with a new bad bar
        assert (out["new"], out["open"]) == (1, 1)    # only the new one holds
        with db.cursor() as cur:
            reasons = {r["barTime"][:10]: r["acceptedReason"]
                       for r in flags.rows(cur, SYMBOL, SOURCE, only_open=False)}
        assert reasons == {"2023-01-04": "known bad print", "2023-01-05": None}

    def test_a_reason_with_the_import_accepts_what_it_flags(self, db, series):  # noqa: F811
        out = land(db, series, bars(4), reason="vendor tick")
        assert (out["accepted"], out["open"]) == (1, 0)

    def test_the_data_stage_fails_on_an_open_flag_and_notes_an_accepted_one(
            self, db, series, monkeypatch):  # noqa: F811
        from nn import staleness

        land(db, series, bars(4))

        class Cfg:
            built = ()
            live_checks = ()

            def required_series(self):
                return {(SYMBOL, SOURCE)}

        monkeypatch.setattr(staleness, "series_timeframes",
                            lambda cfg: {(SYMBOL, SOURCE): {"D1"}})
        monkeypatch.setattr(staleness, "_last_dates",
                            lambda cur, tf: {(SYMBOL, SOURCE): dt.date(2023, 1, 5)})
        monkeypatch.setattr(staleness, "_latest_feed_results", lambda cur: ({}, False))
        monkeypatch.setattr(staleness, "sessions_behind", lambda last, cutoff: 0)
        with db.cursor() as cur:
            ctx = staleness.Context(cur=cur, now=dt.datetime(2023, 1, 6, tzinfo=dt.UTC),
                                    timeframe="D1", cfg=Cfg())
            stage = staleness.data_stage(ctx)
            assert stage["state"] == "failed"
            check = [r for r in stage["reasons"] if r["kind"] == "check"]
            assert len(check) == 1 and check[0]["severity"] == "error" and check[0]["open"] == 1
            assert staleness._upstream_error(stage, {"reasons": []}).startswith("Data has")
        flags.accept(db, "checked", SYMBOL, SOURCE)
        with db.cursor() as cur:
            ctx = staleness.Context(cur=cur, now=dt.datetime(2023, 1, 6, tzinfo=dt.UTC),
                                    timeframe="D1", cfg=Cfg())
            stage = staleness.data_stage(ctx)
            assert stage["state"] == "passed"
            assert stage["notes"] == [f"{SYMBOL}/{SOURCE} D1: 1 flagged row(s) accepted: checked"]


def test_a_run_asks_about_every_series_it_reads():
    from argparse import Namespace

    from nn import walkforward as wf

    class Cur:
        def execute(self, sql, params=None):
            self.sql = sql

        def fetchone(self):
            if "current_run_id" in self.sql:
                return (7,)
            return (json.dumps([{"symbol": "VIX", "source": "yahoo", "timeframe": "D1"}]),)

    args = Namespace(symbol="QQQ", source="yahoo-adj", timeframe="H1", park="bills",
                     execution="margin")
    read = wf.series_read(Cur(), args, 63)
    assert ("QQQ", "yahoo-adj", "H1") in read and ("QQQ", "yahoo-adj", "D1") in read
    assert {("VIX", "yahoo", "D1"), ("DTB3", "fred", "D1"), ("PSQ", "yahoo-adj", "D1"),
            ("DFF", "fred", "D1"), ("SPY", "yahoo-adj", "D1")} <= set(read)
