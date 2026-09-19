-- One-off migration: rebuild `candles` with a one-year chunk interval.
--
-- Why: the hypertable was created at TimescaleDB's default 7-day chunk
-- interval. For daily bars spanning decades that yields thousands of chunks
-- holding a handful of rows each. Every full-table scan then takes a lock per
-- chunk per parallel worker and fails with "out of shared memory ... you might
-- need to increase max_locks_per_transaction" — `backtest-audit` hits this —
-- and compressing ~17-row chunks costs more space than it saves.
--
-- schema.sql now creates yearly chunks and calls set_chunk_time_interval, but
-- that only governs *new* chunks. This script consolidates the ones already on
-- disk, by copying every row into a correctly-partitioned table and swapping
-- the two. It is not run automatically: rebuilding a table people have data in
-- is your call, not the schema bootstrap's.
--
-- Before running:
--   1. Stop anything holding a connection — `docker compose stop web loader`.
--      DROP TABLE needs an exclusive lock and will otherwise block behind the
--      Node and loader pools.
--   2. Back up. `docker exec backtest-timescaledb pg_dump -U backtest -d backtest
--      -t candles -Fc -f /tmp/candles.dump` and copy it out with `docker cp`.
--
-- Run with:
--   docker exec -i backtest-timescaledb psql -U backtest -d backtest \
--     -v ON_ERROR_STOP=1 < scripts/migrate_candles_chunk_interval.sql
--
-- Everything through the swap is one transaction: if any step fails, the old
-- table is still there, untouched. Reads decompress transparently, so this
-- works whether or not the existing chunks are compressed.

\set ON_ERROR_STOP on

BEGIN;

-- Refuse to run if a previous attempt left its scratch table behind, rather
-- than silently building on top of it.
DO $guard$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_class WHERE relname = 'candles_migrate') THEN
        RAISE EXCEPTION
            'candles_migrate already exists — inspect it and DROP it before re-running';
    END IF;
END
$guard$;

-- Mirrors the `candles` definition in schema.sql. Written out rather than
-- CREATE TABLE ... LIKE because LIKE does not copy foreign keys, and copied
-- indexes would keep the scratch table's names after the rename and then be
-- duplicated by schema.sql's CREATE INDEX IF NOT EXISTS on the next boot.
CREATE TABLE candles_migrate (
    id BIGINT NOT NULL DEFAULT nextval('candles_id_seq'),
    instrument_id BIGINT NOT NULL,
    source_id BIGINT NOT NULL,
    timeframe VARCHAR(10) NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL,
    open DOUBLE PRECISION NOT NULL,
    high DOUBLE PRECISION NOT NULL,
    low DOUBLE PRECISION NOT NULL,
    close DOUBLE PRECISION NOT NULL,
    volume DOUBLE PRECISION DEFAULT 0,
    PRIMARY KEY (instrument_id, timeframe, source_id, timestamp)
);

SELECT create_hypertable('candles_migrate', 'timestamp',
                         chunk_time_interval => INTERVAL '1 year');

INSERT INTO candles_migrate
    (id, instrument_id, source_id, timeframe, timestamp, open, high, low, close, volume)
SELECT id, instrument_id, source_id, timeframe, timestamp, open, high, low, close, volume
  FROM candles;

DO $verify$
DECLARE
    old_n BIGINT;
    new_n BIGINT;
BEGIN
    SELECT count(*) INTO old_n FROM candles;
    SELECT count(*) INTO new_n FROM candles_migrate;
    IF old_n <> new_n THEN
        RAISE EXCEPTION 'row count mismatch — refusing to swap: candles=%, candles_migrate=%',
            old_n, new_n;
    END IF;
    RAISE NOTICE 'copied % rows', new_n;
END
$verify$;

-- The sequence belongs to the old table; detach it first or DROP TABLE takes it
-- with them and the new table's default breaks.
ALTER SEQUENCE candles_id_seq OWNED BY NONE;

DROP TABLE candles;
ALTER TABLE candles_migrate RENAME TO candles;
ALTER SEQUENCE candles_id_seq OWNED BY candles.id;

-- RENAME TABLE does not rename the objects hanging off it, so the primary key
-- and the hypertable's time index would keep the scratch name. Put them back to
-- what a fresh schema.sql bootstrap produces, so a dump of a migrated database
-- is indistinguishable from a dump of a new one.
ALTER TABLE candles RENAME CONSTRAINT candles_migrate_pkey TO candles_pkey;
ALTER INDEX candles_migrate_timestamp_idx RENAME TO candles_timestamp_idx;

-- LIKE wouldn't have carried these; re-add them by hand.
ALTER TABLE candles ADD CONSTRAINT candles_instrument_id_fkey
    FOREIGN KEY (instrument_id) REFERENCES instruments(id);
ALTER TABLE candles ADD CONSTRAINT candles_source_id_fkey
    FOREIGN KEY (source_id) REFERENCES data_sources(id);

CREATE INDEX IF NOT EXISTS idx_candles_lookup
    ON candles(instrument_id, timeframe, source_id, timestamp);

-- Same compression settings schema.sql applies. Dropping the old table also
-- dropped its compression policy job; it is re-added after the commit below.
ALTER TABLE candles SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'instrument_id, source_id, timeframe',
    timescaledb.compress_orderby = 'timestamp DESC'
);

COMMIT;

SELECT add_compression_policy('candles', INTERVAL '7 days', if_not_exists => TRUE);

-- Note: with yearly chunks a 7-day compression policy only compresses a chunk
-- once the whole year is older than 7 days, so the current year stays
-- uncompressed. That is an improvement for re-imports (no more 409
-- compressed_chunk on this year's data) but means re-importing an *older* year
-- decompresses a full year at once.

\echo ''
\echo 'Chunk count after migration:'
SELECT count(*) AS chunks FROM timescaledb_information.chunks WHERE hypertable_name = 'candles';
