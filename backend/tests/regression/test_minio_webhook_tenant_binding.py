"""The MinIO webhook must not let its caller choose the tenant.

Re-audit finding N10, and the same shape as H1 on ``/ws/events``: one
deployment-wide ``WEBHOOK_SECRET`` guards the endpoint, that credential names no
project, and the handler read ``project_id`` straight out of the request body::

    user_meta = record["s3"]["object"].get("userMetadata", {})
    sentinel_data.update(user_meta)          # project_id arrives here
    ...
    ingest_test_run.delay(sentinel_dict=sentinel.model_dump(), minio_prefix=...)

Downstream, ``_upsert_test_run`` resolves that string as a UUID **or a slug**
against any project, so a holder of the shared secret could file a fabricated
run into any tenant. And ``process_sentinel`` lists and reads result objects
from ``minio_prefix`` — derived from the caller's object key — so the same
request is a cross-tenant **read**.

The handler was notified *about* an object and never fetched it. It now does,
and takes the project from where the data physically lives rather than from
what the notification claims about it. Writing into a project therefore
requires write access to that project's prefix in the bucket: a storage
credential, not a shared secret.

This survived so long partly because ``/webhooks/`` mounts outside ``/api/v1``,
where neither authorization ratchet looks — the same blind spot that hid H1.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
import pytest

from app.routers.webhooks import minio_webhook

VICTIM = "victim-project"
ATTACKER = "attacker-project"


def _request(body: dict):
    """A Request stand-in exposing only what the handler reads."""

    async def _json():
        return body

    return SimpleNamespace(json=_json)


def _notification(key: str, user_metadata: dict | None = None) -> dict:
    record = {"s3": {"object": {"key": key}}}
    if user_metadata is not None:
        record["s3"]["object"]["userMetadata"] = user_metadata
    return {"EventName": "s3:ObjectCreated:Put", "Key": key, "Records": [record]}


@pytest.fixture
def wired(monkeypatch):
    """Stub the transport and the object store; leave every decision real."""
    queued: list[dict] = []

    def _delay(**kwargs):
        queued.append(kwargs)
        return SimpleNamespace(id="task-1")

    monkeypatch.setattr("app.worker.tasks.ingest_test_run.delay", _delay)

    stored: dict[str, bytes] = {}

    async def _get_object_content(key, bucket=None):
        if key not in stored:
            raise FileNotFoundError(key)
        return stored[key]

    # Patch the name where the HANDLER looks it up, not where it is defined:
    # routers/webhooks.py binds it at import time, so patching
    # app.db.storage.get_storage_provider leaves the handler holding the real
    # provider and every test blocks on a MinIO connection.
    monkeypatch.setattr(
        "app.routers.webhooks.get_storage_provider",
        lambda: SimpleNamespace(get_object_content=_get_object_content),
    )
    return SimpleNamespace(queued=queued, stored=stored)


def _put_sentinel(wired, prefix: str, payload: dict):
    wired.stored[f"{prefix}upload_complete.json"] = json.dumps(payload).encode()


# ── The forgery the finding reported ─────────────────────────────────────


@pytest.mark.asyncio
async def test_body_metadata_cannot_name_the_project(wired):
    """The attack: an object in my prefix, a victim's name in the body."""
    prefix = f"{ATTACKER}/runs/42/"
    _put_sentinel(wired, prefix, {"project_id": ATTACKER, "build_number": "42"})

    result = await minio_webhook(
        _request(
            _notification(
                f"{prefix}upload_complete.json",
                user_metadata={"project_id": VICTIM, "build_number": "42"},
            )
        ),
        background_tasks=SimpleNamespace(),
    )

    assert result["status"] == "queued"
    assert len(wired.queued) == 1
    sentinel = wired.queued[0]["sentinel_dict"]
    assert sentinel["project_id"] == ATTACKER, (
        "the caller's forged project_id reached ingestion — one shared secret "
        "files a fabricated run into any tenant"
    )


@pytest.mark.asyncio
async def test_the_object_key_decides_the_project(wired):
    """The project is where the data IS, not what it says it is.

    ``process_sentinel`` reads the run's result files from this same prefix, so
    binding the project to the key keeps the two from disagreeing — otherwise
    one project's artifacts land in another project's run.
    """
    prefix = f"{VICTIM}/runs/7/"
    _put_sentinel(wired, prefix, {"project_id": ATTACKER, "build_number": "7"})

    await minio_webhook(
        _request(_notification(f"{prefix}upload_complete.json")),
        background_tasks=SimpleNamespace(),
    )

    queued = wired.queued[0]
    assert queued["sentinel_dict"]["project_id"] == VICTIM
    assert queued["minio_prefix"] == prefix


@pytest.mark.asyncio
async def test_a_notification_for_an_object_that_does_not_exist_is_refused(wired):
    """Fail closed.

    Falling back to the request body is the behaviour being removed, and a
    sentinel that cannot be read is not a sentinel: either a race, in which
    case MinIO retries, or a forgery.
    """
    result = await minio_webhook(
        _request(_notification(f"{VICTIM}/runs/9/upload_complete.json")),
        background_tasks=SimpleNamespace(),
    )

    assert result["reason"] == "sentinel_unreadable"
    assert wired.queued == [], "a run was queued for an object nobody uploaded"


@pytest.mark.asyncio
async def test_a_sentinel_that_is_not_a_json_object_is_refused(wired):
    wired.stored[f"{VICTIM}/runs/9/upload_complete.json"] = b'["not", "an", "object"]'

    result = await minio_webhook(
        _request(_notification(f"{VICTIM}/runs/9/upload_complete.json")),
        background_tasks=SimpleNamespace(),
    )

    assert result["reason"] == "invalid_sentinel_content"
    assert wired.queued == []


