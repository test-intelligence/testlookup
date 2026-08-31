"""Regression coverage for outbound webhook Celery retry behavior."""
import uuid
from unittest.mock import AsyncMock, Mock

import pytest
from celery.exceptions import Retry

from app.services import webhook_service
from app.worker import tasks as worker_tasks


def test_unhandled_webhook_service_failure_requests_celery_retry(monkeypatch):
    monkeypatch.setattr(
        webhook_service,
        "deliver",
        AsyncMock(side_effect=RuntimeError("database temporarily unavailable")),
    )
    retry = Mock(side_effect=Retry())
    monkeypatch.setattr(worker_tasks.deliver_webhook, "retry", retry)
    worker_tasks.deliver_webhook.request.retries = 0

    with pytest.raises(Retry):
        worker_tasks.deliver_webhook.run(str(uuid.uuid4()))

    retry.assert_called_once_with(countdown=30, max_retries=5)
