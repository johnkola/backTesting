"""A walk-forward fold keeps the model it trained, and its scaler (G50).

Until this, `run_fold` trained a model per fold and engine, read its signals
off it and let it go. The signals were stored (B10), so a run could be
rescored, but nothing could be predicted with afterwards: not the newest bar,
not the prediction window, and nothing could be checked against the model
(R34, R68, R62's ONNX comparison).

Layout, under `FOLD_MODELS_DIR` (beside the model registry, not inside it:
`GET /api/models` walks that tree as strategy / cache key / version):

    <base>/run-<id>/fold-<nn>/<engine>/
        model.pt + scaler.json      the network: its state dict, and the
                                    scaler fitted on that fold's training rows
        model.onnx + check_*        the same network exported to ONNX, and the
                                    batch the Java side checks it on in ONNX
                                    Runtime (R62, G77; `nn/onnx_export.py`)
        model.json | model.txt      a tree engine, in its library's own format;
                                    it has no scaler (R68)

Each kept model has a row in `walkforward_fold_models` with its path, the
network's shape, and a SHA-256 over its files. `load` recomputes the hash and
refuses a model whose files have changed, so what is predicted with is what
the fold trained (R49: files on disk, record and hash in the database, hash
checked on load).

`check(cur, run_id)` is the proof the right thing was kept: it loads every
fold's model, predicts the fold's own scored bars again from the feature
table, and compares with the signals the run stored. It can only do that
while the run's feature set still holds the build the run read.

The rows go with their run (ON DELETE CASCADE); the files do not, so
`prune` removes the directories of runs that no longer exist.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from nn.file_hash import files_hash

MODEL_FILE = "model.pt"
SCALER_FILE = "scaler.json"

#: The largest difference between a stored signal and the kept model's own
#: output that still counts as the same number. They are the same arithmetic
#: on the same machine, so the difference seen is 0; the allowance is for a
#: different CPU or library build, far below anything a threshold could feel.
TOLERANCE = 1e-9


class ModelChanged(RuntimeError):
    """A kept model's files do not hash to what was recorded."""


@dataclass
class Kept:
    engine: str
    model: Any
    scaler: Any
    arch: dict[str, Any]

    def output(self, x: np.ndarray, loss: str, target: str) -> np.ndarray:
        """The raw output over `x`, exactly as a fold stores it."""
        if self.engine == "mlp":
            from nn.walkforward import predict
            return predict(self.model, self.scaler, x, loss, target)
        return self.model.predict(x)


def base_dir() -> Path:
    """Where fold models are kept: `FOLD_MODELS_DIR`, else `fold-models`
    beside `MODELS_DIR`, else `data/fold-models` in the repository."""
    explicit = os.environ.get("FOLD_MODELS_DIR")
    if explicit:
        return Path(explicit)
    models = os.environ.get("MODELS_DIR")
    if models:
        return Path(models).parent / "fold-models"
    return Path(__file__).resolve().parents[2] / "data" / "fold-models"


def run_dir(base: Path, run_id: int) -> Path:
    return base / f"run-{int(run_id)}"


def fold_dir(base: Path, run_id: int, fold: int, engine: str) -> Path:
    return run_dir(base, run_id) / f"fold-{int(fold):02d}" / engine


def network_arch(model, args) -> dict[str, Any]:
    """The shape a state dict needs to be loaded back into (`model.ArchSpec`),
    read off the trained network rather than repeated from the flags."""
    import torch.nn as nn

    linears = [m for m in model.modules() if isinstance(m, nn.Linear)]
    return {
        "input_size": int(linears[0].in_features),
        "hidden_size": int(linears[0].out_features),
        "num_hidden": len(linears) - 1,
        "output_size": int(linears[-1].out_features),
        "dropout_rate": float(getattr(args, "dropout", 0.0)),
        "seed": int(getattr(args, "seed", 0)),
    }


