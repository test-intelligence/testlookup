"""Standards-compliant error envelopes for the public SCIM protocol surface."""

from fastapi import Request
from fastapi.exception_handlers import (
    http_exception_handler,
    request_validation_exception_handler,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.models.schemas import SCIMErrorResponse

SCIM_PREFIX = "/api/v1/scim/v2"
SCIM_MEDIA_TYPE = "application/scim+json"


class SCIMJSONResponse(JSONResponse):
    """JSON response using the media type required by SCIM 2.0."""

    media_type = SCIM_MEDIA_TYPE


def _is_scim_request(request: Request) -> bool:
    path = request.url.path
    return path == SCIM_PREFIX or path.startswith(f"{SCIM_PREFIX}/")


def _scim_error(status_code: int, detail: str, headers: dict | None = None) -> JSONResponse:
    payload = SCIMErrorResponse(status=str(status_code), detail=detail)
    return JSONResponse(
        status_code=status_code,
        content=payload.model_dump(),
        headers=headers,
        media_type=SCIM_MEDIA_TYPE,
    )


async def scim_http_exception_handler(
    request: Request,
    exc: StarletteHTTPException,
):
    """Use SCIM Error for protocol routes and FastAPI defaults elsewhere."""
    if not _is_scim_request(request):
        return await http_exception_handler(request, exc)
    detail = exc.detail if isinstance(exc.detail, str) else "SCIM request failed"
    return _scim_error(exc.status_code, detail, exc.headers)


async def scim_validation_exception_handler(
    request: Request,
    exc: RequestValidationError,
):
    """Map FastAPI's 422 validation envelope to SCIM's HTTP 400 Error."""
    if not _is_scim_request(request):
        return await request_validation_exception_handler(request, exc)
    messages = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error.get("loc", ()))
        message = str(error.get("msg", "Invalid value"))
        messages.append(f"{location}: {message}" if location else message)
    detail = "; ".join(messages) or "Invalid SCIM request"
    return _scim_error(400, detail)
