"""Wire compatibility for additive live batch and event identity fields."""

import uuid

import pytest
from pydantic import ValidationError

from app.models.schemas import LiveEventBatch, LiveStreamIngestRequest


def test_legacy_live_batch_without_identity_remains_valid():
    batch = LiveEventBatch.model_validate(
        {
            "session_id": "session-1",
            "run_id": "run-1",
            "events": [{"event_type": "test_result"}],
        }
    )

    assert batch.batch_id is None


def test_live_batch_and_api_key_ingest_accept_uuid_batch_identity():
    batch_id = uuid.uuid4()
    event = {"event_type": "test_result"}

    session_batch = LiveEventBatch.model_validate(
        {
            "session_id": "session-1",
            "run_id": "run-1",
            "batch_id": str(batch_id),
            "events": [event],
        }
    )
    api_key_batch = LiveStreamIngestRequest.model_validate(
        {"run_id": "run-1", "batch_id": str(batch_id), "events": [event]}
    )

    assert session_batch.batch_id == str(batch_id)
    assert api_key_batch.batch_id == str(batch_id)


@pytest.mark.parametrize("schema", [LiveEventBatch, LiveStreamIngestRequest])
def test_live_ingest_rejects_run_ids_larger_than_database_contract(schema):
    payload = {"run_id": "r" * 101, "events": [{"event_type": "test_result"}]}
    if schema is LiveEventBatch:
        payload["session_id"] = "session-1"

    with pytest.raises(ValidationError):
        schema.model_validate(payload)


@pytest.mark.parametrize("batch_id", ["bad\x00id", "bad\nid", "bad\x7fid"])
@pytest.mark.parametrize("schema", [LiveEventBatch, LiveStreamIngestRequest])
def test_live_ingest_rejects_control_characters_in_batch_id(schema, batch_id):
    payload = {
        "run_id": "run-1",
        "batch_id": batch_id,
        "events": [{"event_type": "test_result"}],
    }
    if schema is LiveEventBatch:
        payload["session_id"] = "session-1"

    with pytest.raises(ValidationError):
        schema.model_validate(payload)
