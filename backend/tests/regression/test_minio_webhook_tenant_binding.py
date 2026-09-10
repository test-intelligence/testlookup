"""The MinIO webhook must not let its caller choose the tenant -- or lose an upload.

Re-audit finding N10, and the same shape as H1 on ``/ws/events``: one
deployment-wide ``WEBHOOK_SECRET`` guards the endpoint, that credential names no
project, and the handler read ``project_id`` straight out of the request body::

    user_meta = record["s3"]["object"].get("userMetadata", {})
    sentinel_data.update(user_meta)          # project_id arrives here

Downstream, ``_upsert_test_run`` resolves that string as a UUID **or a slug**
against any project, so a holder of the shared secret could file a fabricated
run into any tenant. And ``process_sentinel`` lists and reads result objects
from ``minio_prefix`` -- derived from the caller's object key -- so the same
request is a cross-tenant **read**.

The fix fetches the sentinel and takes the project from where the data lives.
The code review of that fix moved the fetch from the webhook into the
ingestion task (``services/minio_sentinel.py``): the webhook answered 200
"ignored" on any storage error, MinIO treats a 200 as delivered, and so one
transient 503 lost the upload. The task retries a storage hiccup, refuses a
sentinel that can never be used without retrying it, and reads through a size
cap, off the API process.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.routers.webhooks import minio_webhook
from app.services import minio_sentinel
from app.services.minio_sentinel import MAX_SENTINEL_BYTES, SentinelRefused, read_sentinel

VICTIM = "victim-project"
ATTACKER = "attacker-project"
CHUNK = 16 * 1024


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


class _NoSuchKey(Exception):
    """What aiobotocore raises for a missing object: a ClientError's shape."""

    response = {"Error": {"Code": "NoSuchKey"}}


@pytest.fixture
def wired(monkeypatch):
    """Stub the transport and the object store; leave every decision real."""
    queued: list[dict] = []

    def _delay(**kwargs):
        queued.append(kwargs)
        return SimpleNamespace(id="task-1")

    monkeypatch.setattr("app.worker.tasks.ingest_test_run.delay", _delay)

    stored: dict[str, bytes] = {}
    errors: dict[str, BaseException] = {}
    reads: list[str] = []
    chunks_read: list[int] = []

    async def _stream_object(key, bucket=None):
        reads.append(key)
        if key in errors:
            raise errors[key]
        if key not in stored:
            raise FileNotFoundError(key)
        data = stored[key]
        for offset in range(0, len(data), CHUNK):
            chunks_read.append(offset)
            yield data[offset:offset + CHUNK]

    monkeypatch.setattr(
        minio_sentinel,
        "get_storage_provider",
        lambda: SimpleNamespace(stream_object=_stream_object),
    )
    return SimpleNamespace(
        queued=queued, stored=stored, errors=errors, reads=reads, chunks_read=chunks_read
    )


def _put_sentinel(wired, prefix: str, payload: dict) -> str:
    key = f"{prefix}upload_complete.json"
    wired.stored[key] = json.dumps(payload).encode()
    return key


async def _notify(key: str, **kwargs) -> dict:
    return await minio_webhook(
        _request(_notification(key, **kwargs)), background_tasks=SimpleNamespace()
    )


# ── The webhook: it queues the key, and nothing the caller says ─────────


@pytest.mark.asyncio
async def test_the_webhook_queues_the_key_and_nothing_the_caller_claims(wired):
    """The forgery the finding reported: an object in my prefix, a victim's
    name in the body. The body's claim never reaches the queue."""
    key = f"{ATTACKER}/runs/42/upload_complete.json"

    result = await _notify(key, user_metadata={"project_id": VICTIM, "build_number": "42"})

    assert result["status"] == "queued"
    assert result["project_id"] == ATTACKER
    assert wired.queued == [{"sentinel_key": key, "minio_prefix": f"{ATTACKER}/runs/42/"}]


@pytest.mark.asyncio
async def test_the_webhook_reads_nothing_from_storage(wired):
    """A storage error in the webhook became a 200 "ignored": the upload was lost."""
    _put_sentinel(wired, f"{VICTIM}/runs/1/", {"build_number": "1"})
    await _notify(f"{VICTIM}/runs/1/upload_complete.json")
    assert wired.reads == [], "the webhook read the object; a storage hiccup there loses the upload"


@pytest.mark.asyncio
async def test_a_non_sentinel_upload_is_ignored(wired):
    result = await _notify(f"{VICTIM}/runs/1/allure/x-result.json")
    assert result["reason"] == "not_sentinel"
    assert wired.queued == []


@pytest.mark.asyncio
async def test_a_key_too_shallow_to_carry_a_project_is_ignored(wired):
    result = await _notify("upload_complete.json")
    assert result["reason"] == "unexpected_key_format"
    assert wired.queued == []


