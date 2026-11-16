"""Tests for persisting a walk-forward run and its folds.

Split in two. The row builders are pure, so most of this needs no database and
runs anywhere. The round-trip at the bottom needs Postgres and is skipped
without it, because the things it guards — that a fold is readable while the run
is still going, and that the summary figures recompute correctly from the stored
folds — cannot be checked against a fake.
"""

from __future__ import annotations

import json
import os
from argparse import Namespace
from datetime import date

import pytest

from nn import walkforward_store as ws

# --------------------------------------------------------------------------
# Fixtures that need no database
# --------------------------------------------------------------------------


def an_args(**over) -> Namespace:
    """The runner's defaults, as argparse would hand them over."""
    base = dict(
        symbol="QQQ", source="yahoo-adj", timeframe="D1", set_id=None,
        train_start=2003, first_val=2008, last_val=2021,
        objective="return", cost_bps=5.0, park="cash",
        lookback=20, forward=5, buy_threshold=0.02, sell_threshold=-0.02,
        hidden=64, layers=2, dropout=0.2, lr=0.001, epochs=50, batch=32,
        class_weights=False, loss="ce", threshold=0.5, seed=42,
        test=False, label="", no_save=False,
    )
    base.update(over)
    return Namespace(**base)


class FakeFold:
    """A FoldResult's shape without importing it — walkforward pulls in torch."""

    def __init__(self, fold=1, val_year=2016):
        self.fold = fold
        self.val_year = val_year
        self.train_from = date(2003, 1, 2)
        self.train_to = date(2015, 12, 31)
        self.n_train = 3200
        self.n_val = 252
        self.strat_return = 4.5
        self.hold_return = 7.1
        self.strat_sharpe = 0.41
        self.hold_sharpe = 0.63
        self.strat_maxdd = 11.2
        self.hold_maxdd = 13.4
        self.trades = 6
        self.accuracy = 0.52
        self.baseline_acc = 0.48
        self.days_out = 31
        self.drift_tax = -2.1
        self.commissions = -0.3
        self.timing = -0.2
        self.gains_tax = -1.4


# --------------------------------------------------------------------------
# run_row
# --------------------------------------------------------------------------


class TestRunRow:
    def test_carries_the_split_and_the_headline_objective(self):
        row = ws.run_row(an_args(), set_id=6, input_width=580,
                         instrument_id=47, source_id=2)
        assert row["train_start"] == 2003
        assert row["first_val"] == 2008
        assert row["last_val"] == 2021
        assert row["objective"] == "return"
        assert row["loss"] == "ce"
        assert row["is_test"] is False

    def test_input_width_is_network_inputs_not_values_per_bar(self):
        # 29 columns x 20 bars. The number the input layer has to match; the
        # per-bar count would not reconstruct it without the lookback.
        row = ws.run_row(an_args(), 6, 580, 47, 2)
        assert row["input_width"] == 580

    def test_a_classifier_records_no_threshold(self):
        # Only a money-trained model has a position to cut. Storing 0.5 for a
        # classifier would read as a setting that did something.
        row = ws.run_row(an_args(loss="ce", threshold=0.5), 6, 580, 47, 2)
        assert row["threshold"] is None

    def test_a_money_trained_model_records_its_threshold(self):
        row = ws.run_row(an_args(loss="sharpe", threshold=0.35), 6, 580, 47, 2)
        assert row["threshold"] == pytest.approx(0.35)

    def test_a_test_run_is_flagged(self):
        # The design allows the holdout to be spent once, to confirm a variant
        # already chosen. A holdout run sitting unlabelled among validation runs
        # is how it quietly becomes training data.
        assert ws.run_row(an_args(test=True), 6, 580, 47, 2)["is_test"] is True

    def test_a_run_without_lineage_records_no_snapshot(self):
        # G3: NULL, so the check says "no snapshot" rather than "matches".
        row = ws.run_row(an_args(), 6, 580, 47, 2)
        assert {k: row[k] for k in ws.NO_LINEAGE} == ws.NO_LINEAGE

    def test_lineage_is_carried_into_the_row(self):
        lin = {"feature_run_id": 12, "input_snapshot": "s" * 64, "snapshot_json": "{}",
               "code_hash": "c" * 64, "git_sha": None, "lib_versions_json": "{}"}
        row = ws.run_row(an_args(), 6, 580, 47, 2, lineage=lin)
        assert {k: row[k] for k in lin} == lin

    def test_every_row_key_is_a_column_of_the_insert(self):
        # A key the INSERT does not name would be silently dropped by psycopg.
        row = ws.run_row(an_args(), 6, 580, 47, 2)
        for key in row:
            assert f"%({key})s" in ws._INSERT_RUN, key

    def test_the_ids_come_from_the_set_not_the_symbol_flag(self):
        row = ws.run_row(an_args(symbol="NOT-QQQ"), 6, 580,
                         instrument_id=47, source_id=2)
        assert row["instrument_id"] == 47
        assert row["source_id"] == 2

    def test_an_empty_label_is_stored_as_empty_not_null(self):
        # The column is NOT NULL DEFAULT ''.
        assert ws.run_row(an_args(label=""), 6, 580, 47, 2)["label"] == ""
        assert ws.run_row(an_args(label="money-sharpe"), 6, 580, 47, 2)["label"] \
            == "money-sharpe"


