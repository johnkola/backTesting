# QQQ Features Explained

A reading guide to every row in the feature table on the main tab. For each feature: what it is, what it tells you about the market, how it is normalized and why, and its status in the model. Numbers follow the table.

## A. QQQ price and volume

**1. Price - open.** The first traded price of the day. On its own it means little; what matters is the gap from yesterday's close, so it is stored as `open / previous close - 1`. A big positive gap means news or futures moved overnight and buyers arrived eager; a big negative gap means sellers did. Gaps capture everything that happened while the market was closed - earnings released after hours, overseas sessions, macro data at 8:30.

**2. Price - high.** The highest price of the day, again relative to the previous close. Together with the low it describes how far the day stretched. A high far above both open and close means buyers pushed but could not hold it - a sign of selling into strength.

**3. Price - low.** The lowest price of the day relative to the previous close. A deep low with a close near the high means dip buyers stepped in hard; a close near the low means sellers controlled the day.

**4. Price - close.** The final price, stored as the daily return. This is the single most important number: it is what buy-and-hold actually earns each day, and the series the model is ultimately trying to time. Open, high, low and close together form the full bar, which lets the model see not just where price ended but the shape of the day.

**5. Volume.** How many shares traded. Volume measures conviction: a move on heavy volume is backed by real money, a move on thin volume is easier to reverse. Raw volume drifts over decades as the ETF grows, so it is compared to its own recent history - either a rolling z-score or a ratio to its trailing average ("today was 1.4 times normal").

**6. QQQ / SPY relative strength.** QQQ divided by SPY - tech versus the broad market. Rising means tech is leading; falling means money is rotating out of tech into the rest of the market. Leadership changes often precede regime shifts. Undecided: it is price-derived, but it carries information from a second index that QQQ's own series does not have.

## B. Technical indicators (data only)

These three are computed and stored for charts and rule-based strategies, but kept out of the network. They are formulas applied to QQQ's own price, which the model already sees, so feeding them in adds overfitting room without adding information.

**7. RSI (14-day).** The Relative Strength Index compares the size of recent up days to recent down days and outputs a value from 0 to 100. Above 70 is traditionally "overbought" (rallied hard, may pause), below 30 "oversold" (sold off hard, may bounce). Because it already sits on a fixed 0-100 scale, it is simply rescaled to 0-1.

**8. MACD.** Moving Average Convergence Divergence is the gap between a fast (12-day) and slow (26-day) exponential moving average of price. Positive means short-term momentum is above the longer trend; crossing zero is a classic trend-change signal. Its size scales with price, so it is z-scored.

*Data check (MACD and RSI):* computed from our own prices, then compared with values published by a data API (e.g. Twelve Data). Differences beyond a small tolerance are flagged; they point to bad or missing price bars. The first weeks of a series can differ slightly because moving averages depend on their starting point, so the comparison skips that warm-up.

**9. Realized volatility.** How much QQQ has actually been swinging, measured as the standard deviation of recent daily returns. This is backward-looking, unlike VXN, which is the market's forward-looking expectation. High realized volatility tends to cluster - turbulent periods stay turbulent for a while.

## C. Volatility

**Options indicator (template).** Not a feature itself - a pattern for any volatility index: z-score it if it hovers around a typical level, use returns if it drifts. VXN is what fills it.

**10-13. VXN - open, high, low, close.** VXN is the Nasdaq-100 volatility index, the fear gauge for tech. It is computed from the prices of options on the index and expresses the swing traders expect over the next 30 days, annualized. When investors rush to buy protection, option prices rise and VXN climbs. High VXN means fear; low VXN means complacency. VXN tends to spike as QQQ falls, so it often moves opposite to price. The full bar is kept for the same reason as QQQ's: the intraday range of fear matters, not just where it closed. Each is z-scored, so the model sees how stretched fear is versus its own recent history rather than an absolute level.

**14. VIX.** The same idea applied to the S&P 500 - the broad-market fear gauge, the most watched volatility number in the world. Kept alongside VXN because the two can diverge: the whole market can be scared while tech is calm, or the reverse.

