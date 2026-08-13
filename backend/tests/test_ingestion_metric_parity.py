from types import SimpleNamespace

import pytest

from app.agents.ingestion_agent import IngestionAgent
from app.services.run_evidence_bundle import build_run_metric_snapshot


class _Result:
    def __init__(self, *, scalar=None, rows=None):
        self.scalar = scalar
        self.rows = rows or []

    def scalar_one_or_none(self):
        return self.scalar

    def all(self):
        return self.rows


class _Session:
    def __init__(self, run):
        self.results = [_Result(scalar=run), _Result(rows=[SimpleNamespace(id="failed-1")])]

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def execute(self, _statement):
        return self.results.pop(0)


@pytest.mark.asyncio
async def test_ingestion_preserves_unknown_status_count_for_metric_snapshot(monkeypatch):
    run = SimpleNamespace(
        id="run-1",
        build_number="42",
        branch="main",
        jenkins_job="job",
        total_tests=3,
        passed_tests=1,
        failed_tests=1,
        skipped_tests=0,
        broken_tests=0,
        unknown_tests=1,
        pass_rate=50.0,
        duration_ms=100,
        status=SimpleNamespace(value="COMPLETED"),
    )
    monkeypatch.setattr(
        "app.agents.ingestion_agent.AsyncSessionLocal", lambda: _Session(run)
    )

    run_data, failed_ids = await IngestionAgent()._extract_run_data("run-1")
    snapshot = build_run_metric_snapshot({
        "test_run_id": "run-1",
        "test_run_data": run_data,
        "failed_test_ids": failed_ids,
    })

    assert snapshot["values"]["unknown_tests"] == 1
    assert "outcome_total_mismatch" not in {
        item["code"] for item in snapshot["quality_flags"]
    }
