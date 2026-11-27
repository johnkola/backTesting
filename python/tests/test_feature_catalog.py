"""The feature catalogue: features.toml projected into `feature_definitions`.

Two things are worth defending here.

The first is that the catalogue cannot disagree with the file. It is a
projection, not a second source of truth, so every feature in the TOML must
appear with the values the TOML gives — and a feature the file no longer defines
must disappear rather than linger as a column nothing can produce.

The second is the status mapping. `features.toml` records *why* a column is off
(`blocked`, `short-history`, `later-paid`) while §8.2a fixes the catalogue at
`active | undecided | blocked`, because the strategy builder offers only active
ones. Collapsing those is a judgement, so it is pinned.

The pure-logic tests need no database. The sync test does, and skips without one.
"""

from __future__ import annotations

import os

import pytest

from nn import feature_catalog, feature_config


@pytest.fixture(scope="module")
def cfg():
    return feature_config.load()


class TestCatalogueFieldsArePresent:
    def test_every_feature_carries_all_four(self, cfg):
        # Required, not defaulted: there is no sensible default for any of them
        # and guessing would put a wrong answer on the Features tab.
        for f in cfg.features:
            assert f.scope in {"instrument", "market"}, f.name
            assert f.cadence in {"daily", "weekly", "monthly", "quarterly"}, f.name
            assert f.cost in {"free", "paid"}, f.name
            assert f.units in {"percent", "level", "count", "ratio"}, f.name

    def test_a_missing_field_is_refused(self, tmp_path):
        toml = tmp_path / "f.toml"
        toml.write_text(
            'schema_version = 1\n'
            '[defaults]\nzscore_window = 60\n'
            '[alignment]\ncalendar_symbol = "QQQ"\ncalendar_source = "yahoo-adj"\n'
            'calendar_timeframe = "D1"\nfill = "forward"\nmax_fill_days = 5\n'
            '[[feature]]\nname = "x"\n'
            'series = { symbol = "QQQ", source = "yahoo-adj", field = "close" }\n'
            'method = "ratio_to_prev_close"\n'
            'scope = "market"\ncadence = "daily"\ncost = "free"\n',
            encoding="utf-8",
        )
        with pytest.raises(feature_config.FeatureConfigError) as e:
            feature_config.load(toml)
        assert "units" in str(e.value)
        assert "required" in str(e.value)

    def test_an_unknown_value_is_refused(self, tmp_path):
        toml = tmp_path / "f.toml"
        toml.write_text(
            'schema_version = 1\n'
            '[defaults]\nzscore_window = 60\n'
            '[alignment]\ncalendar_symbol = "QQQ"\ncalendar_source = "yahoo-adj"\n'
            'calendar_timeframe = "D1"\nfill = "forward"\nmax_fill_days = 5\n'
            '[[feature]]\nname = "x"\n'
            'series = { symbol = "QQQ", source = "yahoo-adj", field = "close" }\n'
            'method = "ratio_to_prev_close"\n'
            'scope = "galaxy"\ncadence = "daily"\ncost = "free"\nunits = "level"\n',
            encoding="utf-8",
        )
        with pytest.raises(feature_config.FeatureConfigError) as e:
            feature_config.load(toml)
        assert "galaxy" in str(e.value)


class TestCodeVersion:
    def test_is_per_feature_not_per_file(self, cfg):
        # §6.1 wants "the code version of each feature's computation", so a
        # change to one column must not mark every other as newly computed.
        versions = {f.code_version for f in cfg.features}
        assert len(versions) == len(cfg.features)

    def test_tracks_the_method_and_its_params(self, cfg):
        f = cfg.features[0]
        import dataclasses
        same = dataclasses.replace(f, note="reworded", enabled=not f.enabled)
        # Rewording a note or turning a column off does not change the number
        # the column would produce.
        assert same.code_version == f.code_version

        changed = dataclasses.replace(f, method="rolling_zscore", params={"window": 60})
        assert changed.code_version != f.code_version