**15. VXN minus VIX spread.** Subtract broad-market fear from tech fear and you isolate how much extra anxiety is aimed specifically at tech. A widening spread says the worry is concentrated in tech names - typical around tech-specific shocks such as a rate spike that hits long-duration growth stocks hardest.

**16. VIX term structure (VIX3M / VIX).** Three-month expected volatility divided by one-month. Normally the ratio is above one: uncertainty further out is priced higher, a calm state called contango. When panic hits, near-term fear jumps above longer-term fear and the ratio drops below one (backwardation) - historically one of the cleanest signals of acute stress, and often close to a bottom. Undecided because its history only starts in 2006.

## D. Options positioning

A put is a contract that pays when price falls (insurance or a bearish bet); a call pays when price rises. Comparing them shows how traders are positioned. All of these are marked Later - paid, because none has free history.

**17. Average put-call ratio across the Nasdaq-100.** Put volume divided by call volume for each of the index's individual stocks, averaged. It reads positioning in the names themselves. Above one leans bearish or hedged, below one bullish. Often used contrarian: extreme fear can mark bottoms.

**18. QQQ put-call ratio.** The same ratio on QQQ's own options. QQQ options are used heavily by institutions to hedge whole portfolios, so this reads professional index hedging.

**19. CBOE equity put-call ratio.** The aggregate across all US single-stock options on CBOE. Dominated by retail traders, so it reads speculative crowd sentiment. Very low readings mean the crowd is piling into calls - often a sign of froth.

**20. Options volume (Nasdaq-100 total).** How many option contracts trade across the index names. It measures intensity, not direction: a surge means traders are positioning hard for a big move, either hedging or speculating.

## E. Rates, credit and inflation

**21. 10-year Treasury yield.** The interest rate on 10-year US government debt - the benchmark long rate. Stock valuations are future earnings discounted back to today; the higher this rate, the less those future earnings are worth now. Tech companies earn much of their value far in the future, so they are especially sensitive. The 2022 bear market was driven largely by this yield rising fast.

**22. 2-year Treasury yield.** The rate on 2-year government debt, which tracks where the market expects the Federal Reserve to set short-term rates. It reacts quickly to Fed news and is the market's read on monetary policy.

**23. Yield curve slope (10-year minus 2-year).** Normally positive, because lending for longer should pay more. When it turns negative - an inverted curve - the market expects rate cuts ahead, usually because a slowdown is coming. Inversions have preceded most US recessions. It is a slow regime signal, independent of anything price-based.

*Data check:* the feature is computed from the two yields above. The published spread (FRED T10Y2Y) is imported too, only as a cross-check: any day where computed and published values differ is flagged as a possible data problem.

**24. Breakeven inflation rate.** The market's expected average inflation over the next 10 years, taken from the gap between regular Treasuries and inflation-protected ones (TIPS). Rising inflation expectations push rates and the Fed higher, which pressures tech. This series sets the 2003 training start.

**25. Investment-grade credit spread.** The extra yield investors demand to lend to high-quality companies instead of the government. When it widens, lenders are getting nervous about corporate health in general. Moves are calmer than the high-yield spread, but it has full clean history.

**26. High-yield credit spread.** The extra yield on risky "junk" corporate bonds over Treasuries. One of the best early warnings in markets: credit investors are often first to smell trouble, and widening junk spreads tend to show up before stock selloffs. Blocked because the free source only provides recent years.

**41. Fed funds rate.** The US central bank's overnight policy rate. It sets the cost of short-term money: rising rates tighten conditions for growth stocks, and cuts loosen them. It is also the benchmark brokers use for USD margin, so the same raw series feeds the margin interest cost.

**42. Bank of Canada policy rate.** Canada's overnight target rate. It captures Canadian monetary conditions and the CAD side of borrowing costs for a Canadian account.

## F. Commodities and currency

**27. Crude oil (WTI).** The US benchmark oil price. It reflects global demand (strong economy, more oil used) and feeds directly into inflation. Normalized as a z-score rather than returns, because WTI went negative in April 2020 and a percent change across a negative price is meaningless.