@pytest.mark.asyncio
async def test_a_non_json_body_is_ignored(wired):
    async def _boom():
        raise ValueError("not json")

    result = await minio_webhook(SimpleNamespace(json=_boom), background_tasks=SimpleNamespace())
    assert result["reason"] == "invalid_json"
    assert wired.queued == []


# ── The reader: the key decides the project ─────────────────────────────


@pytest.mark.asyncio
async def test_the_object_key_decides_the_project(wired):
    """The project is where the data IS, not what it says it is.

    process_sentinel reads the run's result files from this same prefix, so
    binding the project to the key keeps the two from disagreeing.
    """
    key = _put_sentinel(wired, f"{VICTIM}/runs/7/", {"project_id": ATTACKER, "build_number": "7"})
    sentinel = await read_sentinel(key)
    assert sentinel.project_id == VICTIM


@pytest.mark.asyncio
async def test_the_stored_sentinel_cannot_override_the_project_either(wired):
    """Even authentic content does not get to name the tenant."""
    key = _put_sentinel(wired, f"{ATTACKER}/runs/3/", {"project_id": VICTIM, "build_number": "3"})
    sentinel = await read_sentinel(key)
    assert sentinel.project_id == ATTACKER


@pytest.mark.asyncio
async def test_a_real_upload_keeps_its_other_fields(wired):
    key = _put_sentinel(wired, f"{VICTIM}/runs/101/", {
        "project_id": VICTIM,
        "build_number": "101",
        "jenkins_job": "victim-api-regression",
        "branch": "main",
        "commit_hash": "abc123",
    })
    sentinel = await read_sentinel(key)
    assert (sentinel.project_id, sentinel.build_number) == (VICTIM, "101")
    assert (sentinel.jenkins_job, sentinel.branch, sentinel.commit_hash) == (
        "victim-api-regression", "main", "abc123",
    ), "non-authorizing fields from the stored sentinel were dropped"


@pytest.mark.asyncio
async def test_a_sentinel_without_a_build_number_falls_back_to_the_key(wired):
    """The key carries the build too, and that fallback names no tenant."""
    key = _put_sentinel(wired, f"{VICTIM}/runs/55/", {"branch": "main"})
    sentinel = await read_sentinel(key)
    assert (sentinel.project_id, sentinel.build_number) == (VICTIM, "55")


# ── The reader: what it refuses, and what it leaves to a retry ───────────


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [FileNotFoundError("gone"), _NoSuchKey("gone")])
async def test_a_sentinel_that_does_not_exist_is_refused(wired, error):
    """Fail closed, for either backend: falling back to the request body is the
    behaviour being removed, and a sentinel that is not there is not a sentinel."""
    key = f"{VICTIM}/runs/9/upload_complete.json"
    wired.errors[key] = error
    with pytest.raises(SentinelRefused):
        await read_sentinel(key)


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", [b'["not", "an", "object"]', b"{ this is not json", b"\xff\xfe\x00"])
async def test_a_sentinel_that_is_not_a_json_object_is_refused(wired, raw):
    key = f"{VICTIM}/runs/9/upload_complete.json"
    wired.stored[key] = raw
    with pytest.raises(SentinelRefused):
        await read_sentinel(key)


@pytest.mark.asyncio
async def test_an_invalid_sentinel_is_refused(wired):
    key = _put_sentinel(wired, f"{VICTIM}/runs/9/", {"build_number": None})
    with pytest.raises(SentinelRefused):
        await read_sentinel(key)


@pytest.mark.asyncio
async def test_an_oversized_object_is_refused_without_reading_it_all(wired):
    """A sentinel is a few hundred bytes. Whoever can write to the bucket could
    otherwise make a worker hold an object of any size."""
    key = f"{VICTIM}/runs/9/upload_complete.json"
    wired.stored[key] = b" " * (10 * 1024 * 1024)
    with pytest.raises(SentinelRefused):
        await read_sentinel(key)
    assert len(wired.chunks_read) <= MAX_SENTINEL_BYTES // CHUNK + 1, (
        f"read {len(wired.chunks_read)} chunks of a 10 MiB object before refusing it"
    )


@pytest.mark.asyncio
async def test_a_storage_hiccup_is_left_to_the_tasks_retry(wired):
    """Only a sentinel that can never be used is refused; a timeout is retried."""
    key = f"{VICTIM}/runs/9/upload_complete.json"
    wired.errors[key] = ConnectionError("MinIO SlowDown")
    with pytest.raises(ConnectionError):
        await read_sentinel(key)


# ── The task: refused is final, a hiccup is retried ──────────────────────


