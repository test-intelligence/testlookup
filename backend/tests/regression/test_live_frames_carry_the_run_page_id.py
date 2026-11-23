"""D-86 (2026-11-21): every Live row must know the run's canonical TestRun id.

The Live page links a row to ``/runs/<test_run_id || run_id>``. Two socket
frames built rows without ``test_run_id``: the ``initial_state`` /
``reconcile_required`` bootstrap (raw Redis payloads) and ``live_run_started``.
For a ``LiveStream(run_id="ci-build-42")`` run the link then pointed at
``/runs/ci-build-42``, which the run page refuses ("Invalid run ID").
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

pytest.importorskip("sqlalchemy")

from app.services.stream_service import canonical_test_run_uuid  # noqa: E402

pytestmark = pytest.mark.regression


def test_the_socket_bootstrap_sends_the_canonical_run_id():
    from app.routers.live import bootstrap_session_states

    active = [
        {"run_id": "ci-build-42", "project_id": "p-1", "build_number": "ci-build-42", "status": "running",
         "total": "30", "passed": "10", "failed": "0", "skipped": "0", "broken": "0"},
        {"run_id": "other", "project_id": "p-2", "build_number": "x", "status": "running"},
    ]
    rows = bootstrap_session_states(active, "p-1")

    assert len(rows) == 1
    assert rows[0]["run_id"] == "ci-build-42"
    assert rows[0]["test_run_id"] == str(canonical_test_run_uuid("ci-build-42"))
    assert rows[0]["total"] == 30
    # Unknown fields are left out, so a client merging this row over the
    # poll's row keeps what only the poll knows (the run number).
    assert "run_seq" not in rows[0]


@pytest.mark.asyncio
async def test_the_run_started_frame_carries_the_canonical_run_id(monkeypatch):
    from app.streams import live_consumer

    sent: list[dict] = []

    async def _broadcast(project_id, message):
        sent.append(message)

    monkeypatch.setattr(live_consumer, "_broadcast", _broadcast)
    monkeypatch.setattr(live_consumer.RedisLiveRunState, "start", AsyncMock(return_value=None))

    await live_consumer.LiveEventStreamConsumer()._on_run_start(
        "ci-build-42", {"project_id": "p-1", "build_number": "ci-build-42"}
    )

    assert sent[0]["type"] == "live_run_started"
    assert sent[0]["test_run_id"] == str(canonical_test_run_uuid("ci-build-42"))
