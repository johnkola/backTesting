# QQQ Model — Features

Feature reference for the QQQ neural-network timing model, extracted from [DESIGN.md](DESIGN.md). DESIGN.md is the source of truth.

## The Normalized Feature Table

All inputs live in one table: each row is a trading day, each column is a normalized feature. Nothing goes in as a raw value. The columns:

| Feature | Normalization | In the model | Source and coverage | Intraday availability |
| --- | --- | --- | --- | --- |
| Price - open | `open / previous close - 1`, the overnight gap | Yes | QQQ daily bars, Yahoo dividend-adjusted, 1999-03-10 on | Free hourly \~2yr back (Yahoo); paid for deep history (Polygon \~$29/mo 5yr, \~$79/mo 10yr) |
| Price - high (max) | `high / previous close - 1` | Yes | Same bar | Same as QQQ bar |
| Price - low (min) | `low / previous close - 1` | Yes | Same bar | Same as QQQ bar |
| Price - close | `close / previous close - 1`, the daily return | Yes | Same bar | Same as QQQ bar |
| Volume | Rolling z-score, or a ratio to a rolling average | Yes | Same bar | Same as QQQ bar |
| Options indicator (template) | Rolling z-score if bounded and mean-reverting; returns or z-score if it drifts | Template - VXN is what fills it | - | Template |
| VXN - open | Rolling z-score | Yes | VXN daily bars, Yahoo, 2001-01-23 on | Free hourly \~2yr back (Yahoo); paid for deep history (Polygon) |
| VXN - high (max) | Rolling z-score | Yes | Same bar | Same as VXN bar |
| VXN - low (min) | Rolling z-score | Yes | Same bar | Same as VXN bar |
| VXN - close | Rolling z-score, so the model sees how stretched volatility is versus its own recent history | Yes | Same bar | Same as VXN bar |
| RSI (14-day) | Rescale from 0-100 into 0-1 | No - data only | Computed from QQQ price | Recomputed from intraday QQQ bars |
| MACD | Rolling z-score | No - data only | Computed from QQQ price | Recomputed from intraday QQQ bars |
| Realized volatility | Rolling z-score | No - data only | Computed from QQQ price | Recomputed from intraday QQQ bars |
| Average put-call ratio across the Nasdaq-100 | Rolling z-score | Later - paid | No free source; per-name options history is paid, and CBOE's free daily file is gated | Paid only; intraday options data is expensive and involved |
| QQQ put-call ratio (its own options) | Rolling z-score | Later - paid | QQQ options chain; history is paid. Reads institutional index hedging | Daily; forward-filled through intraday bars |
| CBOE equity put-call ratio (aggregate) | Rolling z-score | Later - paid | CBOE daily equity ratio; the free file is gated (403). Reads retail speculative sentiment | Daily; forward-filled through intraday bars |
| 10-year Treasury yield | Daily change or rolling z-score | Yes | FRED DGS10, 1962-01-02 on | No intraday - FRED publishes once daily |
| Crude oil (WTI) | Rolling z-score, not returns - WTI printed -36.98 on 2020-04-20 and percent change across a negative price is meaningless | Yes | FRED DCOILWTICO, 1986-01-02 on | No intraday - FRED publishes once daily |
| Breakeven inflation rate | Daily change or rolling z-score | Yes | FRED T10YIE, 2003-01-02 on - the series that sets the training start | No intraday - FRED publishes once daily |
| Market breadth (percent above their own 200-day MA) | Rescale 0-100 into 0-1, or rolling z-score | Yes | Computed from the 101 constituents, 1984-03-30 on | Only if constituents go intraday (paid); otherwise daily |
| Day of week | Cyclical sine and cosine | Yes | From the date | From the timestamp - any resolution |
| Day of year | Cyclical sine and cosine | Yes | From the date | From the timestamp - any resolution |
| Day of month | Generated from the date; cyclical encoding | In | Free | Yes — from the date |
| Holiday proximity | Flag, interacted with day of week | Yes | From an exchange trading-holiday calendar | From the calendar - any resolution |
| Yield curve slope (10-year minus 2-year) | Rolling z-score | Yes | FRED T10Y2Y, 1976-06-01 on. A regime signal independent of everything above | No intraday - FRED publishes once daily |
| VXN minus VIX spread | Rolling z-score | Yes | Computed; VIX from 1990, VXN from 2001. Tech-specific fear above broad-market fear | Free hourly \~2yr back (Yahoo VXN and VIX); paid for deep history |
| Dollar index | Daily returns or rolling z-score | Yes | Yahoo DX-Y.NYB, 1971-01-04 on. Dollar strength hits multinational tech earnings | Free hourly \~2yr back (Yahoo); paid for deep history |
| Copper / gold ratio | Rolling z-score | Yes | Yahoo HG=F and GC=F, both 2000-08-30 on. Growth versus fear, tracks the cycle | Free hourly \~2yr back (Yahoo HG=F, GC=F); paid for deep history |
| Nasdaq-100 return dispersion | Rolling z-score | Yes | Computed from the cached constituent closes. Macro-driven versus stock-picking regime | Only if constituents go intraday (paid); otherwise daily |
| Nasdaq-100 advance/decline | Rolling z-score | Yes | Computed from the cached constituent closes. Fast breadth, complementing the slow 200-day measure | Only if constituents go intraday (paid); otherwise daily |
| Gold | Daily returns or rolling z-score | Yes | Yahoo GC=F, 2000-08-30 on. Safe-haven demand on its own, separate from the copper/gold ratio | Free hourly \~2yr back (Yahoo GC=F); paid for deep history |
| 2-year Treasury yield | Daily change or rolling z-score | Yes | FRED DGS2, 1976-06-01 on. Short-rate expectations, which drive tech valuations | No intraday - FRED publishes once daily |
| Nasdaq-100 new highs minus new lows | Rolling z-score | Yes | Computed from the cached constituent closes. Another breadth angle, no new download | Only if constituents go intraday (paid); otherwise daily |
| VIX term structure (VIX3M / VIX) | Rolling z-score | Undecided | Yahoo VIX3M starts 2006-07-17, so it is blank for the first three training years | Free hourly \~2yr back (Yahoo); paid for deep history |
| High-yield credit spread | Rolling z-score | Blocked | FRED's free endpoint truncates the ICE BofA series to 2023-09 on. Needs an API key or another source | Paid only; already blocked at daily |
| QQQ / SPY relative strength | Rolling z-score | Undecided | Both series in hand from 1999. Price-derived, but from a second index | Free hourly \~2yr back (Yahoo QQQ, SPY); paid for deep history |
|  |  |  |  |  |
| VIX | Rolling z-score | Yes | Yahoo ^VIX, 1990-01-02 on. Broad-market fear, separate from the tech-specific VXN | Free hourly \~2yr back (Yahoo); paid for deep history |
| Options volume (Nasdaq-100 total) | Rolling z-score | Later - paid | No free source; per-name options history is paid, same gating as the put-call ratio | Paid only; intraday options data is expensive and involved |
| Investment-grade credit spread | Rolling z-score | Yes | FRED BAMLC0A0CM, 1996-12-31 on. Full history, unlike the truncated high-yield series | No intraday - FRED publishes once daily |

