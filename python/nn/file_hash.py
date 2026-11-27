"""The hash a kept model's files are recorded and checked by (R49).

One rule for every model on disk, the registry's versions (`nn/store.py`) and
the walk-forward folds' (`nn/fold_models.py`), and the one the engine follows
before it reads a fold's ONNX copy (`OnnxCheck.filesHash`): a SHA-256 over
every file of the model's directory in name order, each as its name, a zero
byte, its length, a zero byte and its bytes. One changed, added or missing file
changes it.
"""

from __future__ import annotations

import hashlib
from pathlib import Path


def files_hash(dir_: Path) -> str:
    h = hashlib.sha256()
    for f in sorted(p for p in dir_.iterdir() if p.is_file()):
        raw = f.read_bytes()
        h.update(f.name.encode("utf-8") + b"\0" + str(len(raw)).encode() + b"\0" + raw)
    return h.hexdigest()


def files_bytes(dir_: Path) -> int:
    return sum(p.stat().st_size for p in dir_.iterdir() if p.is_file())
