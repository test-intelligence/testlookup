"""W23d AGT-14: the Mongo exchange store (``services/http_exchange_store.py``).

A small in-memory collection stands in for Motor: equality, ``$in`` / ``$nin`` /
``$ne`` / ``$lt``, ``sort`` / ``limit`` / ``to_list``, ``count_documents(limit=)``,
``delete_many`` and ``insert_many``. ``fail`` makes the named call raise the way a
Mongo outage does.
"""
from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from app.core import metrics
from app.services import http_exchange_store as store
from app.services.http_exchange_vocab import EXCHANGES_PER_RUN_MAX, REDACTED

PROJECT = "project-alpha"
RUN = "run-alpha"
BEARER = "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJ3MjNkLXNhbXBsZSJ9.c2lnbmF0dXJlLXNhbXBsZS12YWx1ZS0x"


class MongoDown(Exception):
    pass


def _match_value(value: Any, cond: Any) -> bool:
    if isinstance(cond, dict) and cond and all(k.startswith("$") for k in cond):
        for op, arg in cond.items():
            if op == "$in" and value not in arg:
                return False
            if op == "$nin" and value in arg:
                return False
            if op == "$ne" and value == arg:
                return False
            if op == "$lt" and not (value is not None and value < arg):
                return False
        return True
    return value == cond


def _matches(doc: dict, query: dict) -> bool:
    return all(_match_value(doc.get(k), v) for k, v in query.items())


class _Cursor:
    def __init__(self, coll: "FakeCollection", docs: list[dict], projection: dict | None):
        self._coll, self._docs, self._projection, self._limit = coll, docs, projection, None

    def sort(self, key: Any, direction: int | None = None) -> "_Cursor":
        keys = key if isinstance(key, list) else [(key, direction or 1)]
        for field, order in reversed(keys):
            self._docs.sort(key=lambda d: d.get(field), reverse=order < 0)
        return self

    def limit(self, n: int) -> "_Cursor":
        self._limit = n
        return self

    async def to_list(self, length: int | None = None) -> list[dict]:
        self._coll._maybe_fail("find")
        docs = self._docs[: self._limit] if self._limit else self._docs
        self._coll.docs_read += len(docs)
        out = []
        for d in docs:
            row = copy.deepcopy(d)
            row.pop("_id", None)
            if self._projection and any(v for k, v in self._projection.items() if k != "_id"):
                row = {k: v for k, v in row.items() if self._projection.get(k)}
            out.append(row)
        return out


class FakeCollection:
    def __init__(self) -> None:
        self.docs: list[dict] = []
        self.fail: set[str] = set()
        self.calls: list[str] = []
        self.docs_read = 0

    def _maybe_fail(self, name: str) -> None:
        self.calls.append(name)
        if name in self.fail or "*" in self.fail:
            raise MongoDown("server selection timed out")

    def find(self, query: dict, projection: dict | None = None) -> _Cursor:
        return _Cursor(self, [d for d in self.docs if _matches(d, query)], projection)

    async def find_one(self, query: dict, projection: dict | None = None) -> dict | None:
        rows = await _Cursor(self, [d for d in self.docs if _matches(d, query)], projection).limit(1).to_list(1)
        return rows[0] if rows else None

    async def count_documents(self, query: dict, limit: int | None = None) -> int:
        self._maybe_fail("count_documents")
        n = sum(1 for d in self.docs if _matches(d, query))
        return min(n, limit) if limit else n

    async def delete_many(self, query: dict) -> None:
        self._maybe_fail("delete_many")
        self.docs = [d for d in self.docs if not _matches(d, query)]

    async def insert_many(self, docs: list[dict], ordered: bool = True) -> None:
        self._maybe_fail("insert_many")
        assert ordered is False
        self.docs.extend(copy.deepcopy(docs))


@pytest.fixture
def coll(monkeypatch) -> FakeCollection:
    fake = FakeCollection()
    monkeypatch.setattr(store, "get_mongo_db", lambda: {"rest_api_payloads": fake})
    return fake


class _Recorder:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict]] = []

    def __getattr__(self, level: str):
        return lambda event, **kw: self.events.append((level, event, kw))


def _metric(source: str, outcome: str) -> float:
    return metrics.http_exchanges_ingested_total.labels(source=source, outcome=outcome)._value.get()


