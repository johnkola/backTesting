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

import hashlib
import json

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


# The catalogue fields, from ARCHITECTURE.md 8.2a. Closed sets rather than free
# text: the Features tab groups and filters on them, and the strategy builder
# decides what to offer from `status`, so a typo would silently hide a column
# instead of failing loudly here.
_SCOPES = {"instrument", "market"}
_CADENCES = {"daily", "weekly", "monthly", "quarterly"}
_COSTS = {"free", "paid"}
_UNITS = {"percent", "level", "count", "ratio"}

#: The one status that changes what the build does. Features.md B: RSI, MACD and
#: realized volatility are "computed and stored for charts and rule-based
#: strategies, but kept out of the network". So a data-only column is built,
#: normalized and stored like any other, and is never a network input. Every
#: other status on a disabled column means "not built".
#:
#: Written out rather than implied by a missing status. It used to be implied,
#: and nothing was built at all: `enabled = false` meant "not built" while the
#: Features tab read the absent status as "data only", so the tab claimed three
#: stored columns that did not exist (G9).
DATA_ONLY = "data-only"


@dataclass(frozen=True)
class Feature:
    name: str
    series: dict[str, Any]
    method: str
    params: dict[str, Any] = dc_field(default_factory=dict)
    enabled: bool = True
    status: str | None = None
    note: str | None = None
    # Catalogue fields. Required on every feature -- there is no sensible
    # default for any of them, and guessing would put a wrong answer on screen.
    scope: str = "market"
    cadence: str = "daily"
    cost: str = "free"
    units: str = "level"

    @property
    def width(self) -> int:
        """How many values this column contributes to a bar's vector."""
        return _WIDTHS.get(self.method, 1)

    @property
    def code_version(self) -> str:
        """Fingerprint of HOW this column is computed.

        6.1 wants a run to record "the ordered set of feature ids and the code
        version of each feature's computation". Per feature rather than one
        number for the whole file, so changing one column's method marks only
        that column as newly computed instead of invalidating the catalogue.

        Covers the method, its parameters and the series it reads -- the three
        things that change the output. Deliberately NOT `enabled` or `note`:
        turning a column off or rewording its description does not change the
        number it would produce.
        """
        payload = json.dumps(
            {"method": self.method, "params": self.params, "series": self.series},
            sort_keys=True, separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode()).hexdigest()[:12]

    @property
    def data_only(self) -> bool:
        """Built and stored, never a network input. See DATA_ONLY."""
        return not self.enabled and self.status == DATA_ONLY

    @property
    def is_derived(self) -> bool:
        """True when the series is computed rather than read straight from a
        stored column — a binary op on two series, or a named computation."""
        return "op" in self.series or "computed" in self.series


@dataclass(frozen=True)
class Check:
    """A computed column against a published one — see [[check]] in the TOML.

    The comparison runs on the feature's RAW aligned series, before
    normalization: a z-score of our spread and a z-score of FRED's are fitted on
    different windows and would disagree by construction, which would make the
    check noise rather than a reading.

    Never blocks a build. It is a data-quality reading like the cohesion report
    on an import, and the useful response to a flagged day is to look at it.
    """

    name: str
    feature: str
    published: dict[str, Any]
    tolerance: float
    warmup_days: int = 0
    note: str | None = None


@dataclass(frozen=True)
class FeatureConfig:
    schema_version: int
    defaults: dict[str, Any]
    alignment: Alignment
    features: tuple[Feature, ...]
    checks: tuple[Check, ...] = ()

    @property
    def enabled(self) -> tuple[Feature, ...]:
        """The network's inputs."""
        return tuple(f for f in self.features if f.enabled)

    @property
    def data_only(self) -> tuple[Feature, ...]:
        """Stored beside the inputs, never fed to the network."""
        return tuple(f for f in self.features if f.data_only)

    @property
    def built(self) -> tuple[Feature, ...]:
        """Everything the build computes and stores: the inputs first, in file
        order, then the data-only columns. Inputs first so their positions in
        `feature_spec` are the same with or without data-only columns beside
        them."""
        return self.enabled + self.data_only

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
        """Every (symbol, source) pair that has to be loadable for the built
        features (inputs and data-only). What the data layer should check before training,
        so a missing import fails up front."""
        out: set[tuple[str, str]] = set()

        def walk(spec: dict[str, Any]) -> None:
            if "op" in spec:
                walk(spec["a"])
                walk(spec["b"])
            elif "symbol" in spec and "source" in spec:
                out.add((spec["symbol"], spec["source"]))

        for f in self.built:
            walk(f.series)

        # The published side of every check whose feature is actually built.
        # Without this, moving a column from "read the vendor's number" to
        # "compute it and check the vendor's number" would silently drop that
        # vendor series from the list of imports the pipeline verifies up front,
        # and the check would fail at build time on missing data instead.
        for c in self.live_checks:
            walk(c.published)
        return out

    @property
    def live_checks(self) -> tuple[Check, ...]:
        """Checks whose feature is built, as an input or data only. Any other
        disabled column is not built, so there is nothing to compare and its
        published series is not needed."""
        on = {f.name for f in self.built}
        return tuple(c for c in self.checks if c.feature in on)


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

    for key, allowed in (("scope", _SCOPES), ("cadence", _CADENCES),
                         ("cost", _COSTS), ("units", _UNITS)):
        value = raw.get(key)
        if value is None:
            raise FeatureConfigError(
                f"{name}: `{key}` is required; expected one of {sorted(allowed)}"
            )
        if value not in allowed:
            raise FeatureConfigError(
                f"{name}: unknown {key} {value!r}; expected one of {sorted(allowed)}"
            )

    enabled = bool(raw.get("enabled", True))
    status = raw.get("status")
    if status == DATA_ONLY and enabled:
        # Contradictory, and either reading is a silent mistake: training on a
        # column the file says is kept out, or keeping out one it says is in.
        raise FeatureConfigError(
            f"{name}: status {DATA_ONLY!r} means kept out of the network, "
            f"so it needs `enabled = false`"
        )

    return Feature(
        name=name,
        series=raw["series"],
        method=method,
        params=params,
        enabled=enabled,
        status=status,
        note=raw.get("note"),
        scope=raw["scope"],
        cadence=raw["cadence"],
        cost=raw["cost"],
        units=raw["units"],
    )


