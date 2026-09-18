"""Scheduled digest failures must preserve their delivery window and history."""
from datetime import datetime, timedelta, timezone
import inspect

import pytest

pytest.importorskip("sqlalchemy")

from app.worker.tasks import (  # noqa: E402
    DIGEST_RETRY_DELAY,
    digest_delivery_updates,
    dispatch_scheduled_digests,
)


def test_atomic_claim_does_not_advance_success_history():
    source = inspect.getsource(dispatch_scheduled_digests)
    claim = source.split("claim = await claim_db.execute", 1)[1].split(
        "if claimed is None", 1
    )[0]

    assert "next_delivery_at=scheduled_next" in claim
    assert "last_delivered_at=" not in claim
    assert "delivery_count=" not in claim


def test_failed_delivery_retries_without_advancing_success_history():
    now = datetime(2026, 11, 1, 7, 0, tzinfo=timezone.utc)
    scheduled_next = now + timedelta(days=1)

    updates = digest_delivery_updates(
        status="failed",
        now=now,
        scheduled_next=scheduled_next,
    )

    assert updates == {"next_delivery_at": now + DIGEST_RETRY_DELAY}
    assert "last_delivered_at" not in updates
    assert "increment_delivery_count" not in updates


def test_success_advances_watermark_and_count_once():
    now = datetime(2026, 11, 1, 7, 0, tzinfo=timezone.utc)
    scheduled_next = now + timedelta(days=1)

    updates = digest_delivery_updates(
        status="sent",
        now=now,
        scheduled_next=scheduled_next,
    )

    assert updates == {
        "last_delivered_at": now,
        "next_delivery_at": scheduled_next,
        "increment_delivery_count": True,
    }


def test_skipped_delivery_does_not_claim_success_history():
    now = datetime(2026, 11, 1, 7, 0, tzinfo=timezone.utc)
    scheduled_next = now + timedelta(days=1)

    assert digest_delivery_updates(
        status="skipped",
        now=now,
        scheduled_next=scheduled_next,
    ) == {"next_delivery_at": scheduled_next}
