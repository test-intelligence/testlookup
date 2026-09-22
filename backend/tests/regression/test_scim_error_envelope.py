from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException


def _request(path: str) -> Request:
    return Request({"type": "http", "method": "GET", "path": path, "headers": []})


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [400, 401, 404, 409])
async def test_scim_http_failures_use_standard_error_envelope(status_code):
    from app.core.scim_errors import scim_http_exception_handler

    response = await scim_http_exception_handler(
        _request("/api/v1/scim/v2/Users"),
        HTTPException(status_code=status_code, detail="Protocol failure"),
    )
    body = json.loads(response.body)

    assert response.status_code == status_code
    assert response.media_type == "application/scim+json"
    assert response.headers["content-type"] == "application/scim+json"
    assert body == {
        "schemas": ["urn:ietf:params:scim:api:messages:2.0:Error"],
        "status": str(status_code),
        "detail": "Protocol failure",
    }


@pytest.mark.asyncio
async def test_invalid_filter_error_preserves_scim_type():
    from app.core.scim_errors import scim_http_exception_handler

    response = await scim_http_exception_handler(
        _request("/api/v1/scim/v2/Users"),
        HTTPException(
            status_code=400,
            detail={
                "detail": "The supplied SCIM filter is invalid or unsupported",
                "scimType": "invalidFilter",
            },
        ),
    )
    body = json.loads(response.body)

    assert response.status_code == 400
    assert response.media_type == "application/scim+json"
    assert body == {
        "schemas": ["urn:ietf:params:scim:api:messages:2.0:Error"],
        "status": "400",
        "detail": "The supplied SCIM filter is invalid or unsupported",
        "scimType": "invalidFilter",
    }


@pytest.mark.asyncio
async def test_list_route_maps_invalid_filter_without_loading_identities(monkeypatch):
    from app.services.scim_service import SCIMInvalidFilterError
    from app.routers import scim

    list_users = AsyncMock(side_effect=SCIMInvalidFilterError("invalid filter"))
    identity_map = AsyncMock()
    monkeypatch.setattr(scim, "scim_list_users", list_users)
    monkeypatch.setattr(scim, "scim_identity_map", identity_map)

    with pytest.raises(HTTPException) as raised:
        await scim.scim_list(
            _request("/api/v1/scim/v2/Users"),
            startIndex=1,
            count=100,
            filter='userName eq "alice" and active eq true',
            scim_token=SimpleNamespace(sso_config_id=None),
            db=AsyncMock(),
        )

    assert raised.value.status_code == 400
    assert raised.value.detail["scimType"] == "invalidFilter"
    identity_map.assert_not_awaited()


@pytest.mark.asyncio
async def test_scim_validation_failure_is_400_not_fastapi_422():
    from app.core.scim_errors import scim_validation_exception_handler

    exc = RequestValidationError(
        [{"type": "missing", "loc": ("body", "userName"), "msg": "Field required", "input": {}}]
    )
    response = await scim_validation_exception_handler(
        _request("/api/v1/scim/v2/Users"),
        exc,
    )
    body = json.loads(response.body)

    assert response.status_code == 400
    assert response.media_type == "application/scim+json"
    assert body["status"] == "400"
    assert body["schemas"] == ["urn:ietf:params:scim:api:messages:2.0:Error"]
    assert "body.userName" in body["detail"]


@pytest.mark.asyncio
async def test_admin_token_api_keeps_default_fastapi_error_envelope():
    from app.core.scim_errors import scim_http_exception_handler

    response = await scim_http_exception_handler(
        _request("/api/v1/scim-tokens/missing"),
        HTTPException(status_code=404, detail="SCIM token not found"),
    )

    assert response.status_code == 404
    assert response.media_type == "application/json"
    assert json.loads(response.body) == {"detail": "SCIM token not found"}


@pytest.mark.asyncio
async def test_near_prefix_does_not_receive_scim_envelope():
    from app.core.scim_errors import scim_http_exception_handler

    response = await scim_http_exception_handler(
        _request("/api/v1/scim/v20/Users"),
        HTTPException(status_code=404, detail="Not found"),
    )

    assert response.media_type == "application/json"
    assert json.loads(response.body) == {"detail": "Not found"}


@pytest.mark.asyncio
async def test_application_registers_both_scim_aware_handlers():
    # Behavioural, not textual: whatever handlers the REAL app registers for
    # these two exception types must still answer a SCIM path with the SCIM
    # envelope. The app's handlers are composites since VIZ-210 (analytics
    # routes get their own body, everything else falls through to SCIM, then
    # FastAPI's default), so a source-text match would say nothing about that.
    from starlette.exceptions import HTTPException as StarletteHTTPException

    from app.main import app

    http_handler = app.exception_handlers[StarletteHTTPException]
    response = await http_handler(
        _request("/api/v1/scim/v2/Users"),
        StarletteHTTPException(status_code=404, detail="Protocol failure"),
    )
    assert response.status_code == 404
    assert response.media_type == "application/scim+json"
    assert json.loads(response.body)["schemas"] == [
        "urn:ietf:params:scim:api:messages:2.0:Error"
    ]

    validation_handler = app.exception_handlers[RequestValidationError]
    response = await validation_handler(
        _request("/api/v1/scim/v2/Users"),
        RequestValidationError(
            [{"type": "missing", "loc": ("body", "userName"), "msg": "Field required", "input": None}]
        ),
    )
    assert response.status_code == 400
    assert response.media_type == "application/scim+json"