def save(base: Path, run_id: int, fold: int, engine: str, model, scaler=None,
         arch: dict[str, Any] | None = None, check=None,
         head: tuple[str, str] | None = None) -> dict[str, Any]:
    """Write one fold's model (and the network's scaler) and return its row.
    A fold trained again replaces what was there.

    With `check` (the fold's scored inputs and its output on them) and `head`
    (loss, target), the network is also exported to ONNX with that batch
    beside it, for the prediction check (G77). An export that fails is said
    and left out: the fold keeps its PyTorch model, and the check reports the
    fold as not exported. It never fails the run."""
    dir_ = fold_dir(base, run_id, fold, engine)
    if dir_.exists():
        shutil.rmtree(dir_)
    dir_.mkdir(parents=True)
    arch = dict(arch or {})
    if engine == "mlp":
        import torch

        torch.save(model.state_dict(), dir_ / MODEL_FILE)
        (dir_ / SCALER_FILE).write_text(json.dumps(scaler.to_dict()), encoding="utf-8")
        if check is not None and head is not None:
            try:
                from nn import onnx_export

                onnx_export.write(dir_, model, scaler, check[0], check[1], *head)
                arch["onnx"] = True
            except Exception as exc:  # noqa: BLE001 - the export is a check, not the run
                for name in ("model.onnx", "check_inputs.f32", "check_outputs.f64",
                             "check.json"):
                    (dir_ / name).unlink(missing_ok=True)
                arch["onnx"] = False
                print(f"  onnx export  fold {fold}: FAILED, {type(exc).__name__}: {exc}"[:300])
    else:
        from nn.trees import MODEL_FILES

        (dir_ / MODEL_FILES[engine]).write_bytes(model.to_bytes())
        arch.setdefault("target", getattr(model, "target", "class"))
    return {
        "run_id": int(run_id), "fold": int(fold), "engine": engine,
        "path": dir_.relative_to(base).as_posix(),
        "file_hash": files_hash(dir_),
        "bytes": sum(p.stat().st_size for p in dir_.iterdir() if p.is_file()),
        "arch_json": json.dumps(arch, sort_keys=True),
    }


_UPSERT = """
INSERT INTO walkforward_fold_models (run_id, fold, engine, path, file_hash, bytes, arch_json)
VALUES (%(run_id)s, %(fold)s, %(engine)s, %(path)s, %(file_hash)s, %(bytes)s, %(arch_json)s)
ON CONFLICT (run_id, fold, engine) DO UPDATE
   SET path = EXCLUDED.path, file_hash = EXCLUDED.file_hash, bytes = EXCLUDED.bytes,
       arch_json = EXCLUDED.arch_json
"""


def record(conn, row: dict[str, Any]) -> None:
    with conn.cursor() as cur:
        cur.execute(_UPSERT, row)
    conn.commit()


def rows(cur, run_id: int) -> list[dict[str, Any]]:
    cur.execute("SELECT fold, engine, path, file_hash, bytes, arch_json "
                "FROM walkforward_fold_models WHERE run_id = %s ORDER BY fold, engine",
                (run_id,))
    keys = ("fold", "engine", "path", "file_hash", "bytes", "arch_json")
    return [dict(zip(keys, r, strict=True)) for r in cur.fetchall()]


def load_row(row: dict[str, Any], base: Path | None = None) -> Kept:
    """The model a row points at, after its files are checked against the
    recorded hash."""
    base = base or base_dir()
    dir_ = base / row["path"]
    if not dir_.is_dir():
        raise FileNotFoundError(f"fold {row['fold']} {row['engine']}: {dir_} is not there")
    now = files_hash(dir_)
    if now != row["file_hash"]:
        raise ModelChanged(f"fold {row['fold']} {row['engine']}: the files under {dir_} hash "
                           f"to {now[:12]}, not the recorded {row['file_hash'][:12]}")
    arch = json.loads(row["arch_json"] or "{}")
    engine = row["engine"]
    if engine == "mlp":
        import torch

        from nn.model import ArchSpec, build
        from nn.standardise import MinMaxScaler

        model = build(ArchSpec(**{k: v for k, v in arch.items() if k != "onnx"}))
        model.load_state_dict(torch.load(dir_ / MODEL_FILE, weights_only=True))
        model.eval()
        scaler = MinMaxScaler.from_dict(json.loads((dir_ / SCALER_FILE).read_text("utf-8")))
        return Kept(engine, model, scaler, arch)
    from nn.trees import MODEL_FILES, TreeModel

    model = TreeModel.from_bytes(engine, (dir_ / MODEL_FILES[engine]).read_bytes())
    model.target = arch.get("target", "class")
    return Kept(engine, model, None, arch)


def load(cur, run_id: int, fold: int, engine: str = "mlp", base: Path | None = None) -> Kept:
    for row in rows(cur, run_id):
        if row["fold"] == fold and row["engine"] == engine:
            return load_row(row, base)
    raise LookupError(f"run {run_id} kept no {engine} model for fold {fold}")


# ---------------------------------------------------------------------------
# The check: a kept model gives the signals its fold stored
# ---------------------------------------------------------------------------


@dataclass
class Checked:
    fold: int
    engine: str
    bars: int
    #: Largest absolute difference from the stored output; None when the
    #: model could not be compared, with `problem` saying why.
    worst: float | None
    problem: str | None = None

    @property
    def ok(self) -> bool:
        return self.problem is None and self.worst is not None and self.worst <= TOLERANCE


