import pytest

from app.services.suite_history_service import compute_suite_history


class _EmptyResult:
    def fetchall(self):
        return []


class _RecordingDb:
    def __init__(self):
        self.query_text = ""
        self.params = None

    async def execute(self, query, params):
        self.query_text = str(query)
        self.params = params
        return _EmptyResult()


@pytest.mark.asyncio
async def test_compute_suite_history_uses_valid_where_when_unscoped():
    db = _RecordingDb()

    result = await compute_suite_history(db, project_id=None, suite_names=None, days=None)

    assert result == {}
    assert "FROM test_runs tr\n            WHERE TRUE\n            AND NULLIF" in db.query_text
    assert "JOIN test_cases tc ON tc.test_run_id = tr.id\n            WHERE TRUE\n            AND NULLIF" in db.query_text


@pytest.mark.asyncio
async def test_compute_suite_history_uses_valid_where_with_only_suite_filter():
    db = _RecordingDb()

    result = await compute_suite_history(db, project_id=None, suite_names=["Smoke"], days=None)

    assert result["Smoke"]["run_count"] == 0
    assert "FROM test_runs tr\n            WHERE TRUE\n            AND NULLIF" in db.query_text
    assert db.params == {"suite_name_0": "Smoke"}
