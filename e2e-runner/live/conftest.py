"""Authenticated fixtures restricted to the disposable release-test deployment."""

from contextlib import ExitStack
import os
import uuid

import httpx
import pytest


@pytest.fixture
def role_client():
    if os.environ.get("TL_TARGET") != "ephemeral":
        pytest.fail("BLOCKED: disposable target required", pytrace=False)
    with ExitStack() as stack:

        def open_role(role):
            client = stack.enter_context(
                httpx.Client(
                    base_url=os.environ["TL_API"] + "/api/v1/",
                    timeout=30,
                    # Native framework runs leave the control client idle while
                    # the deployed server and port-forward expire connections.
                    limits=httpx.Limits(max_keepalive_connections=0),
                )
            )
            token = os.environ.get("TL_TOKEN_" + role)
            if not token:
                auth = client.post("auth/dev-login", params={"role": role.lower()})
                assert auth.status_code == 200
                token = auth.json()["access_token"]
            client.headers["Authorization"] = "Bearer " + token
            me = client.get("auth/me")
            assert me.status_code == 200
            identity = me.json()
            assert (identity["username"], identity["role"]) == (
                os.environ["TL_USER_" + role],
                role,
            )
            return client, identity

        yield open_role


@pytest.fixture
def project(role_client):
    client, _ = role_client("QA_LEAD")
    name = "e2e-" + uuid.uuid4().hex
    response = client.post("projects", json={"name": name, "slug": name})
    assert response.status_code == 201, f"Create project {response.status_code}"
    owned = response.json()["id"]
    try:
        yield client, owned, name
    finally:
        response = client.delete("projects/" + owned)
        assert response.status_code == 204, f"Cleanup {response.status_code}"