def check(cur, run_id: int, base: Path | None = None) -> tuple[list[Checked], str | None]:
    """Every kept model of a run against the signals the run stored.

    Returns (one line per fold and engine, or the reason nothing could be
    checked). The inputs are read from the run's feature set, so the check
    needs that set to hold the build the run read."""
    from nn import feature_store, walkforward_store

    cur.execute(
        "SELECT r.feature_set_id, r.feature_run_id, s.current_run_id, r.columns_json, "
        "r.hyperparams_json, r.loss, r.target, r.strategy_id "
        "FROM walkforward_runs r LEFT JOIN feature_sets s ON s.id = r.feature_set_id "
        "WHERE r.id = %s", (run_id,))
    run = cur.fetchone()
    if run is None:
        return [], f"there is no run {run_id}"
    set_id, read_build, held_build, columns_json, hp_json, loss, target, strategy_id = run
    kept = rows(cur, run_id)
    if not kept:
        return [], f"run {run_id} kept no models (it ran before G50, or was recalculated)"
    if set_id is None or read_build is None or read_build != held_build:
        return [], (f"run {run_id} read feature build {read_build}, and its set now holds "
                    f"{held_build}; its inputs are not on file to predict from")
    names = json.loads(columns_json or "[]")
    lookback = int(json.loads(hp_json or "{}").get("lookback", 20))
    fm = feature_store.read_matrix(cur, int(set_id),
                                   columns=None if strategy_id is None else names)
    x_all = feature_store.stack_lookback(fm.values, lookback)
    row_of = {d: i for i, d in enumerate(fm.dates[lookback - 1:])}
    stored = walkforward_store.load_engine_signals(cur, run_id)
    out: list[Checked] = []
    for row in kept:
        fold, engine = row["fold"], row["engine"]
        sig = stored.get(engine, {}).get(fold)
        if sig is None:
            out.append(Checked(fold, engine, 0, None, "the run stored no signals for it"))
            continue
        missing = [d for d in sig.dates if d not in row_of]
        if missing:
            out.append(Checked(fold, engine, len(sig.dates), None,
                               f"{len(missing)} scored bar(s) are not in the feature table, "
                               f"from {missing[0]}"))
            continue
        try:
            model = load_row(row, base)
        except (ModelChanged, FileNotFoundError) as exc:
            out.append(Checked(fold, engine, len(sig.dates), None, str(exc)))
            continue
        again = model.output(x_all[[row_of[d] for d in sig.dates]], loss or "ce",
                             target or "class")
        worst = float(np.max(np.abs(again - sig.output))) if len(sig.dates) else 0.0
        out.append(Checked(fold, engine, len(sig.dates), worst))
    return out, None


def prune(cur, base: Path | None = None, dry_run: bool = False) -> list[Path]:
    """Remove the directories of runs that no longer exist. A run's rows go
    with it; its files are only reachable by its id, so they are left behind."""
    base = base or base_dir()
    if not base.is_dir():
        return []
    cur.execute("SELECT id FROM walkforward_runs")
    alive = {int(r[0]) for r in cur.fetchall()}
    gone = []
    for d in sorted(base.glob("run-*")):
        tail = d.name[4:]
        if d.is_dir() and tail.isdigit() and int(tail) not in alive:
            gone.append(d)
            if not dry_run:
                shutil.rmtree(d)
    return gone


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="The models a walk-forward run kept (G50).")
    p.add_argument("--check", type=int, metavar="RUN_ID",
                   help="load every kept model of a run and compare its output with the "
                        "signals the run stored")
    p.add_argument("--list", type=int, metavar="RUN_ID", help="list a run's kept models")
    p.add_argument("--prune", action="store_true",
                   help="remove the files of runs that no longer exist")
    p.add_argument("--dry-run", action="store_true", help="with --prune: remove nothing")
    args = p.parse_args(argv)
    from nn.feature_build import connect

    with connect() as conn, conn.cursor() as cur:
        if args.list is not None:
            for r in rows(cur, args.list):
                print(f"fold {r['fold']:>2}  {r['engine']:<9} {r['bytes']:>9} bytes  "
                      f"{r['file_hash'][:12]}  {r['path']}")
            return 0
        if args.prune:
            gone = prune(cur, dry_run=args.dry_run)
            for d in gone:
                print(f"{'would remove' if args.dry_run else 'removed'} {d}")
            print(f"{len(gone)} run director{'y' if len(gone) == 1 else 'ies'} "
                  f"{'to remove' if args.dry_run else 'removed'}")
            return 0
        if args.check is not None:
            lines, why = check(cur, args.check)
            if why:
                print(f"not checked: {why}")
                return 2
            for c in lines:
                print(f"fold {c.fold:>2}  {c.engine:<9} {c.bars:>5} bars  "
                      + (f"NOT CHECKED: {c.problem}" if c.problem else
                         f"largest difference {c.worst:.3g}  "
                         f"{'same' if c.ok else 'DIFFERENT'}"))
            bad = [c for c in lines if not c.ok]
            print(f"{len(lines) - len(bad)} of {len(lines)} kept models give the signals "
                  "their fold stored")
            return 1 if bad else 0
    p.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