Two rules govern the "In the model" column. First, anything derived from QQQ's own price - RSI, MACD, realized volatility - stays out of the network and is kept as data only. The model already sees the full bar, so it can learn whatever those indicators encode; feeding them in again adds dimensions and overfitting room without adding information. They stay computed and stored, where the Ta4j strategies and the charts use them. The line is a lookback window: the raw bar is an observation and goes in, while anything averaged over trailing days is a derivation and stays out. Market breadth, dispersion and advance/decline are the apparent exceptions - all price-derived, but from the other hundred names, which is information QQQ's own series does not contain. Second, sequencing: every free column is built first, and a column that has to be paid for comes last, once the free table is working - which is why the average put-call ratio is marked Later rather than dropped.

The other values in that column: Template marks the generic row that VXN instantiates rather than a column in its own right; Undecided marks a candidate whose history is too short or whose worth is not yet settled; Blocked marks one with no free source at full history. Counting only the committed columns, the model eats 27 of the 38 rows - 29 input values per bar, once the two cyclical calendar columns expand into sine and cosine, times the lookback window.

The key point on calendar features: never encode days as 1, 2, 3, because the model would read them as a number line. Cyclical encoding turns each into two values so the model sees the cycle as continuous.

On the holiday-proximity flag: the day-of-year wrap handles fixed-date holidays (July 4, New Year) well, but floating holidays (Labor Day, Thanksgiving) drift week to week and get smeared. A flag column such as day before a market holiday or day before a long weekend would catch pre-holiday de-risking directly. A holiday's effect also depends on which weekday it falls on - one that creates a three-day weekend drives more position-squaring than a midweek one - so the flag should interact with the day-of-week feature rather than stand alone.