# --------------------------------------------------------------------------
# hyperparams
# --------------------------------------------------------------------------


class TestHyperparams:
    def test_records_the_seed_because_a_run_without_one_is_not_reproducible(self):
        assert ws.hyperparams(an_args(seed=7))["seed"] == 7

    def test_records_the_architecture_and_the_label_definition(self):
        h = ws.hyperparams(an_args())
        for key in ("lookback", "forward", "buy_threshold", "sell_threshold",
                    "hidden", "layers", "dropout", "lr", "epochs", "batch"):
            assert key in h, key

    @pytest.mark.parametrize("key", ["objective", "loss", "cost_bps", "park", "threshold"])
    def test_does_not_duplicate_anything_that_has_its_own_column(self, key):
        # Two copies of one setting is how they come to disagree.
        assert key not in ws.hyperparams(an_args())

    @pytest.mark.parametrize("key", ["symbol", "source", "timeframe", "set_id",
                                     "label", "no_save", "test"])
    def test_leaves_out_plumbing_that_does_not_change_the_model(self, key):
        assert key not in ws.hyperparams(an_args())

    def test_is_stored_as_stable_json(self):
        # sort_keys, so two identical configurations produce identical text and
        # can be compared without parsing.
        a = ws.run_row(an_args(), 6, 580, 47, 2)["hyperparams_json"]
        b = ws.run_row(an_args(), 6, 580, 47, 2)["hyperparams_json"]
        assert a == b
        assert json.loads(a)["seed"] == 42


class TestTaxRate:
    def test_a_run_scored_before_tax_stores_null_not_zero(self):
        # NULL is "off"; 0 would read as a 0% rate that was applied.
        assert ws.run_row(an_args(), 6, 580, 47, 2)["tax_rate"] is None
        assert ws.run_row(an_args(tax_rate=None), 6, 580, 47, 2)["tax_rate"] is None

    def test_a_run_scored_after_tax_stores_its_rate(self):
        assert ws.run_row(an_args(tax_rate=26.5), 6, 580, 47, 2)["tax_rate"] == 26.5

    def test_the_purge_is_stored_and_absent_means_before_it_existed(self):
        assert ws.run_row(an_args(), 6, 580, 47, 2)["purge_bars"] is None
        assert ws.run_row(an_args(purge_bars=5), 6, 580, 47, 2)["purge_bars"] == 5

    def test_patience_is_a_hyperparameter(self):
        assert '"patience": 4' in ws.run_row(an_args(patience=4), 6, 580, 47, 2)["hyperparams_json"]

    def test_no_training_switch_is_recorded_and_a_trained_run_has_no_source(self):
        # D9: an engine never sees a cost, so there is nothing to record; a run
        # that trained was not recalculated from another.
        row = ws.run_row(an_args(tax_rate=25.0, tax_train=True), 6, 580, 47, 2)
        assert "tax_train" not in row and "margin_train" not in row
        assert row["recalculated_from"] is None
        assert ws.run_row(an_args(), 6, 580, 47, 2,
                          lineage={"recalculated_from": 396})["recalculated_from"] == 396


# --------------------------------------------------------------------------
# fold_row
# --------------------------------------------------------------------------


