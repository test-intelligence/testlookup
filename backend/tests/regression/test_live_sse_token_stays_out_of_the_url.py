"""The live-session SSE route takes its access token from a header, never the URL.

``GET /api/v1/stream/sse/{project_id}`` used to read the account's access JWT
from ``?token=``. A request URL is kept by proxy access logs (the frontend
image's nginx logged the whole request line), by browser history and by
traces, and that token works on every REST route its owner may call. Security
review 2026-11-20, TL-09.

The rule:

* the token is read from ``Authorization: Bearer`` only;
* a request that carries ``?token=`` is refused with 400, also when it carries
  a valid header as well, so no client keeps a credential in its URL;
* no query parameter of the route is a credential.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.routers import stream as stream_router

pytestmark = pytest.mark.regression

TOKEN = "eyJhbGciOiJIUzI1NiJ9.e30.synthetic-token-for-this-test"


def _request(*, query: str = "", authorization: str | None = None) -> Request:
    headers = []
    if authorization is not None:
        headers.append((b"authorization", authorization.encode()))
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/stream/sse/p1",
            "query_string": query.encode(),
            "headers": headers,
        }
    )


def test_the_token_is_read_from_the_authorization_header():
    assert stream_router.sse_bearer_token(_request(authorization=f"Bearer {TOKEN}")) == TOKEN
    assert stream_router.sse_bearer_token(_request(authorization=f"bearer {TOKEN}")) == TOKEN


@pytest.mark.parametrize(
    "query, authorization",
    [
        (f"token={TOKEN}", None),
        (f"token={TOKEN}", f"Bearer {TOKEN}"),
        ("token=", f"Bearer {TOKEN}"),
    ],
    ids=["query-only", "query-and-header", "empty-query-value"],
)
def test_a_token_in_the_query_is_refused(query, authorization):
    with pytest.raises(HTTPException) as refused:
        stream_router.sse_bearer_token(_request(query=query, authorization=authorization))

    assert refused.value.status_code == 400
    assert "Authorization: Bearer" in refused.value.detail
    assert TOKEN not in refused.value.detail


@pytest.mark.parametrize(
    "authorization", [None, "", "Bearer", "Bearer   ", f"Basic {TOKEN}", TOKEN],
    ids=["absent", "empty", "no-credential", "blank-credential", "wrong-scheme", "no-scheme"],
)
def test_a_request_without_a_bearer_header_is_unauthenticated(authorization):
    with pytest.raises(HTTPException) as refused:
        stream_router.sse_bearer_token(_request(authorization=authorization))

    assert refused.value.status_code == 401
    assert refused.value.headers == {"WWW-Authenticate": "Bearer"}


def test_the_route_declares_no_credential_query_parameter():
    route = next(
        r for r in stream_router.router.routes if getattr(r, "path", "").endswith("/sse/{project_id}")
    )
    query_names = {param.name for param in route.dependant.query_params}
    for dependency in route.dependant.dependencies:
        query_names |= {param.name for param in dependency.query_params}

    assert query_names == set(), f"the SSE route reads these from the URL: {sorted(query_names)}"
    assert any(dep.call is stream_router.sse_bearer_token for dep in route.dependant.dependencies)
