"""A webhook's target URL and delivery history are for QA_LEAD and above.

For Slack, Teams and Discord style receivers the target URL is the credential:
anyone who knows it can post to the channel. Managing a subscription needs
QA_LEAD, so reading the full URL must need it too.

The rule these tests pin, on ``app/routers/webhooks_outbound.py``:

* list and detail: a caller whose instance role is below QA_LEAD gets
  ``target_url`` reduced to scheme and host followed by ``/...``. No path
  segment and no query value of the stored URL appears anywhere in the body.
  QA_LEAD and ADMIN get the stored URL.
* delivery history: QA_LEAD and above only (403 below), because a delivery row
  carries the event payload and the start of the receiver's response.
* ``limit`` on the delivery history is 1..200; anything else is a 422.
* the signing secret is returned to nobody.

The service layer and the project-access check are stubbed: these tests are
about what the route hands back to which role.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("sqlalchemy")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.core.deps import get_current_active_user, get_db  # noqa: E402
from app.models.postgres import UserRole  # noqa: E402
from app.routers import webhooks_outbound as router_module  # noqa: E402

pytestmark = pytest.mark.regression

_PROJECT_ID = uuid.uuid4()
_SUBSCRIPTION_ID = uuid.uuid4()
_PATH_TOKEN = "PATHTOKEN-not-a-real-one"
_QUERY_TOKEN = "QUERYTOKEN-not-a-real-one"
_FULL_URL = f"https://hooks.example.test/services/T000/B000/{_PATH_TOKEN}?token={_QUERY_TOKEN}"
_MASKED_URL = "https://hooks.example.test/..."
_NOW = datetime(2026, 11, 19, 12, 0, tzinfo=timezone.utc)

_BELOW_LEAD = [UserRole.VIEWER, UserRole.TESTER, UserRole.QA_ENGINEER]
_LEAD_AND_ABOVE = [UserRole.QA_LEAD, UserRole.ADMIN]


def _subscription_row() -> SimpleNamespace:
    return SimpleNamespace(
        id=_SUBSCRIPTION_ID,
        project_id=_PROJECT_ID,
        name="team channel",
        target_url=_FULL_URL,
        events=["run.completed"],
        enabled=True,
        has_secret=True,
        # Present on the row, never in a response.
        secret="signing-value-not-a-real-one",
        max_retries=3,
        last_delivered_at=None,
        last_failure_at=_NOW,
        # Delivery errors can quote the URL that was called.
        last_error=f"Client error '404 Not Found' for url '{_FULL_URL}'",
        failure_count=1,
        total_delivered=0,
        created_at=_NOW,
        updated_at=_NOW,
        consecutive_failures=1,
        breaker_threshold=10,
        disabled_reason=None,
        disabled_at=None,
    )


def _delivery_row() -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        subscription_id=_SUBSCRIPTION_ID,
        event_type="run.completed",
        event_payload={"run_id": "delivery-payload-marker"},
        status="SUCCESS",
        attempt_count=1,
        http_status=200,
        response_preview="receiver-response-marker",
        error=None,
        delivered_at=_NOW,
        created_at=_NOW,
    )


class _Client:
    """The router mounted alone, signed in as one role, with the service stubbed."""

    def __init__(self, role: UserRole):
        self.user = SimpleNamespace(id=uuid.uuid4(), role=role.value, is_active=True)
        self.list_deliveries = AsyncMock(return_value=[_delivery_row()])
        app = FastAPI()
        app.include_router(router_module.router)
        app.dependency_overrides[get_current_active_user] = lambda: self.user
        app.dependency_overrides[get_db] = lambda: AsyncMock()
        self._patches = [
            patch.object(router_module, "resolve_project_scope", AsyncMock(return_value=_PROJECT_ID)),
            patch.object(router_module, "get_accessible_project_ids", AsyncMock(return_value={_PROJECT_ID})),
            patch.object(router_module.svc, "get_subscription", AsyncMock(return_value=_subscription_row())),
            patch.object(router_module.svc, "list_subscriptions", AsyncMock(return_value=[_subscription_row()])),
            patch.object(router_module.svc, "list_deliveries", self.list_deliveries),
        ]
        self.http = TestClient(app)

    def __enter__(self) -> "_Client":
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *exc) -> None:
        for p in self._patches:
            p.stop()


# ── list and detail: masked below QA_LEAD ──────────────────────────────────


@pytest.mark.parametrize("role", _BELOW_LEAD)
@pytest.mark.parametrize("path", [
    f"/api/v1/webhooks?project_id={_PROJECT_ID}",
    "/api/v1/webhooks",
    f"/api/v1/webhooks/{_SUBSCRIPTION_ID}",
])
def test_below_lead_never_receives_the_url_path_or_query(role, path):
    with _Client(role) as client:
        response = client.http.get(path)
    assert response.status_code == 200
    assert _PATH_TOKEN not in response.text
    assert _QUERY_TOKEN not in response.text
    assert "/services/" not in response.text
    body = response.json()
    row = body[0] if isinstance(body, list) else body
    assert row["target_url"] == _MASKED_URL


@pytest.mark.parametrize("role", _LEAD_AND_ABOVE)
@pytest.mark.parametrize("path", [
    f"/api/v1/webhooks?project_id={_PROJECT_ID}",
    f"/api/v1/webhooks/{_SUBSCRIPTION_ID}",
])
def test_lead_and_admin_receive_the_stored_url(role, path):
    with _Client(role) as client:
        response = client.http.get(path)
    assert response.status_code == 200
    body = response.json()
    row = body[0] if isinstance(body, list) else body
    assert row["target_url"] == _FULL_URL
    assert _FULL_URL in row["last_error"]


@pytest.mark.parametrize("stored, shown", [
    ("https://hooks.example.test/services/a/b?token=c", "https://hooks.example.test/..."),
    ("http://receiver.example.test:8443/hook/abc", "http://receiver.example.test:8443/..."),
    ("https://receiver.example.test", "https://receiver.example.test/..."),
    # Nothing recognisable as scheme and host: show nothing of it.
    ("not a url", "..."),
    ("", "..."),
])
def test_mask_keeps_scheme_and_host_only(stored, shown):
    assert router_module._mask_target_url(stored) == shown


def test_mask_drops_credentials_written_into_the_url():
    # Built at run time so no credential-shaped URL sits in the source.
    stored = "https://" + "someone" + ":" + "not-a-real-one" + "@" + "receiver.example.test/hook"
    shown = router_module._mask_target_url(stored)
    assert shown == "https://receiver.example.test/..."


# ── delivery history: QA_LEAD and above ────────────────────────────────────


@pytest.mark.parametrize("role", _BELOW_LEAD)
def test_below_lead_is_refused_the_delivery_history(role):
    with _Client(role) as client:
        response = client.http.get(f"/api/v1/webhooks/{_SUBSCRIPTION_ID}/deliveries")
        assert response.status_code == 403
        assert "delivery-payload-marker" not in response.text
        assert "receiver-response-marker" not in response.text
        client.list_deliveries.assert_not_awaited()


@pytest.mark.parametrize("role", _LEAD_AND_ABOVE)
def test_lead_and_admin_read_full_delivery_rows(role):
    with _Client(role) as client:
        response = client.http.get(f"/api/v1/webhooks/{_SUBSCRIPTION_ID}/deliveries")
    assert response.status_code == 200
    (row,) = response.json()
    assert row["event_payload"] == {"run_id": "delivery-payload-marker"}
    assert row["response_preview"] == "receiver-response-marker"


@pytest.mark.parametrize("limit", [100000, 201, 0, -1])
def test_delivery_limit_outside_1_to_200_is_a_422(limit):
    with _Client(UserRole.QA_LEAD) as client:
        response = client.http.get(
            f"/api/v1/webhooks/{_SUBSCRIPTION_ID}/deliveries", params={"limit": limit},
        )
        assert response.status_code == 422
        client.list_deliveries.assert_not_awaited()


def test_delivery_limit_of_200_is_accepted():
    with _Client(UserRole.QA_LEAD) as client:
        response = client.http.get(
            f"/api/v1/webhooks/{_SUBSCRIPTION_ID}/deliveries", params={"limit": 200},
        )
        assert response.status_code == 200
        assert client.list_deliveries.await_args.kwargs["limit"] == 200


# ── the signing secret is returned to nobody ───────────────────────────────


@pytest.mark.parametrize("role", _BELOW_LEAD + _LEAD_AND_ABOVE)
def test_no_role_receives_the_signing_secret(role):
    with _Client(role) as client:
        response = client.http.get(f"/api/v1/webhooks/{_SUBSCRIPTION_ID}")
    assert response.status_code == 200
    body = response.json()
    assert "secret" not in body
    assert body["has_secret"] is True
    assert "signing-value-not-a-real-one" not in response.text