class TestFoldRow:
    def test_carries_every_metric_and_both_sides_of_the_comparison(self):
        row = ws.fold_row(99, FakeFold())
        assert row["run_id"] == 99
        assert row["strat_return"] == 4.5
        assert row["hold_return"] == 7.1
        assert row["strat_sharpe"] == 0.41
        assert row["hold_sharpe"] == 0.63
        assert row["strat_maxdd"] == 11.2
        assert row["hold_maxdd"] == 13.4

    def test_carries_the_cost_attribution(self):
        # The point of storing it: a fold can lose heavily while the model's own
        # contribution is ~0. Without the split, "the model lost 2.6 points" and
        # "the model is absent and the structure lost 2.6 points" are the same
        # row.
        row = ws.fold_row(99, FakeFold())
        assert row["days_out"] == 31
        assert row["drift_tax"] == -2.1
        assert row["commissions"] == -0.3
        assert row["timing"] == -0.2
        assert row["gains_tax"] == -1.4
        assert row["best_epoch"] is None and row["epochs_run"] is None

    def test_carries_the_training_window_so_a_fold_can_be_rebuilt(self):
        row = ws.fold_row(99, FakeFold())
        assert row["train_from"] == date(2003, 1, 2)
        assert row["train_to"] == date(2015, 12, 31)
        assert row["n_train"] == 3200
        assert row["n_val"] == 252

    def test_carries_buy_and_hold_s_own_costs_and_no_benchmark_row(self):
        # B6: buy-and-hold is scored with the fold under its settings and stored
        # with it; no shared row is read or pointed at.
        fold = FakeFold()
        fold.hold_commissions, fold.hold_gains_tax = -0.1, -2.5
        row = ws.fold_row(99, fold)
        assert (row["hold_commissions"], row["hold_gains_tax"]) == (-0.1, -2.5)
        assert "benchmark_id" not in row and "benchmark" not in ws._INSERT_FOLD
        assert not hasattr(ws, "find_or_create_benchmark")

    def test_every_fold_row_key_is_a_column_of_the_insert(self):
        for key in ws.fold_row(99, FakeFold()):
            assert f"%({key})s" in ws._INSERT_FOLD

    def test_does_not_store_excess_because_it_is_derivable(self):
        # strat_return - hold_return. A stored copy goes stale the moment
        # either side is corrected.
        assert "excess" not in ws.fold_row(99, FakeFold())
        assert "attributed" not in ws.fold_row(99, FakeFold())


class TestExecutionColumns:
    def test_strategy1_records_signals_and_null_dials(self):
        cols = ws.execution_columns(an_args())
        assert cols["execution"] == "signals"
        assert all(v is None for k, v in cols.items() if k != "execution")

    def test_strategy2_records_every_dial_and_no_long_threshold_for_ce(self):
        cols = ws.execution_columns(an_args(execution="margin", leverage_pct=30,
                                            hedge_pct=10, margin_spread_pct=1.5,
                                            maintenance_pct=30, long_threshold=0.75))
        assert (cols["execution"], cols["leverage_pct"], cols["hedge_pct"]) == ("margin", 30, 10)
        assert cols["long_threshold"] is None
        cols = ws.execution_columns(an_args(execution="margin", loss="return", leverage_pct=30,
                                            hedge_pct=10, margin_spread_pct=1.5,
                                            maintenance_pct=30, long_threshold=0.75))
        assert cols["long_threshold"] == 0.75

    def test_a_strategy1_fold_stores_null_margin_figures(self):
        row = ws.fold_row(1, FakeFold())
        assert all(row[k] is None for k in ws.MARGIN_FOLD_FIELDS)


# --------------------------------------------------------------------------
# Round-trip against a real database
# --------------------------------------------------------------------------


def _conn():
    psycopg = pytest.importorskip("psycopg")
    try:
        return psycopg.connect(
            host=os.environ.get("PGHOST", "localhost"),
            port=int(os.environ.get("PGPORT", 5432)),
            dbname=os.environ.get("PGDATABASE", "backtest"),
            user=os.environ.get("PGUSER", "backtest"),
            password=os.environ.get("PGPASSWORD", "backtest"),
            connect_timeout=5,
        )
    except Exception as e:  # noqa: BLE001 - no database is a skip, not a failure
        pytest.skip(f"no database: {e}")


@pytest.fixture
def db():
    conn = _conn()
    yield conn
    conn.rollback()
    conn.close()


#: The instrument and source a seeded set is created under, on a database with none.
_SEED_SYMBOL, _SEED_SOURCE = "ZZPYSET", "zz-py-set"


@pytest.fixture
def a_set(db):
    """A feature set, so the run's FK and owner lookup are real.

    The newest existing one on a developer's database. On an empty one (CI)
    a throwaway set is created and removed afterwards, rather than skipping:
    a green run that skipped the round trip would say nothing about it."""
    with db.cursor() as cur:
        cur.execute("SELECT id FROM feature_sets ORDER BY id DESC LIMIT 1")
        row = cur.fetchone()
    if row is not None:
        yield int(row[0])
        return
    with db.cursor() as cur:
        cur.execute("INSERT INTO instruments (symbol, name, type) VALUES (%s, 'pytest set', "
                    "'STOCK') RETURNING id", (_SEED_SYMBOL,))
        inst = cur.fetchone()[0]
        cur.execute("INSERT INTO data_sources (name) VALUES (%s) RETURNING id", (_SEED_SOURCE,))
        src = cur.fetchone()[0]
        cur.execute("INSERT INTO feature_sets (instrument_id, source_id, timeframe, "
                    "schema_version, config_hash) VALUES (%s, %s, 'D1', 0, 'pytest') "
                    "RETURNING id", (inst, src))
        set_id = int(cur.fetchone()[0])
    db.commit()
    try:
        yield set_id
    finally:
        db.rollback()
        with db.cursor() as cur:
            cur.execute("DELETE FROM walkforward_folds WHERE run_id IN "
                        "(SELECT id FROM walkforward_runs WHERE feature_set_id = %s)", (set_id,))
            cur.execute("DELETE FROM walkforward_runs WHERE feature_set_id = %s", (set_id,))
            cur.execute("DELETE FROM feature_sets WHERE id = %s", (set_id,))
            cur.execute("DELETE FROM instruments WHERE id = %s", (inst,))
            cur.execute("DELETE FROM data_sources WHERE id = %s", (src,))
        db.commit()


