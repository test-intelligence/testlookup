"""A build dropped into the bucket is held to the same limits as an upload.

The rules:

* A sentinel key is ``<project>/runs/<build>/.../upload_complete.json``: the
  second segment is ``runs`` and a build segment follows it. A key under any
  other folder is not a sentinel key. ``uploads`` is never a project: it is
  the folder the API archives every HTTP upload under
  (``uploads/<project id>/<run id>/<client file name>``), so a client file
  named ``upload_complete.json`` must not be read as a sentinel.
* One object of a dropped build may be at most
  ``INGEST_DROP_MAX_OBJECT_BYTES`` (50 MiB, the HTTP upload limit). A larger
  one refuses the build before anything is downloaded.
* A dropped build may carry at most ``INGEST_MAX_RESULTS_PER_UPLOAD`` test
  results, like one uploaded report. Allure writes one result per file, so
  that is checked on the listing, before any download.
* A build inside the limits is still ingested.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

pytest.importorskip("defusedxml")

from app.core.config import settings  # noqa: E402
from app.models.schemas import SentinelFile  # noqa: E402
from app.services import ingestion  # noqa: E402
from app.services.minio_sentinel import SentinelRefused, read_sentinel, sentinel_key_problem  # noqa: E402
from app.services.upload_limits import TooManyResults  # noqa: E402

pytestmark = pytest.mark.regression

PROJECT = "3f1c2a9e-7d4b-4c61-9a0e-2b8f5d6c7e10"
RUN = "9b2d6f1e-5a3c-4e7f-8d1a-0c4b7e9f2a35"
PREFIX = "proj/runs/7/"


# ── what a sentinel key is ───────────────────────────────────────────────────


@pytest.mark.parametrize("key", [
    f"uploads/{PROJECT}/{RUN}/upload_complete.json",
    "uploads/runs/7/upload_complete.json",
    "Uploads/runs/7/upload_complete.json",
    "proj/other/7/upload_complete.json",
    "proj/7/upload_complete.json",
    "proj/runs/upload_complete.json",
    f"{PROJECT}/{RUN}/runs/upload_complete.json",
], ids=["http-archive", "uploads-as-project", "uploads-other-case", "not-under-runs",
        "no-runs-segment", "no-build-segment", "runs-in-the-wrong-place"])
def test_a_key_outside_a_projects_runs_folder_is_not_a_sentinel_key(key):
    assert sentinel_key_problem(key) is not None


@pytest.mark.parametrize("key", [
    "proj/runs/7/upload_complete.json",
    f"{PROJECT}/runs/1.0.0.123/upload_complete.json",
    "auth-service/runs/nightly/shard-2/upload_complete.json",
    "my-uploads/runs/7/upload_complete.json",
], ids=["slug", "uuid", "nested-build", "uploads-in-a-longer-name"])
def test_a_key_in_a_projects_runs_folder_is_a_sentinel_key(key):
    assert sentinel_key_problem(key) is None


@pytest.mark.asyncio
async def test_the_reader_never_opens_an_archived_http_upload(monkeypatch):
    opened = []
    monkeypatch.setattr(
        "app.services.minio_sentinel.get_storage_provider",
        lambda: SimpleNamespace(stream_object=lambda key: opened.append(key)),
    )
    with pytest.raises(SentinelRefused):
        await read_sentinel(f"uploads/{PROJECT}/{RUN}/upload_complete.json")
    assert opened == []


# ── the dropped build itself ─────────────────────────────────────────────────


class _Reached(Exception):
    """Raised by the stubbed persistence step: the build passed every check."""


def _allure(name: str) -> bytes:
    return json.dumps({"name": name, "status": "passed", "uuid": name, "start": 1, "stop": 2}).encode()


def _testng(count: int) -> bytes:
    cases = "".join(f'<testcase classname="C" name="t{i}" time="0.1"/>' for i in range(count))
    return f'<testsuite name="s" tests="{count}">{cases}</testsuite>'.encode()


@pytest.fixture
def drop(monkeypatch):
    """A fake bucket and a stubbed database: ``process_sentinel`` up to persistence."""
    objects: dict[str, bytes] = {}
    listed_size: dict[str, int] = {}
    downloads: list[str] = []

    class _Storage:
        async def list_objects(self, prefix):
            return [
                {"Key": key, "Size": listed_size.get(key, len(body))}
                for key, body in sorted(objects.items()) if key.startswith(prefix)
            ]

        async def get_object_content(self, key):
            downloads.append(key)
            return objects[key]

    db = MagicMock()
    db.rollback = AsyncMock()

    class _Session:
        async def __aenter__(self):
            return db

        async def __aexit__(self, *_exc):
            return False

    reached: list[list[dict]] = []

    async def _prefetch(_db, _run_id, fingerprints):
        reached.append(list(fingerprints))
        raise _Reached()

    monkeypatch.setattr(ingestion, "AsyncSessionLocal", lambda: _Session())
    monkeypatch.setattr(ingestion, "get_storage_provider", lambda: _Storage())
    monkeypatch.setattr(
        ingestion, "_upsert_test_run",
        AsyncMock(return_value=SimpleNamespace(id=RUN, project_id=PROJECT, ocp_pod_name=None, ocp_namespace=None)),
    )
    monkeypatch.setattr(ingestion, "_store_raw_allure_batch", AsyncMock())
    monkeypatch.setattr(ingestion, "_prefetch_test_cases", _prefetch)
    monkeypatch.setattr(settings, "INGEST_MAX_RESULTS_PER_UPLOAD", 5)
    monkeypatch.setattr(settings, "INGEST_DROP_MAX_OBJECT_BYTES", 4096)

    async def run():
        sentinel = SentinelFile(project_id="proj", build_number="7")
        await ingestion.process_sentinel(sentinel, PREFIX)

    return SimpleNamespace(
        objects=objects, listed_size=listed_size, downloads=downloads, reached=reached, run=run, db=db,
    )


@pytest.mark.asyncio
async def test_a_build_inside_the_limits_is_ingested(drop):
    for index in range(3):
        drop.objects[f"{PREFIX}allure/{index}-result.json"] = _allure(f"a{index}")
    drop.objects[f"{PREFIX}testng/results.xml"] = _testng(2)

    with pytest.raises(_Reached):
        await drop.run()

    assert len(drop.reached[0]) == 5  # three Allure results and two TestNG cases, at the cap
    assert len(drop.downloads) == 4


@pytest.mark.asyncio
async def test_more_allure_files_than_the_result_cap_refuses_the_build_unread(drop):
    for index in range(6):
        drop.objects[f"{PREFIX}allure/{index}-result.json"] = _allure(f"a{index}")

    with pytest.raises(TooManyResults):
        await drop.run()

    assert drop.downloads == [], "result files were downloaded before the count was checked"
    assert drop.reached == []
    drop.db.rollback.assert_awaited()


@pytest.mark.asyncio
async def test_more_results_than_the_cap_across_both_formats_refuses_the_build(drop):
    for index in range(3):
        drop.objects[f"{PREFIX}allure/{index}-result.json"] = _allure(f"a{index}")
    drop.objects[f"{PREFIX}testng/results.xml"] = _testng(3)

    with pytest.raises(TooManyResults):
        await drop.run()

    assert drop.reached == []


@pytest.mark.asyncio
async def test_an_xml_file_far_over_the_cap_is_refused_before_it_is_parsed(drop, monkeypatch):
    drop.objects[f"{PREFIX}testng/results.xml"] = _testng(30)  # over 4 x the cap of 5
    parser = MagicMock(side_effect=AssertionError("parsed a report that should have been refused"))
    monkeypatch.setattr(ingestion, "parse_testng_xml_with_timings", parser)

    with pytest.raises(TooManyResults):
        await drop.run()

    parser.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("key", [f"{PREFIX}allure/big-result.json", f"{PREFIX}testng/big.xml"],
                         ids=["allure", "testng"])
async def test_an_oversized_object_refuses_the_build_before_any_download(drop, key):
    drop.objects[f"{PREFIX}allure/0-result.json"] = _allure("a0")
    drop.objects[key] = b"x" * 4097

    with pytest.raises(ingestion.DropObjectTooLarge) as refused:
        await drop.run()

    assert key in str(refused.value)
    assert "INGEST_DROP_MAX_OBJECT_BYTES" in str(refused.value)
    assert key not in drop.downloads
    assert drop.reached == []


@pytest.mark.asyncio
async def test_an_object_larger_than_its_listing_said_is_refused_after_the_read(drop):
    key = f"{PREFIX}allure/grew-result.json"
    drop.objects[key] = b"x" * 5000
    drop.listed_size[key] = 10

    with pytest.raises(ingestion.DropObjectTooLarge):
        await drop.run()

    assert drop.reached == []


@pytest.mark.asyncio
async def test_an_object_at_the_size_cap_is_read(drop):
    padding = "p" * (4096 - len(_allure("a0")) - len(', "pad": ""'))
    body = json.dumps(
        {"name": "a0", "status": "passed", "uuid": "a0", "start": 1, "stop": 2, "pad": padding}
    ).encode()
    assert len(body) == 4096
    drop.objects[f"{PREFIX}allure/0-result.json"] = body

    with pytest.raises(_Reached):
        await drop.run()

    assert len(drop.reached[0]) == 1


@pytest.mark.asyncio
async def test_zero_turns_each_cap_off(drop, monkeypatch):
    monkeypatch.setattr(settings, "INGEST_MAX_RESULTS_PER_UPLOAD", 0)
    monkeypatch.setattr(settings, "INGEST_DROP_MAX_OBJECT_BYTES", 0)
    for index in range(8):
        drop.objects[f"{PREFIX}allure/{index}-result.json"] = _allure(f"a{index}") + b" " * 5000

    with pytest.raises(_Reached):
        await drop.run()

    assert len(drop.reached[0]) == 8


@pytest.mark.asyncio
async def test_a_file_that_cannot_be_parsed_is_still_only_skipped(drop):
    """The existing behaviour for a broken file is unchanged: logged and skipped."""
    drop.objects[f"{PREFIX}allure/0-result.json"] = _allure("a0")
    drop.objects[f"{PREFIX}allure/1-result.json"] = b"{ not json"

    with pytest.raises(_Reached):
        await drop.run()

    assert len(drop.reached[0]) == 1


# -- the worker task and the slug -------------------------------------------------


def test_uploads_cannot_be_a_project_slug():
    from pydantic import ValidationError

    from app.models.schemas import ProjectCreate

    with pytest.raises(ValidationError) as refused:
        ProjectCreate(name="Uploads", slug="uploads")
    assert "reserved" in str(refused.value)
    assert ProjectCreate(name="Uploads team", slug="uploads-team").slug == "uploads-team"


@pytest.mark.parametrize("kind", ["too_many_results", "object_too_large"])
def test_the_task_refuses_an_over_limit_build_once_and_does_not_retry(monkeypatch, kind):
    """The same build is over the limit on every retry: refuse it and free the prefix."""
    import app.services.ingestion as ingestion_mod
    from app.models.schemas import SentinelFile
    from app.services.upload_limits import TooManyResults
    from app.worker import tasks as worker_tasks

    released: list[str] = []
    retried: list[dict] = []

    async def _not_a_duplicate(*_a, **_k):
        return False

    def _error():
        for cls in (TooManyResults, ingestion_mod.DropObjectTooLarge):
            if (cls is TooManyResults) == (kind == "too_many_results"):
                try:
                    return cls("over the limit")
                except TypeError:
                    return cls.__new__(cls)
        raise AssertionError(kind)

    async def _process(_sentinel, _prefix):
        raise _error()

    def _retry(*_a, **kwargs):
        retried.append(kwargs)
        raise RuntimeError("retry requested")

    monkeypatch.setattr(worker_tasks, "_is_duplicate", _not_a_duplicate)
    monkeypatch.setattr(worker_tasks, "_release_dedup_for_retry", lambda key, owner, task_id: released.append(key))
    monkeypatch.setattr(worker_tasks.reindex_search, "apply_async", lambda *a, **k: None)
    monkeypatch.setattr(ingestion_mod, "process_sentinel", _process)
    monkeypatch.setattr(worker_tasks.ingest_test_run, "retry", _retry)

    prefix = "shop/runs/77/"
    sentinel = SentinelFile(project_id="shop", build_number="77").model_dump(mode="json")
    worker_tasks.ingest_test_run.apply(
        kwargs={"sentinel_dict": sentinel, "minio_prefix": prefix}, task_id="t-over", throw=False,
    )

    assert retried == [], "an over-limit build was retried"
    assert released == [f"testlookup:dedup:ingest:{prefix}"]