## The Instruments Explained

This section exists because the project is also a teaching tool for the financial side, not just an application. Each non-trivial input is described here: what the instrument actually is, what it tells you about the market, and where the data comes from. The obvious price and volume fields are skipped. The instruments are grouped by theme.

### Volatility

**VIX** is the volatility index of the S&P 500 — the broad-market fear gauge, measuring the market's expected 30-day swing read from index option prices. It is carried on its own, alongside VXN, because it captures fear across the whole market rather than tech specifically, and the two can diverge. From Yahoo as daily bars back to 1990.

**VXN** is the Nasdaq-100 volatility index — the fear gauge for tech, the Nasdaq cousin of the better-known VIX. It measures the market's expectation of how much QQQ will swing over the next 30 days, read from the prices of options on the index: when traders pay up for protection, VXN rises. High VXN means fear and expected turbulence; low VXN means complacency. From Yahoo as daily bars back to 2001.

**VXN minus VIX spread** is tech-specific fear measured above broad-market fear. VIX is the volatility of the S&P 500, VXN the volatility of the Nasdaq-100; subtract one from the other and you isolate how much extra anxiety is aimed at tech specifically, over and above whatever is worrying the whole market. A widening spread says the fear is concentrated in tech. Both series come free from Yahoo (VIX from 1990, VXN from 2001), and the feature is just the subtraction.

**VIX term structure (VIX3M / VIX)** compares three-month expected volatility against one-month. Normally the longer horizon is priced higher, so the ratio sits above one — a calm, contango market. When near-term fear spikes, one-month volatility jumps above three-month and the ratio flips below one, which historically flags acute stress. Yahoo's VIX3M series only starts in 2006, so it is blank for the first three training years, which is why it is marked undecided.

### Rates and Macro

**10-year Treasury yield (DGS10)** is the interest rate on 10-year US government debt — the benchmark risk-free long rate the whole market prices against. It drives discount rates on future earnings, which matters enormously for growth-heavy tech valuations: when the 10-year rises, long-dated tech cash flows are worth less today. From FRED, daily, back to 1962.

**2-year Treasury yield (DGS2)** is the rate on 2-year government debt, a close proxy for where the market thinks the Fed will set short-term rates. Short-rate expectations drive tech valuations directly, since they set the cost of money in the near term. From FRED, daily, back to 1976.

**Yield curve slope (10-year minus 2-year, T10Y2Y)** is the gap between the long and short Treasury yields. Normally positive — longer money costs more — but when it goes negative (an inverted curve), it has preceded most recessions, making it one of the most watched macro regime signals there is. It is independent of everything price-based above, which is its value. FRED publishes the spread directly, daily, back to 1976.

**Breakeven inflation rate (T10YIE)** is the market's expected average inflation over the next 10 years, derived from the gap between regular Treasuries and inflation-protected ones. FRED publishes it directly rather than making you subtract TIPS yields yourself. It is the series that sets the 2003 training start, since that is where clean daily history begins.

### Commodities

**Crude oil (WTI, DCOILWTICO)** is the US benchmark oil price, a broad read on economic demand and an input to inflation. It is normalized as a rolling z-score rather than returns for a specific reason: WTI printed negative — minus 36.98 dollars — on April 20, 2020, and a percent change across a negative price is meaningless. FRED's spot series runs to 1986 without the contract-roll artifacts a futures chain would carry.

