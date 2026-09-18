"""Scheduled digest failures must preserve their delivery window and history."""
from datetime import datetime, timedelta, timezone
import inspect

import pytest

pytest.importorskip("sqlalchemy")

from app.worker.tasks import (  # noqa: E402
    DIGEST_RETRY_DELAY,
    digest_email_config_error,
    digest_delivery_updates,
    digest_window_start,
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
        "increment_delivery_count": True,
    }


def test_skipped_delivery_does_not_claim_success_history():
    now = datetime(2026, 11, 1, 7, 0, tzinfo=timezone.utc)
    scheduled_next = now + timedelta(days=1)

    assert digest_delivery_updates(
        status="skipped",
        now=now,
        scheduled_next=scheduled_next,
    ) == {}


def test_first_delivery_retry_keeps_original_creation_window():
    created_at = datetime(2026, 11, 1, 6, 0, tzinfo=timezone.utc)
    first_attempt = datetime(2026, 11, 2, 6, 0, tzinfo=timezone.utc)
    retry_attempt = first_attempt + DIGEST_RETRY_DELAY

    assert digest_window_start(
        last_delivered_at=None,
        created_at=created_at,
        now=retry_attempt,
        delta=timedelta(days=1),
    ) == created_at


def test_disabled_smtp_is_a_failed_delivery_not_a_successful_skip():
    assert digest_email_config_error({"enabled": False}) == "SMTP is disabled"
    assert digest_email_config_error({"enabled": True}) is None


def test_failure_retry_update_is_bound_to_the_claimed_schedule_slot():
    source = inspect.getsource(dispatch_scheduled_digests)
    finalization = source.split("outcome = digest_delivery_updates", 1)[1]

    assert 'if status == "failed":' in finalization
    assert "DigestSubscription.next_delivery_at == scheduled_next" in finalization
