"""Project `features.toml` into the `feature_definitions` table.

ARCHITECTURE.md §8.1a: the Features tab reads "the feature catalogue from
`feature_definitions`: every feature with its range, cadence, scope, status,
cost, units and lineage".

The TOML stays the source of truth -- the pipeline reads it to decide what to
compute, and two editable copies of one decision is how a feature ends up
enabled in one place and disabled in the other. This module is the one writer
of the table, and it only ever writes what the file says.

Two fields do not come from the file, because they are facts about stored data
rather than about configuration:

  range_start / range_end  the feature's OWN available range, read back from
                           `feature_values`. NULL until the column has been
                           built once, which is the honest answer: its range is
                           not yet known. Design.md is explicit that the table
                           is never trimmed to the overlap and never padded, so
                           each feature's range really is its own.

Run it after editing features.toml, or after rebuilding a feature set:

    python -m nn.feature_catalog --sync
"""

from __future__ import annotations

import argparse
import os
from typing import Any

from nn import feature_config

# `status` in the TOML is absent for a usable column and names the reason when
# it is not. The table wants a closed set, and §8.2a fixes it at these three:
# "active | undecided | blocked — only active ones are offered in the form".
#
# `later-paid` and `short-history` both map to `undecided` rather than `blocked`:
# nothing about them is impossible, the decision is just deferred. `blocked`
# means there is no free source at full history, which is a different problem
# and the one the design treats as a hard stop.
# `not-imported` joins them as `undecided` for the same reason: the series is
# free at full history and nothing about it is impossible, it simply is not in
# the store yet. Calling it `blocked` would claim a dead end that does not
# exist, and `active` would offer the builder a column with no values behind it.
_STATUS_MAP = {
    None: "active",
    "": "active",
    "blocked": "blocked",
    "undecided": "undecided",
    "later-paid": "undecided",
    "short-history": "undecided",
    "not-imported": "undecided",
}


def status_of(feature: feature_config.Feature) -> str:
    """The catalogue status for a configured feature.

    A column that is disabled with no reason recorded is `undecided`, not
    `active`: it is off, and saying `active` would offer it in the builder.
    """
    mapped = _STATUS_MAP.get(feature.status)
    if mapped is None:
        # An unrecognised status is reported rather than guessed -- a typo in
        # the TOML should be visible, not silently become `active`.
        raise ValueError(
            f"{feature.name}: unknown status {feature.status!r}; "
            f"expected one of {sorted(k for k in _STATUS_MAP if k)}"
        )
    if mapped == "active" and not feature.enabled:
        return "undecided"
    return mapped


def source_of(feature: feature_config.Feature) -> str:
    """Where the values come from, for the catalogue's `source` column.

    A stored series names its provider; a derived one says `computed`, because
    naming one of its inputs would imply the column is read from there.
    """
    if feature.is_derived:
        return "computed"
    return str(feature.series.get("source", "computed"))


def rows_from_config(cfg: feature_config.FeatureConfig) -> list[dict[str, Any]]:
    """Every feature in the file, as catalogue rows. Pure -- no database."""
    return [
        {
            "name": f.name,
            "source": source_of(f),
            "cadence": f.cadence,
            "scope": f.scope,
            "status": status_of(f),
            "cost": f.cost,
            "units": f.units,
            "code_version": f.code_version,
            "method": f.method,
            "enabled": f.enabled,
            "note": f.note,
            "width": f.width,
        }
        for f in cfg.features
    ]


_UPSERT = """
INSERT INTO feature_definitions
    (name, source, cadence, scope, status, cost, units, code_version, method,
     enabled, note, width)
VALUES (%(name)s, %(source)s, %(cadence)s, %(scope)s, %(status)s, %(cost)s,
        %(units)s, %(code_version)s, %(method)s, %(enabled)s, %(note)s, %(width)s)
ON CONFLICT (name) DO UPDATE SET
    source = EXCLUDED.source,
    cadence = EXCLUDED.cadence,
    scope = EXCLUDED.scope,
    status = EXCLUDED.status,
    cost = EXCLUDED.cost,
    units = EXCLUDED.units,
    code_version = EXCLUDED.code_version,
    method = EXCLUDED.method,
    enabled = EXCLUDED.enabled,
    note = EXCLUDED.note,
    width = EXCLUDED.width
"""

# The range is whatever the stored values actually span, per feature. A LEFT
# JOIN would be wrong here: a feature with no rows yet must end up NULL, not 0.
# A cyclical column is stored under TWO names (`day_of_week_sin` and
# `day_of_week_cos`), so matching `feature_name = name` finds nothing for the
# three calendar columns and left their range NULL. `width` is the
# discriminator rather than a prefix match, because `price_close` would
# otherwise claim any future `price_close_*`.
_REFRESH_RANGES = """
UPDATE feature_definitions d
   SET range_start = r.lo, range_end = r.hi
  FROM (SELECT d2.name, MIN(v.timestamp) AS lo, MAX(v.timestamp) AS hi
          FROM feature_definitions d2
          JOIN feature_values v
            ON (d2.width = 1 AND v.feature_name = d2.name)
            OR (d2.width > 1
                AND v.feature_name IN (d2.name || '_sin', d2.name || '_cos'))
         WHERE v.value IS NOT NULL
         GROUP BY d2.name) r
 WHERE r.name = d.name
   AND (d.range_start IS DISTINCT FROM r.lo OR d.range_end IS DISTINCT FROM r.hi)
"""

# A feature the file no longer defines is removed, so the catalogue cannot show
# a column that nothing can produce. Deliberately a delete rather than a
# tombstone: the TOML is the record of what was ever considered, and it keeps
# retired columns with their reason instead.
_PRUNE = "DELETE FROM feature_definitions WHERE name <> ALL(%(names)s)"


def sync(conn, cfg: feature_config.FeatureConfig | None = None) -> dict[str, int]:
    """Write the catalogue from the file. Returns what changed."""
    cfg = cfg or feature_config.load()
    rows = rows_from_config(cfg)

    with conn.cursor() as cur:
        # Attribute the write to this job rather than to the login, so the
        # catalogue's audit columns say what produced it.
        cur.execute("SELECT set_config('app.actor', %s, false)",
                    (os.environ.get("DB_ACTOR", "loader:feature_catalog"),))
        for row in rows:
            cur.execute(_UPSERT, row)
        upserted = len(rows)

        cur.execute(_PRUNE, {"names": [r["name"] for r in rows]})
        pruned = cur.rowcount

        cur.execute(_REFRESH_RANGES)
        ranged = cur.rowcount
    conn.commit()
    return {"upserted": upserted, "pruned": pruned, "rangesUpdated": ranged}


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Sync feature_definitions from features.toml.")
    ap.add_argument("--sync", action="store_true",
                    help="Write the catalogue. Without it, print what would be written.")
    args = ap.parse_args()

    cfg = feature_config.load()
    rows = rows_from_config(cfg)

    if not args.sync:
        print(f"{len(rows)} features in {feature_config.DEFAULT_PATH}:")
        for r in rows:
            print(f"  {r['name']:<24} {r['scope']:<11} {r['cadence']:<8} "
                  f"{r['status']:<10} {r['cost']:<5} {r['units']:<8} {r['code_version']}")
        print("\nNothing written. Pass --sync to write.")
        return 0

    from loader.db import pool

    with pool().connection() as conn:
        counts = sync(conn, cfg)
    print(f"synced: {counts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
