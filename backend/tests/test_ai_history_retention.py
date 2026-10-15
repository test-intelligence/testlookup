"""AIH-06: retention of AI history (owner decision D41).

Retention bugs are data-loss bugs, so what is pinned here is structural:

* the policy: two caps (``ai_versions_per_run`` default 20, ``ai_versions_per_test``
  default 10, 1-100), a partial PUT keeps the other cap, the change is audited;
* the runs clock: the policy purge removes ``ai_run_versions`` of purged runs and
  counts the ``ai_analysis_versions`` rows the run delete cascades; the single-run
  delete leaves no orphan version;
* the cap sweep: a cap of 3 keeps v1 and the newest, NEVER a version cited by a
  published decision report; a citation store that cannot be read prunes NOTHING;
* preview == execute: the preview's ``ai_history_versions`` is exactly what the
  execute removes (one candidate function, ``plan_over_cap``).

Hermetic: Postgres, Mongo and MinIO are faked (the Postgres semantics of the
over-cap query are proved against a real database in
``tests/integration/test_ai_history_retention_postgres.py``).
"""
from __future__ import annotations

import copy
import uuid
from types import SimpleNamespace

import pytest

from app.db.mongo import Collections
from app.services import ai_history_retention as air
from app.services import retention_service as svc
from app.services.ai_history_core import AIHistoryCaps
from app.services.retention_service import EffectiveRetentionPolicy, RetentionValidationError
from tests.test_retention_purge import NOW, PROJECT_ID, _age, _Dml, _FakeDB, _FakeStorage, _policy_row, _Rows

KEEP_RUN = uuid.UUID("aaaaaaaa-0000-0000-0000-00000000000a")
OLD_RUN = uuid.UUID("aaaaaaaa-0000-0000-0000-00000000000b")
KEEP_TC = uuid.UUID("bbbbbbbb-0000-0000-0000-00000000000a")
OLD_TC = uuid.UUID("bbbbbbbb-0000-0000-0000-00000000000b")
PIPES = [uuid.UUID(f"cccccccc-0000-0000-0000-00000000000{i}") for i in range(1, 6)]


def _pid(i: int) -> str:
    return str(PIPES[i - 1])


# ── Fakes ────────────────────────────────────────────────────────────────────


def _in_values(stmt) -> set:
    """The values of the (single) expanding IN parameter of a statement."""
    for value in stmt.compile().params.values():
        if isinstance(value, (list, tuple)):
            return set(value)
    return set()


class _AIDB(_FakeDB):
    """``tests.test_retention_purge._FakeDB`` plus an in-memory
    ``ai_analysis_versions`` table (the shapes ``ai_history_retention`` issues)."""

    def __init__(self, *, versions=(), **kwargs):
        super().__init__(**kwargs)
        self.versions = [dict(v) for v in versions]

    async def execute(self, stmt):
        kind = type(stmt).__name__
        if kind == "Delete" and stmt.table.name == "ai_analysis_versions":
            ids = _in_values(stmt)
            before = len(self.versions)
            self.versions = [v for v in self.versions if v["id"] not in ids]
            self.events.append(("pg_delete", "ai_analysis_versions"))
            return _Dml(before - len(self.versions))
        text = str(stmt)
        if kind == "Select" and "ai_analysis_versions" in text:
            if "GROUP BY" in text:
                params = stmt.compile().params
                cap = next(v for v in params.values() if isinstance(v, int) and not isinstance(v, bool))
                counts: dict = {}
                for v in self.versions:
                    counts[v["test_case_id"]] = counts.get(v["test_case_id"], 0) + 1
                return _Rows([
                    (v["id"], v["test_case_id"], v["test_run_id"], v["version"], v["pipeline_run_id"])
                    for v in self.versions
                    if v["project_id"] == PROJECT_ID and counts[v["test_case_id"]] > cap
                ])
            runs = _in_values(stmt)
            return _Rows([sum(1 for v in self.versions if v["test_run_id"] in runs)])
        return await super().execute(stmt)


class _Cursor:
    def __init__(self, rows):
        self.rows = rows

    def sort(self, *_args, **_kwargs):
        return self

    def limit(self, *_args):
        return self

    async def to_list(self, length=None):
        return list(self.rows if length is None else self.rows[:length])