class TestRoundTrip:
    """Needs Postgres. These are the parts a fake cannot check."""

    @staticmethod
    def _cleanup(conn, run_id):
        # A test that failed mid-statement leaves the transaction aborted, and
        # every statement after it would fail too, leaving its run behind in
        # the developer's database. Roll back first.
        conn.rollback()
        with conn.cursor() as cur:
            # Folds cascade, but be explicit so a schema change cannot leave
            # orphans behind in a developer's database.
            cur.execute("DELETE FROM walkforward_folds WHERE run_id = %s", (run_id,))
            cur.execute("DELETE FROM walkforward_runs WHERE id = %s", (run_id,))
        conn.commit()

    def test_a_run_is_readable_before_any_fold_is_scored(self, db, a_set):
        # The whole reason open_run commits up front: the Training tab reads
        # this row to say a run is in progress.
        run_id = ws.open_run(db, an_args(label="pytest-open"), a_set, 580)
        try:
            with db.cursor() as cur:
                cur.execute(
                    "SELECT label, is_test, finished_at, input_width, objective "
                    "FROM walkforward_runs WHERE id = %s", (run_id,))
                label, is_test, finished_at, width, objective = cur.fetchone()
            assert label == "pytest-open"
            assert is_test is False
            assert finished_at is None, "an unfinished run must read as in progress"
            assert width == 580
            assert objective == "return"
        finally:
            self._cleanup(db, run_id)

    def test_folds_land_one_at_a_time_and_the_run_closes(self, db, a_set):
        run_id = ws.open_run(db, an_args(label="pytest-folds"), a_set, 580)
        try:
            ws.save_fold(db, run_id, FakeFold(fold=1, val_year=2016))
            with db.cursor() as cur:
                cur.execute(
                    "SELECT count(*), bool_and(finished_at IS NULL) "
                    "FROM walkforward_folds f JOIN walkforward_runs r ON r.id = f.run_id "
                    "WHERE r.id = %s", (run_id,))
                n, still_running = cur.fetchone()
            assert n == 1
            assert still_running, "fold 1 must be visible while the run continues"

            ws.save_fold(db, run_id, FakeFold(fold=2, val_year=2017))
            ws.close_run(db, run_id)

            with db.cursor() as cur:
                cur.execute(
                    "SELECT count(*) FROM walkforward_folds WHERE run_id = %s", (run_id,))
                assert cur.fetchone()[0] == 2
                cur.execute(
                    "SELECT finished_at IS NOT NULL FROM walkforward_runs WHERE id = %s",
                    (run_id,))
                assert cur.fetchone()[0] is True
        finally:
            self._cleanup(db, run_id)

    def test_signals_round_trip_and_a_rewrite_replaces_them(self, db, a_set):
        # B10: what the profit step scored is what a recalculation reads back,
        # float for float; rewriting a fold replaces its signals, and a
        # rewrite without signals (a recalculation in place) keeps them.
        from nn import profit
        run_id = ws.open_run(db, an_args(label="pytest-signals"), a_set, 580)
        money = profit.Settings(loss="return", threshold=0.5)
        try:
            sig = a_signals([[0.1234567890123], [0.5], [0.987654321]])
            ws.save_fold(db, run_id, FakeFold(fold=1), sig, money)
            with db.cursor() as cur:
                back = ws.load_signals(cur, run_id)[1]
            assert back.dates == sig.dates and back.exits == sig.exits
            assert back.output.tolist() == sig.output.tolist()
            assert back.labels.tolist() == [0, 1, 2]

            ws.save_fold(db, run_id, FakeFold(fold=1), a_signals([[0.9], [0.9], [0.9]]), money)
            ws.save_fold(db, run_id, FakeFold(fold=1))
            with db.cursor() as cur:
                cur.execute("SELECT count(*), array_agg(call_class ORDER BY bar_date) "
                            "FROM walkforward_signals WHERE run_id = %s", (run_id,))
                assert cur.fetchone() == (3, [0, 0, 0])
        finally:
            self._cleanup(db, run_id)
        with db.cursor() as cur:
            cur.execute("SELECT count(*) FROM walkforward_signals WHERE run_id = %s", (run_id,))
            assert cur.fetchone()[0] == 0, "signals are deleted with their run"

    def test_a_recalculation_in_place_reproduces_the_run_and_repairs_its_figures(
            self, db, a_set):
        # B10 against the store: the run writes its signals, and rescoring them
        # at its own settings gives back exactly what it stored.
        import numpy as np

        from nn import profit, recalculate
        from nn import walkforward as wf
        with db.cursor() as cur:
            cur.execute("SELECT i.symbol, ds.name FROM feature_sets fs "
                        "JOIN instruments i ON i.id = fs.instrument_id "
                        "JOIN data_sources ds ON ds.id = fs.source_id WHERE fs.id = %s", (a_set,))
            symbol, source = cur.fetchone()
            prices = wf.load_prices(cur, symbol, source, "D1")
        closes = prices.closes
        days = sorted(closes)[-4:]
        if len(days) < 4:
            pytest.skip(f"no D1 candles for {symbol}/{source}, which the set is of")
        sig = profit.Signals(days[:3], days[1:], np.array([[0.9], [0.1], [0.9]]),
                             np.array([0, 2, 1]))
        settings = profit.Settings(loss="return", cost_bps=5.0)
        money = profit.score(sig.output, profit.market(prices, sig.dates, sig.exits, None,
                                                       "cash"), settings)
        fold = wf.FoldResult(fold=1, train_from=days[0], train_to=days[0], val_year=days[0].year,
                             n_train=1, n_val=3, accuracy=0.5, baseline_acc=0.5,
                             **money.fields())
        run_id = ws.open_run(db, an_args(loss="return", label="pytest-recalc"), a_set, 580)
        try:
            ws.save_fold(db, run_id, fold, sig, settings)
            ws.close_run(db, run_id)

            dry = recalculate.recalculate(db, run_id, dry_run=True)
            assert (dry.mode, dry.run_id) == ("in place", None)
            for name in profit.PROFIT_FIELDS:
                assert getattr(dry.after[0], name) == getattr(fold, name), name

            with db.cursor() as cur:
                cur.execute("UPDATE walkforward_folds SET strat_return = -99 WHERE run_id = %s",
                            (run_id,))
            db.commit()
            done = recalculate.recalculate(db, run_id)
            assert done.run_id == run_id
            with db.cursor() as cur:
                cur.execute("SELECT strat_return FROM walkforward_folds WHERE run_id = %s",
                            (run_id,))
                assert cur.fetchone()[0] == fold.strat_return
        finally:
            self._cleanup(db, run_id)

    def test_each_engine_stores_its_fold_and_signals_and_recalculates(self, db, a_set):
        # B9: a fold has a row and signals per engine, and rescoring in place
        # gives each engine back exactly what it stored.
        import json

        import numpy as np

        from nn import profit, recalculate
        from nn import walkforward as wf
        with db.cursor() as cur:
            cur.execute("SELECT i.symbol, ds.name FROM feature_sets fs "
                        "JOIN instruments i ON i.id = fs.instrument_id "
                        "JOIN data_sources ds ON ds.id = fs.source_id WHERE fs.id = %s", (a_set,))
            symbol, source = cur.fetchone()
            prices = wf.load_prices(cur, symbol, source, "D1")
        days = sorted(prices.closes)[-4:]
        if len(days) < 4:
            pytest.skip(f"no D1 candles for {symbol}/{source}, which the set is of")
        settings = profit.Settings(loss="ce", cost_bps=5.0)
        market = profit.market(prices, days[:3], days[1:], None, "cash")
        outputs = {"mlp": [[0.6, 0.3, 0.1], [0.1, 0.2, 0.7], [0.5, 0.4, 0.1]],
                   "xgboost": [[0.2, 0.7, 0.1], [0.6, 0.2, 0.2], [0.1, 0.1, 0.8]]}
        folds, sigs = {}, {}
        for name, out in outputs.items():
            sigs[name] = profit.Signals(days[:3], days[1:], np.array(out), np.array([0, 2, 1]))
            money = profit.score(sigs[name].output, market, settings)
            folds[name] = wf.FoldResult(
                fold=1, engine=name, train_from=days[0], train_to=days[0],
                val_year=days[0].year, n_train=1, n_val=3, accuracy=0.5, baseline_acc=0.4,
                **money.fields())
        args = an_args(label="pytest-engines", engines={"mlp": {}, "xgboost": {"maxDepth": 4}})
        run_id = ws.open_run(db, args, a_set, 580)
        try:
            for name in outputs:
                ws.save_fold(db, run_id, folds[name], sigs[name], settings)
            ws.close_run(db, run_id)
            with db.cursor() as cur:
                cur.execute("SELECT engine, strat_return FROM walkforward_folds "
                            "WHERE run_id = %s ORDER BY engine", (run_id,))
                assert cur.fetchall() == [("mlp", folds["mlp"].strat_return),
                                          ("xgboost", folds["xgboost"].strat_return)]
                cur.execute("SELECT engines_json FROM walkforward_runs WHERE id = %s",
                            (run_id,))
                rec = json.loads(cur.fetchone()[0])
            assert [r["engine"] for r in rec] == ["mlp", "xgboost"]
            assert rec[1]["settings"]["maxDepth"] == 4 and rec[1]["version"]
            with db.cursor() as cur:
                stored = ws.load_engine_signals(cur, run_id)
            assert np.array_equal(stored["xgboost"][1].output, sigs["xgboost"].output)

            dry = recalculate.recalculate(db, run_id, dry_run=True)
            assert [a.engine for a in dry.after] == ["mlp", "xgboost"]
            for a in dry.after:
                for name in profit.PROFIT_FIELDS:
                    assert getattr(a, name) == getattr(folds[a.engine], name), (a.engine, name)
        finally:
            self._cleanup(db, run_id)

    def test_epochs_are_stored_as_they_come_and_a_failed_run_says_why(self, db, a_set):
        # G11: the rows the Training tab draws the loss curve from, and the
        # reason a diverging run stopped.
        run_id = ws.open_run(db, an_args(label="pytest-epochs"), a_set, 580)
        try:
            ws.save_epochs(db, run_id, 1, "mlp", [(1, 1.10, 1.12)])
            ws.save_epochs(db, run_id, 1, "mlp", [(2, 1.05, 1.11)])
            ws.save_epochs(db, run_id, 1, "xgboost", [(1, 1.09, None), (2, 1.02, None)])
            ws.fail_run(db, run_id, "fold 2 (2017), mlp, epoch 4: training loss is nan")
            with db.cursor() as cur:
                cur.execute("SELECT engine, epoch, train_loss, stop_loss FROM walkforward_epochs "
                            "WHERE run_id = %s ORDER BY engine, epoch", (run_id,))
                assert cur.fetchall() == [("mlp", 1, 1.10, 1.12), ("mlp", 2, 1.05, 1.11),
                                          ("xgboost", 1, 1.09, None), ("xgboost", 2, 1.02, None)]
                cur.execute("SELECT failed_reason, finished_at FROM walkforward_runs WHERE id = %s",
                            (run_id,))
                assert cur.fetchone() == ("fold 2 (2017), mlp, epoch 4: training loss is nan", None)
        finally:
            self._cleanup(db, run_id)

    def test_a_run_without_signals_is_refused_with_the_reason(self, db, a_set):
        from nn import recalculate
        run_id = ws.open_run(db, an_args(label="pytest-nosignals"), a_set, 580)
        try:
            ws.save_fold(db, run_id, FakeFold(fold=1))
            ws.close_run(db, run_id)
            with pytest.raises(recalculate.Refused, match="stored no signals") as exc:
                recalculate.recalculate(db, run_id, dry_run=True)
            assert exc.value.status == 409
        finally:
            self._cleanup(db, run_id)

    def test_a_fold_s_rule_benchmarks_are_rewritten_with_it(self, db, a_set):
        # B7: one row per fold and rule; rewriting the fold replaces them.
        from nn import profit
        run_id = ws.open_run(db, an_args(label="pytest-rules"), a_set, 580)
        try:
            fold = FakeFold(fold=1)
            faber = profit.RuleFigures("faber", True, None, 5.0, 0.6, 7.0, 4, 200, -0.2, 0.0)
            fold.rules = (faber, profit.RuleFigures("dual_momentum", False, "needs 12 months"))
            ws.save_fold(db, run_id, fold)
            fold.rules = (profit.RuleFigures("faber", True, None, 6.0, 0.6, 7.0, 2, 150, -0.1,
                                             0.0),)
            ws.save_fold(db, run_id, fold)
            with db.cursor() as cur:
                cur.execute("SELECT name, available, return_pct, reason "
                            "FROM walkforward_benchmarks WHERE run_id = %s ORDER BY name",
                            (run_id,))
                assert cur.fetchall() == [("faber", True, 6.0, None)]
        finally:
            self._cleanup(db, run_id)

    def test_rescoring_a_fold_corrects_it_instead_of_failing(self, db, a_set):
        run_id = ws.open_run(db, an_args(), a_set, 580)
        try:
            ws.save_fold(db, run_id, FakeFold(fold=1))
            corrected = FakeFold(fold=1)
            corrected.strat_return = 99.0
            ws.save_fold(db, run_id, corrected)
            with db.cursor() as cur:
                cur.execute(
                    "SELECT count(*), max(strat_return) FROM walkforward_folds "
                    "WHERE run_id = %s", (run_id,))
                n, ret = cur.fetchone()
            assert n == 1, "the primary key is (run_id, fold)"
            assert float(ret) == 99.0
        finally:
            self._cleanup(db, run_id)

    def test_the_summary_figures_recompute_from_the_folds(self, db, a_set):
        # None of these is stored, by design. This is the query that replaces
        # them, and it has to give the same answers the runner prints.
        run_id = ws.open_run(db, an_args(), a_set, 580)
        try:
            win = FakeFold(fold=1, val_year=2016)
            win.strat_return, win.hold_return = 12.0, 7.0   # +5 excess
            lose = FakeFold(fold=2, val_year=2017)
            lose.strat_return, lose.hold_return = 1.0, 8.0  # -7 excess
            lose.strat_maxdd = 20.0
            ws.save_fold(db, run_id, win)
            ws.save_fold(db, run_id, lose)

            with db.cursor() as cur:
                cur.execute(
                    "SELECT count(*),"
                    "       count(*) FILTER (WHERE strat_return > hold_return),"
                    "       avg(strat_return - hold_return),"
                    "       max(strat_maxdd)"
                    "  FROM walkforward_folds WHERE run_id = %s", (run_id,))
                n, won, mean_excess, worst_dd = cur.fetchone()
            assert n == 2
            assert won == 1
            assert float(mean_excess) == pytest.approx(-1.0)  # (+5 + -7) / 2
            assert float(worst_dd) == pytest.approx(20.0)
        finally:
            self._cleanup(db, run_id)

    def test_the_run_is_attributed_to_the_job_not_the_login(self, db, a_set):
        # audit_actor() falls back to the database role, which would make every
        # run read "backtest". The design wants the job that produced it.
        run_id = ws.open_run(db, an_args(), a_set, 580)
        try:
            ws.save_fold(db, run_id, FakeFold(fold=1))
            with db.cursor() as cur:
                cur.execute(
                    "SELECT created_by, updated_by FROM walkforward_runs WHERE id = %s",
                    (run_id,))
                assert cur.fetchone() == (ws.DEFAULT_ACTOR, ws.DEFAULT_ACTOR)
                # The folds too: they are written on a second call, so a missing
                # set_actor there would attribute them to the role instead.
                cur.execute(
                    "SELECT DISTINCT created_by FROM walkforward_folds WHERE run_id = %s",
                    (run_id,))
                assert [r[0] for r in cur.fetchall()] == [ws.DEFAULT_ACTOR]
        finally:
            self._cleanup(db, run_id)

    def test_a_run_against_a_missing_set_is_refused(self, db):
        with pytest.raises(ValueError, match="no feature_set with id"):
            ws.open_run(db, an_args(), 10**9, 580)
        db.rollback()