**Gold (GC=F)** is the classic safe-haven asset. When fear rises or the dollar weakens, money often flows into gold, so it carries information about risk appetite on its own. From Yahoo futures, back to 2000.

**Copper / gold ratio** is growth versus fear in a single number. Copper is an industrial metal whose demand tracks real economic activity (Dr. Copper), while gold is the fear asset; divide copper by gold and a rising ratio signals optimism about growth, a falling ratio signals a flight to safety. It tracks the economic cycle well. Both legs come free from Yahoo, and the feature is just the division.

### Breadth and Internals

These all describe the health of the move underneath the index — whether a rally is broad or driven by just a few giant names, which matters a lot for a cap-weighted index like the Nasdaq-100.

**Market breadth (percent above the 200-day moving average)** is the share of the 100 constituents trading above their own long-term trend line. High means most stocks are healthy and the rally is broad; low means the index is being propped up by a handful of names while the rest sag. Computed yourself from the constituent prices — no single feed provides it directly.

**Advance / decline** is a faster breadth measure: how many names rose versus fell on the day. It complements the slow 200-day breadth with a day-to-day pulse. Falls out of the same cached constituent closes, so no extra download.

**New highs minus new lows** counts how many constituents hit new highs against how many hit new lows — another angle on whether strength or weakness is spreading. Also computed from the cached closes.

**Return dispersion** measures how spread out the individual stock returns are. Low dispersion means names are moving together (a macro-driven, risk-on-risk-off regime); high dispersion means stock-picking matters and names are diverging. It distinguishes a market moving as one block from one moving name by name. Computed from the same constituent closes.

A shared caveat on this whole family: no free source has point-in-time index membership, so today's constituent list is applied to the entire history. That is survivorship bias — the names that survived to today look better in past drawdowns than the real index did — and any backtest leaning on these columns has to account for it.

### Cross-Asset

**Dollar index (DX-Y.NYB)** measures the US dollar against a basket of major currencies. A strong dollar eats into the overseas earnings of the multinational tech names that dominate QQQ, so dollar strength is a genuine headwind for the index. From Yahoo, back to 1971.

**QQQ / SPY relative strength** is the Nasdaq-100 measured against the S&P 500 — is tech leading the broad market or lagging it? It is price-derived like the QQQ features, but the information comes from a second index, so it adds something QQQ's own series does not contain. Both series are in hand from 1999; marked undecided pending whether it earns its place.

### Options Activity

**Put-call ratios (three separate features)** compare put volume to call volume — above 1 leans bearish or hedged, below 1 leans bullish. They are kept as three columns because they measure different crowds, not substitutes for each other. **QQQ's own ratio** reads institutional index hedging. The **CBOE aggregate equity ratio** reads retail speculative sentiment. The **Nasdaq-100 average** reads positioning across the individual index names. All three are daily, forward-filled in the intraday phase, and marked Later, paid, since none has free history.

**Options volume (Nasdaq-100 total)** is how many option contracts trade across the index names on the day. A surge signals that traders are positioning hard — hedging or speculating — ahead of an expected move, so it reads the intensity of activity rather than its direction. It is not price-derived; it comes from options exchanges. Like the put-call ratio, there is no free history, so it is marked Later, paid, and waits until the free table is working.

### Credit

**Investment-grade credit spread** is the extra yield demanded on safe, high-rated corporate bonds over Treasuries. It is the calmer cousin of the high-yield spread: it moves less, but it has full clean history from FRED (unlike the truncated high-yield series), so it carries a usable credit signal across the whole training period. From FRED, daily, back to 1996.

**High-yield credit spread** is the extra yield investors demand to hold risky corporate bonds over safe Treasuries. It is one of the best early-warning signals in all of markets: when the spread widens, credit investors are getting nervous, and that stress usually shows up in stocks soon after. The problem is purely practical — FRED's free endpoint truncates the ICE BofA series to just the last few years instead of the full thirty, so without a paid API key or another source this column is blocked at full history.