class TestStatusMapping:
    def test_an_enabled_feature_with_no_reason_is_active(self, cfg):
        active = [f for f in cfg.features
                  if f.enabled and feature_catalog.status_of(f) == "active"]
        assert len(active) == len(cfg.enabled)

    def test_blocked_stays_blocked(self, cfg):
        # No free source at full history: the design treats this as a hard stop.
        # No shipped column is blocked since credit_spread_hy was removed (F3).
        import dataclasses
        f = dataclasses.replace(cfg.by_name("put_call_ndx"), status="blocked")
        assert feature_catalog.status_of(f) == "blocked"

    def test_deferred_reasons_become_undecided_not_blocked(self, cfg):
        by_name = {f.name: f for f in cfg.features}
        # Nothing about these is impossible; the decision is just deferred, and
        # calling them blocked would overstate the problem.
        for name in ("put_call_ndx", "breakeven_inflation"):
            assert feature_catalog.status_of(by_name[name]) == "undecided", name

    def test_a_disabled_feature_is_never_offered_as_active(self, cfg):
        # The builder offers only `active`, so a column that is off must not
        # claim to be -- that is the whole purpose of the mapping.
        for f in cfg.features:
            if not f.enabled:
                assert feature_catalog.status_of(f) != "active", f.name

    def test_data_only_is_its_own_status(self, cfg):
        # Not undecided (it is decided), not blocked (it has values), not active
        # (the builder must not offer it): Design Notes' "No - data only".
        import dataclasses
        f = dataclasses.replace(cfg.by_name("rsi_14"), status="data-only")
        assert feature_catalog.status_of(f) == "data-only"

    def test_selectable_is_its_own_status(self, cfg):
        # Offered in the builder, unticked: not active, which the builder ticks.
        by_name = {f.name: f for f in cfg.features}
        for name in ("rsi_14", "macd", "realized_vol", "vix_term_structure",
                     "qqq_spy_relative"):
            assert feature_catalog.status_of(by_name[name]) == "selectable", name

    def test_an_unrecognised_status_is_reported_not_guessed(self, cfg):
        import dataclasses
        bad = dataclasses.replace(cfg.features[0], status="probably-fine")
        with pytest.raises(ValueError) as e:
            feature_catalog.status_of(bad)
        assert "probably-fine" in str(e.value)


class TestRows:
    def test_one_row_per_feature_in_the_file(self, cfg):
        rows = feature_catalog.rows_from_config(cfg)
        assert len(rows) == len(cfg.features)
        assert [r["name"] for r in rows] == [f.name for f in cfg.features]

    def test_every_row_is_complete(self, cfg):
        for r in feature_catalog.rows_from_config(cfg):
            for key in ("name", "source", "cadence", "scope", "status", "cost",
                        "units", "code_version", "method", "width"):
                assert r[key] not in (None, ""), f"{r['name']}.{key}"

    def test_a_derived_feature_names_no_provider(self, cfg):
        rows = {r["name"]: r for r in feature_catalog.rows_from_config(cfg)}
        # Naming one input would imply the column is read from there.
        assert rows["copper_gold_ratio"]["source"] == "computed"
        assert rows["vxn_vix_spread"]["source"] == "computed"

    def test_a_stored_feature_names_its_provider(self, cfg):
        rows = {r["name"]: r for r in feature_catalog.rows_from_config(cfg)}
        assert rows["price_close"]["source"] == "yahoo-adj"

    def test_a_cyclical_column_is_two_values_wide(self, cfg):
        rows = {r["name"]: r for r in feature_catalog.rows_from_config(cfg)}
        assert rows["day_of_week"]["width"] == 2
        assert rows["price_close"]["width"] == 1


