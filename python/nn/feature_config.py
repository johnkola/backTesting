"""Reader and validator for `features.toml` — the feature table's single source
of truth for which columns exist and how each is normalized.

Parsed with `tomllib` from the standard library, so the config costs no
dependency. The point of validating here rather than at training time is that a
typo in a method name or a missing window should fail in a tenth of a second
with the offending feature named, not forty epochs into a run.

Two invariants this module enforces, both of which are silent corruption if
they slip through:

  * Every method is CAUSAL — a row's value depends only on its own column's
    past. That is what makes the output safe to precompute into the database.
    The train-slice-fitted scaler is not causal and deliberately has no
    representation here.
  * `rescale` takes a fixed, declared range. A data-driven min/max would look
    identical in the config and leak the future into every row.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field as dc_field
from pathlib import Path
from typing import Any

import tomllib

DEFAULT_PATH = Path(__file__).with_name("features.toml")

# method -> (required params, params filled from [defaults] when absent)
_METHODS: dict[str, tuple[frozenset[str], dict[str, str]]] = {
    "ratio_to_prev_close": (frozenset(), {}),
    "daily_return": (frozenset(), {}),
    "daily_change": (frozenset(), {}),
    "rolling_zscore": (frozenset({"window"}), {"window": "zscore_window"}),
    "ratio_to_rolling_mean": (frozenset({"window"}), {"window": "rolling_mean_window"}),
    "rescale": (frozenset({"min", "max"}), {}),
    "cyclical": (frozenset({"period"}), {}),
    "flag": (frozenset(), {}),
    "passthrough": (frozenset(), {}),
}

# Methods that emit more than one value per bar.
_WIDTHS = {"cyclical": 2}

_FILL_MODES = frozenset({"forward", "drop", "error"})
_OPS = frozenset({"subtract", "divide", "add", "multiply"})


class FeatureConfigError(ValueError):
    """Raised with the offending feature named, so a bad edit is obvious."""


@dataclass(frozen=True)
class Alignment:
    calendar_symbol: str
    calendar_source: str
    calendar_timeframe: str
    fill: str
    max_fill_days: int


@dataclass(frozen=True)
class Feature:
    name: str
    series: dict[str, Any]
    method: str
    params: dict[str, Any] = dc_field(default_factory=dict)
    enabled: bool = True
    status: str | None = None
    note: str | None = None

    @property
    def width(self) -> int:
        """How many values this column contributes to a bar's vector."""
        return _WIDTHS.get(self.method, 1)

    @property
    def is_derived(self) -> bool:
        """True when the series is computed rather than read straight from a
        stored column — a binary op on two series, or a named computation."""
        return "op" in self.series or "computed" in self.series


@dataclass(frozen=True)
class FeatureConfig:
    schema_version: int
    defaults: dict[str, Any]
    alignment: Alignment
    features: tuple[Feature, ...]

    @property
    def enabled(self) -> tuple[Feature, ...]:
        return tuple(f for f in self.features if f.enabled)

    @property
    def input_width(self) -> int:
        """Values per bar, before the lookback window multiplies it. Cyclical
        columns count twice, which is the number that has to match the
        network's input layer."""
        return sum(f.width for f in self.enabled)

    def by_name(self, name: str) -> Feature:
        for f in self.features:
            if f.name == name:
                return f
        raise KeyError(name)

    def required_series(self) -> set[tuple[str, str]]:
        """Every (symbol, source) pair that has to be loadable for the enabled
        features to build. What the data layer should check before training,
        so a missing import fails up front."""
        out: set[tuple[str, str]] = set()

        def walk(spec: dict[str, Any]) -> None:
            if "op" in spec:
                walk(spec["a"])
                walk(spec["b"])
            elif "symbol" in spec and "source" in spec:
                out.add((spec["symbol"], spec["source"]))

        for f in self.enabled:
            walk(f.series)
        return out


def _validate_series(name: str, spec: Any) -> None:
    if not isinstance(spec, dict):
        raise FeatureConfigError(f"{name}: `series` must be a table")
    if "op" in spec:
        if spec["op"] not in _OPS:
            raise FeatureConfigError(
                f"{name}: unknown op {spec['op']!r}; expected one of {sorted(_OPS)}"
            )
        for side in ("a", "b"):
            if side not in spec:
                raise FeatureConfigError(f"{name}: op needs both `a` and `b`")
            _validate_series(f"{name}.{side}", spec[side])
        return
    if "computed" in spec:
        return
    missing = {"symbol", "source", "field"} - set(spec)
    if missing:
        raise FeatureConfigError(
            f"{name}: series needs {sorted(missing)} (or `computed`, or an `op`)"
        )


