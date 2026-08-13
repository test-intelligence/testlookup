from __future__ import annotations

import uuid

import pytest

from app.services.retention_service import _published_report_artifact_ids


class _Cursor:
    def __init__(self, rows):
        self.rows = rows

    async def to_list(self, length):
        assert length == 10_000
        return self.rows


class _Collection:
    def __init__(self, rows=None, error=None):
        self.rows = rows or []
        self.error = error

    def find(self, *_args):
        if self.error:
            raise self.error
        return _Cursor(self.rows)


class _Mongo:
    def __init__(self, collection):
        self.collection = collection

    def __getitem__(self, _name):
        return self.collection


@pytest.mark.asyncio
async def test_published_report_artifact_references_are_protected():
    artifact_id = uuid.uuid4()
    other_id = uuid.uuid4()
    mongo = _Mongo(_Collection([
        {
            "decision_intelligence": {
                "claims": [{"evidence": [{"artifact_id": str(artifact_id)}]}],
                "evidence_ids": [str(other_id)],
            }
        }
    ]))

    result = await _published_report_artifact_ids(
        mongo,
        uuid.uuid4(),
        [artifact_id, other_id],
    )

    assert result == {artifact_id, other_id}


@pytest.mark.asyncio
async def test_report_reference_scan_failure_protects_all_candidates():
    artifact_ids = [uuid.uuid4(), uuid.uuid4()]
    mongo = _Mongo(_Collection(error=RuntimeError("mongo unavailable")))

    result = await _published_report_artifact_ids(
        mongo,
        uuid.uuid4(),
        artifact_ids,
    )

    assert result == set(artifact_ids)
