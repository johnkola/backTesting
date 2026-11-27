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
from a DL4J-trained one, even with the same hyperparameters.

THE DATABASE SAYS WHICH MODELS EXIST (R49). The files are on disk; each
version has a row in `model_versions` with its path and a SHA-256 over its
files (`files_hash`, the rule the walk-forward folds' models use too). The
registry writes the row when it saves, checks it before it loads, and drops it
when a version is deleted or pruned. A version with no row, or whose files no
longer hash to it, is refused rather than loaded: a weights file copied in by
hand, or edited, would otherwise be served as if it were the model that was
trained. `python -m nn.store --record` records the versions saved before the
rows existed, and `--verify` checks every version against its row."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import torch
import torch.nn as nn

from nn.file_hash import files_bytes, files_hash
from nn.standardise import MinMaxScaler

MODEL_FILE = "model.pt"
SCALER_FILE = "scaler.json"
METADATA_FILE = "metadata.json"


def engine_of(extra: Mapping | None) -> str:
    """The engine a version holds (B9): `extra.engine`, or the network for a
    version saved before the tree engines, which recorded none."""
    return str((extra or {}).get("engine") or "mlp")


def required_files(engine: str) -> tuple[str, ...]:
    """What a complete version directory of `engine` holds. A tree engine is
    saved in its library's own format and has no scaler (R68)."""
    if engine == "mlp":
        return (MODEL_FILE, SCALER_FILE, METADATA_FILE)
    from nn.trees import MODEL_FILES
    return (MODEL_FILES[engine], METADATA_FILE)

# Compact ISO-8601 UTC w/ ms precision: 20260603T134522.123Z. Sortable
# lexicographically, filesystem-safe on every host the project runs on.
_VERSION_RE = re.compile(r"\d{8}T\d{6}\.\d{3}Z")


class ModelRefused(RuntimeError):
    """A version the database does not vouch for: it is not loaded."""


class UnrecordedModel(ModelRefused):
    """A version on disk with no row in `model_versions`."""


class ModelChanged(ModelRefused):
    """A version whose files no longer hash to its row."""


class ModelRecords:
    """The `model_versions` rows: each saved version's path, hash and size."""

    def __init__(self, connect: Callable[[], Any] | None = None) -> None:
        if connect is None:
            from loader.db import pool

            def connect():
                return pool().connection()
        self._connect = connect

    def record(self, meta: MetadataRecord, path: str, file_hash: str, size: int) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO model_versions (strategy, cache_key, version_id, path, file_hash, "
                "bytes, trained_at) VALUES (%s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (strategy, cache_key, version_id) DO UPDATE SET path = EXCLUDED.path, "
                "file_hash = EXCLUDED.file_hash, bytes = EXCLUDED.bytes, recorded_at = now(), "
                "recorded_by = audit_actor()",
                (meta.strategy, meta.cache_key, meta.version_id, path, file_hash, size,
                 meta.created_at))
            conn.commit()

    def get(self, strategy: str, cache_key: str, version_id: str) -> dict[str, Any] | None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT path, file_hash, bytes FROM model_versions WHERE strategy = %s "
                        "AND cache_key = %s AND version_id = %s",
                        (strategy, cache_key, version_id))
            row = cur.fetchone()
        return None if row is None else {"path": row[0], "fileHash": row[1], "bytes": int(row[2])}

    def all(self) -> dict[tuple[str, str, str], dict[str, Any]]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT strategy, cache_key, version_id, path, file_hash, bytes "
                        "FROM model_versions")
            return {(a, b, c): {"path": p, "fileHash": h, "bytes": int(n)}
                    for a, b, c, p, h, n in cur.fetchall()}

    def forget(self, strategy: str, cache_key: str, version_id: str) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM model_versions WHERE strategy = %s AND cache_key = %s "
                        "AND version_id = %s", (strategy, cache_key, version_id))
            conn.commit()