def _matches(doc: dict, filt: dict) -> bool:
    for key, cond in filt.items():
        if isinstance(cond, dict) and "$in" in cond:
            if doc.get(key) not in cond["$in"]:
                return False
        elif doc.get(key) != cond:
            return False
    return True


class _Coll:
    def __init__(self, name, events, docs=(), fail=()):
        self.name = name
        self.events = events
        self.docs = [dict(d) for d in docs]
        self.fail = set(fail)
        self.delete_filters: list[dict] = []

    def _maybe_fail(self, op):
        if op in self.fail:
            raise RuntimeError(f"mongo down ({self.name}.{op})")

    def find(self, filt, projection=None):
        self._maybe_fail("find")
        rows = [copy.deepcopy(d) for d in self.docs if _matches(d, filt)]
        if projection:
            keep = {k for k, v in projection.items() if v and k != "_id"}
            rows = [{k: v for k, v in r.items() if k in keep} for r in rows]
        return _Cursor(rows)

    def aggregate(self, pipeline):
        self._maybe_fail("aggregate")
        match, group, having = pipeline
        counts: dict = {}
        for d in self.docs:
            if _matches(d, match["$match"]):
                key = (d.get("test_run_id"), d.get("kind"))
                counts[key] = counts.get(key, 0) + 1
        cap = having["$match"]["n"]["$gt"]
        assert group["$group"]["_id"] == {"run": "$test_run_id", "kind": "$kind"}
        return _Cursor([{"_id": {"run": r, "kind": k}, "n": n} for (r, k), n in counts.items() if n > cap])

    async def count_documents(self, filt):
        self._maybe_fail("count")
        self.events.append(("mongo_count", self.name))
        return sum(1 for d in self.docs if _matches(d, filt))

    async def delete_many(self, filt):
        self._maybe_fail("delete")
        self.events.append(("mongo_delete", self.name))
        self.delete_filters.append(filt)
        before = len(self.docs)
        self.docs = [d for d in self.docs if not _matches(d, filt)]
        return SimpleNamespace(deleted_count=before - len(self.docs))


class _Mongo:
    def __init__(self, events, docs: dict | None = None, fail: dict | None = None):
        self.events = events
        self.collections: dict[str, _Coll] = {}
        for name, rows in (docs or {}).items():
            self.collections[name] = _Coll(name, events, rows, (fail or {}).get(name, ()))
        self.fail = fail or {}

    def __getitem__(self, name):
        if name not in self.collections:
            self.collections[name] = _Coll(name, self.events, (), self.fail.get(name, ()))
        return self.collections[name]


def _analysis_version(tc, run, version, pipeline=None) -> dict:
    return {"id": uuid.uuid4(), "test_case_id": tc, "test_run_id": run, "project_id": PROJECT_ID,
            "version": version, "pipeline_run_id": pipeline}


def _run_version(run, kind, version, pipeline=None) -> dict:
    return {"version_id": str(uuid.uuid4()), "test_run_id": str(run), "project_id": str(PROJECT_ID),
            "kind": kind, "version": version, "pipeline_run_id": pipeline, "trigger": "pipeline"}


def _world(*, caps=(3, 3), cited=(2,), fail=None, keep_versions=5):
    """One run past the runs clock (2 AI versions of each store) and one kept run
    with ``keep_versions`` versions of its digest and of its test result."""
    events: list = []
    versions = [
        *[_analysis_version(KEEP_TC, KEEP_RUN, v, PIPES[v - 1]) for v in range(1, keep_versions + 1)],
        _analysis_version(OLD_TC, OLD_RUN, 1),
        _analysis_version(OLD_TC, OLD_RUN, 2),
    ]
    db = _AIDB(
        policy=_policy_row(ai_versions_per_run=caps[0], ai_versions_per_test=caps[1]),
        run_rows=[(OLD_RUN, None, _age(400))],
        tc_rows=[(OLD_TC, OLD_RUN)],
        versions=versions,
        delete_rowcounts={"test_runs": 1},
        events=events,
    )
    mongo = _Mongo(events, docs={
        Collections.AI_RUN_VERSIONS: [
            *[_run_version(KEEP_RUN, "analysis_digest", v, _pid(v)) for v in range(1, keep_versions + 1)],
            _run_version(KEEP_RUN, "run_summary", 1, _pid(1)),
            _run_version(OLD_RUN, "analysis_digest", 1),
            _run_version(OLD_RUN, "run_summary", 1),
        ],
        Collections.DECISION_REPORTS: [
            {"report_id": f"r{v}", "project_id": str(PROJECT_ID), "test_run_id": str(KEEP_RUN),
             "pipeline_run_id": _pid(v), "status": "published", "report_version": 1}
            for v in cited
        ],
    }, fail=fail)
    return db, mongo, _FakeStorage(events), events


