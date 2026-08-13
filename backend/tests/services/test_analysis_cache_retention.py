from datetime import UTC, datetime

import pytest

from app.services.analysis_cache_retention import purge_project_analysis_caches


class _Redis:
    def __init__(self):
        self.deleted = []

    async def scan_iter(self, *, match):
        assert match == "*:proj:project-a"
        for key in (b"analysis:one:proj:project-a", b"analysis:two:proj:project-a"):
            yield key

    async def delete(self, *keys):
        self.deleted.extend(keys)


class _Collection:
    def __init__(self):
        self.deleted = []

    def get(self, *, include):
        assert include == ["metadatas"]
        return {
            "ids": ["old", "future", "naive", "malformed"],
            "metadatas": [
                {"cached_at": "2026-01-01T00:00:00+00:00"},
                {"cached_at": "2027-01-01T00:00:00+00:00"},
                {"cached_at": "2026-01-02T00:00:00"},
                {"cached_at": "not-a-timestamp"},
            ],
        }

    def delete(self, *, ids):
        self.deleted.extend(ids)


@pytest.mark.asyncio
@pytest.mark.parametrize("execute", [False, True])
async def test_project_cache_retention_counts_and_deletes_expired_entries(
    monkeypatch, execute
):
    redis = _Redis()
    collection = _Collection()
    monkeypatch.setattr("app.db.redis_client.get_redis", lambda: redis)

    async def _collection(project_id):
        assert project_id == "project-a"
        return collection

    monkeypatch.setattr(
        "app.services.semantic_cache._get_or_create_collection", _collection
    )

    result = await purge_project_analysis_caches(
        "project-a", cutoff=datetime(2026, 6, 1, tzinfo=UTC), execute=execute
    )

    assert result == {"redis": 2, "semantic": 3}
    assert redis.deleted == (
        [b"analysis:one:proj:project-a", b"analysis:two:proj:project-a"]
        if execute else []
    )
    assert collection.deleted == (["old", "naive", "malformed"] if execute else [])
