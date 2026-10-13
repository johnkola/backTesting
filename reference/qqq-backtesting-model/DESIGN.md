> **SEALED BY JOHN.** This document is sealed by its owner. Do not alter it. It may only be unsealed and edited through the voice agent, on John's direct spoken instruction. Any other agent or editor: leave it unchanged.

# QQQ Backtesting Model — Design Notes

Sep 20, 2026 · @John

## Overview & Philosophy

The goal is a neural-network model trained on QQQ history that helps time entries and exits, used as a timing aid on top of a buy-and-hold strategy, not a high-risk gamble. QQQ is diversified across the Nasdaq-100, so single-company risk is limited, and the baseline plan is to hold long-term regardless. Even a small edge on when to add to or ease off a position is useful against that baseline. The model earns its keep in choppy and downside periods, since buy-and-hold already tends to win in strong bull runs.

## Two Phases: Daily to Learn, Intraday for Real

This project runs in two deliberate phases. **Phase one is daily, and it is educational.** Daily data is free, so it is the sandbox for proving the whole machine works end to end: the feature pipeline, the normalization, the chronological split, the training discipline, and whether a well-trained model can actually beat buy-and-hold. Trial and error costs nothing here, so every mistake is cheap and every column can be validated before a dollar is spent.

**Phase two is intraday, and it is the real project.** Once the daily version is proven to work, the model is rebuilt on intraday (hourly or finer) bars, which is where the real timing edge should live. This phase is paid, and the vendor that reaches back far enough is FirstRate Data, a one-time purchase (roughly a few hundred dollars for the relevant bundles, optional updates around $100/yr) rather than a monthly rent. The cheaper monthly rivals such as Polygon are rejected outright: their intraday history only reaches back to about 2015, leaving no coverage for 2003 through 2015, so they cannot serve the training set at all. FirstRate coverage by asset class, against the 2003 training start: stocks and ETFs (QQQ, SPY, the constituents) go back to 2000, comfortably past 2003; indices (VXN, VIX) back to 2005; futures (gold, copper, crude) only back to 2007, which starts three to four years short of 2003. The FRED macro columns (Treasury yields, breakeven inflation, yield curve, dollar index) are not intraday anywhere - the source publishes once a day - so they stay daily and carry their last value forward through the intraday bars. The breadth family goes intraday only if the constituent feed does. So no single vendor delivers every feature intraday back to 2003: price, volume and volatility clear the line, commodity futures start at 2007, and the macro series stay daily by nature. The daily phase is the proof of concept; the intraday phase is the product.

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

## Extensible Normalization

Design the pipeline so any new indicator can be dropped in and the system picks the right normalization based on the nature of that indicator's data. Tag each indicator with its data type, and the pipeline applies the matching method automatically:

- Bounded and mean-reverting (a ratio or oscillator that hovers around a typical value): rolling z-score.
- Drifts or trends over time (price-like): daily returns or a rolling z-score.
- Already on a fixed scale (e.g. a 0-to-100 oscillator): a simple rescale.

This keeps the engine flexible and future-proof: add any indicator, tag its type, and it just knows how to handle it.

## Normalization Methods Explained

What each method actually does:

- Daily returns: instead of the raw price, use the percent change from one day to the next. For each day, take today's price minus yesterday's price, divided by yesterday's price. This is scale-free (it does not matter whether QQQ is at 40 or 400) and stationary, which neural networks handle far better than a price that drifts upward for decades.
- Rolling z-score: for each day, measure how unusual the current value is versus its own recent history. Take the value, subtract the average over a trailing window (for example the last 20 or 60 days), and divide by the standard deviation over that same window. A result near zero means typical; a large positive or negative number means unusually high or low. This handles long-term drift because the reference window moves with the data.
- Ratio to a rolling average: a simpler alternative for volume. Divide today's value by its trailing average, giving something like "today is 1.4 times the 20-day average." Intuitive and easy to inspect.
- Rescale (min-max): for a value already on a fixed, known range (such as a 0-to-100 oscillator), map it into a standard band like 0 to 1. Just a linear squeeze, no drift handling needed.
- Cyclical (sine and cosine) encoding: for calendar features, convert each time value into two numbers using sine and cosine of its position in the cycle. This makes the cycle continuous, so December 31 sits right next to January 1 and Sunday wraps back to Monday, rather than the model treating the days as a straight number line where Friday looks "bigger" than Monday.