def _digest_versions(mongo) -> list[int]:
    return sorted(d["version"] for d in mongo[Collections.AI_RUN_VERSIONS].docs
                  if d["test_run_id"] == str(KEEP_RUN) and d["kind"] == "analysis_digest")


def _test_versions(db) -> list[int]:
    return sorted(v["version"] for v in db.versions if v["test_case_id"] == KEEP_TC)


# ── The policy ───────────────────────────────────────────────────────────────


async def test_the_caps_default_to_20_and_10_without_a_row_or_with_null_columns():
    assert EffectiveRetentionPolicy().as_dict()["ai_versions_per_run"] == 20
    eff = await svc.get_effective_policy(_FakeDB(policy=None), PROJECT_ID)
    assert (eff.ai_versions_per_run, eff.ai_versions_per_test) == (20, 10)
    eff = await svc.get_effective_policy(
        _FakeDB(policy=_policy_row(ai_versions_per_run=None, ai_versions_per_test=None)), PROJECT_ID,
    )
    assert (eff.ai_versions_per_run, eff.ai_versions_per_test) == (20, 10)
    assert eff.ai_history_caps == AIHistoryCaps(per_run=20, per_test=10)


async def test_a_stored_cap_is_the_effective_cap_and_reaches_as_dict():
    eff = await svc.get_effective_policy(
        _FakeDB(policy=_policy_row(ai_versions_per_run=3, ai_versions_per_test=7)), PROJECT_ID,
    )
    assert eff.as_dict()["ai_versions_per_run"] == 3
    assert eff.as_dict()["ai_versions_per_test"] == 7
    assert eff.ai_history_caps == AIHistoryCaps(per_run=3, per_test=7)


async def test_a_partial_put_keeps_the_other_cap_and_audits_the_change():
    row = _policy_row(ai_versions_per_run=None, ai_versions_per_test=4)
    db = _FakeDB(policy=row)
    eff = await svc.upsert_policy(db, project_id=PROJECT_ID, ai_versions_per_run=3)
    assert (eff.ai_versions_per_run, eff.ai_versions_per_test) == (3, 4)
    assert row.ai_versions_per_run == 3 and row.ai_versions_per_test == 4
    audit = next(o for o in db.added if type(o).__name__ == "SettingsAuditLog")
    assert audit.changed_fields["changes"] == {"ai_versions_per_run": {"old": 20, "new": 3}}
    assert db.commits == 0


async def test_restating_the_effective_cap_is_not_a_change():
    db = _FakeDB(policy=_policy_row(ai_versions_per_run=None, ai_versions_per_test=None))
    await svc.upsert_policy(db, project_id=PROJECT_ID, ai_versions_per_run=20, ai_versions_per_test=10)
    audit = next(o for o in db.added if type(o).__name__ == "SettingsAuditLog")
    assert audit.changed_fields["changes"] == {}


async def test_a_new_row_stores_only_the_caps_the_admin_set():
    db = _FakeDB(policy=None)
    eff = await svc.upsert_policy(db, project_id=PROJECT_ID, ai_versions_per_test=5)
    row = next(o for o in db.added if type(o).__name__ == "ProjectRetentionPolicy")
    assert row.ai_versions_per_run is None, "NULL = the code default, not a frozen copy of it"
    assert row.ai_versions_per_test == 5
    assert (eff.ai_versions_per_run, eff.ai_versions_per_test) == (20, 5)
    audit = next(o for o in db.added if type(o).__name__ == "SettingsAuditLog")
    assert audit.changed_fields["changes"]["ai_versions_per_test"] == 5


@pytest.mark.parametrize("field", ["ai_versions_per_run", "ai_versions_per_test"])
@pytest.mark.parametrize("value", [0, 101, -1])
async def test_a_cap_out_of_bounds_is_rejected_and_stages_nothing(field, value):
    db = _FakeDB(policy=None)
    with pytest.raises(RetentionValidationError, match=field):
        await svc.upsert_policy(db, project_id=PROJECT_ID, **{field: value})
    assert db.added == []