def _exchange(n: int = 7, *, method: str = "GET", body: str | None = None, status: int = 200) -> dict:
    return {
        "method": method,
        "url": f"https://api.example.test/api/products/{n}?page=2",
        "endpoint": "/api/products/{id}",
        "status": status,
        "duration_ms": 12,
        "request": {"headers": {"Authorization": BEARER, "Accept": "application/json"}},
        "response": {"headers": {"Content-Type": "application/json"},
                     "body": body if body is not None else json.dumps({"id": n, "price": 9.5})},
    }


async def _write(items, *, run: str = RUN, created: datetime | None = None, source: str = "json_batch"):
    return await store.write_exchanges(
        project_id=PROJECT, test_run_id=run, build_number="41",
        run_created_at=created or datetime(2026, 10, 30, 9, 0, tzinfo=timezone.utc),
        items=items, source=source,
    )


# ── write_exchanges ──────────────────────────────────────────────────────────


async def test_a_write_stores_one_redacted_doc_per_exchange_with_server_ids(coll):
    before = _metric("json_batch", "stored")
    counts = await _write([("case-one", [_exchange(7), _exchange(8, method="post")], 0)])
    assert counts == store.WriteCounts(stored=2, truncated=0, dropped=0, write_failed=False)
    assert _metric("json_batch", "stored") == before + 2
    first, second = sorted(coll.docs, key=lambda d: d["seq"])
    assert (first["seq"], second["seq"]) == (0, 1)
    assert first["v"] == 1 and first["source"] == "json_batch"
    assert (first["project_id"], first["test_run_id"], first["test_case_id"]) == (PROJECT, RUN, "case-one")
    assert first["build_number"] == "41" and first["method"] == "GET" and second["method"] == "POST"
    assert first["endpoint"] == "/api/products/{id}" and first["host"] == "api.example.test"
    assert first["status"] == 200 and first["status_class"] == 2
    assert first["request"]["headers"]["Authorization"] == REDACTED
    assert first["response_schema_state"] == "parsed"
    assert first["response_schema"] == {"id": "number", "price": "number"}
    assert first["dropped_for_test"] == 0 and "dropped_for_test" not in second
    assert isinstance(first["captured_at"], datetime)
    assert BEARER.split()[1] not in json.dumps(coll.docs, default=str)


async def test_twenty_one_exchanges_keep_twenty_and_count_one_dropped(coll):
    counts = await _write([("case-one", [_exchange(i) for i in range(21)], 0)])
    assert (counts.stored, counts.dropped) == (20, 1)
    assert sorted(d["seq"] for d in coll.docs) == list(range(20))
    (seq0,) = [d for d in coll.docs if d["seq"] == 0]
    assert seq0["dropped_for_test"] == 1


async def test_a_malformed_exchange_is_dropped_and_the_rest_stored(coll):
    counts = await _write([("case-one", [_exchange(1), {"method": "GET"}, _exchange(2, method="get me")], 2)])
    assert (counts.stored, counts.dropped) == (1, 4)
    assert coll.docs[0]["dropped_for_test"] == 4


async def test_the_per_run_cap_counts_other_cases_and_drops_the_rest(coll):
    coll.docs = [{"test_run_id": RUN, "test_case_id": f"other-{i}", "seq": 0} for i in range(EXCHANGES_PER_RUN_MAX - 5)]
    counts = await _write([("case-one", [_exchange(i) for i in range(4)], 0),
                           ("case-two", [_exchange(i) for i in range(6)], 0)])
    assert (counts.stored, counts.dropped) == (5, 5)
    two = sorted((d for d in coll.docs if d.get("test_case_id") == "case-two"), key=lambda d: d["seq"])
    assert [d["seq"] for d in two] == [0] and two[0]["dropped_for_test"] == 5
    assert len(coll.docs) == EXCHANGES_PER_RUN_MAX


async def test_a_re_delivery_of_the_same_build_replaces_instead_of_duplicating(coll):
    items = [("case-one", [_exchange(1), _exchange(2)], 0), ("case-two", [_exchange(3)], 0)]
    await _write(items)
    again = await _write(items)
    assert again.stored == 3 and len(coll.docs) == 3


