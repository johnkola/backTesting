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
        blocked = [f.name for f in cfg.features
                   if feature_catalog.status_of(f) == "blocked"]
        # No free source at full history: the design treats this as a hard stop.
        assert "credit_spread_hy" in blocked

    def test_deferred_reasons_become_undecided_not_blocked(self, cfg):
        by_name = {f.name: f for f in cfg.features}
        # Nothing about these is impossible; the decision is just deferred, and
        # calling them blocked would overstate the problem.
        for name in ("put_call_ndx", "breakeven_inflation", "vix_term_structure"):
            assert feature_catalog.status_of(by_name[name]) == "undecided", name

    def test_a_disabled_feature_is_never_offered_as_active(self, cfg):
        # The builder offers only `active`, so a column that is off must not
        # claim to be -- that is the whole purpose of the mapping.
        for f in cfg.features:
            if not f.enabled:
                assert feature_catalog.status_of(f) != "active", f.name

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

    def test_ranges_come_from_stored_values_not_from_config(self, cfg):
        conn = self._conn()
        try:
            feature_catalog.sync(conn, cfg)
            with conn.cursor() as cur:
                # The expectation uses the same width-aware name match the
                # sync does. A cyclical column is stored as `<name>_sin` and
                # `<name>_cos`, so a naive `feature_name = d.name` finds
                # nothing for the three calendar columns -- which is the bug
                # this join was fixed to avoid, and an expectation computed the
                # naive way would re-introduce it here.
                cur.execute(
                    "SELECT d.name, d.range_start, d.range_end, "
                    "       (SELECT min(v.timestamp) FROM feature_values v "
                    "         WHERE ((d.width = 1 AND v.feature_name = d.name) "
                    "             OR (d.width > 1 AND v.feature_name IN "
                    "                 (d.name || '_sin', d.name || '_cos'))) "
                    "           AND v.value IS NOT NULL) "
                    "  FROM feature_definitions d "
                    " WHERE d.range_start IS NOT NULL ORDER BY d.name LIMIT 8")
                rows = cur.fetchall()
            for name, lo, hi, actual_lo in rows:
                assert lo == actual_lo, name
                assert hi >= lo, name
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
