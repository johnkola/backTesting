// What kind each registered strategy is, as the engine's registry says
// (`GET /api/strategies`, `kind`): `model`, or `benchmark` for the five
// indicator rules the author keeps runnable for comparison only (Design-Gaps
// C8, 2026-11-19).
//
// The list is the engine's; nothing is listed here. A result names its
// strategy, and the label travels with it from this one place, so Results and
// a result's page cannot disagree with the Run tab about what a rule is.

/** Name -> kind, from the engine's `items`. Rows without a kind are left out. */
function kindsFrom(items) {
  const out = new Map();
  for (const s of Array.isArray(items) ? items : []) {
    if (s && typeof s.name === 'string' && (s.kind === 'model' || s.kind === 'benchmark')) {
      out.set(s.name, s.kind);
    }
  }
  return out;
}

/**
 * The kind of the strategy a result ran, or null when it is not known: the
 * engine did not answer, or the name is no longer registered. Null is shown as
 * no label, never guessed.
 */
function kindOf(kinds, strategyName) {
  return kinds?.get(strategyName) ?? null;
}

/**
 * A reader over `fetchItems` (the engine call) that remembers the last good
 * answer for `ttlMs`. A failed call returns the previous answer when there is
 * one, else an empty map: a result page must still render with the engine down.
 */
function cachedKinds(fetchItems, ttlMs = 60_000, now = Date.now) {
  let kinds = new Map();
  let at = -Infinity;
  return async function read() {
    if (now() - at < ttlMs) return kinds;
    try {
      kinds = kindsFrom(await fetchItems());
      at = now();
    } catch {
      // Keep what was known; try again on the next request.
    }
    return kinds;
  };
}

module.exports = { kindsFrom, kindOf, cachedKinds };
