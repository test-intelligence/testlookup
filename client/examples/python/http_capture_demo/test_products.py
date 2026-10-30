"""HTTP exchange capture demo (W23d AGT-14) -- self-contained: no server under test, no network.

A tiny "products API" runs inside ``httpx.MockTransport``. With ``DEMO_DROP_PRICE=1`` its product
answer silently loses the ``price`` field -- the kind of contract drift the deep investigation's
API-contract stage reports ("field `price` missing from GET /api/products/{id}; drift since build …").

Run it twice against a TestLookup project, the second time with the drift (the reporter must be
installed -- ``pip install ./client`` -- or put on the path with ``-p testlookup_reporter``):

    pytest client/examples/python/http_capture_demo --testlookup-capture-http \
        --testlookup-url http://<testlookup> --testlookup-api-key <key> --testlookup-project <project> \
        --testlookup-build demo-1

    DEMO_DROP_PRICE=1 pytest client/examples/python/http_capture_demo --testlookup-capture-http \
        --testlookup-url http://<testlookup> --testlookup-api-key <key> --testlookup-project <project> \
        --testlookup-build demo-2

Then open the failing ``test_product_has_a_price`` of the second run → ``HTTP exchanges`` →
``GET /api/products/{id}``: the ``Authorization`` header reads ``[REDACTED]`` (the SDK redacted it
before sending; the server redacts again before storing) and the response body has no ``price``.
Without ``--testlookup-capture-http`` (or ``TESTLOOKUP_CAPTURE_HTTP=1``) nothing is captured.
"""
from __future__ import annotations

import os

import httpx
import pytest

API = "https://products.demo.test"
#: A sample bearer value shaped like a JWT -- never a real credential.
SAMPLE_TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJkZW1vLXVzZXIifQ.ZGVtby1zaWduYXR1cmUtdmFsdWU"
CATALOGUE = {7: {"id": 7, "name": "Desk lamp", "price": 24.5, "currency": "EUR", "stock": 12}}


def _products_api(request: httpx.Request) -> httpx.Response:
    if request.headers.get("authorization") != f"Bearer {SAMPLE_TOKEN}":
        return httpx.Response(401, json={"detail": "missing token"})
    parts = request.url.path.strip("/").split("/")
    if request.method == "GET" and parts[:2] == ["api", "products"] and len(parts) == 3:
        product = CATALOGUE.get(int(parts[2]))
        if product is None:
            return httpx.Response(404, json={"detail": "not found"})
        body = dict(product)
        if os.environ.get("DEMO_DROP_PRICE") == "1":
            body.pop("price")
        return httpx.Response(200, json=body, headers={"Set-Cookie": "session=demo-session-value; HttpOnly"})
    if request.method == "POST" and parts == ["api", "orders"]:
        return httpx.Response(201, json={"order": 1001, "status": "created"})
    return httpx.Response(404, json={"detail": "no route"})


@pytest.fixture
def api() -> httpx.Client:
    with httpx.Client(base_url=API, transport=httpx.MockTransport(_products_api),
                      headers={"Authorization": f"Bearer {SAMPLE_TOKEN}"}) as client:
        yield client


def test_product_has_a_price(api):
    response = api.get("/api/products/7")
    assert response.status_code == 200
    assert "price" in response.json(), "the products API stopped sending price"


def test_order_can_be_placed(api):
    response = api.post("/api/orders", json={"product_id": 7, "qty": 1})
    assert response.status_code == 201
