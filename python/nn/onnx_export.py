"""A fold's network as an ONNX model, and the batch it is checked on (R62, G77).

The prediction check: the Java side runs the exported network in ONNX Runtime
and compares its output with what PyTorch produced on the same inputs, for
every walk-forward fold. This module writes what that needs, beside the fold's
`model.pt` (`nn/fold_models.py`):

    model.onnx           the network with its scaler and its output head
                         inside. It takes the fold's raw stacked inputs
                         (n, width) float32, named `x`, and returns exactly
                         what the fold stores per bar, named `y`.
    check_inputs.f32     the check batch: rows x width float32, little-endian,
                         row by row
    check_outputs.f64    PyTorch's output on that batch: rows x outputs
                         float64, little-endian. The run's stored signals.
    check.json           {rows, width, outputs, loss, target, tolerance, ...}

The scaler and the head are inside the graph on purpose. A check that fed
ONNX already-scaled inputs and compared logits would prove the layers were
exported and nothing about the two steps where a prediction most easily goes
wrong on another runtime: scaling a constant column, and turning logits into
the position or probability the profit step reads.

The batch is the fold's own scored bars, the same inputs its stored signals
came from. A fold with more than `MAX_ROWS` (an intraday year) is checked on
that many, evenly spaced; `check.json` says so.

Tree engines are not exported: R62 is a check of the network.
"""

from __future__ import annotations

import io
import json
import logging
import warnings
from pathlib import Path
from typing import Any

import numpy as np
import torch
import torch.nn as nn

ONNX_FILE = "model.onnx"
INPUTS_FILE = "check_inputs.f32"
OUTPUTS_FILE = "check_outputs.f64"
CHECK_FILE = "check.json"

#: The largest difference between ONNX Runtime's output and PyTorch's that
#: still counts as agreement. Both compute in float32; the same layers on two
#: runtimes differ in the order they add, by about 1e-7 on a probability. 1e-5
#: is two orders above that and four below anything a threshold could feel.
TOLERANCE = 1e-5

#: The most bars a fold is checked on.
MAX_ROWS = 2000


class Exported(nn.Module):
    """Raw inputs to the stored output: scale, the network, the head.

    Mirrors `standardise.MinMaxScaler.transform` and `walkforward.predict`
    line for line, in float32, so the graph is the fold's whole prediction."""

    def __init__(self, model: nn.Module, scaler, loss: str, target: str):
        super().__init__()
        lo = np.asarray(scaler.feature_min, dtype=np.float32)
        span = np.asarray(scaler.feature_max, dtype=np.float32) - lo
        self.model = model
        self.register_buffer("lo", torch.from_numpy(lo))
        self.register_buffer("span", torch.from_numpy(np.where(span == 0.0, 1.0, span)
                                                      .astype(np.float32)))
        self.register_buffer("constant", torch.from_numpy(span == 0.0))
        self.out_min = float(scaler.out_min)
        self.out_range = float(scaler.out_max) - float(scaler.out_min)
        self.head = ("position" if loss != "ce" else
                     {"direction": "p_up", "return": "raw"}.get(target, "classes"))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        unit = (x - self.lo) / self.span
        unit = torch.where(self.constant, torch.full_like(unit, 0.5), unit)
        logits = self.model(unit * self.out_range + self.out_min)
        if self.head == "position":
            return torch.sigmoid(logits).reshape(-1, 1)
        if self.head == "p_up":
            return torch.softmax(logits, dim=1)[:, 1:2]
        if self.head == "raw":
            return logits.reshape(-1, 1)
        return torch.softmax(logits, dim=1)


def to_onnx(model: nn.Module, scaler, loss: str, target: str, width: int) -> bytes:
    """The fold's prediction as ONNX bytes, with a free batch size."""
    wrapped = Exported(model, scaler, loss, target).eval()
    example = torch.zeros((2, width), dtype=torch.float32)
    buf = io.BytesIO()
    noisy = logging.getLogger("torch.onnx")
    level = noisy.level
    noisy.setLevel(logging.ERROR)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            torch.onnx.export(
                wrapped, (example,), buf, input_names=["x"], output_names=["y"],
                dynamo=True, dynamic_shapes=({0: torch.export.Dim("rows")},),
                verbose=False)
    finally:
        noisy.setLevel(level)
    return buf.getvalue()


def check_rows(n: int) -> np.ndarray:
    """Which of a fold's `n` scored bars go in the check batch: all of them,
    or `MAX_ROWS` evenly spaced."""
    if n <= MAX_ROWS:
        return np.arange(n)
    return np.unique(np.linspace(0, n - 1, MAX_ROWS).round().astype(np.int64))


def write(dir_: Path, model: nn.Module, scaler, x: np.ndarray, output: np.ndarray,
          loss: str, target: str) -> dict[str, Any]:
    """Write the ONNX model and its check batch into a fold's directory.
    `x` is the fold's scored bars as the network was given them, before
    scaling; `output` what `walkforward.predict` returned on them."""
    x = np.ascontiguousarray(x, dtype=np.float32)
    output = np.asarray(output, dtype=np.float64).reshape(len(x), -1)
    rows = check_rows(len(x))
    (dir_ / ONNX_FILE).write_bytes(to_onnx(model, scaler, loss, target, x.shape[1]))
    (dir_ / INPUTS_FILE).write_bytes(x[rows].astype("<f4").tobytes())
    (dir_ / OUTPUTS_FILE).write_bytes(output[rows].astype("<f8").tobytes())
    meta = {"rows": int(len(rows)), "of": int(len(x)), "width": int(x.shape[1]),
            "outputs": int(output.shape[1]), "loss": loss, "target": target,
            "tolerance": TOLERANCE, "torch": torch.__version__}
    (dir_ / CHECK_FILE).write_text(json.dumps(meta, sort_keys=True), encoding="utf-8")
    return meta