class TestRunProvenance:
    def test_the_column_list_is_stored_in_input_order(self):
        row = ws.run_row(an_args(), 6, 580, 47, 2, columns=["price_close", "vxn_close"])
        assert json.loads(row["columns_json"]) == ["price_close", "vxn_close"]

    def test_a_run_from_flags_records_no_strategy(self):
        row = ws.run_row(an_args(), 6, 580, 47, 2, columns=["a"])
        assert row["strategy_id"] is None
        assert row["train_first"] is None

    def test_a_strategy_run_records_what_it_came_from(self):
        prov = {**ws.NO_PROVENANCE, "strategy_id": 7, "strategy_name": "baseline",
                "strategy_version": 2, "train_first": date(2006, 1, 3),
                "train_bars_used": 2500, "train_bars": 3270}
        row = ws.run_row(an_args(), 6, 580, 47, 2, columns=["a"], provenance=prov)
        assert (row["strategy_id"], row["strategy_version"]) == (7, 2)
        assert row["train_bars_used"] == 2500

    def test_every_insert_placeholder_has_a_value(self):
        """A key the INSERT names but run_row omits fails only against Postgres."""
        import re
        wanted = set(re.findall(r"%\((\w+)\)s", ws._INSERT_RUN))
        assert wanted == set(ws.run_row(an_args(), 6, 580, 47, 2, columns=["a"]))