def _build_check(raw: dict[str, Any], feature_names: set[str]) -> Check:
    name = raw.get("name")
    if not name:
        raise FeatureConfigError("a [[check]] block has no `name`")

    feature = raw.get("feature")
    if not feature:
        raise FeatureConfigError(f"check {name}: `feature` is required")
    if feature not in feature_names:
        # Naming a column that does not exist is almost always a rename that
        # missed the check, and a check that silently never runs is worse than
        # no check -- it reads as coverage that is not there.
        raise FeatureConfigError(
            f"check {name}: no feature named {feature!r}; "
            f"expected one of {sorted(feature_names)}"
        )

    if "published" not in raw:
        raise FeatureConfigError(f"check {name}: `published` series is required")
    _validate_series(f"check {name}.published", raw["published"])

    if "tolerance" not in raw:
        # No default. A tolerance is a claim about how closely two sources ought
        # to agree, which depends entirely on the series' units and rounding --
        # there is no number this module could pick that would mean anything.
        raise FeatureConfigError(f"check {name}: `tolerance` is required")
    tolerance = raw["tolerance"]
    if not isinstance(tolerance, (int, float)) or isinstance(tolerance, bool) or tolerance <= 0:
        raise FeatureConfigError(f"check {name}: tolerance must be a positive number")

    warmup = raw.get("warmup_days", 0)
    if not isinstance(warmup, int) or isinstance(warmup, bool) or warmup < 0:
        raise FeatureConfigError(f"check {name}: warmup_days must be an integer >= 0")

    return Check(
        name=name,
        feature=feature,
        published=raw["published"],
        tolerance=float(tolerance),
        warmup_days=warmup,
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

    checks = tuple(_build_check(c, seen) for c in raw.get("check", []))
    check_names: set[str] = set()
    for c in checks:
        if c.name in check_names:
            raise FeatureConfigError(f"duplicate check name {c.name!r}")
        check_names.add(c.name)

    return FeatureConfig(
        schema_version=int(raw["schema_version"]),
        defaults=defaults,
        alignment=alignment,
        features=features,
        checks=checks,
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
        f"{len(cfg.data_only)} data only, {cfg.input_width} values per bar",
        f"× lookback {cfg.defaults.get('lookback_window', '?')} "
        f"= {cfg.input_width * int(cfg.defaults.get('lookback_window', 0))} network inputs",
        "",
        f"{'COLUMN':<22} {'METHOD':<22} {'PARAMS':<16} STATE",
    ]
    for f in cfg.features:
        params = ", ".join(f"{k}={v}" for k, v in sorted(f.params.items()))
        state = "enabled" if f.enabled else (f.status or "disabled")
        lines.append(f"{f.name:<22} {f.method:<22} {params:<16} {state}")

    if cfg.checks:
        live = {c.name for c in cfg.live_checks}
        lines += ["", f"{'CHECK':<26} {'FEATURE':<22} {'TOL':>8}  STATE"]
        for c in cfg.checks:
            state = "live" if c.name in live else "skipped (feature not built)"
            lines.append(f"{c.name:<26} {c.feature:<22} {c.tolerance:>8.4g}  {state}")

    lines += ["", "Series that must be loadable:"]
    for symbol, source in sorted(cfg.required_series()):
        lines.append(f"  {symbol} ({source})")
    return "\n".join(lines)


if __name__ == "__main__":
    print(describe(load()))