@pytest.mark.parametrize("value", [0, 101])
def test_the_write_schema_422s_a_cap_out_of_bounds(value):
    from pydantic import ValidationError

    from app.models.schemas import RetentionPolicyWrite

    for field in ("ai_versions_per_run", "ai_versions_per_test"):
        with pytest.raises(ValidationError):
            RetentionPolicyWrite(**{field: value})
    ok = RetentionPolicyWrite(ai_versions_per_run=1, ai_versions_per_test=100)
    assert (ok.ai_versions_per_run, ok.ai_versions_per_test) == (1, 100)


async def test_the_put_route_passes_both_caps_and_reads_them_back(monkeypatch):
    from app.models.schemas import RetentionPolicyWrite
    from app.routers import retention as router

    seen: dict = {}

    async def _upsert(db, **kwargs):
        seen.update(kwargs)
        return EffectiveRetentionPolicy(ai_versions_per_run=3, ai_versions_per_test=4, source="custom")

    async def _last(db, project_id):
        return None

    monkeypatch.setattr(router.svc, "upsert_policy", _upsert)
    monkeypatch.setattr(router.svc, "get_last_purge", _last)
    user = SimpleNamespace(id=uuid.uuid4(), full_name="Admin", email="a@x")
    out = await router.put_retention_policy(
        PROJECT_ID, RetentionPolicyWrite(ai_versions_per_run=3), db=object(), current_user=user, _=user,
    )
    assert seen["ai_versions_per_run"] == 3 and seen["ai_versions_per_test"] is None
    assert (out.ai_versions_per_run, out.ai_versions_per_test) == (3, 4)


