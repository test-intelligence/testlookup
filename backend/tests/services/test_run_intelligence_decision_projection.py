import pytest

from app.services.run_intelligence_service import _structured_summary_projection


class _Cursor:
    def __init__(self, rows):
        self.rows = rows

    def sort(self, *_args):
        return self

    def limit(self, amount):
        self.rows = self.rows[:amount]
        return self

    async def to_list(self, length=0):
        return self.rows[:length or None]


class _Collection:
    def find(self, query, _projection):
        rows = [
            {"report_id": "r2", "report_version": 2, "status": "published", "test_run_id": query["test_run_id"]},
            {"report_id": "r1", "report_version": 1, "status": "published", "test_run_id": query["test_run_id"]},
        ]
        return _Cursor(rows)


class _Mongo:
    def __getitem__(self, _name):
        return _Collection()


def test_stale_verified_report_and_rejected_latest_attempt_remain_distinct():
    verified_report = {
        "evidence_bundle_sha256": "a" * 64,
        "verification": {"status": "passed"},
    }
    rejected_verification = {
        "status": "failed",
        "unresolved_failures": ["release_policy_replay"],
    }
    projected = _structured_summary_projection({
        "decision_intelligence": verified_report,
        "decision_report_verification": rejected_verification,
        "latest_decision_attempt": {
            "pipeline_run_id": "new-pipeline",
            "status": "rejected",
            "verification_status": "failed",
        },
        "schema_version": 5,
    })

    assert projected["decision_intelligence"] is verified_report
    assert projected["decision_report_verification"] is rejected_verification
    assert projected["latest_decision_attempt"]["status"] == "rejected"


@pytest.mark.asyncio
async def test_decision_report_version_endpoint_returns_bounded_metadata(monkeypatch):
    from app.routers import run_intelligence

    monkeypatch.setattr(run_intelligence, "get_mongo_db", lambda: _Mongo())
    result = await run_intelligence.list_run_decision_reports("run-1", limit=1)

    assert result == [{
        "report_id": "r2",
        "report_version": 2,
        "supersedes_report_id": None,
        "generated_at": None,
        "status": "published",
    }]
