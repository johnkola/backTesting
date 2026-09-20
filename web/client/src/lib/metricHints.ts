/**
 * What each performance metric means, and how to read it.
 *
 * The Run page and the result detail page both render metric tiles, with
 * slightly different label wording ("Return" vs "Total return", "Final equity"
 * vs "Final"), so the hints are keyed here rather than written beside either
 * set of labels — the alternative is two copies that drift apart.
 *
 * The wording tracks the "Glossary: performance metrics" and "How to read a
 * result" sections of ARCHITECTURE.md. A number is only worth showing if the
 * reader knows what would make it good, so each hint says how to judge it, not
 * just what it is. Keep them in sync with the doc when either changes.
 */
export const metricHints: Record<string, string> = {
  'total return':
    'Percentage change in equity from the first bar to the last, after costs. Read it against Buy & hold — beating "do nothing" on this instrument is the bar.',
  sharpe:
    'Return per unit of volatility, computed from per-bar returns. There is no universal good value; use it to rank strategies on the same data, not as a pass mark.',
  'max drawdown':
    'The worst peak-to-trough fall in equity during the run. Lower is better. A big number means a live trader would likely have switched the system off before the end.',
  'win rate':
    'Share of completed trades that made money. Misleading on its own — a 30% win rate with large wins and small losses is fine. Read it with profit factor.',
  'profit factor':
    'Gross wins divided by gross losses. Above 1 means the winners outweighed the losers in total dollars; below 1 means the strategy bled money overall.',
  trades:
    'Completed round trips — an entry paired with its exit. Under roughly 20, treat every other number here as unreliable rather than as a result.',
  'buy & hold':
    'What buying at the first bar and holding to the last would have returned, with no trading. This is the benchmark the strategy has to beat to be worth running.',
  'final equity':
    'Account value at the last bar, after any open position is force-closed. Commission and slippage are already taken out, so this is money you would actually hold.',
  initial:
    'Capital the run started with, before the first trade. Set it on the Run form, or leave it empty to use the engine default.',
}

/**
 * Hint for a tile label. Lookup is case-insensitive and tolerates the two
 * spellings each metric has across the pages; an unknown label returns
 * undefined so the caller renders a plain tile rather than an empty bubble.
 */
export function metricHint(label: string): string | undefined {
  const key = label.trim().toLowerCase()
  const aliases: Record<string, string> = {
    return: 'total return',
    final: 'final equity',
  }
  return metricHints[aliases[key] ?? key]
}