**28. Gold.** The classic safe haven. Money flows into gold when fear rises, real rates fall, or the dollar weakens. On its own it reads risk appetite and inflation worry.

**29. Copper / gold ratio.** Copper is an industrial metal used in construction, wiring and manufacturing, so its demand tracks real economic activity ("Dr. Copper"). Gold is the fear asset. Divide one by the other: a rising ratio means growth optimism, a falling ratio means a flight to safety. It tracks the economic cycle and tends to move with long-term yields.

**30. Dollar index.** The US dollar against a basket of major currencies. The big QQQ names earn a large share of revenue abroad; a strong dollar shrinks those overseas earnings when converted back, and tightens global financial conditions. Dollar strength is generally a headwind for tech.

## G. Nasdaq-100 breadth

Breadth asks whether a move is broad or narrow. The Nasdaq-100 is weighted by size, so a handful of giant companies can lift the index while most stocks fall. A narrow rally is fragile. These are price-derived, but from the other hundred names, which QQQ's own series cannot show.

**31. Market breadth (percent above 200-day moving average).** The share of index members trading above their own long-term trend line. High means most stocks are healthy; low means the index is being carried by a few. When the index makes new highs while breadth falls, that divergence is a classic warning.

**32. Advance / decline.** How many names rose versus fell on the day. A fast daily pulse that complements the slow 200-day measure.

**33. New highs minus new lows.** How many members hit a 52-week high against how many hit a 52-week low. Shows whether strength or weakness is spreading through the index.

**34. Return dispersion.** How spread out the individual stock returns are on a given day. Low dispersion means everything moves together - a macro-driven, risk-on or risk-off market. High dispersion means companies are moving on their own news and stock-picking matters.

Caveat for this whole group: today's member list is applied to all of history, since no free source tracks past membership. That is survivorship bias - names that dropped out are missing, so past drawdowns look milder than they really were.

## H. Calendar and earnings

**35-37. Day of week, day of month, day of year.** Markets show calendar habits: Monday and Friday behave differently from midweek, flows cluster around month-end and paydays, and seasonal patterns repeat (the year-end rally, "sell in May"). Each is encoded with sine and cosine so the model sees a cycle - December 31 sits next to January 1 - rather than a number line where Friday looks bigger than Monday.

**38. Holiday proximity.** A flag for the day before a market holiday or long weekend, when traders often reduce risk. Combined with day of week, because a holiday that creates a three-day weekend matters more than a midweek one.

**39. Earnings season.** The share of index members reporting earnings in the next five trading days, plus an in-season on/off flag. Earnings weeks bring bigger single-stock moves and higher volatility; a heavy reporting week for the giants can move the whole index.

**40. Nasdaq-100 aggregate earnings.** The earnings per share of all members added up, both trailing (what companies actually earned) and forward (what analysts expect). This is the E in the P/E ratio, with no price in it - the fundamental engine behind the index. Rising earnings support higher prices over time; falling estimates are a warning. Updated quarterly and carried forward daily.

## Where to Get the Data

At least two sources per feature, free and paid. Coverage dates are approximate; check them before buying.