@pytest.fixture
def task_env(monkeypatch):
    from app.worker import tasks as worker_tasks

    released: list[str] = []
    ingested: list[tuple] = []
    retried: list[dict] = []

    async def _not_a_duplicate(*_args, **_kwargs):
        return False

    async def _process(sentinel, minio_prefix):
        ingested.append((sentinel, minio_prefix))

    def _retry(*_args, **kwargs):
        retried.append(kwargs)
        raise RuntimeError("retry requested")

    import app.services.ingestion as ingestion_mod

    monkeypatch.setattr(worker_tasks, "_is_duplicate", _not_a_duplicate)
    monkeypatch.setattr(
        worker_tasks, "_release_dedup_for_retry", lambda key, owner, task_id: released.append(key)
    )
    monkeypatch.setattr(worker_tasks.reindex_search, "apply_async", lambda *a, **k: None)
    monkeypatch.setattr(ingestion_mod, "process_sentinel", _process)
    monkeypatch.setattr(worker_tasks.ingest_test_run, "retry", _retry)
    return SimpleNamespace(
        task=worker_tasks.ingest_test_run, released=released, ingested=ingested, retried=retried
    )


def _run_task(task_env, key: str, prefix: str):
    return task_env.task.apply(
        kwargs={"sentinel_key": key, "minio_prefix": prefix}, task_id="t-1", throw=False
    )


def test_the_task_ingests_the_sentinel_it_reads(wired, task_env):
    prefix = f"{VICTIM}/runs/12/"
    key = _put_sentinel(wired, prefix, {"project_id": ATTACKER, "build_number": "12"})

    _run_task(task_env, key, prefix)

    [(sentinel, used_prefix)] = task_env.ingested
    assert (sentinel.project_id, used_prefix) == (VICTIM, prefix)
    assert task_env.retried == []


def test_a_refused_sentinel_is_not_retried_and_frees_the_prefix(wired, task_env):
    """Retrying cannot conjure the object; holding the lock would refuse a
    real upload to the same prefix for an hour."""
    prefix = f"{VICTIM}/runs/13/"

    _run_task(task_env, f"{prefix}upload_complete.json", prefix)

    assert task_env.ingested == []
    assert task_env.retried == [], "a sentinel that can never be used was retried"
    assert task_env.released == [f"testlookup:dedup:ingest:{prefix}"]


def test_a_storage_hiccup_is_retried_by_the_task(wired, task_env):
    """The case the webhook used to turn into a lost upload."""
    prefix = f"{VICTIM}/runs/14/"
    key = f"{prefix}upload_complete.json"
    wired.errors[key] = ConnectionError("MinIO SlowDown")

    _run_task(task_env, key, prefix)

    assert task_env.ingested == []
    assert len(task_env.retried) == 1, "a transient storage error was not retried"


# ── The handler must not read the tenant, or the object, at all ──────────


def _handler_tree():
    import ast
    import inspect
    import textwrap

    from app.routers import webhooks

    return ast.parse(textwrap.dedent(inspect.getsource(webhooks.minio_webhook)))


def test_the_handler_no_longer_reads_userMetadata():
    """A source check, because that payload field is the whole vulnerability.

    Uses the AST, not a substring: the handler's own comment explains the old
    behaviour, and a grep cannot tell a comment from code.
    """
    import ast

    tree = _handler_tree()
    names = {
        node.value for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    } | {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    assert "userMetadata" not in names, (
        "the handler reads userMetadata from the request body again -- that is "
        "caller-controlled, and the only credential in front of this endpoint "
        "is a deployment-wide secret that names no tenant"
    )


def test_the_handler_leaves_the_read_to_the_task():
    import ast

    tree = _handler_tree()
    used = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)} | {
        node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
    }
    assert not used & {"get_object_content", "stream_object", "get_storage_provider"}, (
        "the webhook reads storage again; a storage error there is a 200 that "
        "MinIO treats as delivered"
    )


def test_the_reader_goes_through_the_capped_stream():
    """get_object_content reads the whole object before anything can refuse it."""
    import inspect

    source = inspect.getsource(minio_sentinel)
    assert "stream_object" in source
    assert "get_object_content" not in source


def test_the_ast_check_can_see_the_pattern_at_all():
    """Guards the guard -- a scan that matches nothing passes forever."""
    import ast

    tree = ast.parse('x = record["s3"]["object"].get("userMetadata", {})')
    literals = {
        node.value for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    assert "userMetadata" in literals


def test_a_comment_naming_the_field_is_not_a_violation():
    """The exact false positive that a substring check produced."""
    import ast

    tree = ast.parse("# we used to read userMetadata here" + chr(10) + "x = 1")
    literals = {
        node.value for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    assert "userMetadata" not in literals