# --------------------------------------------------------------------------
# Signals (B10)
# --------------------------------------------------------------------------


def a_signals(output, labels=(0, 1, 2)):
    from datetime import date

    import numpy as np

    from nn import profit
    days = [date(2021, 1, 4), date(2021, 1, 5), date(2021, 1, 6)]
    return profit.Signals(days, [*days[1:], None], np.asarray(output, dtype=np.float64),
                          np.asarray(labels, dtype=np.int64))


def test_signal_rows_store_the_raw_output_and_the_call_the_run_made():
    from nn import profit
    sig = a_signals([[0.2], [0.5], [0.9]])
    rows = ws.signal_rows(7, 3, sig, profit.Settings(loss="return", threshold=0.5))
    assert [r[:4] for r in rows] == [(7, 3, sig.dates[0], sig.dates[1]),
                                     (7, 3, sig.dates[1], sig.dates[2]),
                                     (7, 3, sig.dates[2], None)]
    assert [r[4] for r in rows] == [[0.2], [0.5], [0.9]]
    assert [r[5] for r in rows] == [2, 0, 0]       # SELL below the threshold
    assert [r[6] for r in rows] == [0, 1, 2]


def test_a_classifier_s_call_is_its_most_probable_class():
    from nn import profit
    sig = a_signals([[0.6, 0.3, 0.1], [0.2, 0.7, 0.1], [0.1, 0.2, 0.7]])
    rows = ws.signal_rows(7, 1, sig, profit.Settings(loss="ce"))
    assert [r[5] for r in rows] == [0, 1, 2]
    assert rows[0][4] == [0.6, 0.3, 0.1]