def _build_feature(raw: dict[str, Any], defaults: dict[str, Any]) -> Feature:
    name = raw.get("name")
    if not name:
        raise FeatureConfigError("a [[feature]] block has no `name`")

    method = raw.get("method")
    if method not in _METHODS:
        raise FeatureConfigError(
            f"{name}: unknown method {method!r}; expected one of {sorted(_METHODS)}"
        )

    _validate_series(name, raw.get("series"))

    required, fallbacks = _METHODS[method]
    params: dict[str, Any] = {}
    for key in required:
        if key in raw:
            params[key] = raw[key]
        elif key in fallbacks and fallbacks[key] in defaults:
            params[key] = defaults[fallbacks[key]]
        else:
            raise FeatureConfigError(f"{name}: method {method!r} needs `{key}`")

    if "window" in params and (not isinstance(params["window"], int) or params["window"] < 2):
        raise FeatureConfigError(f"{name}: window must be an integer >= 2")
    if method == "rescale" and params["max"] == params["min"]:
        raise FeatureConfigError(f"{name}: rescale needs max != min")

    return Feature(
        name=name,
        series=raw["series"],
        method=method,
        params=params,
        enabled=bool(raw.get("enabled", True)),
        status=raw.get("status"),
        note=raw.get("note"),
    )


def load(path: Path | str = DEFAULT_PATH) -> FeatureConfig:
    """Parse and validate the feature table. Raises FeatureConfigError naming
    the offending feature rather than letting a bad edit reach training."""
    path = Path(path)
    with path.open("rb") as fh:
        raw = tomllib.load(fh)

    if "schema_version" not in raw:
        raise FeatureConfigError("`schema_version` is required — it keys the model cache")

    defaults = raw.get("defaults", {})

    a = raw.get("alignment", {})
    missing = {"calendar_symbol", "calendar_source", "calendar_timeframe", "fill"} - set(a)
    if missing:
        raise FeatureConfigError(f"[alignment] needs {sorted(missing)}")
    if a["fill"] not in _FILL_MODES:
        raise FeatureConfigError(
            f"[alignment] fill {a['fill']!r} must be one of {sorted(_FILL_MODES)}"
        )
    alignment = Alignment(
        calendar_symbol=a["calendar_symbol"],
        calendar_source=a["calendar_source"],
        calendar_timeframe=a["calendar_timeframe"],
        fill=a["fill"],
        max_fill_days=int(a.get("max_fill_days", 5)),
    )

    features = tuple(_build_feature(f, defaults) for f in raw.get("feature", []))
    if not features:
        raise FeatureConfigError("no [[feature]] blocks found")

    seen: set[str] = set()
    for f in features:
        if f.name in seen:
            raise FeatureConfigError(f"duplicate feature name {f.name!r}")
        seen.add(f.name)

    if not any(f.enabled for f in features):
        raise FeatureConfigError("every feature is disabled — nothing to train on")

    return FeatureConfig(
        schema_version=int(raw["schema_version"]),
        defaults=defaults,
        alignment=alignment,
        features=features,
    )


def describe(cfg: FeatureConfig) -> str:
    """Human-readable summary — what `python -m nn.feature_config` prints."""
    lines = [
        f"schema_version  {cfg.schema_version}",
        f"calendar        {cfg.alignment.calendar_symbol} "
        f"({cfg.alignment.calendar_source}, {cfg.alignment.calendar_timeframe})",
        f"fill            {cfg.alignment.fill}, max {cfg.alignment.max_fill_days} days",
        "",
        f"{len(cfg.features)} columns defined, {len(cfg.enabled)} enabled, "
        f"{cfg.input_width} values per bar",
        f"× lookback {cfg.defaults.get('lookback_window', '?')} "
        f"= {cfg.input_width * int(cfg.defaults.get('lookback_window', 0))} network inputs",
        "",
        f"{'COLUMN':<22} {'METHOD':<22} {'PARAMS':<16} STATE",
    ]
    for f in cfg.features:
        params = ", ".join(f"{k}={v}" for k, v in sorted(f.params.items()))
        state = "enabled" if f.enabled else (f.status or "disabled")
        lines.append(f"{f.name:<22} {f.method:<22} {params:<16} {state}")

    lines += ["", "Series that must be loadable:"]
    for symbol, source in sorted(cfg.required_series()):
        lines.append(f"  {symbol} ({source})")
    return "\n".join(lines)


if __name__ == "__main__":
    print(describe(load()))