def _retention_client(db):
    """The real retention router, its auth dependencies overridden (the guards
    themselves are covered by the authorization ratchet)."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.core.deps import get_db
    from app.routers import retention as router

    app = FastAPI()
    app.include_router(router.router)
    user = SimpleNamespace(id=uuid.uuid4(), full_name="Admin", email="admin@example.com")
    for route in router.router.routes:
        if not getattr(route, "path", "").endswith("/retention-policy"):
            continue
        for dep in route.dependant.dependencies:
            app.dependency_overrides[dep.call] = (lambda: db) if dep.call is get_db else (lambda: user)
    return TestClient(app)


@pytest.mark.parametrize("body", [
    {"ai_versions_per_run": 0}, {"ai_versions_per_run": 101},
    {"ai_versions_per_test": 0}, {"ai_versions_per_test": 101},
])
def test_the_put_route_answers_422_for_a_cap_out_of_bounds(body):
    db = _FakeDB(policy=None)
    response = _retention_client(db).put(f"/api/v1/projects/{PROJECT_ID}/retention-policy", json=body)
    assert response.status_code == 422, response.text
    assert db.added == []


def test_the_put_route_round_trips_the_caps_over_http():
    db = _FakeDB(policy=_policy_row(ai_versions_per_run=None, ai_versions_per_test=None))
    client = _retention_client(db)
    response = client.put(f"/api/v1/projects/{PROJECT_ID}/retention-policy", json={"ai_versions_per_run": 3})
    assert response.status_code == 200, response.text
    assert (response.json()["ai_versions_per_run"], response.json()["ai_versions_per_test"]) == (3, 10)
    got = client.get(f"/api/v1/projects/{PROJECT_ID}/retention-policy").json()
    assert (got["ai_versions_per_run"], got["ai_versions_per_test"]) == (3, 10)


async def test_the_get_route_reports_the_defaults(monkeypatch):
    from app.routers import retention as router

    async def _last(db, project_id):
        return None

    monkeypatch.setattr(router.svc, "get_last_purge", _last)
    user = SimpleNamespace(id=uuid.uuid4())
    out = await router.get_retention_policy(PROJECT_ID, db=_FakeDB(policy=None), current_user=user, _=user)
    assert (out.ai_versions_per_run, out.ai_versions_per_test) == (20, 10)


# ── The cap sweep (one candidate function) ───────────────────────────────────


async def test_a_cap_of_3_keeps_v1_and_the_two_newest():
    db, mongo, _, _ = _world(caps=(3, 3), cited=())
    plan = await air.plan_over_cap(db, mongo, project_id=PROJECT_ID, caps=AIHistoryCaps(3, 3))
    assert not plan.unreadable
    await air.execute_over_cap(db, mongo, plan)
    assert _digest_versions(mongo) == [1, 4, 5]
    assert _test_versions(db) == [1, 4, 5]


async def test_a_version_cited_by_a_published_report_is_never_pruned():
    db, mongo, _, _ = _world(caps=(3, 3), cited=(2,))
    plan = await air.plan_over_cap(db, mongo, project_id=PROJECT_ID, caps=AIHistoryCaps(3, 3))
    await air.execute_over_cap(db, mongo, plan)
    assert _digest_versions(mongo) == [1, 2, 5], "v1 and the cited v2 survive; v3, v4 go"
    assert _test_versions(db) == [1, 2, 5]


async def test_protected_versions_alone_over_the_cap_delete_nothing_more():
    db, mongo, _, _ = _world(caps=(1, 1), cited=(2, 3, 4, 5))
    plan = await air.plan_over_cap(
        db, mongo, project_id=PROJECT_ID, caps=AIHistoryCaps(1, 1), exclude_run_ids=[OLD_RUN],
    )
    assert plan.total == 0
    await air.execute_over_cap(db, mongo, plan)
    assert _digest_versions(mongo) == [1, 2, 3, 4, 5]


@pytest.mark.parametrize("fail", [
    {Collections.DECISION_REPORTS: {"find"}},
    {Collections.AI_RUN_VERSIONS: {"aggregate"}},
    {Collections.AI_RUN_VERSIONS: {"find"}},
])
async def test_an_unreadable_mongo_side_prunes_nothing_in_either_store(fail):
    db, mongo, _, _ = _world(caps=(3, 3), cited=(), fail=fail)
    plan = await air.plan_over_cap(db, mongo, project_id=PROJECT_ID, caps=AIHistoryCaps(3, 3))
    assert plan.unreadable is True
    assert plan.analysis_version_ids == [] and plan.run_version_docs == []
    out = await air.execute_over_cap(db, mongo, plan)
    assert out == {"analysis_versions_over_cap": 0, "run_versions_over_cap": 0}
    assert _test_versions(db) == [1, 2, 3, 4, 5], "Postgres is not pruned either: the citations were unknown"
    assert _digest_versions(mongo) == [1, 2, 3, 4, 5]


async def test_nothing_over_the_cap_reads_no_citations():
    db, mongo, _, _ = _world(caps=(20, 10), cited=(), fail={Collections.DECISION_REPORTS: {"find"}})
    plan = await air.plan_over_cap(db, mongo, project_id=PROJECT_ID, caps=AIHistoryCaps(20, 10))
    assert (plan.total, plan.unreadable) == (0, False)


async def test_runs_about_to_be_purged_are_left_to_their_clock():
    db, mongo, _, _ = _world(caps=(1, 1), cited=())
    plan = await air.plan_over_cap(
        db, mongo, project_id=PROJECT_ID, caps=AIHistoryCaps(1, 1), exclude_run_ids=[OLD_RUN],
    )
    assert {doc.test_run_id for doc in plan.run_version_docs} == {str(KEEP_RUN)}
    planned = {v["id"] for v in db.versions if v["test_run_id"] == OLD_RUN}
    assert not planned & set(plan.analysis_version_ids)


async def test_a_citation_matched_by_run_protects_even_without_a_project_id():
    """A report is matched by its run as well: a report document that lacked
    ``project_id`` must not unprotect the version it cites."""
    db, mongo, _, _ = _world(caps=(3, 3), cited=())
    mongo[Collections.DECISION_REPORTS].docs.append(
        {"report_id": "legacy", "test_run_id": str(KEEP_RUN), "pipeline_run_id": _pid(3), "status": "published"}
    )
    plan = await air.plan_over_cap(db, mongo, project_id=PROJECT_ID, caps=AIHistoryCaps(3, 3))
    await air.execute_over_cap(db, mongo, plan)
    assert _digest_versions(mongo) == [1, 3, 5]


# ── run_purge: runs clock + caps, preview == execute ─────────────────────────


async def test_preview_counts_exactly_what_the_execute_removes():
    db, mongo, storage, _ = _world(caps=(3, 3), cited=(2,))
    preview = await svc.run_purge(db, project_id=PROJECT_ID, mode="preview", now=NOW, mongo=mongo, storage=storage)
    candidates = preview["candidates"]
    # 2 docs of the purged run (runs clock) + 2 cascaded test versions + 2 + 2 over the cap.
    assert candidates["ai_history_versions"] == 8
    assert candidates["mongo_docs"][Collections.AI_RUN_VERSIONS] == 2
    assert _digest_versions(mongo) == [1, 2, 3, 4, 5], "preview deletes nothing"

    executed = await svc.run_purge(db, project_id=PROJECT_ID, mode="execute", now=NOW, mongo=mongo, storage=storage)
    counts = executed["counts"]
    removed = (
        counts["mongo"][Collections.AI_RUN_VERSIONS]
        + counts["ai_history"]["analysis_versions_cascaded"]
        + counts["ai_history"]["analysis_versions_over_cap"]
        + counts["ai_history"]["run_versions_over_cap"]
    )
    assert removed == candidates["ai_history_versions"] == 8
    assert counts["ai_history"] == {
        "analysis_versions_cascaded": 2, "analysis_versions_over_cap": 2, "run_versions_over_cap": 2,
    }
    assert _digest_versions(mongo) == [1, 2, 5]
    assert not [d for d in mongo[Collections.AI_RUN_VERSIONS].docs if d["test_run_id"] == str(OLD_RUN)], (
        "the purged run's versions are gone"
    )
    assert db.commits == 0


async def test_the_purge_takes_the_purged_runs_versions_on_the_runs_clock():
    db, mongo, storage, _ = _world(caps=(20, 10), cited=())
    await svc.run_purge(db, project_id=PROJECT_ID, mode="execute", now=NOW, mongo=mongo, storage=storage)
    assert mongo[Collections.AI_RUN_VERSIONS].delete_filters[0] == {"test_run_id": {"$in": [str(OLD_RUN)]}}
    assert _digest_versions(mongo) == [1, 2, 3, 4, 5], "under the cap: the kept run loses nothing"


async def test_an_unreadable_citation_store_prunes_nothing_through_the_purge():
    db, mongo, storage, _ = _world(caps=(3, 3), cited=(), fail={Collections.DECISION_REPORTS: {"find"}})
    preview = await svc.run_purge(db, project_id=PROJECT_ID, mode="preview", now=NOW, mongo=mongo, storage=storage)
    assert preview["candidates"]["ai_history_versions"] == 4, "only the runs clock: 2 docs + 2 cascaded rows"
    executed = await svc.run_purge(db, project_id=PROJECT_ID, mode="execute", now=NOW, mongo=mongo, storage=storage)
    assert executed["counts"]["ai_history"]["analysis_versions_over_cap"] == 0
    assert executed["counts"]["ai_history"]["run_versions_over_cap"] == 0
    assert _digest_versions(mongo) == [1, 2, 3, 4, 5]
    assert _test_versions(db) == [1, 2, 3, 4, 5]


async def test_the_over_cap_sweep_runs_after_the_run_delete():
    db, mongo, storage, events = _world(caps=(3, 3), cited=())
    await svc.run_purge(db, project_id=PROJECT_ID, mode="execute", now=NOW, mongo=mongo, storage=storage)
    run_delete = events.index(("pg_delete", "test_runs"))
    over_cap = events.index(("pg_delete", "ai_analysis_versions"))
    assert run_delete < over_cap


async def test_the_single_run_delete_leaves_no_orphan_version():
    db, mongo, storage, _ = _world(caps=(20, 10), cited=())
    cand, plan = await svc.resolve_run_candidates(db, project_id=PROJECT_ID, run_id=KEEP_RUN, minio_prefix=None)
    await svc.execute_candidates(
        db, project_id=PROJECT_ID, cand=cand, plan=plan, mongo=mongo, storage=storage,
        cache_counts={"redis": None, "semantic": None}, search_index_documents=None, now=NOW,
    )
    assert not [d for d in mongo[Collections.AI_RUN_VERSIONS].docs if d["test_run_id"] == str(KEEP_RUN)]
    assert [d["test_run_id"] for d in mongo[Collections.AI_RUN_VERSIONS].docs] == [str(OLD_RUN)] * 2


def test_the_plan_table_puts_ai_run_versions_on_the_runs_clock():
    plans = {name: (field, attr) for name, field, attr in svc._MONGO_PLANS}
    assert plans[Collections.AI_RUN_VERSIONS] == ("test_run_id", "purge_run_strs")


def test_storage_accounting_counts_the_collection():
    from app.services import storage_accounting_service as sas

    assert Collections.AI_RUN_VERSIONS in sas._RUN_SCOPED_COLLECTIONS