def test_load_signals_groups_rows_by_engine_and_fold_in_date_order():
    from datetime import date

    class Cur:
        def execute(self, sql, params):
            assert "ORDER BY engine, fold, bar_date" in sql and params == (5,)

        def fetchall(self):
            return [("mlp", 1, date(2020, 1, 2), date(2020, 1, 3), [0.4], 2),
                    ("mlp", 1, date(2020, 1, 3), None, [0.6], 0),
                    ("mlp", 2, date(2021, 1, 4), date(2021, 1, 5), [0.8], 1),
                    ("xgboost", 1, date(2020, 1, 2), date(2020, 1, 3), [0.2, 0.5, 0.3], 2)]

    got = ws.load_signals(Cur(), 5)
    assert sorted(got) == [1, 2]
    assert got[1].exits == [date(2020, 1, 3), None]
    assert got[1].output.shape == (2, 1) and got[1].labels.tolist() == [2, 0]
    both = ws.load_engine_signals(Cur(), 5)
    assert sorted(both) == ["mlp", "xgboost"]
    assert both["xgboost"][1].output.shape == (1, 3)


def test_storing_signals_needs_the_settings_that_made_the_call():
    import pytest as _pytest

    class Conn:
        def cursor(self):
            class C:
                def __enter__(s):
                    return s

                def __exit__(s, *a):
                    return False

                def execute(s, *a):
                    pass
            return C()

        def commit(self):
            pass

    with _pytest.raises(ValueError, match="settings"):
        ws.save_fold(Conn(), 1, FakeFold(), a_signals([[0.2], [0.5], [0.9]]))


