from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "expected", "message"),
    [(302, True, "reachable"), (503, False, "HTTP 503")],
)
async def test_idp_probe_reports_actual_http_health(
    monkeypatch,
    status_code,
    expected,
    message,
):
    from app.core import http_client
    from app.core.config import settings
    from app.services import sso_service, url_safety

    monkeypatch.setattr(settings, "SSO_ALLOW_PRIVATE_IDP_ENDPOINTS", False)
    safety = AsyncMock()
    monkeypatch.setattr(url_safety, "assert_public_url", safety)
    client = MagicMock()
    client.get = AsyncMock(return_value=SimpleNamespace(status_code=status_code))
    monkeypatch.setattr(http_client, "get_http_client", lambda: client)

    healthy, detail = await sso_service.probe_idp_endpoint("https://idp.example/sso")

    assert healthy is expected
    assert message in detail
    safety.assert_awaited_once_with("https://idp.example/sso")
    client.get.assert_awaited_once_with(
        "https://idp.example/sso",
        timeout=5.0,
        follow_redirects=False,
    )


@pytest.mark.asyncio
async def test_idp_probe_reports_connection_failure(monkeypatch):
    from app.core import http_client
    from app.services import sso_service, url_safety

    monkeypatch.setattr(url_safety, "assert_public_url", AsyncMock())
    client = MagicMock()
    client.get = AsyncMock(side_effect=ConnectionError("DNS failed"))
    monkeypatch.setattr(http_client, "get_http_client", lambda: client)

    healthy, detail = await sso_service.probe_idp_endpoint("https://missing.example/sso")

    assert healthy is False
    assert "unreachable" in detail
    assert "DNS failed" in detail


@pytest.mark.asyncio
async def test_idp_probe_blocks_private_target_before_request(monkeypatch):
    from app.core import http_client
    from app.core.config import settings
    from app.services import sso_service, url_safety

    monkeypatch.setattr(settings, "SSO_ALLOW_PRIVATE_IDP_ENDPOINTS", False)
    monkeypatch.setattr(
        url_safety,
        "assert_public_url",
        AsyncMock(side_effect=ValueError("target resolves to a non-public address")),
    )
    client_factory = MagicMock()
    monkeypatch.setattr(http_client, "get_http_client", client_factory)

    healthy, detail = await sso_service.probe_idp_endpoint("http://127.0.0.1:6379")

    assert healthy is False
    assert "blocked by SSRF policy" in detail
    client_factory.assert_not_called()


@pytest.mark.asyncio
async def test_private_idp_probe_requires_explicit_operator_opt_in(monkeypatch):
    from app.core import http_client
    from app.core.config import settings
    from app.services import sso_service, url_safety

    monkeypatch.setattr(settings, "SSO_ALLOW_PRIVATE_IDP_ENDPOINTS", True)
    safety = AsyncMock(side_effect=AssertionError("guard should be skipped"))
    monkeypatch.setattr(url_safety, "assert_public_url", safety)
    client = MagicMock()
    client.get = AsyncMock(return_value=SimpleNamespace(status_code=200))
    monkeypatch.setattr(http_client, "get_http_client", lambda: client)

    healthy, _ = await sso_service.probe_idp_endpoint("https://idp.internal/sso")

    assert healthy is True
    safety.assert_not_awaited()


@pytest.mark.asyncio
async def test_sso_test_endpoint_fails_when_idp_probe_is_unreachable(monkeypatch):
    """The admin endpoint must consume the reachability result, not field presence."""
    from app.routers import sso

    config = SimpleNamespace(
        id=uuid.uuid4(),
        idp_certificate="-----BEGIN CERTIFICATE-----\nYQ==\n-----END CERTIFICATE-----",
        idp_sso_url="https://missing.example/sso",
        idp_entity_id="https://missing.example",
        last_test_at=None,
        last_test_success=None,
        last_test_error=None,
    )
    result = MagicMock()
    result.scalar_one_or_none.return_value = config
    db = MagicMock()
    db.execute = AsyncMock(return_value=result)
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    probe = AsyncMock(return_value=(False, "IdP endpoint unreachable: DNS failed"))
    monkeypatch.setattr(sso, "probe_idp_endpoint", probe)
    monkeypatch.setattr(sso, "validate_certificate_format", lambda _: (True, "Valid"))
    monkeypatch.setattr(sso, "log_identity_event", AsyncMock())

    response = await sso.test_sso_connection(
        config.id,
        request=SimpleNamespace(client=None),
        current_user=SimpleNamespace(id=uuid.uuid4(), username="admin"),
        db=db,
    )

    assert response.success is False
    assert config.last_test_success is False
    assert "unreachable" in config.last_test_error
    probe.assert_awaited_once_with("https://missing.example/sso")
    db.commit.assert_awaited_once()
