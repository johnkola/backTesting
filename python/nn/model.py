"""PyTorch port of the DL4J MultiLayerNetwork built by
src/main/java/.../strategy/nn/NetworkBuilder.java:

  Input(input_size)
    → Dense(hidden_size, ReLU, Dropout=dropout_rate)  × num_hidden
    → Output(NUM_CLASSES, Softmax via CrossEntropyLoss)

DL4J's `.dropOut(keepProb)` takes the KEEP probability and the Java code
passes (1.0 - dropout_rate). torch.nn.Dropout(p) takes the DROP
probability, so we pass dropout_rate directly. Behaviour matches.

CrossEntropyLoss internally applies log_softmax + NLL, so the final
`forward()` returns raw logits — that's the convention every PyTorch
training loop expects. Use `predict_proba()` when you need the
softmaxed class distribution at inference time."""

from __future__ import annotations

from dataclasses import dataclass

import torch
import torch.nn as nn

from nn.labels import NUM_CLASSES


@dataclass
class ArchSpec:
    """Hyperparameters that define the network shape. Mirrors the subset
    of NeuralNetworkConfig that actually affects weight count."""

    input_size: int
    hidden_size: int = 64
    num_hidden: int = 2
    dropout_rate: float = 0.5
    seed: int = 42


def build(spec: ArchSpec) -> nn.Module:
    """Build a feedforward classifier matching the Java NetworkBuilder.
    Weights are initialised with Xavier-uniform (the DL4J `WeightInit.XAVIER`
    equivalent) and the seed is honoured so a given (data, spec) trains
    deterministically within a single PyTorch version."""

    torch.manual_seed(spec.seed)

    layers: list[nn.Module] = []
    prev = spec.input_size
    for _ in range(spec.num_hidden):
        linear = nn.Linear(prev, spec.hidden_size)
        nn.init.xavier_uniform_(linear.weight)
        nn.init.zeros_(linear.bias)
        layers.append(linear)
        layers.append(nn.ReLU())
        layers.append(nn.Dropout(p=spec.dropout_rate))
        prev = spec.hidden_size

    head = nn.Linear(prev, NUM_CLASSES)
    nn.init.xavier_uniform_(head.weight)
    nn.init.zeros_(head.bias)
    layers.append(head)

    return nn.Sequential(*layers)


def predict_proba(model: nn.Module, x: torch.Tensor) -> torch.Tensor:
    """Softmaxed class distribution for batch x. Use this at inference;
    the raw forward() output is logits (training uses CrossEntropyLoss
    which expects logits)."""
    model.eval()
    with torch.no_grad():
        logits = model(x)
        return torch.softmax(logits, dim=1)
