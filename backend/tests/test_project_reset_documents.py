"""W32 F-7: ``retention_service.delete_project_run_documents`` — the project
reset's Mongo phase, driven from ``_MONGO_PLANS``.

A fake Mongo records every ``delete_many`` filter; a fake session answers the
three Postgres reads (run ids, pipeline ids, live-session slugs) and streams the
project's case ids in pages.
"""
from __future__ import annotations

import uuid
from collections import defaultdict
from types import SimpleNamespace

import pytest

from app.services import retention_service
from app.services.retention_service import _MONGO_PLANS, delete_project_run_documents


class _Collection:
    def __init__(self, name: str, calls: dict[str, list[list[str]]]):
        self._name = name
        self._calls = calls

    async def delete_many(self, flt):
        ((field, cond),) = flt.items()
        values = list(cond["$in"])
        self._calls[self._name].append(values)
        return SimpleNamespace(deleted_count=len(values))


class _Mongo:
    def __init__(self):
        self.calls: dict[str, list[list[str]]] = defaultdict(list)

    def __getitem__(self, name: str) -> _Collection:
        return _Collection(name, self.calls)


class _Stream:
    def __init__(self, pages):
        self._pages = pages
        self.sizes: list[int] = []

    async def partitions(self, size):
        self.sizes.append(size)
        for page in self._pages:
            yield list(page)


class _Db:
    def __init__(self, *, runs, pipelines=(), slugs=(), case_pages=()):
        self._runs, self._pipelines, self._slugs = list(runs), list(pipelines), list(slugs)
        self.stream = _Stream(case_pages)
        self.streamed: list[object] = []
        self.sql: list[str] = []

    async def execute(self, stmt):
        sql = str(stmt)
        self.sql.append(sql)
        if "agent_pipeline_runs" in sql:
            rows = self._pipelines
        elif "live_sessions" in sql:
            rows = self._slugs
        else:
            rows = self._runs
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: list(rows)))

    async def stream_scalars(self, stmt):
        self.streamed.append(stmt)
        return self.stream


def _keys_by_attr(runs, pipelines, slugs=()):
    run_strs = [str(r) for r in runs]
    return {
        "raw_run_strs": run_strs,
        "purge_run_strs": run_strs,
        "audit_run_strs": run_strs,
        "raw_live_run_keys": run_strs + list(slugs),
        "audit_pipeline_strs": [str(p) for p in pipelines],
    }


@pytest.mark.asyncio
async def test_every_run_keyed_collection_is_deleted_in_500_id_chunks():
    runs = [uuid.uuid4() for _ in range(1200)]
    pipelines = [uuid.uuid4() for _ in range(1200)]
    db, mongo = _Db(runs=runs, pipelines=pipelines), _Mongo()

    counts = await delete_project_run_documents(db, mongo, project_id=uuid.uuid4())

    expected = _keys_by_attr(runs, pipelines)
    run_keyed = [(c, a) for c, _f, a in _MONGO_PLANS if a != "purge_tc_strs"]
    assert run_keyed, "the table has run-keyed collections"
    for collection, attr in run_keyed:
        calls = mongo.calls[collection]
        assert [len(c) for c in calls] == [500, 500, 200], collection
        assert sorted(v for c in calls for v in c) == sorted(expected[attr]), collection
        assert counts[collection] == 1200, collection


@pytest.mark.asyncio
async def test_the_case_keyed_collection_takes_streamed_case_ids_page_by_page():
    runs = [uuid.uuid4() for _ in range(3)]
    pages = [[uuid.uuid4() for _ in range(n)] for n in (4, 4, 2)]
    db, mongo = _Db(runs=runs, case_pages=pages), _Mongo()

    counts = await delete_project_run_documents(
        db, mongo, project_id=uuid.uuid4(), case_chunk=4
    )

    case_keyed = [c for c, _f, a in _MONGO_PLANS if a == "purge_tc_strs"]
    assert case_keyed, "the table has a case-keyed collection"
    run_strs = {str(r) for r in runs}
    for collection in case_keyed:
        calls = mongo.calls[collection]
        assert calls == [[str(tc) for tc in page] for page in pages]
        assert not run_strs & {v for c in calls for v in c}, "never run ids"
        assert counts[collection] == 10
    assert db.stream.sizes == [4]
    assert len(db.streamed) == 1
    assert db.streamed[0].get_execution_options()["yield_per"] == 4


@pytest.mark.asyncio
async def test_a_live_slug_of_a_project_run_is_included_and_an_unmapped_one_is_not():
    mapped, unmapped = "local-runner" + "slugqx", "local-orphan" + "slugzk"
    runs = [uuid.uuid4(), uuid.uuid5(uuid.NAMESPACE_DNS, mapped)]
    db, mongo = _Db(runs=runs, slugs=[mapped, unmapped, None, str(runs[0])]), _Mongo()

    await delete_project_run_documents(db, mongo, project_id=uuid.uuid4())

    live = [c for c, _f, a in _MONGO_PLANS if a == "raw_live_run_keys"]
    assert live, "the table has the live-events collection"
    for collection in live:
        sent = [v for c in mongo.calls[collection] for v in c]
        assert sorted(sent) == sorted([str(r) for r in runs] + [mapped])
        assert unmapped not in sent
    for collection, _f, attr in _MONGO_PLANS:
        if attr not in ("raw_live_run_keys", "purge_tc_strs", "audit_pipeline_strs"):
            assert mapped not in [v for c in mongo.calls[collection] for v in c]


@pytest.mark.asyncio
async def test_the_result_has_a_key_for_every_mongo_plans_collection():
    runs = [uuid.uuid4() for _ in range(2)]
    pipelines = [uuid.uuid4()]
    pages = [[uuid.uuid4()]]
    db, mongo = _Db(runs=runs, pipelines=pipelines, case_pages=pages), _Mongo()

    counts = await delete_project_run_documents(db, mongo, project_id=uuid.uuid4())

    # Iterate the table (declaration is not emission): every entry is reached.
    expected = _keys_by_attr(runs, pipelines)
    assert set(counts) == {c for c, _f, _a in retention_service._MONGO_PLANS}
    for collection, _f, attr in retention_service._MONGO_PLANS:
        want = 1 if attr == "purge_tc_strs" else len(expected[attr])
        assert counts[collection] == want, collection
        assert mongo.calls[collection], f"{collection} was never visited"


@pytest.mark.asyncio
async def test_zero_runs_make_no_mongo_call():
    db, mongo = _Db(runs=[], pipelines=[uuid.uuid4()], case_pages=[[uuid.uuid4()]]), _Mongo()

    counts = await delete_project_run_documents(db, mongo, project_id=uuid.uuid4())

    assert counts == {c: 0 for c, _f, _a in _MONGO_PLANS}
    assert dict(mongo.calls) == {}
    assert db.streamed == []
    assert len(db.sql) == 1, "only the run-id read"