| # | Feature | Free options | Paid options |
| --- | --- | --- | --- |
| 1-5 | QQQ open, high, low, close, volume | Yahoo Finance (daily from 1999; hourly \~2 years); Stooq (daily); Alpha Vantage free tier (daily, limited calls) | FirstRate Data (intraday from 2000, one-time); Polygon.io / Massive (intraday from \~2015); Norgate Data (daily, adjusted); EODHD; Interactive Brokers API |
| 6 | QQQ / SPY relative strength | Yahoo Finance; Stooq (both tickers) | FirstRate Data; Polygon.io; Norgate Data |
| 7-9 | RSI, MACD, realized volatility | Computed from QQQ bars (Ta4j, pandas-ta, TA-Lib); Alpha Vantage technical endpoints | Any paid bar vendor above, then computed locally; Twelve Data indicator API |
| 10-13 | VXN open, high, low, close | Yahoo Finance ^VXN (from 2001); CBOE website historical CSV | FirstRate Data (indices from 2005 intraday); CBOE DataShop; Norgate Data |
| 14 | VIX | Yahoo Finance ^VIX (from 1990); CBOE historical CSV; FRED VIXCLS (daily close) | FirstRate Data; CBOE DataShop; Polygon.io indices |
| 15 | VXN minus VIX spread | Computed from the two free series above | Computed from paid VXN and VIX |
| 16 | VIX term structure (VIX3M / VIX) | Yahoo Finance ^VIX3M (from 2006); CBOE historical CSV; FRED VXVCLS | CBOE DataShop; FirstRate Data |
| 17 | Nasdaq-100 average put-call ratio | None with full history; Barchart and Market Chameleon show current values only | ORATS; Cboe LiveVol / DataShop; OptionMetrics IvyDB (academic, expensive); Polygon.io options |
| 18 | QQQ put-call ratio | None with full history; Barchart current only | ORATS; Cboe DataShop; OptionMetrics; Polygon.io options |
| 19 | CBOE equity put-call ratio | CBOE daily market statistics page (gated for bulk history); Quandl / Nasdaq Data Link legacy archives | Cboe DataShop; Barchart OnDemand; Nasdaq Data Link premium |
| 20 | Nasdaq-100 options volume | None with full history | ORATS; Cboe DataShop; OptionMetrics; Polygon.io options |
| 21 | 10-year Treasury yield | FRED DGS10; US Treasury daily yield curve site | Bloomberg; Refinitiv / LSEG; FirstRate Data (Treasury futures as proxy) |
| 22 | 2-year Treasury yield | FRED DGS2; US Treasury site | Bloomberg; Refinitiv / LSEG |
| 23 | Yield curve slope | FRED T10Y2Y; computed from Treasury site yields | Bloomberg; Refinitiv / LSEG |
| 24 | Breakeven inflation | FRED T10YIE; computed from FRED DGS10 minus DFII10 (TIPS) | Bloomberg; Refinitiv / LSEG |
| 25 | Investment-grade credit spread | FRED BAMLC0A0CM; FRED BAA10Y (Moody's Baa minus 10-year, from 1986) as a fallback | ICE Data Services; Bloomberg |
| 26 | High-yield credit spread | FRED BAMLH0A0HYM2 with a free FRED API key (full history); proxy from Yahoo HYG vs IEF ratio (from 2007) | ICE Data Services; Bloomberg; Refinitiv |
| 27 | Crude oil (WTI) | FRED DCOILWTICO (from 1986); EIA website; Yahoo CL=F | FirstRate Data (futures from 2007 intraday); CME DataMine; Norgate futures |
| 28 | Gold | Yahoo GC=F (from 2000); LBMA gold price history; Stooq | FirstRate Data; CME DataMine; Norgate futures |
| 29 | Copper / gold ratio | Yahoo HG=F and GC=F; Stooq | FirstRate Data; CME DataMine |
| 30 | Dollar index | Yahoo DX-Y.NYB (from 1971); FRED DTWEXBGS (broad trade-weighted dollar) | FirstRate Data (DX futures); ICE Data Services |
| 31-34 | Breadth, advance/decline, new highs-lows, dispersion | Computed from Yahoo constituent closes (survivorship-biased); Stooq constituents | Norgate Data (point-in-time Nasdaq-100 membership, fixes survivorship bias); FirstRate Data constituents; Sharadar via Nasdaq Data Link; StockCharts breadth series |
| 35-37 | Day of week, month, year | Computed from the date | Not needed |
| 38 | Holiday proximity | pandas\_market\_calendars / exchange\_calendars libraries; NYSE / Nasdaq holiday pages | Not needed |
| 39 | Earnings season | Yahoo Finance earnings calendar; Nasdaq.com earnings calendar; SEC EDGAR filing dates | Zacks; Wall Street Horizon; FactSet; Sharadar |
| 40 | Nasdaq-100 aggregate earnings | SEC EDGAR XBRL (reported EPS); Yahoo Finance (recent estimates only) | Sharadar fundamentals; Zacks estimates; FactSet; Refinitiv I/B/E/S |

The gaps to note: the options group (17-20) has no free source with full history, and point-in-time index membership for the breadth group is only clean on a paid source like Norgate.
