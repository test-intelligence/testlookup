"""Bound request bodies for SCIM provisioning mutations."""

from __future__ import annotations

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send


SCIM_REQUEST_BODY_MAX_BYTES = 1024 * 1024
_SCIM_PROVISIONING_PREFIX = "/api/v1/scim/v2/"
_BODY_METHODS = {"POST", "PUT", "PATCH"}


class SCIMRequestBodyLimitMiddleware:
    """Reject oversized SCIM mutations before validation and service work."""

    def __init__(self, app: ASGIApp, max_bytes: int = SCIM_REQUEST_BODY_MAX_BYTES):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not self._applies(scope):
            await self.app(scope, receive, send)
            return

        content_length = self._content_length(scope)
        if content_length is not None and content_length > self.max_bytes:
            await self._reject(scope, receive, send)
            return

        buffered: list[Message] = []
        total = 0
        while True:
            message = await receive()
            buffered.append(message)
            if message["type"] == "http.request":
                total += len(message.get("body", b""))
                if total > self.max_bytes:
                    await self._reject(scope, receive, send)
                    return
                if not message.get("more_body", False):
                    break
            elif message["type"] == "http.disconnect":
                break

        index = 0

        async def replay() -> Message:
            nonlocal index
            if index < len(buffered):
                message = buffered[index]
                index += 1
                return message
            return await receive()

        await self.app(scope, replay, send)

    @staticmethod
    def _applies(scope: Scope) -> bool:
        return (
            scope["type"] == "http"
            and scope.get("method") in _BODY_METHODS
            and scope.get("path", "").startswith(_SCIM_PROVISIONING_PREFIX)
        )

    @staticmethod
    def _content_length(scope: Scope) -> int | None:
        for name, value in scope.get("headers", []):
            if name.lower() != b"content-length":
                continue
            try:
                parsed = int(value)
            except ValueError:
                return None
            return parsed if parsed >= 0 else None
        return None

    @staticmethod
    async def _reject(scope: Scope, receive: Receive, send: Send) -> None:
        response = JSONResponse(
            status_code=413,
            media_type="application/scim+json",
            content={
                "schemas": ["urn:ietf:params:scim:api:messages:2.0:Error"],
                "status": "413",
                "detail": "SCIM request body is too large",
            },
        )
        await response(scope, receive, send)
