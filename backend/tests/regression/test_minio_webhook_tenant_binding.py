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

import asyncio
import json
from types import SimpleNamespace

import pytest

from app.routers.webhooks import minio_webhook
from app.services import minio_sentinel
from app.services.minio_sentinel import SentinelRefused, read_sentinel

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


# The cap's sizes are literal on purpose. This test first stored 10 MiB of
# spaces and bounded the chunks it read by MAX_SENTINEL_BYTES itself: raise the
# cap to 64 GiB and the object was read whole and still refused, as "not
# JSON", so the test passed (QA of R2, mutation R2-3). A VALID sentinel can
# only be refused for its size, and a size derived from the cap moves with it.
_CAP = 64 * 1024


def _sentinel_of_exactly(size: int) -> bytes:
    """A valid sentinel, padded with JSON whitespace to exactly ``size`` bytes."""
    body = json.dumps({"build_number": "9", "branch": "main"}).encode()
    assert len(body) <= size
    return body + b" " * (size - len(body))


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [_CAP + 1, 10 * 1024 * 1024], ids=["one-byte-over", "10MiB"])
async def test_an_oversized_object_is_refused_without_reading_it_all(wired, size):
    """A sentinel is a few hundred bytes. Whoever can write to the bucket could
    otherwise make a worker hold an object of any size."""
    key = f"{VICTIM}/runs/9/upload_complete.json"
    wired.stored[key] = _sentinel_of_exactly(size)
    with pytest.raises(SentinelRefused, match="is larger than"):
        await read_sentinel(key)
    assert len(wired.chunks_read) <= _CAP // CHUNK + 1, (
        f"read {len(wired.chunks_read)} chunks of a {size}-byte object before refusing it"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [_CAP - 1, _CAP], ids=["one-byte-under", "at-the-cap"])
async def test_a_valid_sentinel_up_to_the_cap_is_read(wired, size):
    key = f"{VICTIM}/runs/9/upload_complete.json"
    wired.stored[key] = _sentinel_of_exactly(size)
    sentinel = await read_sentinel(key)
    assert (sentinel.project_id, sentinel.build_number, sentinel.branch) == (VICTIM, "9", "main")


@pytest.mark.asyncio
async def test_a_storage_hiccup_is_left_to_the_tasks_retry(wired):
    """Only a sentinel that can never be used is refused; a timeout is retried."""
    key = f"{VICTIM}/runs/9/upload_complete.json"
    wired.errors[key] = ConnectionError("MinIO SlowDown")
    with pytest.raises(ConnectionError):
        await read_sentinel(key)


# ── A key names one prefix only (code review of the N10 follow-up) ───────
#
# The local storage backend resolves a path, so
# ``victim/runs/../../attacker/runs/7/upload_complete.json`` read the
# attacker's sentinel and bound it to project ``victim``. A sentinel key must
# be a plain path: no empty, "." or ".." segment, no backslash, no leading /.

_BS = chr(92)
_UNPLAIN_KEYS = [
    pytest.param(f"{VICTIM}/runs/../../{ATTACKER}/runs/7/upload_complete.json", id="dot-dot"),
    pytest.param(f"{VICTIM}/runs/7/../upload_complete.json", id="dot-dot-in-own-prefix"),
    pytest.param(f"{VICTIM}/./runs/7/upload_complete.json", id="dot"),
    pytest.param(f"{VICTIM}/runs//7/upload_complete.json", id="empty-segment"),
    pytest.param(f"/{VICTIM}/runs/7/upload_complete.json", id="leading-slash"),
    pytest.param(
        f"{VICTIM}/runs/7{_BS}..{_BS}..{_BS}../{ATTACKER}/runs/7/upload_complete.json",
        id="backslash",
    ),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("key", _UNPLAIN_KEYS)
async def test_the_webhook_queues_no_key_that_is_not_a_plain_path(wired, key):
    result = await _notify(key)
    assert result == {"status": "ignored", "reason": "unexpected_key_format"}
    assert wired.queued == []


@pytest.mark.asyncio
@pytest.mark.parametrize("key", _UNPLAIN_KEYS)
async def test_the_reader_refuses_a_key_that_is_not_a_plain_path_unread(wired, key):
    """The task gets keys from more than the webhook: queued before this fix,
    or by hand. The reader checks the key itself, before touching storage."""
    with pytest.raises(SentinelRefused):
        await read_sentinel(key)
    assert wired.reads == [], "the key was read before its shape was checked"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "key",
    [
        f"{VICTIM}/runs/../../{ATTACKER}/runs/7/upload_complete.json",
        f"{VICTIM}/runs/7{_BS}..{_BS}..{_BS}../{ATTACKER}/runs/7/upload_complete.json",
    ],
    ids=["dot-dot", "backslash"],
)
async def test_a_key_cannot_bind_another_prefixs_sentinel_on_local_storage(
    tmp_path, monkeypatch, key
):
    """The review's case on the real local backend, which resolves the path.

    A backslash escapes only where it is a separator (Windows). Elsewhere that
    key names a file that does not exist, and is refused either way.
    """
    from app.core.config import settings
    from app.db.storage import LocalStorageProvider

    monkeypatch.setattr(settings, "LOCAL_STORAGE_PATH", str(tmp_path))
    storage = LocalStorageProvider(default_bucket="testlookup")
    attacker_key = f"{ATTACKER}/runs/7/upload_complete.json"
    await storage.put_object(
        attacker_key,
        json.dumps({"build_number": "7", "ocp_namespace": "attacker-ns"}).encode(),
    )
    monkeypatch.setattr(minio_sentinel, "get_storage_provider", lambda: storage)

    with pytest.raises(SentinelRefused):
        await read_sentinel(key)
    # The attacker's own key still reads -- as the attacker's.
    sentinel = await read_sentinel(attacker_key)
    assert (sentinel.project_id, sentinel.ocp_namespace) == (ATTACKER, "attacker-ns")


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