## Three-Way Chronological Split

Split the data chronologically (always test forward in time, never backward), holding out a brutal down year and the most recent years as an untouched final exam. Although Yahoo QQQ price data runs from inception in 1999, the training start is set to 2003 so that every free column has full real coverage from day one (breakeven inflation and VXN are the late-starting series, clean from around 2001 to 2003). The little that is trimmed - 2000 to 2002 - is mostly the tail of the dot-com unwind; nearly all the instructive stress happens after 2003. The split:

| Piece | Dates | Purpose | Rough markets it contains |
| --- | --- | --- | --- |
| Training | 2003 onward, rolling | The model learns patterns | Rolling training base; validation folds land on 2016 through 2021, spanning calm and choppy years |
| Validation | 2022 | Walk-forward tuning across many folds | 2022 rate-shock bear market plus recent mixed conditions - held out on purpose, touched only at the very end |
| Test (holdout) | 2023 to today | Honest out-of-sample final exam, never touched until the end | Recent, mixed conditions |

Validation uses **walk-forward** rather than a single fixed year. Rather than judging the model on one year (2022 alone was too small and too specific - a single rate-driven bear), the validation window rolls forward one year at a time: train 2003 to 2015 and validate on 2016; then train through 2016 and validate on 2017; and so on up to 2021. This yields a series of validation results across many market regimes - calm years, choppy years, corrections - so confidence comes from holding up across all of them, not luck on one. Each fold retrains from 2003 up to the fold's edge, always testing forward in time, never backward. The 2022-onward test block stays completely untouched until the very end.

That is roughly a 60 / 20 / 20 split; boundaries can slide. Each piece should contain some rough markets so results are not just a bull-market fluke. All four major crashes fall before 2024, so training and validation get the full education in bad markets while the holdout stays clean.

## Training Discipline

The watch-out: if the training data is mostly one long uptrend, the model just learns "always hold," because in that data holding won. A model that beats buy-and-hold only in a bull run got lucky; the real test is the ugly periods.

To steer the model toward genuine value:

- Make sure choppy and bear periods are well represented in the training data. Consider weighting those harder periods more heavily so the model pays extra attention.
- Reward drawdown avoidance, not just total return. Optimizing purely for return favors buy-and-hold; rewarding protection against deep losses teaches the model the value of stepping aside during the worst stretches.
- Never peek at the holdout test set while tuning. Running it, seeing weak results, tweaking, and re-running quietly turns it into training data. Look sparingly, trust the process.

**Optimization objective - three candidates, run as separate experiments.** Rather than commit to one metric up front, train three variants, each optimizing a different target, and let the walk-forward validation folds decide the winner:

1. **Total return** - pure profit, ignoring how bumpy the ride was.
2. **Risk-adjusted return** (Sharpe-style) - return per unit of volatility, rewarding smoother gains.
3. **Drawdown avoidance** - keeping the deepest losses shallow, even at some cost to upside.

The winner is chosen on the validation folds: whichever variant makes the most money across the walk-forward folds wins. The 2022-onward test block is then touched once, only to confirm the chosen variant holds up - never to pick between them, since selecting on the test would contaminate it. The natural benchmark throughout is plain buy-and-hold QQQ: a variant that can't beat simply holding isn't earning its keep.

Major rough periods available over \~25 years: dot-com crash (2000-2002, \~80% down), 2008 (\~50%), 2020 COVID (\~30% in a month), 2022 (\~35%), plus corrections in 2011, 2015-2016, and late 2018.

## News & Sentiment (monitoring, not training)

News and social sentiment is a genuine leading signal - fear can spike in headlines before it fully lands in price - but it is deliberately kept OUT of the training features because clean, free historical data does not exist back to 1999 (social media barely existed in the early training years, and good news feeds are paid). Training on recent-only sentiment would leave the old crashes blind and bias the model.

