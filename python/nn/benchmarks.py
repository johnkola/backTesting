"""The benchmarks' catalogue entries (R74): what every strategy is scored
beside, in the same words on the Strategy, Results and Docs tabs and in their
tooltips.

Each benchmark has one entry: its name as it is stored and served (a fold's
`walkforward_benchmarks.name`, a result's `benchmarks[].name`), a title, the
library it is computed with and that library's version, what it does in plain
words, and what checks it. Buy-and-hold and the two monthly rules are this
project's own code, so their version is the code's (`lineage.code_hash`); the
indicator rules read TA-Lib. The entries follow the rules' own definitions
(`nn/rules.py`, `nn/profit.py`), and `tests/test_benchmark_catalogue.py` holds
every benchmark the runner scores to exactly one entry.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from typing import Any

from nn import rules

BUY_AND_HOLD = "buy_and_hold"
#: The library of a benchmark written in this project.
OWN_CODE = "this project's code"


@dataclass(frozen=True)
class Benchmark:
    name: str
    title: str
    #: OWN_CODE, or a library's name.
    library: str
    description: str
    checked_by: str
    #: The import name of the library, for its version; None for own code.
    module: str | None = None
    settings: dict[str, float] = field(default_factory=dict)

    def version(self) -> str | None:
        if self.module is None:
            from nn import lineage

            return f"code {lineage.code_hash()[:12]}"
        try:
            return str(importlib.import_module(self.module).__version__)
        except ImportError:
            return None

    def catalogue(self) -> dict[str, Any]:
        return {"name": self.name, "title": self.title, "kind": "benchmark",
                "library": self.library, "version": self.version(),
                "description": self.description, "checkedBy": self.checked_by,
                "settings": dict(self.settings)}


_MONTHLY_CHECK = ("The 10-month average and the 12-month return behind every call are "
                  "computed again by an independent copy each time the calls are served, and "
                  "shown with the result as its data checks")
_INDICATOR_CHECK = ("Every indicator it reads comes from TA-Lib and is checked against a "
                    "hand-written copy within 1% (the data checks shown with the result); the "
                    "engine and the walk-forward scorer are held to the same entries and exits")
_HELD = "Held from the next open; cash earns what the strategy's idle money earns."


def _indicator(name: str, title: str, rule: str) -> Benchmark:
    return Benchmark(name=name, title=title, library="TA-Lib", module="talib",
                     description=f"{rule} {_HELD}", checked_by=_INDICATOR_CHECK,
                     settings=dict(rules.INDICATOR_SETTINGS[name]))


BENCHMARKS: dict[str, Benchmark] = {b.name: b for b in (
    Benchmark(
        name=BUY_AND_HOLD, title="Buy & hold", library=OWN_CODE,
        description="Buys at the first open the strategy could trade at and holds to the last "
                    "close, paying the same commission, slippage and tax. Doing nothing on the "
                    "same instrument: the figure a strategy has to beat to be worth running.",
        checked_by="A model that never leaves the market must score exactly buy-and-hold; the "
                   "engine and the walk-forward scorer are held to the same fills, at the next "
                   "open, on one fixture of bars"),
    Benchmark(
        name=rules.FABER, title="Faber 10-month", library=OWN_CODE,
        description=f"Faber's timing rule: at each month end, hold the instrument when its close "
                    f"is above its {rules.FABER_MONTHS}-month average, otherwise cash. Re-decided "
                    f"monthly. {_HELD}",
        checked_by=_MONTHLY_CHECK, settings={"months": rules.FABER_MONTHS}),
    Benchmark(
        name=rules.DUAL_MOMENTUM, title="Dual momentum", library=OWN_CODE,
        description=f"Antonacci's dual momentum: at each month end, hold whichever of the "
                    f"instrument and SPY returned more over {rules.MOMENTUM_MONTHS} months, if "
                    f"that beats T-bills over the same months; otherwise cash, which earns "
                    f"T-bills. Needs SPY from the instrument's own source and T-bills (DTB3). "
                    f"Held from the next open.",
        checked_by=_MONTHLY_CHECK, settings={"months": rules.MOMENTUM_MONTHS}),
    _indicator(rules.SMA_CROSSOVER, "SMA crossover 50/200",
               "The golden cross: enters when the 50-day average crosses above the 200-day, "
               "leaves when it crosses back below."),
    _indicator(rules.SMA_CROSSOVER_FAST, "SMA crossover 10/50",
               "The same crossover rule at 10 and 50 days."),
    _indicator(rules.EMA_TRIPLE, "Triple EMA 5/13/34",
               "Enters when the 5-day average crosses above the 13-day while the 13-day is "
               "above the 34-day; leaves when the 5-day falls below the 13-day."),
    _indicator(rules.MACD, "MACD 12/26/9",
               "Appel's MACD: enters when the 12/26 MACD line crosses above its 9-day signal "
               "line, leaves when it crosses back below."),
    _indicator(rules.RSI, "RSI 14, 30/70",
               "Wilder's RSI: enters when the 14-day RSI falls through 30, leaves when it "
               "rises through 70."),
    _indicator(rules.BOLLINGER, "Bollinger 20, 2σ",
               "Bollinger's bands: enters when the close falls below the lower band (20 days, "
               "2 deviations), leaves when it rises back above the middle."),
)}


def registry_json() -> list[dict[str, Any]]:
    """Every benchmark's entry, in the order results list them."""
    return [b.catalogue() for b in BENCHMARKS.values()]
