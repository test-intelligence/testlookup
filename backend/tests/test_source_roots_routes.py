"""FAIL-08: ``/api/v1/projects/{project_id}/ownership/source-roots`` (GET, PUT,
POST preview) on a fake session, with the REAL guards.

Who may read (any member), who may write (QA_LEAD+, ``allow_project_key`` with
the path binding), the 422 texts, the ``project.updated`` ledger row (only
when the stored list changes, ``context.changed == "Java source roots"``, the
counts in before / after) and the preview that writes nothing.

Contract: ``docs/deferred-scope-2026-10-13/work/W07-contract.md`` §2.8, §1.6.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
import pytest

PROJECT = uuid.UUID("00000000-0000-4000-8000-00000000f008")
OTHER = uuid.UUID("99999999-9999-4999-8999-99999999f008")
BASE = f"/api/v1/projects/{PROJECT}/ownership/source-roots"
JAVA = (
    "java.lang.AssertionError\n"
    "\tat org.junit.Assert.fail(Assert.java:89)\n"
    "\tat com.acme.pay.CheckoutTest.pays(CheckoutTest.java:42)\n"
)


def _user(role: str, *, bound_project=None):
    user = SimpleNamespace(id=uuid.uuid4(), role=role, is_active=True, username="lead",
                           full_name="Priya Nair", email="p@x.com")
    if bound_project is not None:
        user._testlookup_api_key_project_id = bound_project
    return user


class _Res:
    def __init__(self, value=None, rows=()):
        self._value, self._rows = value, list(rows)

    def scalar_one_or_none(self):
        return self._value

    def scalars(self):
        return SimpleNamespace(all=lambda: list(self._rows))

    def all(self):
        return list(self._rows)


class _RouteDB:
    """Answers the membership guard, the project row, its stored roots, the path
    rules and the failure sample; records every write."""

    def __init__(self, state):
        self.state = state
        self.added: list = []
        self.commits = 0
        self.flushes = 0

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        self.flushes += 1

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        return None

    async def scalar(self, stmt, *a, **k):
        sql = str(stmt)
        assert "projects.source_roots" in sql, sql
        return self.state.project.source_roots

    async def execute(self, stmt, params=None):
        sql = str(stmt)
        if "FROM project_members" in sql:
            ids = {v for v in stmt.compile().params.values() if isinstance(v, uuid.UUID)}
            return _Res(uuid.uuid4() if self.state.member_of & ids else None)
        if "FROM service_ownership_rules" in sql:
            return _Res(rows=self.state.rules)
        if "FROM test_cases" in sql:
            return _Res(rows=self.state.sample)
        if "FROM projects" in sql:
            return _Res(self.state.project)
        raise AssertionError(f"unexpected query: {sql[:200]}")


@pytest.fixture
def api():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.core.deps import get_current_active_user
    from app.db.postgres import get_db
    from app.routers import ownership

    state = SimpleNamespace(
        user=_user("QA_LEAD"),
        member_of={PROJECT},
        project=SimpleNamespace(id=PROJECT, name="Payments", source_roots=None),
        rules=[SimpleNamespace(
            id=uuid.uuid4(), match_type="path", match_pattern="payments/**", service_name="CODEOWNERS",
            team_name="@alice", team_contact="@alice", priority=0, is_active=True, created_at=None,
        )],
        sample=[
            SimpleNamespace(test_name="pays", stack_trace=JAVA, error_message=None),
            SimpleNamespace(test_name="py", stack_trace='File "web/x.py", line 3, in f', error_message=None),
        ],
    )
    db = _RouteDB(state)

    async def _db():
        yield db

    app = FastAPI()
    app.include_router(ownership.router)
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_active_user] = lambda: state.user
    return SimpleNamespace(http=TestClient(app, raise_server_exceptions=False), state=state, db=db)


ROOTS = [
    {"prefix": "com.acme.pay", "root": "payments/src/test/java"},
    {"prefix": "", "root": "src/test/java"},
]


def _ledger(db):
    return [row for row in db.added if getattr(row, "event_type", None) == "project.updated"]


# ── GET ──────────────────────────────────────────────────────────────────────


def test_any_member_reads_the_roots(api):
    api.state.user = _user("VIEWER")
    api.state.project.source_roots = [*ROOTS, {"prefix": "", "root": "../bad"}]   # a hand-edited row
    res = api.http.get(BASE)
    assert res.status_code == 200
    assert res.json() == {"roots": ROOTS, "max_roots": 20}   # the bad entry is dropped on read


def test_no_roots_reads_an_empty_list(api):
    res = api.http.get(BASE)
    assert res.status_code == 200 and res.json() == {"roots": [], "max_roots": 20}


def test_a_non_member_cannot_read(api):
    api.state.user = _user("QA_ENGINEER")
    api.state.member_of = {OTHER}
    assert api.http.get(BASE).status_code == 403


# ── PUT ──────────────────────────────────────────────────────────────────────


def test_a_qa_lead_saves_and_the_ledger_records_the_change(api):
    res = api.http.put(BASE, json={"roots": [
        {"prefix": " com.acme.pay ", "root": "payments/src/test/java/"},
        {"root": "src/test/java"},
    ]})
    assert res.status_code == 200, res.text
    assert res.json() == {"roots": ROOTS, "max_roots": 20}
    assert api.state.project.source_roots == ROOTS
    assert api.db.commits == 1
    (row,) = _ledger(api.db)
    assert row.context["changed"] == "Java source roots"
    assert row.summary == "Project settings changed: Java source roots"
    assert row.entity_id == str(PROJECT)
    assert row.diff["changed_fields"] == ["source_roots"]
    assert "source_roots" in str(row.diff)
    assert row.diff["before"]["source_roots"] == 0 and row.diff["after"]["source_roots"] == 2


def test_swapping_two_roots_for_two_others_still_names_the_field(api):
    api.state.project.source_roots = [{"prefix": "", "root": "a"}, {"prefix": "x", "root": "b"}]
    assert api.http.put(BASE, json={"roots": ROOTS}).status_code == 200
    (row,) = _ledger(api.db)
    assert row.diff["changed_fields"] == ["source_roots"]   # equal counts, a real change


def test_resending_the_same_list_writes_no_ledger_row(api):
    api.state.project.source_roots = list(ROOTS)
    res = api.http.put(BASE, json={"roots": ROOTS})
    assert res.status_code == 200
    assert _ledger(api.db) == [] and api.db.commits == 0


def test_removing_every_root_restores_none(api):
    api.state.project.source_roots = list(ROOTS)
    res = api.http.put(BASE, json={"roots": []})
    assert res.status_code == 200 and res.json()["roots"] == []
    assert api.state.project.source_roots is None
    (row,) = _ledger(api.db)
    assert row.diff["before"]["source_roots"] == 2 and row.diff["after"]["source_roots"] == 0


@pytest.mark.parametrize("role", ["TESTER", "QA_ENGINEER", "VIEWER"])
def test_below_qa_lead_cannot_write(api, role):
    api.state.user = _user(role)
    res = api.http.put(BASE, json={"roots": ROOTS})
    assert res.status_code == 403
    assert api.state.project.source_roots is None and api.db.added == []


def test_a_non_member_qa_lead_cannot_write(api):
    api.state.member_of = {OTHER}
    assert api.http.put(BASE, json={"roots": ROOTS}).status_code == 403
    assert api.state.project.source_roots is None


def test_an_api_key_bound_to_another_project_is_refused(api):
    api.state.user = _user("ADMIN", bound_project=OTHER)
    assert api.http.put(BASE, json={"roots": ROOTS}).status_code == 403
    assert api.http.get(BASE).status_code == 403
    assert api.state.project.source_roots is None


@pytest.mark.parametrize("body, detail", [
    ({"roots": [{"prefix": "", "root": "../x"}]},
     'Source root "../x" must be a relative path inside the repository (letters, digits, . _ - /; no ..).'),
    ({"roots": [{"prefix": "com..acme", "root": "src"}]},
     'Package prefix "com..acme" must be a dotted Java package, e.g. com.acme.pay.'),
    ({"roots": [{"prefix": "a", "root": "x"}, {"prefix": "a", "root": "y"}]},
     'Package prefix "a" is listed twice.'),
    ({"roots": [{"prefix": f"p{i}", "root": "src"} for i in range(21)]}, "At most 20 source roots."),
])
def test_a_refused_list_answers_its_one_text(api, body, detail):
    res = api.http.put(BASE, json=body)
    assert res.status_code == 422
    assert res.json()["detail"] == detail
    assert api.state.project.source_roots is None and api.db.added == [] and api.db.commits == 0


def test_an_extra_field_is_refused(api):
    res = api.http.put(BASE, json={"roots": [{"prefix": "", "root": "src", "glob": "**"}]})
    assert res.status_code == 422
    res = api.http.put(BASE, json={"roots": [], "default": "src"})
    assert res.status_code == 422


# ── POST preview ─────────────────────────────────────────────────────────────


def test_preview_counts_the_edited_list_and_writes_nothing(api):
    api.state.user = _user("QA_ENGINEER")   # any member may preview
    res = api.http.post(f"{BASE}/preview", json={"roots": ROOTS})
    assert res.status_code == 200, res.text
    assert res.json() == {
        "sampled": 2, "java_frames": 1, "located": 1, "matched": 1, "lookback_days": 30,
        "examples": [{"test_name": "pays", "path": "payments/src/test/java/com/acme/pay/CheckoutTest.java"}],
    }
    assert api.state.project.source_roots is None
    assert api.db.added == [] and api.db.commits == 0 and api.db.flushes == 0


def test_preview_refuses_what_the_put_refuses(api):
    res = api.http.post(f"{BASE}/preview", json={"roots": [{"prefix": "", "root": "/abs"}]})
    assert res.status_code == 422
    assert res.json()["detail"].startswith('Source root "/abs" must be a relative path')


def test_preview_by_a_non_member_is_refused(api):
    api.state.user = _user("QA_LEAD")
    api.state.member_of = set()
    assert api.http.post(f"{BASE}/preview", json={"roots": ROOTS}).status_code == 403


def test_the_coverage_route_reports_the_root_count(api):
    api.state.project.source_roots = list(ROOTS)
    res = api.http.get(f"/api/v1/projects/{PROJECT}/ownership/codeowners/coverage")
    assert res.status_code == 200
    body = res.json()
    assert body["source_roots"] == 2
    assert body["located"] == 2 and body["matched"] == 1 and body["coverage_pct"] == 50.0


def test_no_unexpected_writes_from_reads(api):
    api.state.project.source_roots = list(ROOTS)
    api.http.get(BASE)
    api.http.get(f"/api/v1/projects/{PROJECT}/ownership/codeowners/coverage")
    assert api.db.added == [] and api.db.commits == 0