async def test_a_full_run_re_delivered_is_not_refused_by_its_own_docs(coll):
    items = [(f"case-{i}", [_exchange(j) for j in range(20)], 0) for i in range(EXCHANGES_PER_RUN_MAX // 20)]
    first = await _write(items)
    again = await _write(items)
    assert first.stored == again.stored == EXCHANGES_PER_RUN_MAX and again.dropped == 0
    assert len(coll.docs) == EXCHANGES_PER_RUN_MAX


async def test_a_write_failure_is_swallowed_counted_and_logged_without_content(coll, monkeypatch):
    recorder = _Recorder()
    monkeypatch.setattr(store, "_slog", recorder)
    coll.fail = {"insert_many"}
    before = _metric("live", "write_failed")
    counts = await _write([("case-one", [_exchange(1), _exchange(2)], 0)], source="live")
    assert counts == store.WriteCounts(stored=0, truncated=0, dropped=0, write_failed=True)
    assert _metric("live", "write_failed") == before + 2
    ((level, event, fields),) = recorder.events
    assert (level, event) == ("warning", "http_exchanges_write_failed")
    assert fields == {"test_run_id": RUN, "source": "live", "exchanges": 2, "error_type": "MongoDown"}


async def test_mongo_unreachable_at_write_never_raises(monkeypatch):
    def _down():
        raise MongoDown("no server")

    monkeypatch.setattr(store, "get_mongo_db", _down)
    counts = await _write([("case-one", [_exchange(1)], 0)])
    assert counts.write_failed is True and counts.stored == 0


async def test_a_body_cut_by_the_store_counts_truncated_with_its_original_size(coll):
    big = json.dumps({"items": [f"item {i} is a lamp with a linen shade" for i in range(1200)]})
    counts = await _write([("case-one", [_exchange(1, body=big)], 0)])
    assert counts.truncated == 1
    response = coll.docs[0]["response"]
    assert response["body_truncated"] is True and response["body_bytes"] == len(big.encode())
    assert len(response["body"].encode()) < len(big.encode())


async def test_nothing_valid_to_store_makes_no_mongo_call(coll):
    counts = await _write([("case-one", [{"url": "https://x.example.test/"}], 0)])
    assert counts == store.WriteCounts(stored=0, truncated=0, dropped=1, write_failed=False)
    assert coll.calls == []


# ── list_for_case / docs_for_case ────────────────────────────────────────────


async def test_list_for_case_answers_items_total_dropped_and_the_run_cap(coll):
    await _write([("case-one", [_exchange(i) for i in range(22)], 1), ("case-two", [_exchange(1)], 0)])
    items, total, dropped, cap = await store.list_for_case(project_id=PROJECT, test_run_id=RUN, test_case_id="case-one")
    assert [d["seq"] for d in items] == list(range(20)) and total == 20
    assert dropped == 3 and cap is False
    assert all("_id" not in d for d in items)
    assert coll.calls.count("insert_many") == 1


async def test_list_for_case_is_scoped_by_project_run_and_case(coll):
    await _write([("case-one", [_exchange(1)], 0)])
    for scope in ({"project_id": "project-other", "test_run_id": RUN}, {"project_id": PROJECT, "test_run_id": "run-other"}):
        items, total, dropped, cap = await store.list_for_case(test_case_id="case-one", **scope)
        assert (items, total, dropped, cap) == ([], 0, 0, False)


async def test_list_for_case_reports_the_run_cap_reached(coll):
    coll.docs = [{"project_id": PROJECT, "test_run_id": RUN, "test_case_id": f"c{i}", "seq": 0}
                 for i in range(EXCHANGES_PER_RUN_MAX)]
    items, total, _dropped, cap = await store.list_for_case(project_id=PROJECT, test_run_id=RUN, test_case_id="none")
    assert (items, total, cap) == ([], 0, True)


async def test_a_read_failure_raises_exchanges_unavailable_and_writes_nothing(coll):
    coll.fail = {"*"}
    with pytest.raises(store.ExchangesUnavailable):
        await store.list_for_case(project_id=PROJECT, test_run_id=RUN, test_case_id="case-one")
    with pytest.raises(store.ExchangesUnavailable):
        await store.docs_for_case(project_id=PROJECT, test_run_id=RUN, test_case_id="case-one")
    assert not {"insert_many", "delete_many"} & set(coll.calls)


async def test_to_read_marks_a_redacted_exchange(coll):
    await _write([("case-one", [_exchange(1)], 0)])
    (doc,) = await store.docs_for_case(project_id=PROJECT, test_run_id=RUN, test_case_id="case-one")
    read = store.to_read(doc)
    assert read["redacted"] is True and read["request"]["headers"]["Authorization"] == REDACTED
    assert read["endpoint"] == "/api/products/{id}" and read["source"] == "json_batch"


# ── drift_baseline ───────────────────────────────────────────────────────────


async def _seed_runs(coll, n: int, *, base: datetime) -> list[str]:
    runs = []
    for i in range(n):
        run = f"run-{chr(ord('a') + i)}"
        runs.append(run)
        await _write([("case-one", [_exchange(1), _exchange(2)], 0)], run=run, created=base + timedelta(hours=i))
    return runs


async def test_drift_baseline_reads_the_five_newest_earlier_runs_newest_first(coll):
    base = datetime(2026, 10, 1, tzinfo=timezone.utc)
    runs = await _seed_runs(coll, 8, base=base)
    docs = await store.drift_baseline(
        project_id=PROJECT, method="GET", endpoint="/api/products/{id}", status_class=2,
        before=base + timedelta(hours=7), exclude_run_id=runs[7],
    )
    seen = list(dict.fromkeys(d["test_run_id"] for d in docs))
    assert seen == [runs[6], runs[5], runs[4], runs[3], runs[2]]
    assert len(docs) == 10
    assert set(docs[0]) == {"test_run_id", "build_number", "run_created_at", "response_schema", "response_schema_state"}


async def test_drift_baseline_honours_before_status_class_and_the_parsed_state(coll):
    base = datetime(2026, 10, 1, tzinfo=timezone.utc)
    runs = await _seed_runs(coll, 3, base=base)
    await _write([("case-one", [_exchange(1, status=404)], 0)], run="run-404", created=base + timedelta(minutes=30))
    await _write([("case-one", [_exchange(1, body="plain text")], 0)], run="run-text", created=base + timedelta(minutes=40))
    docs = await store.drift_baseline(
        project_id=PROJECT, method="GET", endpoint="/api/products/{id}", status_class=2,
        before=base + timedelta(hours=1), exclude_run_id="current",
    )
    assert {d["test_run_id"] for d in docs} == {runs[0]}
    other = await store.drift_baseline(
        project_id="project-other", method="GET", endpoint="/api/products/{id}", status_class=2,
        before=base + timedelta(days=1), exclude_run_id="current",
    )
    assert other == []


async def test_drift_baseline_reads_at_most_the_docs_cap(coll):
    base = datetime(2026, 10, 1, tzinfo=timezone.utc)
    await _write([(f"case-{i}", [_exchange(j) for j in range(20)], 0) for i in range(8)], run="run-big", created=base)
    coll.docs_read = 0
    docs = await store.drift_baseline(
        project_id=PROJECT, method="GET", endpoint="/api/products/{id}", status_class=2,
        before=base + timedelta(hours=1), exclude_run_id="current",
    )
    assert len(docs) == 100 and coll.docs_read == 100


# ── store_batch_http_exchanges (ingestion_pipeline) and the batch task ───────


def _results() -> list[dict]:
    return [
        {"test_name": "test_a", "class_name": "C", "status": "FAILED", "http_exchanges": [_exchange(1)]},
        {"test_name": "test_b", "status": "PASSED"},
        {"test_name": "test_a", "class_name": "C", "status": "PASSED", "http_exchanges": [_exchange(2)],
         "http_exchanges_dropped": 3},
        {"test_name": "test_rejected", "status": "FAILED", "http_exchanges": [_exchange(3)]},
    ]


async def test_store_batch_maps_fingerprints_to_case_ids_and_merges_duplicates(monkeypatch):
    import uuid
    from unittest.mock import AsyncMock, MagicMock

    from app.models.postgres import TestRun
    from app.services import ingestion_pipeline
    from app.services.ingestion import make_test_fingerprint

    case_a = uuid.uuid4()
    run = TestRun(id=uuid.uuid4(), project_id=uuid.uuid4(), build_number="41")
    created = datetime(2026, 10, 30, 9, 0, tzinfo=timezone.utc)
    db = MagicMock()
    db.execute = AsyncMock(return_value=MagicMock(all=MagicMock(return_value=[(case_a, make_test_fingerprint("test_a", "C"))])))
    db.scalar = AsyncMock(return_value=created)
    write = AsyncMock(return_value=store.WriteCounts(stored=2, truncated=0, dropped=3, write_failed=False))
    monkeypatch.setattr(store, "write_exchanges", write)
    recorder = _Recorder()
    monkeypatch.setattr(ingestion_pipeline, "logger", recorder)

    counts = await ingestion_pipeline.store_batch_http_exchanges(db, run, _results())

    assert counts.stored == 2
    kwargs = write.await_args.kwargs
    assert (kwargs["project_id"], kwargs["test_run_id"], kwargs["build_number"]) == (str(run.project_id), str(run.id), "41")
    assert kwargs["run_created_at"] == created and kwargs["source"] == "json_batch"
    ((case_id, exchanges, dropped),) = kwargs["items"]
    assert case_id == str(case_a) and dropped == 3
    assert [ex["url"] for ex in exchanges] == [_exchange(1)["url"], _exchange(2)["url"]]
    db.execute.assert_awaited_once()
    ((level, event, fields),) = recorder.events
    assert (level, event) == ("info", "http_exchanges_stored")
    assert fields["unmatched_cases"] == 1 and fields["stored"] == 2


async def test_store_batch_never_raises_when_the_case_lookup_fails(monkeypatch):
    import uuid
    from unittest.mock import AsyncMock, MagicMock

    from app.models.postgres import TestRun
    from app.services import ingestion_pipeline

    db = MagicMock()
    db.execute = AsyncMock(side_effect=RuntimeError("connection reset"))
    write = AsyncMock()
    monkeypatch.setattr(store, "write_exchanges", write)
    recorder = _Recorder()
    monkeypatch.setattr(ingestion_pipeline, "logger", recorder)
    run = TestRun(id=uuid.uuid4(), project_id=uuid.uuid4(), build_number="41")
    assert await ingestion_pipeline.store_batch_http_exchanges(db, run, _results()) is None
    write.assert_not_awaited()
    assert recorder.events == [("warning", "http_exchanges_case_lookup_failed",
                                {"run_id": str(run.id), "error_type": "RuntimeError"})]
    assert await ingestion_pipeline.store_batch_http_exchanges(db, run, [{"test_name": "t", "status": "PASSED"}]) is None


def _drive_batch_task(payload: dict) -> dict:
    """The REAL ``ingest_uploaded_results`` body with its collaborators recorded."""
    import asyncio
    import inspect
    import uuid
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock, patch

    from app.services import ingestion_pipeline
    from app.worker import tasks

    order: list[str] = []
    session = MagicMock()
    session.commit = AsyncMock(side_effect=lambda: order.append("commit"))
    run = SimpleNamespace(id=uuid.uuid4())

    @asynccontextmanager
    async def _session():
        yield session

    store_batch = AsyncMock(side_effect=lambda *a, **k: order.append("store"))
    finalize = AsyncMock(side_effect=lambda **k: order.append("finalize"))
    kwargs = {"run_id": str(run.id), "payload": payload}
    inspect.signature(tasks.ingest_uploaded_results.run).bind(**kwargs)   # Celery's own argument check
    with (
        patch.object(tasks, "_run_async", side_effect=asyncio.run),
        patch.object(tasks, "reindex_search", MagicMock()),
        patch.object(ingestion_pipeline, "create_run_from_payload", new=AsyncMock(return_value=run)),
        patch.object(ingestion_pipeline, "ingest_test_results", new=AsyncMock(return_value=2)),
        patch.object(ingestion_pipeline, "store_batch_http_exchanges", new=store_batch),
        patch.object(ingestion_pipeline, "finalize_run", new=finalize),
        patch("app.db.postgres.AsyncSessionLocal", _session),
        patch("app.services.cache_service.bump_analytics_epoch", new=AsyncMock()),
    ):
        tasks.ingest_uploaded_results.run(**kwargs)
    return {"order": order, "store": store_batch, "finalize": finalize, "session": session, "run": run}


def test_the_batch_task_stores_exchanges_after_its_commit_and_threads_run_ai():
    payload = {"project_id": "p1", "build_number": "41", "run_ai": False, "results": _results()}
    got = _drive_batch_task(payload)
    assert got["order"] == ["commit", "store", "finalize"]
    got["store"].assert_awaited_once_with(got["session"], got["run"], payload["results"])
    assert got["finalize"].await_args.kwargs["run_ai"] is False


def test_the_batch_task_without_exchanges_never_calls_the_store_and_keeps_run_ai_true():
    payload = {"project_id": "p1", "build_number": "41", "results": [{"test_name": "t", "status": "PASSED"}]}
    got = _drive_batch_task(payload)
    assert got["order"] == ["commit", "finalize"]
    got["store"].assert_not_awaited()
    assert got["finalize"].await_args.kwargs["run_ai"] is True


def test_the_host_of_a_redacted_userinfo_url_is_kept():
    # W23d (G-A1's finding): urlsplit raises on "user:[REDACTED]@host", so the store kept no host for
    # every url whose password the redaction masked. The store reads the host with url_host now.
    assert store._host("https://walker:[REDACTED]@api.example.test:8443/v1/items?access_token=[REDACTED]") == "api.example.test"
    assert store._host("https://api.example.test/v1/items") == "api.example.test"
    assert store._host("/relative/path") is None