def _plain(name: str) -> bool:
    """A name the registry could have written: one directory, not a path. A
    strategy or cache key from a request that is not one could point outside
    the registry."""
    return name not in ("", ".", "..") and "/" not in name and "\\" not in name


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
    already been folded into `model`. For a tree engine `model` is a
    `trees.TreeModel` and `scaler` is None (B9)."""

    model: object
    scaler: MinMaxScaler | None
    metadata: MetadataRecord


class ModelRegistry:
    """The model files under `base_dir`, with their rows in `records`.

    Listing walks the directories, which is cheap at our model count. Saving
    records, and loading checks the record (see the module comment). `records`
    is always given: the service and the trainers pass `ModelRecords()`; only a
    test's own registry in a temporary directory passes None, and then nothing
    is recorded or checked."""

    def __init__(self, base_dir: Path | str, *, records: ModelRecords | None,
                 keep_last_n: int = 0) -> None:
        self.base_dir = Path(base_dir)
        self.records = records
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
        model: object,
        scaler: MinMaxScaler | None,
        metadata_extra: Mapping[str, object] | None = None,
        input_size: int,
        hidden_size: int,
        num_hidden: int,
    ) -> str:
        """Save a version. `model` is a torch module with its scaler, or a
        `trees.TreeModel` with none, written in its library's format (B9)."""
        version_id = _new_version_id()
        dir_ = self.version_dir(strategy, cache_key, version_id)
        dir_.mkdir(parents=True, exist_ok=True)

        engine = getattr(model, "engine", None) if not isinstance(model, nn.Module) else None
        if engine is not None:
            from nn.trees import MODEL_FILES
            (dir_ / MODEL_FILES[engine]).write_bytes(model.to_bytes())
            metadata_extra = {**dict(metadata_extra or {}), "engine": engine}
        else:
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
        if self.records is not None:
            try:
                self.records.record(meta, dir_.relative_to(self.base_dir).as_posix(),
                                    files_hash(dir_), files_bytes(dir_))
            except Exception:
                # Files without a row would be refused on load anyway; leave none.
                shutil.rmtree(dir_)
                raise

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

        dir_ = self.resolve_version_dir(strategy, cache_key, version_id)
        if dir_ is None:
            return None
        return self._load_from_dir(dir_, build_model)

    def resolve_version_dir(
        self, strategy: str, cache_key: str, version_id: str | None = None
    ) -> Path | None:
        """Directory holding one saved version: the pinned one when
        `version_id` is given, otherwise the lexicographically-latest.
        None when the entry doesn't exist or the pin doesn't resolve —
        the API layer turns that into a 404."""

        entry = self.entry_dir(strategy, cache_key)
        if not entry.exists():
            return None
        if version_id is not None:
            dir_ = entry / version_id
            return dir_ if dir_.is_dir() else None
        return self._find_latest(entry)

    def read_metadata(
        self, strategy: str, cache_key: str, version_id: str | None = None
    ) -> MetadataRecord | None:
        """Read metadata.json without touching model.pt.

        Rehydrating a state_dict needs an empty module of the right shape
        first, and the weights can't report their own shape before they're
        loaded — so the architecture has to come from metadata, read on its
        own. Applies the same all-three-files-present rule as the full load,
        so a half-written version directory reads as a miss either way."""

        dir_ = self.resolve_version_dir(strategy, cache_key, version_id)
        if dir_ is None or not (dir_ / METADATA_FILE).exists():
            return None
        blob = json.loads((dir_ / METADATA_FILE).read_text(encoding="utf-8"))
        if not all((dir_ / f).exists() for f in required_files(engine_of(blob.get("extra")))):
            return None
        return MetadataRecord(**blob)

    def verify(self, dir_: Path, metadata: MetadataRecord) -> None:
        """Raise unless the version's files hash to its row. Nothing to check
        without records."""
        if self.records is None:
            return
        name = f"{metadata.strategy}/{metadata.cache_key[:12]}/{metadata.version_id}"
        row = self.records.get(metadata.strategy, metadata.cache_key, metadata.version_id)
        if row is None:
            raise UnrecordedModel(
                f"model {name} has no record in the database, so it is not loaded; a "
                f"version saved before the records existed is recorded by "
                f"`python -m nn.store --record`")
        now = files_hash(dir_)
        if now != row["fileHash"]:
            raise ModelChanged(
                f"model {name}: its files hash to {now[:12]}, not the recorded "
                f"{row['fileHash'][:12]}; they changed after it was saved, so it is not loaded")

    def _find_latest(self, entry: Path) -> Path | None:
        versions = [p for p in entry.iterdir() if p.is_dir() and _VERSION_RE.fullmatch(p.name)]
        if not versions:
            return None
        return max(versions, key=lambda p: p.name)

    def _load_from_dir(self, dir_: Path, build_model) -> LoadedModel | None:
        model_path = dir_ / MODEL_FILE
        scaler_path = dir_ / SCALER_FILE
        metadata_path = dir_ / METADATA_FILE
        if not metadata_path.exists():
            return None
        metadata_blob = json.loads(metadata_path.read_text(encoding="utf-8"))
        engine = engine_of(metadata_blob.get("extra"))
        if not all((dir_ / f).exists() for f in required_files(engine)):
            return None
        metadata = MetadataRecord(**metadata_blob)
        self.verify(dir_, metadata)
        if engine != "mlp":
            from nn.trees import MODEL_FILES, TreeModel
            tree = TreeModel.from_bytes(engine, (dir_ / MODEL_FILES[engine]).read_bytes())
            return LoadedModel(model=tree, scaler=None, metadata=metadata)

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
        if not self.base_dir.exists() or (strategy and not _plain(strategy)):
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

    def delete_version(self, strategy: str, cache_key: str, version_id: str) -> bool:
        """Remove one stored version's directory. False when there is none.

        The three names are checked against what the registry itself writes
        before anything is removed: they come from a request, and a name that
        is not a plain directory name could point outside the registry."""
        if not _VERSION_RE.fullmatch(version_id):
            return False
        if not (_plain(strategy) and _plain(cache_key)):
            return False
        ver_dir = self.entry_dir(strategy, cache_key) / version_id
        if not ver_dir.is_dir():
            return False
        shutil.rmtree(ver_dir)
        if self.records is not None:
            self.records.forget(strategy, cache_key, version_id)
        entry = ver_dir.parent
        # The key's directory goes with its last version, so the listing does
        # not read an empty one as a version-less entry from before versions.
        if entry.is_dir() and not any(entry.iterdir()):
            entry.rmdir()
        return True

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
            if self.records is not None:
                self.records.forget(strategy, cache_key, d.name)
            pruned.append(d.name)
        return pruned

    def versions_on_disk(self) -> list[tuple[str, str, str, Path]]:
        """(strategy, cache key, version, directory) of every version directory
        with metadata, whatever its record says."""
        out = []
        if not self.base_dir.is_dir():
            return out
        for strat_dir in sorted(p for p in self.base_dir.iterdir() if p.is_dir()):
            for key_dir in sorted(p for p in strat_dir.iterdir() if p.is_dir()):
                for ver_dir in sorted(p for p in key_dir.iterdir() if p.is_dir()):
                    if _VERSION_RE.fullmatch(ver_dir.name) and (ver_dir / METADATA_FILE).exists():
                        out.append((strat_dir.name, key_dir.name, ver_dir.name, ver_dir))
        return out