Instead, treat an X (formerly Twitter) sentiment score as a live monitoring signal watched alongside the model's output, going forward only - which sidesteps the historical-data gap entirely. Practical caveat: there is no off-the-shelf plug-in indicator; X removed free data access, so this means their paid API plus a self-built pipeline (collect posts, score tone with a sentiment model, aggregate into a daily number). Use it to sanity-check the model's signals in real time, not to train it.

Scoring options for the captured text: general-purpose cloud sentiment APIs (Google Cloud Natural Language, Amazon Comprehend, Azure Text Analytics) give quick positive/negative/neutral scores. For finance-specific tone, FinBERT is a model trained on financial language and reads market phrasing better than general models. A large language model can also be prompted to score tone. Workflow: capture posts/headlines from X, score each with one of these, then aggregate into a single daily sentiment number to watch against the model's output.

## Data Sources

No single free source has all thirty columns; the plan is Yahoo Finance plus FRED for the bulk, self-computed for the breadth family, and one paid column left until the end.

**Yahoo Finance (free):** QQQ price and volume, and everything derived from price (RSI, MACD, realized volatility). Also VXN and VIX, SPY, the dollar index (DX-Y.NYB), gold and copper futures (GC=F, HG=F), and the daily closes of the 101 Nasdaq-100 constituents that breadth, dispersion and advance/decline are built from.

**FRED - Federal Reserve Economic Data (free):** 10-year Treasury yield (DGS10), breakeven inflation (T10YIE - FRED publishes the series directly rather than making you subtract TIPS from nominal), the yield curve slope (T10Y2Y), and WTI crude (DCOILWTICO, whose spot series runs to 1986 without the contract-roll artefacts a futures chain carries). Two things to know about this source. A FRED series is one number per day, so it is stored as a degenerate bar - open, high, low and close all the same value, volume zero - because the importer's only shape is OHLCV; the feature pipeline must read only the close from these. And the free graph endpoint truncates licensed series: the ICE BofA credit spreads come back with three years of history rather than thirty, which is what Blocked means on that row.

**Self-computed, and the one paid column:**

- Market breadth (percent of names above their 200-day MA): compute yourself from the daily prices of all 100 constituents - pull each name's price history (Yahoo can supply the constituent prices), calculate each 200-day MA, then take the daily percentage above it. No free source has point-in-time index membership, so today's list is applied to the whole history; that is survivorship-biased and reads optimistic in past drawdowns, which any backtest leaning on the column has to account for.
- Nasdaq-100 return dispersion, and advance/decline: both fall out of the same cached constituent closes that breadth is built from, so they cost no extra download once that cache exists.
- VXN minus VIX spread, and the copper/gold ratio: arithmetic on series already held. No new source, just a subtraction and a division.
- Average put-call ratio across the Nasdaq-100: requires per-name options data; extract from an options data provider, then average across the 100 constituents yourself. The costliest and most involved piece, so it is deliberately last - CBOE's free daily ratio file is now gated behind a 403, and no free source carries per-name history. Everything else is built and validated before any money is spent here.

**Calendar features** (day of week, day of month, day of year, holiday-proximity flag): generated from the date itself, no external source needed - just an exchange trading-holiday calendar for the holiday flag.

Rule of thumb: Yahoo + FRED cover every free column, and market breadth is computed from Yahoo constituent prices. Only the average put-call ratio needs a provider, and it waits until the free twenty-seven are working - if the model earns its keep without it, the spend is optional; if it does not, buying one more column was unlikely to save it.

## Tax and Trading Costs

A timing model trades in and out, while buy-and-hold almost never realizes a gain. If the backtest charges tax on the model's trades but the benchmark sits untouched, the active strategy is handicapped from the start, and this is a likely main reason the variants have so far failed to beat buy-and-hold: the tax drag on every round trip eats the edge. This is a working diagnosis drawn from experience, not a proven cause — other factors may contribute, and the goal of the training is to see whether a well-trained model can overcome it.

The fix is not to remove the tax, which is real, but to put it inside the objective the model optimizes against. In the backtest's accounting layer, the part that turns buy and sell signals into an equity curve, deduct tax on each realized gain at the moment a position is closed at a profit. That after-tax equity curve is then what feeds the optimization objective. All three variants, total return, risk-adjusted return, and drawdown avoidance, are computed on the after-tax curve rather than the gross one.

