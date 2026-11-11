/**
 * The end of a date range, as a SQL bound (DESIGN-GAPS I6).
 *
 * A range end is meant to include its last day. `timestamp <= '2025-12-31'`
 * compares against midnight, which is where a D1 bar sits, so daily series
 * kept their last day while intraday series lost it: every M30 bar of the
 * 31st is after midnight. A bare date therefore becomes `timestamp < the next
 * day at 00:00 UTC`, which holds the whole day on any timeframe and leaves D1
 * exactly as it was. A value with a time in it is an instant the caller chose
 * and is kept, inclusive, as typed.
 *
 * UTC, because candle timestamps are stored and served as UTC; leaving the
 * date for Postgres to cast would read it in the session's time zone.
 */
const BARE_DATE = /^\d{4}-\d{2}-\d{2}$/;

function rangeEnd(to) {
  const value = String(to).trim();
  if (!BARE_DATE.test(value)) return { op: '<=', value };
  const t = Date.parse(`${value}T00:00:00Z`);
  // An impossible date ('2025-02-30') is left for Postgres to refuse rather
  // than rolled over to a day nobody typed.
  if (Number.isNaN(t) || new Date(t).toISOString().slice(0, 10) !== value) {
    return { op: '<=', value };
  }
  return { op: '<', value: new Date(t + 86_400_000).toISOString() };
}

module.exports = { rangeEnd };
