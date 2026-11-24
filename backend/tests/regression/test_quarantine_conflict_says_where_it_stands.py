"""S-E-4: a refused quarantine decision (409) says where the request stands in
words. The Flaky tests page shows the server's sentence as it is; it read
"Cannot approve from status QUARANTINED" (the stored enum value)."""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.services import flaky_quarantine_service as svc


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("call", "status", "expected"),
    [
        (svc.approve, "QUARANTINED", "This request is quarantined now, so it cannot be approved."),
        (svc.reject, "REJECTED", "This request is rejected now, so it cannot be rejected."),
        (svc.release, "PROPOSED", "This request is proposed now, so it cannot be released."),
        (svc.release, "EXPIRED", "This request is expired now, so it cannot be released."),
    ],
)
async def test_a_refused_decision_names_the_state_in_words(call, status, expected):
    row = SimpleNamespace(id=uuid.uuid4(), status=status)
    with patch.object(svc, "get_request", AsyncMock(return_value=row)):
        with pytest.raises(HTTPException) as caught:
            await call(SimpleNamespace(), row.id, SimpleNamespace(id=uuid.uuid4()))
    assert caught.value.status_code == 409
    detail = caught.value.detail
    assert detail.startswith(expected)
    assert detail.endswith("Refresh the list to see where it stands.")
    assert status not in detail
    assert "from status" not in detail