@pytest.mark.asyncio
async def test_unparseable_sentinel_bytes_are_refused(wired):
    wired.stored[f"{VICTIM}/runs/9/upload_complete.json"] = b"{ this is not json"

    result = await minio_webhook(
        _request(_notification(f"{VICTIM}/runs/9/upload_complete.json")),
        background_tasks=SimpleNamespace(),
    )

    assert result["reason"] == "sentinel_unreadable"
    assert wired.queued == []


# ── The legitimate producer must still work ──────────────────────────────


@pytest.mark.asyncio
async def test_a_real_upload_is_still_ingested(wired):
    prefix = f"{VICTIM}/runs/101/"
    _put_sentinel(
        wired,
        prefix,
        {
            "project_id": VICTIM,
            "build_number": "101",
            "jenkins_job": "victim-api-regression",
            "branch": "main",
            "commit_hash": "abc123",
        },
    )

    result = await minio_webhook(
        _request(_notification(f"{prefix}upload_complete.json")),
        background_tasks=SimpleNamespace(),
    )

    assert result["status"] == "queued"
    sentinel = wired.queued[0]["sentinel_dict"]
    assert sentinel["project_id"] == VICTIM
    assert sentinel["build_number"] == "101"
    assert sentinel["jenkins_job"] == "victim-api-regression", (
        "non-authorizing fields from the stored sentinel were dropped"
    )


@pytest.mark.asyncio
async def test_a_sentinel_without_a_build_number_falls_back_to_the_key(wired):
    """The key carries the build too, and that fallback names no tenant."""
    prefix = f"{VICTIM}/runs/55/"
    _put_sentinel(wired, prefix, {"branch": "main"})

    await minio_webhook(
        _request(_notification(f"{prefix}upload_complete.json")),
        background_tasks=SimpleNamespace(),
    )

    sentinel = wired.queued[0]["sentinel_dict"]
    assert sentinel["build_number"] == "55"
    assert sentinel["project_id"] == VICTIM


# ── Unchanged behaviour that must stay unchanged ─────────────────────────


@pytest.mark.asyncio
async def test_a_non_sentinel_upload_is_ignored(wired):
    result = await minio_webhook(
        _request(_notification(f"{VICTIM}/runs/1/allure/x-result.json")),
        background_tasks=SimpleNamespace(),
    )
    assert result["reason"] == "not_sentinel"
    assert wired.queued == []


@pytest.mark.asyncio
async def test_a_key_too_shallow_to_carry_a_project_is_ignored(wired):
    result = await minio_webhook(
        _request(_notification("upload_complete.json")),
        background_tasks=SimpleNamespace(),
    )
    assert result["reason"] == "unexpected_key_format"
    assert wired.queued == []


@pytest.mark.asyncio
async def test_a_non_json_body_is_ignored(wired):
    async def _boom():
        raise ValueError("not json")

    result = await minio_webhook(
        SimpleNamespace(json=_boom), background_tasks=SimpleNamespace()
    )
    assert result["reason"] == "invalid_json"
    assert wired.queued == []


# ── The handler must not read the tenant from the request at all ─────────


def test_the_handler_no_longer_reads_userMetadata():
    """A source check, because that payload field is the whole vulnerability.

    The behavioural tests above prove the current paths ignore it; this catches
    someone reinstating the read for a field that looks harmless and later
    grows a project_id.

    Uses the AST, not a substring: the handler's own comment explains the old
    behaviour and names the field, and a grep cannot tell a comment from code.
    Comments are not in the tree, so this asks the question exactly.
    """
    import ast
    import inspect
    import textwrap

    from app.routers import webhooks

    tree = ast.parse(textwrap.dedent(inspect.getsource(webhooks.minio_webhook)))

    literals = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    attributes = {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    }

    assert "userMetadata" not in literals | attributes, (
        "the handler reads userMetadata from the request body again — that is "
        "caller-controlled, and the only credential in front of this endpoint "
        "is a deployment-wide secret that names no tenant"
    )
    assert "get_object_content" in attributes, (
        "the handler no longer fetches the sentinel from storage, so it is "
        "back to trusting the notification's description of the object"
    )


def test_the_ast_check_can_see_the_pattern_at_all():
    """Guards the guard — a scan that matches nothing passes forever."""
    import ast

    tree = ast.parse('x = record["s3"]["object"].get("userMetadata", {})')
    literals = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    assert "userMetadata" in literals


def test_a_comment_naming_the_field_is_not_a_violation():
    """The exact false positive that a substring check produced."""
    import ast

    source = "# we used to read userMetadata here" + chr(10) + "x = 1"
    tree = ast.parse(source)
    literals = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    assert "userMetadata" not in literals


@pytest.mark.asyncio
async def test_the_stored_sentinel_cannot_override_the_project_either(wired):
    """Even authentic content does not get to name the tenant.

    Whoever can write the object could otherwise still redirect the run, and
    the result files would be read from a different project's prefix.
    """
    prefix = f"{ATTACKER}/runs/3/"
    _put_sentinel(
        wired, prefix, {"project_id": VICTIM, "build_number": "3"}
    )

    await minio_webhook(
        _request(_notification(f"{prefix}upload_complete.json")),
        background_tasks=SimpleNamespace(),
    )

    assert wired.queued[0]["sentinel_dict"]["project_id"] == ATTACKER
