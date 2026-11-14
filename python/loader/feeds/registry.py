"""Which stored series are fed, and from where.

A feed is one stored series (symbol + source name, at D1) and the provider call
that produces it. The list is explicit rather than derived from
`features.toml`: a feature input says what a column reads, not which provider
ticker serves it (`DXY` is Yahoo's `DX-Y.NYB`, `CORRA` is the Bank of Canada's
`AVG.INTWO`). `tests/test_feeds_registry.py` holds the two together: a series
the feature table reads with no feed here fails the suite, unless it is one of
the deliberately manual ones in `MANUAL`.
"""

from __future__ import annotations

from dataclasses import dataclass

#: provider -> what `ref` names
PROVIDERS = {
    "yahoo": "Yahoo chart API ticker, raw prices",
    "yahoo-adj": "Yahoo chart API ticker, dividend-adjusted prices",
    "fred": "FRED series id",
    "boc": "Bank of Canada Valet series id",
    "breadth": "breadth metric over the Nasdaq-100 member closes",
    "sec": "earnings metric over the Nasdaq-100 members' SEC filings",
}


@dataclass(frozen=True)
class Feed:
    symbol: str      # instruments.symbol
    source: str      # data_sources.name
    type: str        # instruments.type
    provider: str    # a PROVIDERS key
    ref: str         # the provider's name for it
    group: str       # prices | macro | breadth | earnings, for the panel

    @property
    def key(self) -> str:
        return f"{self.symbol}/{self.source}"

    def to_dict(self) -> dict[str, str]:
        return {"key": self.key, "symbol": self.symbol, "source": self.source,
                "type": self.type, "provider": self.provider, "ref": self.ref,
                "group": self.group}


FEEDS: tuple[Feed, ...] = (
    # QQQ adjusted is the feature table's calendar; raw QQQ and SPY are what the
    # Backtest tab and benchmarks read; PSQ is Strategy 2's hedge.
    Feed("QQQ", "yahoo-adj", "STOCK", "yahoo-adj", "QQQ", "prices"),
    Feed("QQQ", "yahoo", "STOCK", "yahoo", "QQQ", "prices"),
    Feed("SPY", "yahoo-adj", "STOCK", "yahoo-adj", "SPY", "prices"),
    Feed("SPY", "yahoo", "STOCK", "yahoo", "SPY", "prices"),
    Feed("PSQ", "yahoo-adj", "STOCK", "yahoo-adj", "PSQ", "prices"),
    Feed("PSQ", "yahoo", "STOCK", "yahoo", "PSQ", "prices"),
    # XIU, the iShares S&P/TSX 60 ETF (Toronto, in CAD): the Canadian market, for
    # backtests. Not a feature input. Loaded from the API from its first day
    # (1999-10-04), so it never went through a CSV.
    Feed("XIU", "yahoo-adj", "STOCK", "yahoo-adj", "XIU.TO", "prices"),
    Feed("XIU", "yahoo", "STOCK", "yahoo", "XIU.TO", "prices"),
    # Canadian crypto ETFs (Toronto, CAD), for backtests; not feature inputs.
    # The two the author chose on 2026-11-12: the longest-running large bitcoin
    # fund (Purpose, unhedged units, from 2021-02-18) and the most-traded ether
    # fund (CI Galaxy, from 2021-04-20).
    Feed("BTCC-B", "yahoo-adj", "STOCK", "yahoo-adj", "BTCC-B.TO", "prices"),
    Feed("BTCC-B", "yahoo", "STOCK", "yahoo", "BTCC-B.TO", "prices"),
    Feed("ETHX-B", "yahoo-adj", "STOCK", "yahoo-adj", "ETHX-B.TO", "prices"),
    Feed("ETHX-B", "yahoo", "STOCK", "yahoo", "ETHX-B.TO", "prices"),
    Feed("VIX", "yahoo", "INDEX", "yahoo", "^VIX", "prices"),
    Feed("VXN", "yahoo", "INDEX", "yahoo", "^VXN", "prices"),
    Feed("DXY", "yahoo", "INDEX", "yahoo", "DX-Y.NYB", "prices"),
    Feed("COPPER", "yahoo", "COMMODITY", "yahoo", "HG=F", "prices"),
    Feed("GOLD", "yahoo", "COMMODITY", "yahoo", "GC=F", "prices"),
    Feed("DGS10", "fred", "INDEX", "fred", "DGS10", "macro"),
    Feed("DGS2", "fred", "INDEX", "fred", "DGS2", "macro"),
    Feed("DTB3", "fred", "INDEX", "fred", "DTB3", "macro"),
    Feed("T10Y2Y", "fred", "INDEX", "fred", "T10Y2Y", "macro"),
    Feed("T10YIE", "fred", "INDEX", "fred", "T10YIE", "macro"),
    Feed("DFF", "fred", "INDEX", "fred", "DFF", "macro"),
    Feed("BAA10Y", "fred", "INDEX", "fred", "BAA10Y", "macro"),
    Feed("DCOILWTICO", "fred", "INDEX", "fred", "DCOILWTICO", "macro"),
    Feed("CORRA", "boc", "INDEX", "boc", "AVG.INTWO", "macro"),
    Feed("NDXBREADTH", "computed", "INDEX", "breadth", "breadth", "breadth"),
    Feed("NDXAD", "computed", "INDEX", "breadth", "advance-decline", "breadth"),
    Feed("NDXDISP", "computed", "INDEX", "breadth", "dispersion", "breadth"),
    Feed("NDXNHNL", "computed", "INDEX", "breadth", "new-highs-lows", "breadth"),
    # The SEC earnings series (Features.md 39-40), computed from the members'
    # filings (`earnings`, `sec`). Manual until 2026-11-12.
    Feed("NDXERN5D", "computed", "INDEX", "sec", "release-share", "earnings"),
    Feed("NDXERNSEASON", "computed", "INDEX", "sec", "season", "earnings"),
    Feed("NDXNIQOQ", "computed", "INDEX", "sec", "ni-qoq", "earnings"),
    Feed("NDXNITTM", "computed", "INDEX", "sec", "ni-ttm", "earnings"),
)

#: Feature inputs deliberately not fed, (symbol, source) -> how they are kept
#: current instead. Empty since the SEC earnings series joined the feeds.
MANUAL: dict[tuple[str, str], str] = {}

BY_KEY: dict[str, Feed] = {f.key: f for f in FEEDS}


def select(keys: list[str] | None) -> list[Feed]:
    """The feeds named (all when None), in registry order. Raises KeyError
    naming the first unknown key."""
    if not keys:
        return list(FEEDS)
    unknown = [k for k in keys if k not in BY_KEY]
    if unknown:
        raise KeyError(unknown[0])
    wanted = set(keys)
    return [f for f in FEEDS if f.key in wanted]
