"""The hard stop (R12, R65; Design-Gaps C2, D11): data whose checks found an
error is not stored, unless someone overrides it on purpose with a reason.

No database: the gate is pure, the drop folder decides before it opens a pool,
and the upload route is given a pool that answers "this file is new" and would
fail the test if anything tried to write.
"""

from __future__ import annotations

import contextlib
import json

import pytest
from fastapi.testclient import TestClient

from loader import cohesion, imports_api, ingest
from loader.cohesion import ChecksFailed, Override, check_candles, gate
from loader.main import app

HEADER = "Date,Open,High,Low,Close,Volume\n"
GOOD = HEADER + "2023-01-03,100,101,99,100.5,1000\n2023-01-04,100.5,102,100,101.5,1000\n"
#: The second bar's high is below its low: it cannot be true.
BROKEN = HEADER + "2023-01-03,100,101,99,100.5,1000\n2023-01-04,100.5,99,101,100.5,1000\n"


def _report(csv_rows):
    return check_candles(csv_rows, "D1")


BAD_BAR = [("2023-01-03T00:00:00Z", 100.0, 99.0, 101.0, 100.5, 1000.0)]
OK_BAR = [("2023-01-03T00:00:00Z", 100.0, 101.0, 99.0, 100.5, 1000.0)]


class TestGate:
    def test_a_clean_report_passes_and_records_no_override(self):
        assert gate(_report(OK_BAR)) is None
        assert gate(_report(OK_BAR), "not needed") is None, "nothing was overridden"

    def test_an_error_is_refused_with_the_error_in_the_message(self):
        with pytest.raises(ChecksFailed, match=r"1 error\(s\).*not stored.*high 99.0 < low 101.0"):
            gate(_report(BAD_BAR))

    @pytest.mark.parametrize("blank", [None, "", "   "])
    def test_an_override_needs_a_reason(self, blank):
        with pytest.raises(ChecksFailed):
            gate(_report(BAD_BAR), blank)

    def test_an_override_with_a_reason_passes_and_says_what_it_accepted(self):
        assert gate(_report(BAD_BAR), " vendor tick, close is right ") == Override(
            reason="vendor tick, close is right", errors=1)

    def test_a_warning_never_stops_anything(self):
        gap = [OK_BAR[0], ("2023-01-12T00:00:00Z", 100.5, 102.0, 100.0, 101.5, 1000.0)]
        report = _report(gap)
        assert report.warnings == 1 and gate(report) is None


class TestDropFolder:
    def test_a_file_with_a_broken_bar_is_refused_before_the_database(self, tmp_path, capsys):
        f = tmp_path / "yahoo__QQQ__D1.csv"
        f.write_text(BROKEN, encoding="utf-8")
        assert ingest._process_one(f, force=False, dry_run=True) == "check-failed"
        out = capsys.readouterr().out
        assert "1 error(s), not stored" in out and "--override-checks" in out

    def test_the_report_is_written_beside_the_refused_file(self, tmp_path):
        failed = tmp_path / "failed" / "yahoo__QQQ__D1.csv"
        failed.parent.mkdir()
        failed.write_text(BROKEN, encoding="utf-8")
        out = ingest._write_report(failed, _report(BAD_BAR))
        assert out.name == "yahoo__QQQ__D1.csv.checks.json"
        body = json.loads(out.read_text())
        assert body["refused"] is True and body["cohesion"]["errors"] == 1
        assert body["errors"][0].startswith("ohlc at 2023-01-03")


class _NoWrites:
    """A pool whose connection may be opened and must never be used to write."""

    @contextlib.contextmanager
    def connection(self):
        yield object()


@pytest.fixture
def upload(monkeypatch):
    monkeypatch.setattr(imports_api, "pool", lambda: _NoWrites())
    monkeypatch.setattr(imports_api, "find_loaded_file", lambda *a, **k: None)

    def never(*a, **k):
        raise AssertionError("a refused file must not be planned or committed")

    monkeypatch.setattr(imports_api, "plan_slices", never)
    monkeypatch.setattr(imports_api, "commit_file", never)
    client = TestClient(app)

    def post(csv: str, **extra):
        return client.post(
            "/api/imports",
            data={"symbol": "QQQ", "type": "STOCK", "timeframe": "D1", "source": "yahoo",
                  **extra},
            files={"file": ("qqq.csv", csv.encode("utf-8"), "text/csv")},
        )
    return post


class TestUpload:
    def test_a_file_with_a_broken_bar_is_a_422_naming_every_error(self, upload):
        r = upload(BROKEN)
        assert r.status_code == 422
        body = r.json()
        assert body["status"] == "check_failed"
        assert body["errors"] == [
            "ohlc at 2023-01-04T00:00:00Z: high 99.0 < low 101.0; "
            "high 99.0 below open/close (100.5/100.5); low 101.0 above open/close (100.5/100.5)"]
        assert body["cohesion"]["blocked"] is True and "override_reason" in body["hint"]

    def test_a_blank_override_reason_is_still_a_refusal(self, upload):
        assert upload(BROKEN, override_reason="  ").status_code == 422

    def test_with_a_reason_the_file_goes_on_to_be_stored_with_the_override(self, monkeypatch):
        """The gate lets it through and hands the override to the commit, which
        writes it on the audit rows."""
        seen = {}
        monkeypatch.setattr(imports_api, "pool", lambda: _NoWrites())
        monkeypatch.setattr(imports_api, "find_loaded_file", lambda *a, **k: None)
        monkeypatch.setattr(imports_api, "plan_slices", lambda *a, **k: [])
        monkeypatch.setattr(imports_api, "write_archive_files", lambda slices: None)

        def commit(conn, slices, source_file, **kw):
            seen.update(kw)
            return [], {"id": 1}

        monkeypatch.setattr(imports_api, "commit_file", commit)
        r = TestClient(app).post(
            "/api/imports",
            data={"symbol": "QQQ", "type": "STOCK", "timeframe": "D1", "source": "yahoo",
                  "override_reason": "vendor tick, close is right"},
            files={"file": ("qqq.csv", BROKEN.encode("utf-8"), "text/csv")},
        )
        assert r.status_code == 200, r.text
        assert seen["override"] == Override(reason="vendor tick, close is right", errors=1)
        assert r.json()["checkOverride"] == {"reason": "vendor tick, close is right", "errors": 1}
        assert r.json()["cohesion"]["errors"] == 1


def test_the_commit_writes_the_override_on_the_audit_row():
    import inspect

    from loader import csv_import
    src = inspect.getsource(csv_import._commit)
    assert "check_override_reason, check_override_errors, check_override_by" in src
    assert "audit_actor()" in src
    assert cohesion.Override.__dataclass_fields__.keys() == {"reason", "errors"}
