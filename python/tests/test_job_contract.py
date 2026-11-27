"""What the Java side asks the Python worker for, held to what the worker has.

The service (`server/`) and the engine (`src/`) reach Python only through the
jobs table: a job type, and for a route a method and a path that the worker
replays against the FastAPI app (`jobs.calls.request`). Neither side is
compiled against the other, so a renamed job type or route was found only
when a page or a run hit it: a job no handler takes, or a 404 from inside the
worker. These read the Java sources and check every name against the worker.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SERVER = ROOT / "server" / "src" / "main" / "java"
ENGINE = ROOT / "src" / "main" / "java"


def _java(root: Path) -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in sorted(root.rglob("*.java")))


@pytest.fixture(scope="module")
def worker():
    """The worker's handlers and the app its route jobs replay against."""
    pytest.importorskip("fastapi")
    pytest.importorskip("torch")  # nn_api imports nn.train, which imports torch
    if not SERVER.is_dir() or not ENGINE.is_dir():
        pytest.skip("the Java sources are not beside this checkout")
    import loader.main  # registers every job_route and task as it loads
    from jobs import calls
    from jobs.handlers import HANDLERS

    return {**calls.REGISTRY, **HANDLERS}, loader.main.app


def _shape(path: str) -> str:
    """A path with its parameters' names dropped: `/api/x/{id}` and `/api/x/{job_id}` match."""
    return re.sub(r"\{[^}]+\}", "{}", path.split("?", 1)[0])


def _match(app, method: str, path: str):
    """How the app's routes match a request for `method` `path`, as the
    worker's replay resolves it: FULL, PARTIAL (the path, another method) or
    NONE. A parameter is filled with 1, which every route here accepts."""
    from starlette.routing import Match

    scope = {"type": "http", "method": method, "path": path.replace("{}", "1"),
             "root_path": "", "path_params": {}, "query_string": b"", "headers": []}
    best = Match.NONE
    for r in app.routes:
        m, _ = r.matches(scope)
        if m == Match.FULL:
            return Match.FULL
        best = max(best, m, key=lambda x: x.value)
    return best


def server_job_types() -> set[str]:
    src = _java(SERVER)
    types = set(re.findall(r'python\.(?:forward|ask|run)\(\s*"([a-z0-9-]+)"', src))
    types |= set(re.findall(r'jobs\.insert\(\s*"([a-z0-9-]+)",\s*"python"', src))
    if "PythonGateway.CALL" in src:
        types.add("python-call")
    return types


def server_routes() -> set[tuple[str, str]]:
    """Every (method, path) the service hands Python: the pass-through routes,
    and the routes it asks on its own behalf (`PythonGateway.ask`)."""
    routes = set()
    passthrough = (SERVER / "com/bazarbozorg/backtest/server/python/PythonRoutes.java").read_text(
        encoding="utf-8")
    for m, path in re.findall(r'@(Get|Post|Put|Delete)Mapping\("([^"]+)"\)', passthrough):
        routes.add((m.upper(), _shape(path)))
    for m, path in re.findall(r'python\.ask\([^,]+,\s*"([A-Z]+)",\s*"([^"]+)"', _java(SERVER)):
        routes.add((m, _shape(path)))
    return routes


def engine_paths() -> set[str]:
    """Every path the engine asks the loader for (`LoaderClient`, `PythonCalls`)."""
    paths = set()
    for p in ENGINE.rglob("*.java"):
        src = p.read_text(encoding="utf-8")
        if "LoaderClient" not in src and "PythonCalls" not in src:
            continue
        paths |= {_shape(x) for x in re.findall(r'"(/api/[A-Za-z0-9_/{}-]+)', src)}
    return paths


def test_the_sources_are_read():
    # Guards the regular expressions: a rename that hid every name would pass
    # the checks below with nothing to check.
    assert {"python-call", "feature-build", "train", "walkforward-split"} <= server_job_types()
    assert ("GET", "/api/nn/pipeline/status") in server_routes()
    assert ("GET", "/api/nn/features/config") in server_routes()
    assert {"/api/nn/rules", "/api/nn/predict_range", "/api/nn/train"} <= engine_paths()


def test_every_job_type_the_service_queues_has_a_handler(worker):
    handlers, _ = worker
    missing = sorted(server_job_types() - set(handlers))
    assert not missing, f"the service queues job types no Python handler takes: {missing}"


def test_every_route_the_service_forwards_is_one_the_worker_has(worker):
    from starlette.routing import Match

    _, app = worker
    missing = sorted(r for r in server_routes() if _match(app, *r) != Match.FULL)
    assert not missing, f"the service forwards routes the Python app does not have: {missing}"


def test_every_path_the_engine_asks_for_is_one_the_worker_has(worker):
    # The engine's method is chosen at the call (`PythonCalls.call`), so the
    # path is held to existing under some method.
    from starlette.routing import Match

    _, app = worker
    missing = sorted(p for p in engine_paths()
                     if all(_match(app, m, p) == Match.NONE for m in ("GET", "POST", "DELETE")))
    assert not missing, f"the engine asks for paths the Python app does not have: {missing}"


def test_a_route_the_worker_lacks_is_caught(worker):
    # The check itself: a made-up route and a wrong method do not pass.
    from starlette.routing import Match

    _, app = worker
    assert _match(app, "GET", "/api/nn/no-such-route") == Match.NONE
    assert _match(app, "DELETE", "/api/nn/pipeline/status") == Match.PARTIAL
