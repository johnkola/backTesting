"""The pipeline status, whole, against what the client reads (step 9).

`staleness.status` is the one answer every tab of the Pipeline page reads
(`GET /api/nn/pipeline/status`). Its parts have their tests
(`test_staleness.py`); nothing ran the whole of it and held it to the client:
one entry per tab the client lists (`web/client/src/lib/stages.ts`), each in a
state the client knows (`StageState` in `stageStatus.ts`), with its reasons,
and on another instrument (step 7) the instrument named. The client's own
types tie its tabs to `PipelineStatus`; this ties `PipelineStatus` to Python.

Read-only: the status writes nothing, and the transaction is rolled back.
"""

from __future__ import annotations

import re
from pathlib import Path

import psycopg
import pytest

from .dbfixtures import dsn, requires_db

CLIENT = Path(__file__).resolve().parents[2] / "web" / "client" / "src" / "lib"


def client_stages() -> list[str]:
    return re.findall(r"slug: '([a-z]+)'", (CLIENT / "stages.ts").read_text(encoding="utf-8"))


def client_states() -> set[str]:
    src = (CLIENT / "stageStatus.ts").read_text(encoding="utf-8")
    line = re.search(r"export type StageState = ([^\n]+)", src).group(1)
    return set(re.findall(r"'([a-z]+)'", line))


def test_the_client_files_are_read():
    if not CLIENT.is_dir():
        pytest.skip("the client is not beside this checkout")
    assert client_stages() == ["data", "features", "split", "training", "backtest"]
    assert {"passed", "failed", "stale", "empty"} <= client_states()


@pytest.fixture
def cur():
    if not CLIENT.is_dir():
        pytest.skip("the client is not beside this checkout")
    with psycopg.connect(dsn()) as conn, conn.cursor() as c:
        yield c
        conn.rollback()


@requires_db
@pytest.mark.parametrize("timeframe", ["D1", "H1", "M30"])
def test_every_tab_the_client_shows_has_a_stage_in_a_state_it_knows(cur, timeframe):
    from nn import staleness

    body = staleness.status(cur, timeframe)
    states = client_states()
    for slug in client_stages():
        stage = body[slug]
        assert stage["state"] in states, f"{timeframe} {slug}: {stage['state']!r}"
        assert isinstance(stage.get("reasons", []), list)
        for r in stage.get("reasons", []):
            assert r.get("kind") and isinstance(r.get("text"), str), f"{timeframe} {slug}: {r}"
    assert body["timeframe"] == timeframe
    # The training and backtest tabs read their lists from it.
    assert isinstance(body["training"]["runs"], list)
    assert isinstance(body["backtest"]["results"], list)


@requires_db
def test_on_another_instrument_the_status_names_it(cur):
    # Step 7: the picker's instrument comes back in the answer, which is how
    # the Features and Data tabs know whose sets and series to show.
    from nn import staleness

    body = staleness.status(cur, "D1", symbol="ZZSTATUS", source="zz-none")
    assert (body["symbol"], body["source"]) == ("ZZSTATUS", "zz-none")
    for slug in client_stages():
        assert body[slug]["state"] in client_states()