def check_all(registry: ModelRegistry, record: bool = False,
              log: Callable[[str], None] = print) -> dict[str, int]:
    """Every version on disk against its row, and every row against the disk.
    With `record`, a version with no row is recorded as its files are now."""
    if registry.records is None:
        raise ValueError("a registry without records has nothing to check against")
    rows = registry.records.all()
    counts = {"ok": 0, "recorded": 0, "unrecorded": 0, "changed": 0, "missing": 0}
    seen = set()
    for strategy, key, version, dir_ in registry.versions_on_disk():
        seen.add((strategy, key, version))
        name = f"{strategy}/{key[:12]}/{version}"
        row = rows.get((strategy, key, version))
        now = files_hash(dir_)
        if row is None:
            if record:
                meta = MetadataRecord(**json.loads((dir_ / METADATA_FILE).read_text("utf-8")))
                registry.records.record(meta, dir_.relative_to(registry.base_dir).as_posix(),
                                        now, files_bytes(dir_))
                counts["recorded"] += 1
                log(f"recorded   {name}  {now[:12]}")
            else:
                counts["unrecorded"] += 1
                log(f"UNRECORDED {name}")
        elif row["fileHash"] != now:
            counts["changed"] += 1
            log(f"CHANGED    {name}  {now[:12]}, recorded {row['fileHash'][:12]}")
        else:
            counts["ok"] += 1
    for strategy, key, version in sorted(set(rows) - seen):
        counts["missing"] += 1
        log(f"MISSING    {strategy}/{key[:12]}/{version}  recorded, but its files are gone")
    return counts


def main(argv: list[str] | None = None) -> int:
    import argparse
    import os

    p = argparse.ArgumentParser(
        prog="python -m nn.store",
        description="The trained models under MODELS_DIR against their database records.")
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--verify", action="store_true",
                      help="check every version's files against its record; writes nothing")
    mode.add_argument("--record", action="store_true",
                      help="as --verify, and record each version that has no record yet, as "
                           "its files are now")
    p.add_argument("--models-dir", default=os.environ.get("MODELS_DIR", "data/models"))
    args = p.parse_args(argv)
    counts = check_all(ModelRegistry(args.models_dir, records=ModelRecords()),
                       record=args.record)
    print(f"{counts['ok']} as recorded, {counts['recorded']} recorded now, "
          f"{counts['unrecorded']} without a record, {counts['changed']} changed, "
          f"{counts['missing']} recorded but gone")
    return 1 if counts["unrecorded"] or counts["changed"] or counts["missing"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