# ── The last failure is kept for an admin to replay (N10 review, V) ──────
#
# After max_retries an object-store outage still lost the build: MinIO had its
# 200 and does not notify again, and the only trace was an ERROR log line.


class _StreamRedis:
    """XADD and XREVRANGE, as the dead-letter writer and the admin reader use them."""

    def __init__(self) -> None:
        self.entries: list[tuple[str, str, dict]] = []

    async def xadd(self, name, fields, maxlen=None, approximate=True):
        msg_id = f"{1725960000000 + len(self.entries) + 1}-0"
        self.entries.append((name, msg_id, dict(fields)))
        return msg_id

    async def xrevrange(self, name, count=None, **_kwargs):
        rows = [(i, f) for stream, i, f in reversed(self.entries) if stream == name]
        return rows[:count] if count else rows


@pytest.fixture
def dead_letters(monkeypatch):
    fake = _StreamRedis()
    monkeypatch.setattr("app.db.redis_client.get_redis", lambda: fake)
    return fake


def _attempt(task_env, key: str, prefix: str, retries: int):
    return task_env.task.apply(
        kwargs={"sentinel_key": key, "minio_prefix": prefix},
        task_id="t-1", retries=retries, throw=False,
    )


def test_the_last_retry_leaves_a_dead_letter_an_admin_can_replay(wired, task_env, dead_letters):
    from app.services.ingestion_dlq import list_recent_stream_failures

    prefix = f"{VICTIM}/runs/15/"
    key = f"{prefix}upload_complete.json"
    wired.errors[key] = ConnectionError("MinIO SlowDown")

    _attempt(task_env, key, prefix, retries=task_env.task.max_retries)

    assert len(dead_letters.entries) == 1, "the final failure did not leave exactly one dead letter"
    [entry] = asyncio.run(list_recent_stream_failures())  # what the admin route returns
    assert entry["task_name"] == "app.worker.tasks.ingest_test_run"
    assert entry["kwargs"] == {"sentinel_key": key, "minio_prefix": prefix}
    assert "MinIO SlowDown" in entry["error"]

    # Replayed as recorded, once storage is back, through the real task.
    del wired.errors[key]
    _put_sentinel(wired, prefix, {"build_number": "15"})
    task_env.task.apply(kwargs=entry["kwargs"], task_id="t-replay", throw=True)
    [(sentinel, used_prefix)] = task_env.ingested
    assert (sentinel.project_id, used_prefix) == (VICTIM, prefix)


@pytest.mark.parametrize("retries", [0, 1, 2])
def test_an_earlier_retry_leaves_no_dead_letter(wired, task_env, dead_letters, retries):
    prefix = f"{VICTIM}/runs/16/"
    key = f"{prefix}upload_complete.json"
    wired.errors[key] = ConnectionError("MinIO SlowDown")

    _attempt(task_env, key, prefix, retries=retries)

    assert dead_letters.entries == [], f"retry {retries} of 3 was dead-lettered"
    assert len(task_env.retried) == 1


def test_a_refused_sentinel_leaves_no_dead_letter(wired, task_env, dead_letters):
    """A refusal is final and would be refused again on replay: logged, not kept."""
    prefix = f"{VICTIM}/runs/17/"
    _attempt(task_env, f"{prefix}upload_complete.json", prefix, retries=task_env.task.max_retries)
    assert dead_letters.entries == []


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
