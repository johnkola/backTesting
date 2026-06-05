"""Filesystem-backed model registry — Python port of
src/main/java/.../strategy/persistence/ModelStore.java.

Layout mirrors the Java side exactly:

  <base>/<strategy>/<cache_key>/<version_id>/
      ├── model.pt        # torch.save() of the nn.Sequential state_dict
      ├── scaler.json     # MinMaxScaler.to_dict() — replaces normalizer.bin
      └── metadata.json   # MetadataRecord — what was trained, when, on what

version_id format: yyyyMMddTHHmmss.SSSZ — same regex as the Java side so
external tools that walked the model tree (the existing Node
GET /api/models endpoint, ops scripts) keep working through the cutover.

Cache key: deterministic SHA-256 over sorted `key=value\\n` lines. The
input map for the Python side intentionally drops the `DL4J version`
contributor — a Python-trained model lives in a different key space
from a DL4J-trained one, even with the same hyperparameters."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

import torch
import torch.nn as nn

from nn.standardise import MinMaxScaler

MODEL_FILE = "model.pt"
SCALER_FILE = "scaler.json"
METADATA_FILE = "metadata.json"

# Compact ISO-8601 UTC w/ ms precision: 20260603T134522.123Z. Sortable
# lexicographically, filesystem-safe on every host the project runs on.
_VERSION_RE = re.compile(r"\d{8}T\d{6}\.\d{3}Z")


@dataclass
class MetadataRecord:
    """Persisted alongside the model. Free-form `extra` lets callers
    attach training metrics, label distribution, etc. without changing
    the schema."""

    strategy: str
    cache_key: str
    version_id: str
    created_at: str
    input_size: int
    hidden_size: int
    num_hidden: int
    extra: dict = field(default_factory=dict)


def compute_cache_key(inputs: Mapping[str, str]) -> str:
    """Same SHA-256-of-sorted-pairs contract as ModelStore.computeCacheKey()."""
    body = "".join(f"{k}={v}\n" for k, v in sorted(inputs.items()))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _new_version_id() -> str:
    now = datetime.now(timezone.utc)
    # Truncate microseconds → milliseconds, then format. strftime can't
    # do ms width by itself, so we splice the millisecond digits in.
    base = now.strftime("%Y%m%dT%H%M%S")
    ms = f"{now.microsecond // 1000:03d}"
    return f"{base}.{ms}Z"


@dataclass
class LoadedModel:
    """In-memory triple returned by load(). The PyTorch state_dict has
    already been folded into `model`."""

    model: nn.Module
    scaler: MinMaxScaler
    metadata: MetadataRecord


class ModelRegistry:
    """Disk-only registry. SQLite indexing was deferred — at our model
    count (handful per strategy), the filesystem walk for list/find is
    cheap and the Node GET /api/models endpoint already does it."""

    def __init__(self, base_dir: Path | str, *, keep_last_n: int = 0) -> None:
        self.base_dir = Path(base_dir)
        self.keep_last_n = keep_last_n

    # ---- paths --------------------------------------------------------------

    def entry_dir(self, strategy: str, cache_key: str) -> Path:
        return self.base_dir / strategy / cache_key

    def version_dir(self, strategy: str, cache_key: str, version_id: str) -> Path:
        return self.entry_dir(strategy, cache_key) / version_id

    # ---- save ---------------------------------------------------------------

    def save(
        self,
        *,
        strategy: str,
        cache_key: str,
        model: nn.Module,
        scaler: MinMaxScaler,
        metadata_extra: Mapping[str, object] | None = None,
        input_size: int,
        hidden_size: int,
        num_hidden: int,
    ) -> str:
        version_id = _new_version_id()
        dir_ = self.version_dir(strategy, cache_key, version_id)
        dir_.mkdir(parents=True, exist_ok=True)

        torch.save(model.state_dict(), dir_ / MODEL_FILE)
        (dir_ / SCALER_FILE).write_text(json.dumps(scaler.to_dict()), encoding="utf-8")

        meta = MetadataRecord(
            strategy=strategy,
            cache_key=cache_key,
            version_id=version_id,
            created_at=datetime.now(timezone.utc).isoformat(),
            input_size=input_size,
            hidden_size=hidden_size,
            num_hidden=num_hidden,
            extra=dict(metadata_extra or {}),
        )
        (dir_ / METADATA_FILE).write_text(
            json.dumps(asdict(meta), indent=2), encoding="utf-8"
        )

        if self.keep_last_n > 0:
            self.prune_to(strategy, cache_key, self.keep_last_n)
        return version_id

    # ---- load ---------------------------------------------------------------

    def load(
        self,
        strategy: str,
        cache_key: str,
        version_id: str | None = None,
        *,
        build_model: callable,  # type: ignore[valid-type]
    ) -> LoadedModel | None:
        """Resolve a version (specific if pinned, otherwise the
        lexicographically-latest) and rehydrate. `build_model` is a
        factory the caller supplies — usually `lambda: nn.build(spec)` —
        because state_dict reload needs an empty module of the right
        shape first."""

        entry = self.entry_dir(strategy, cache_key)
        if not entry.exists():
            return None

        if version_id is not None:
            dir_ = entry / version_id
            if not dir_.is_dir():
                return None
            return self._load_from_dir(dir_, build_model)

        latest = self._find_latest(entry)
        if latest is None:
            return None
        return self._load_from_dir(latest, build_model)

    def _find_latest(self, entry: Path) -> Path | None:
        versions = [p for p in entry.iterdir() if p.is_dir() and _VERSION_RE.fullmatch(p.name)]
        if not versions:
            return None
        return max(versions, key=lambda p: p.name)

    def _load_from_dir(self, dir_: Path, build_model) -> LoadedModel | None:
        model_path = dir_ / MODEL_FILE
        scaler_path = dir_ / SCALER_FILE
        metadata_path = dir_ / METADATA_FILE
        if not (model_path.exists() and scaler_path.exists() and metadata_path.exists()):
            return None

        metadata_blob = json.loads(metadata_path.read_text(encoding="utf-8"))
        metadata = MetadataRecord(**metadata_blob)

        model = build_model()
        state = torch.load(model_path, map_location="cpu")
        model.load_state_dict(state)
        model.eval()

        scaler = MinMaxScaler.from_dict(json.loads(scaler_path.read_text(encoding="utf-8")))
        return LoadedModel(model=model, scaler=scaler, metadata=metadata)

    # ---- listing + retention ------------------------------------------------

    def list_versions(self, strategy: str, cache_key: str) -> list[str]:
        """Sorted ascending; the last element is the newest."""
        entry = self.entry_dir(strategy, cache_key)
        if not entry.exists():
            return []
        return sorted(p.name for p in entry.iterdir() if p.is_dir() and _VERSION_RE.fullmatch(p.name))

    def list_all(self, strategy: str | None = None) -> list[MetadataRecord]:
        """Walk the tree and rehydrate metadata. Backs GET /api/nn/models."""
        out: list[MetadataRecord] = []
        if not self.base_dir.exists():
            return out
        strategy_dirs = (
            [self.base_dir / strategy] if strategy else [p for p in self.base_dir.iterdir() if p.is_dir()]
        )
        for strat_dir in strategy_dirs:
            if not strat_dir.is_dir():
                continue
            for key_dir in strat_dir.iterdir():
                if not key_dir.is_dir():
                    continue
                for ver_dir in key_dir.iterdir():
                    if not ver_dir.is_dir() or not _VERSION_RE.fullmatch(ver_dir.name):
                        continue
                    metadata_path = ver_dir / METADATA_FILE
                    if not metadata_path.exists():
                        continue
                    try:
                        out.append(MetadataRecord(**json.loads(metadata_path.read_text("utf-8"))))
                    except (json.JSONDecodeError, TypeError):
                        # Corrupt or schema-drifted entries are skipped rather than
                        # blowing up the whole listing endpoint.
                        continue
        out.sort(key=lambda m: m.created_at, reverse=True)
        return out

    def prune_to(self, strategy: str, cache_key: str, keep_last_n: int) -> list[str]:
        if keep_last_n <= 0:
            return []
        entry = self.entry_dir(strategy, cache_key)
        if not entry.exists():
            return []
        versions = sorted(
            p for p in entry.iterdir() if p.is_dir() and _VERSION_RE.fullmatch(p.name)
        )
        if len(versions) <= keep_last_n:
            return []
        doomed = versions[: len(versions) - keep_last_n]
        pruned: list[str] = []
        for d in doomed:
            shutil.rmtree(d)
            pruned.append(d.name)
        return pruned