The critical detail: the tax must be inside the signal the model learns from, not applied afterward in reporting. If tax is deducted only after training, the model never feels it and keeps churning. Taxed inside the reward, the model learns on its own that a trade has to clear the tax hurdle to be worth making, and marginal in-and-out trades get punished automatically.

Tax is not a feature column. It is not something the model observes about the market, so it does not belong in the normalized feature table. It belongs in the scoring, as a cost of acting. Refinements to consider: distinguish short-term from long-term holding rates so longer holds are taxed more lightly, and compare the model's after-tax curve against a buy-and-hold benchmark taxed on the same honest basis, so the comparison is fair.

In the application itself this is exposed as a user-facing tax toggle. The app's primary job is to generate signals (buy, sell, hold), not to execute trades, so a pure signal carries no realized gain and no tax. The switch lets the user choose how results are scored and displayed. Tax off: the raw, pre-tax signal quality, how good the timing is on its own, measured against buy-and-hold. Tax on: the same equity curve run through the tax deduction, showing the after-tax picture that matters to someone trading a taxable account, with the user entering their own rate since every tax situation differs. The model and its signals are identical underneath either way; only the scoring and presentation layer changes.

## Trading Strategies

The model is unchanged underneath all of these: it still produces buy, sell and hold signals from the feature table. What differs between strategies is how those signals are acted on. Like the tax treatment, each strategy is a layer on top of the signals in the scoring and execution layer, not a new feature column, and all are switchable — the same model can be scored under any one of them. Margin interest, the inverse ETF's decay and cost of carry, and tax all have to be charged honestly against the equity curve, so a strategy only earns when it clears its full real cost against plain buy-and-hold QQQ as the benchmark.

### Strategy 1 — Signals only

The simplest use of the model is to generate and broadcast the signal, not to trade. The app puts out a buy, sell or hold call each bar, and whoever is listening acts on it themselves. Because the app is not buying or selling anything, there is no realized gain and no tax here at all — it is pure signal quality, measured against buy-and-hold QQQ. This is the baseline the other strategy is built on.

### Strategy 2 — Margin, long and inverse

This is the money-making strategy, and it is one mechanism with two directions. The core QQQ position, bought with your own money, is never sold — so no capital-gains tax is ever triggered on it. All the action happens on margin, alongside that untouched core:

When the model signals up with conviction, use margin to buy more QQQ than cash alone would allow, amplifying the upside the timing model is trying to capture. When the model signals down, instead of selling the core, borrow on margin to buy the inverse ETF, which rises as QQQ falls and cushions the drop; close it when the signal turns back up. One signal drives both directions — long on margin in an up regime, inverse on margin in a down regime — and the core never moves either way.

The inverse instrument is PSQ, the one-to-one ProShares Short QQQ. The leveraged inverse funds like QID and SQQQ are left out: they amplify a wrong call and decay in choppy markets, and buying a 2x or 3x fund on margin stacks leverage on leverage, multiplying margin-call risk. The one-to-one PSQ on a capped margin position is the controlled version.

Leverage ratio and hedge size are the main dials, each expressed as a percentage of the QQQ holding — typical settings to test are 10, 20 and 30 percent. The hard rule is that the margin position never exceeds 50 percent of the holding: that is a ceiling, not a target, and the backtest should refuse any configuration that breaches it, keeping the borrowed amount small enough that a sharp adverse move cannot force-liquidate the core.

The real cost here is margin interest, which is not flat. It moves with prevailing rates, so the backtest charges a variable rate tracking each historical period's rate environment, not one fixed figure — a trade that pays off in the cheap-money 2010s might not clear its interest bill in a high-rate period. Interest accrues only while a margin position is open. Margin-call risk is the other real cost: if the market moves against the borrowed position, it can trigger a call. Both the variable interest and this risk belong inside the scoring, deducted the same way, so the model only values a margin trade that clears its full real cost. Since the core is never sold, tax is not the main factor it is in a sell-and-rotate approach, though it still applies to any realized gains on the margin positions themselves.

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

## Staged Pipeline Architecture

The whole system is built as a chain of discrete, isolated stages. Each stage takes a defined input, produces a defined output, and has its own validation gate at the boundary. A stage does not hand off until its output passes validation, so a problem is caught where it happens rather than surfacing three stages later. Each stage also reports status while it runs, so a long operation (data download, training) shows progress rather than sitting silent.

