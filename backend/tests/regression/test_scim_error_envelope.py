from __future__ import annotations

import json
from pathlib import Path

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


def test_application_registers_both_scim_aware_handlers():
    main_source = (Path(__file__).parents[2] / "app" / "main.py").read_text(encoding="utf-8")

    assert "app.add_exception_handler(StarletteHTTPException, scim_http_exception_handler)" in main_source
    assert (
        "app.add_exception_handler(RequestValidationError, scim_validation_exception_handler)"
        in main_source
    )