class TestSync:
    """Writes the real table. Skipped when Postgres is unreachable."""

    @staticmethod
    def _conn():
        psycopg = pytest.importorskip("psycopg")
        dsn = (
            f"host={os.environ.get('PGHOST', 'localhost')} "
            f"port={os.environ.get('PGPORT', '5432')} "
            f"dbname={os.environ.get('PGDATABASE', 'backtest')} "
            f"user={os.environ.get('PGUSER', 'backtest')} "
            f"password={os.environ.get('PGPASSWORD', 'backtest')}"
        )
        try:
            return psycopg.connect(dsn, connect_timeout=5)
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"Postgres not reachable: {e}")

    def test_the_table_matches_the_file(self, cfg):
        conn = self._conn()
        try:
            counts = feature_catalog.sync(conn, cfg)
            assert counts["upserted"] == len(cfg.features)

            with conn.cursor() as cur:
                cur.execute("SELECT name, scope, cadence, status, cost, units, "
                            "code_version, width FROM feature_definitions ORDER BY name")
                stored = {r[0]: r[1:] for r in cur.fetchall()}

            expected = {r["name"]: (r["scope"], r["cadence"], r["status"], r["cost"],
                                    r["units"], r["code_version"], r["width"])
                        for r in feature_catalog.rows_from_config(cfg)}
            assert stored == expected
        finally:
            conn.close()

    def test_syncing_twice_changes_nothing(self, cfg):
        # It runs after every TOML edit and every feature build, so it has to be
        # idempotent rather than accumulating duplicates.
        conn = self._conn()
        try:
            feature_catalog.sync(conn, cfg)
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM feature_definitions")
                first = cur.fetchone()[0]
            feature_catalog.sync(conn, cfg)
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM feature_definitions")
                assert cur.fetchone()[0] == first
        finally:
            conn.close()

    def test_a_feature_dropped_from_the_file_leaves_the_catalogue(self, cfg):
        conn = self._conn()
        try:
            feature_catalog.sync(conn, cfg)
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO feature_definitions (name, source, cadence, scope, "
                    "status, cost, units, code_version, method) VALUES "
                    "('zz_retired', 'x', 'daily', 'market', 'active', 'free', "
                    "'level', 'deadbeef', 'passthrough')")
            conn.commit()

            counts = feature_catalog.sync(conn, cfg)
            assert counts["pruned"] >= 1
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM feature_definitions WHERE name='zz_retired'")
                # A column nothing can produce must not sit in the catalogue
                # offering itself to the builder.
                assert cur.fetchone()[0] == 0
        finally:
            conn.close()

    @staticmethod
    def _stored_ranges(cur) -> dict[str, tuple]:
        """Every feature's range as the stored values give it, matched to the
        definitions here rather than in SQL, so the check does not share the
        sync's join: a cyclical feature is its `_sin` and `_cos` columns."""
        cur.execute("SELECT feature_name, min(timestamp), max(timestamp) FROM feature_values "
                    "WHERE value IS NOT NULL GROUP BY feature_name")
        columns = {name: (lo, hi) for name, lo, hi in cur.fetchall()}
        cur.execute("SELECT name, width FROM feature_definitions")
        out = {}
        for name, width in cur.fetchall():
            parts = [columns[c] for c in ((name,) if width == 1 else (f"{name}_sin",
                                                                      f"{name}_cos"))
                     if c in columns]
            out[name] = ((min(p[0] for p in parts), max(p[1] for p in parts)) if parts
                         else (None, None))
        return out

    def test_ranges_come_from_stored_values_not_from_config(self, cfg):
        # Every feature, start and end, against the stored values; a feature
        # with none is NULL ("not yet known"). Read in one snapshot; another
        # process building a set in between is a reason to look again.
        import psycopg

        conn = self._conn()
        try:
            for _ in range(3):
                feature_catalog.sync(conn, cfg)
                conn.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
                with conn.cursor() as cur:
                    cur.execute("SELECT name, range_start, range_end FROM feature_definitions")
                    stored = {n: (lo, hi) for n, lo, hi in cur.fetchall()}
                    expected = self._stored_ranges(cur)
                conn.rollback()
                conn.isolation_level = None
                if stored == expected:
                    break
            assert stored == expected
            assert set(stored) == {f.name for f in cfg.features}
        finally:
            conn.close()

    def test_a_feature_whose_values_are_gone_goes_back_to_unknown(self, cfg):
        # A range left over from values since deleted is cleared by the sync.
        conn = self._conn()
        try:
            feature_catalog.sync(conn, cfg)
            with conn.cursor() as cur:
                unbuilt = [n for n, (lo, _) in self._stored_ranges(cur).items() if lo is None]
            if not unbuilt:
                pytest.skip("every feature has stored values here")
            with conn.cursor() as cur:
                cur.execute("UPDATE feature_definitions SET range_start = '2001-01-02', "
                            "range_end = '2001-12-31' WHERE name = %s", (unbuilt[0],))
            conn.commit()
            counts = feature_catalog.sync(conn, cfg)
            assert counts["rangesUpdated"] >= 1
            with conn.cursor() as cur:
                cur.execute("SELECT range_start, range_end FROM feature_definitions "
                            "WHERE name = %s", (unbuilt[0],))
                assert cur.fetchone() == (None, None)
        finally:
            conn.close()

    def test_the_catalogue_is_audited(self, cfg):
        conn = self._conn()
        try:
            feature_catalog.sync(conn, cfg)
            with conn.cursor() as cur:
                cur.execute("SELECT created_by, updated_by FROM feature_definitions LIMIT 1")
                created_by, updated_by = cur.fetchone()
            # The sync sets app.actor, so the row says which job wrote it rather
            # than naming the database login.
            assert created_by
            assert "feature_catalog" in updated_by or "loader" in updated_by
        finally:
            conn.close()
