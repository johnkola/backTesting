"""A model's training window, so an in-sample result says so (B4, G30)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from nn import model_windows as mw

from .dbfixtures import dsn, requires_db


@dataclass
class Record:
    version_id: str
    extra: dict = field(default_factory=dict)


class Registry:
    def __init__(self, *records):
        self.records = list(records)

    def list_all(self, strategy=None):
        return self.records


def window(since, until, symbol="QQQ"):
    return {"training_window": {"since": since, "until": until, "symbol": symbol,
                                "source": "yahoo"}}


class TestReading:
    def test_both_recorded_forms_read_as_dates(self):
        w = mw.from_metadata(window("2003-01-01T00:00:00.000Z", "2015-12-31T00:00:00.001Z"))
        assert (w.status, w.since, w.until, w.symbol) == (
            mw.KNOWN, date(2003, 1, 1), date(2015, 12, 31), "QQQ")
        assert mw.from_metadata(window("2003-01-01", "2015-12-31")).since == date(2003, 1, 1)

    def test_a_version_saved_without_dates_is_not_recorded(self):
        assert mw.from_metadata(window(None, None)).status == mw.NOT_RECORDED
        assert mw.from_metadata({}).status == mw.NOT_RECORDED

    def test_a_pruned_version_is_gone(self):
        reg = Registry(Record("v1", window("2003-01-01", "2015-12-31")))
        assert mw.lookup(reg, "nn-feedforward", "v1").status == mw.KNOWN
        assert mw.lookup(reg, "nn-feedforward", "v2").status == mw.VERSION_GONE


class TestOverlap:
    W = mw.ModelWindow(mw.KNOWN, date(2003, 1, 1), date(2015, 12, 31))

    def test_inside_after_and_touching(self):
        assert mw.overlaps(self.W, date(1999, 6, 18), date(2026, 9, 25)) is True
        assert mw.overlaps(self.W, date(2016, 1, 4), date(2021, 12, 31)) is False
        assert mw.overlaps(self.W, date(2015, 12, 31), date(2016, 6, 1)) is True

    def test_unknown_says_nothing(self):
        assert mw.overlaps(mw.ModelWindow(mw.VERSION_GONE), date(2016, 1, 4),
                           date(2017, 1, 3)) is None


@requires_db
def test_the_backfill_fills_the_columns_and_the_database_derives_in_sample():
    import psycopg
    conn = psycopg.connect(dsn())
    strategy, other = "zz-b4-backfill", "zz-b4-backfill-other"
    try:
        with conn.cursor() as cur:
            for version, start in (("v-in", "1999-06-18"), ("v-out", "2016-01-04"),
                                   ("v-gone", "2016-01-04")):
                cur.execute(
                    "INSERT INTO backtest_results (instrument_symbol, strategy_name, timeframe,"
                    " data_source, start_date, end_date, initial_capital, final_equity,"
                    " total_return_pct, sharpe_ratio, max_drawdown_pct, total_trades, win_rate,"
                    " model_version_id, result_json) VALUES ('QQQ', %s, 'D1', 'yahoo', %s,"
                    " '2021-12-31', 10000, 11000, 10, 0.5, 5, 2, 50, %s, '{}')",
                    (strategy, start, version))
            # Another strategy's row, which the scoped backfill must not touch:
            # a live result looks like this, and this registry does not hold it.
            cur.execute(
                "INSERT INTO backtest_results (instrument_symbol, strategy_name, timeframe,"
                " data_source, start_date, end_date, initial_capital, final_equity,"
                " total_return_pct, sharpe_ratio, max_drawdown_pct, total_trades, win_rate,"
                " model_version_id, model_window, result_json) VALUES ('QQQ', %s, 'D1', 'yahoo',"
                " '2016-01-04', '2021-12-31', 10000, 11000, 10, 0.5, 5, 2, 50, 'v-live', 'known',"
                " '{}')", (other,))
        conn.commit()
        reg = Registry(Record("v-in", window("2003-01-01", "2015-12-31")),
                       Record("v-out", window("2003-01-01", "2015-12-31")))
        lines: list[str] = []
        mw.backfill(conn, reg, dry_run=True, log=lines.append, strategy=strategy)
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM backtest_results WHERE strategy_name = %s"
                        " AND model_window IS NOT NULL", (strategy,))
            assert cur.fetchone()[0] == 0, "a dry run writes nothing"
        mw.backfill(conn, reg, log=lines.append, strategy=strategy)
        with conn.cursor() as cur:
            cur.execute("SELECT model_window FROM backtest_results WHERE strategy_name = %s",
                        (other,))
            assert cur.fetchone()[0] == "known", "another strategy's row is left alone"
            cur.execute("SELECT model_version_id, model_window, in_sample FROM backtest_results"
                        " WHERE strategy_name = %s ORDER BY model_version_id", (strategy,))
            assert cur.fetchall() == [("v-gone", "version gone", None), ("v-in", "known", True),
                                      ("v-out", "known", False)]
        assert any("IN-SAMPLE" in line for line in lines)
    finally:
        conn.rollback()
        with conn.cursor() as cur:
            cur.execute("DELETE FROM backtest_results WHERE strategy_name IN (%s, %s)",
                        (strategy, other))
        conn.commit()
        conn.close()


def test_the_route_answers_with_the_window(monkeypatch):
    from fastapi.testclient import TestClient

    from loader.main import app
    from nn import nn_api
    monkeypatch.setattr(nn_api, "_registry",
                        lambda: Registry(Record("v1", window("2003-01-01", "2015-12-31"))))
    body = TestClient(app).get("/api/nn/models/window",
                               params={"strategy": "nn-feedforward", "versionId": "v1"}).json()
    assert body == {"status": "known", "since": "2003-01-01", "until": "2015-12-31",
                    "symbol": "QQQ", "source": "yahoo"}
