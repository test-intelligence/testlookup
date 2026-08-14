"""Regression coverage for Phase 3 frozen execution-context observability."""

from unittest.mock import Mock

import pytest

from app.agents import workflow


@pytest.mark.asyncio
async def test_execution_context_persist_failure_increments_metric(monkeypatch):
    metric = Mock()
    monkeypatch.setattr(workflow, "pipeline_execution_context_persist_failures_total", metric)

    class _BrokenSession:
        async def __aenter__(self):
            raise RuntimeError("database unavailable")

        async def __aexit__(self, *_args):
            return False

    monkeypatch.setattr(workflow, "AsyncSessionLocal", _BrokenSession)

    await workflow._persist_execution_context("pipeline-1", {"resolved": "offline"})

    metric.inc.assert_called_once_with()