### Principles

- **Isolated stages.** Each stage is a self-contained unit with one job. It reads its input from storage, does its work, writes its output to storage. Stages communicate only through stored artifacts, never by reaching into each other.
- **Validation at every boundary.** Each stage has a dedicated validation step that confirms its output is correct and complete before the next stage is allowed to run. Validation is part of the stage, not a separate pass bolted on at the end.
- **Visible status.** Each stage emits progress and a final pass/fail state. The user can see which stage is running, how far along it is, and whether it succeeded.
- **Staleness tracking.** When a parameter changes, every downstream stage that depended on it is marked stale and must rerun. A stage whose inputs and parameters are unchanged can be skipped and its cached output reused.

### The stages

1. **Data gathering and storage.** Pull raw series from each source (Yahoo, FRED, constituent prices) and write them to the database. *Validation:* every expected series is present, date ranges cover the training window, no unexpected gaps, row counts sane, FRED degenerate bars read close-only. Report rows fetched per series and any gaps found.
2. **Feature building.** Compute the derived columns (breadth, dispersion, advance/decline, spreads, ratios, RSI/MACD/realized-vol for the data-only set) from the stored raw data. *Validation:* every committed feature column exists, no unexpected nulls, computed values in sane ranges. Report which columns were built.
3. **Normalization.** Apply each column's tagged method (rolling z-score, returns, rescale, cyclical). *Validation:* output distributions look right — z-scores roughly centered, rescaled columns inside their band, cyclical columns bounded to -1..1, no NaNs leaking from window edges. Report per-column normalization applied.
4. **Chronological split.** Cut the normalized table into training / validation / test by date. *Validation:* no overlap between pieces, boundaries fall where configured, each piece non-empty and contains some rough-market periods, strictly forward in time. Report the date ranges and row counts of each piece.
5. **Training.** Train the network on the training piece, tuning across the walk-forward validation folds. *Validation:* loss actually decreases, no NaN loss, each fold completes, the chosen variant beats buy-and-hold on the validation folds. Report live progress — current fold, epoch, loss curve, elapsed time — so training is visibly in process, not a black box.
6. **Backtest / scoring.** Run the trained signals through the accounting layer (after-tax equity curve, selected strategy, costs) and compare against the benchmark. *Validation:* equity curve is continuous, costs applied, metrics computed, result reproducible. Report the final metrics against buy-and-hold.

### Parameter changes and reruns

Parameters are declared per stage (e.g. z-score window length, split boundaries, learning rate, leverage ratio). When a parameter changes, the stage that owns it and every stage downstream are marked stale; upstream stages are untouched. The pipeline reruns only the stale stages, reusing cached outputs for the rest. This makes experimentation cheap: change the normalization window and only normalization onward reruns, not the hours-long data pull.

## Web Monitoring App

The application also has a web-based front end whose job is to monitor the pipeline, not to run heavy work itself. It gives a live window into every stage: which are done, which is running, which is stale, and what each one produced. The pipeline does the work; the web app watches it.

### A tab per stage

The interface has one tab for each pipeline stage, in order:

1. **Data** — series pulled, date ranges, row counts, any gaps found, last refresh time.
2. **Features** — which columns were built, null checks, value-range summaries.
3. **Normalization** — per-column method applied, distribution checks, before/after previews.
4. **Split** — the training / validation / test date ranges and row counts, with a timeline view.
5. **Training** — the live view: current fold and epoch, the loss curve updating as it trains, elapsed and estimated time, and validation-fold results against buy-and-hold as they come in.
6. **Backtest / scoring** — the equity curve, metrics against the benchmark, and the tax and strategy toggles.

### What each tab shows

Each tab surfaces that stage's status (idle, running, passed, failed, stale), its validation result, and its output so it can be inspected directly. A stage that failed validation shows why, on its own tab, so a problem is diagnosed where it happened. The training tab is the one to follow in real time — it shows the model is actively in process, with progress indicators rather than a silent wait — and the backtest tab is where the final result is read and the toggles are flipped to re-score. Because the stages are isolated and cached, each tab can be viewed and its stage rerun independently of the others.