def test_a_strategy2_fold_carries_its_always_long_baseline():
    fold = FakeFold()
    fold.levered_return = 12.06
    row = ws.fold_row(1, fold)
    assert row["levered_return"] == 12.06
    assert "%(levered_return)s" in ws._INSERT_FOLD
    assert ws.fold_row(1, FakeFold())["levered_return"] is None


def test_a_look_at_a_test_window_is_recorded_and_a_second_is_a_reuse(db):
    """G48, R69: the shared rule records a run over a saved test window as a
    look at it; the next look at the same window counts the one before."""
    conn = db
    symbol, name = "ZZG48PY", "zz-g48-py"
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('strategies') IS NOT NULL")
            if not cur.fetchone()[0]:
                pytest.skip("the web layer's strategies table is not there yet")
            cur.execute(
                "INSERT INTO strategies (name, version, instrument, source, timeframe, model_type,"
                " validation_scheme, features_json, windows_json, label_json, hyperparams_json)"
                " VALUES (%s, 1, %s, 'yahoo', 'D1', 'mlp', 'walkforward', '[]', %s, '{}', '{}')",
                (name, symbol, '{"test": {"from": "2022-01-01", "to": "2022-12-31"}}'))
        conn.commit()
        from datetime import date
        first = ws.record_look(conn, "walkforward", -901, symbol, date(2022, 1, 3),
                               date(2022, 12, 30))
        assert [(f.isoformat(), t.isoformat(), b) for f, t, b, _ in first] == [
            ("2022-01-01", "2022-12-31", 0)]
        second = ws.record_look(conn, "backtest", -902, symbol, date(2022, 6, 1),
                                date(2023, 3, 1))
        assert second[0][2] == 1
        assert ws.record_look(conn, "backtest", -903, symbol, date(2021, 1, 4),
                              date(2021, 12, 31)) == []      # outside the window: no look
    finally:
        conn.rollback()
        with conn.cursor() as cur:
            cur.execute("DELETE FROM test_window_looks WHERE instrument = %s", (symbol,))
            cur.execute("DELETE FROM strategies WHERE name = %s", (name,))
        conn.commit()


def test_a_holdout_run_that_records_nothing_is_refused(capsys):
    from nn import walkforward as wf
    assert wf.main(["--test", "--no-save"]) == 2
    assert "always recorded" in capsys.readouterr().out
