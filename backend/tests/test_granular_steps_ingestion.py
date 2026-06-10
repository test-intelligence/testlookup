"""Phase 1 granular test-step capture — parsers, ingestion snapshot, endpoint.

Covers:
  * Allure recursive step-tree flattening (nested steps + attachments + status/
    duration/assertion mapping).
  * pytest setup/call/teardown pseudo-step synthesis.
  * LATEST-RUN-ONLY delete-then-insert snapshot in ``_upsert_test_case`` and its
    idempotency on re-ingest of the same run.
  * The read endpoint service builds the ordered/nested tree; the router wires
    ``require_run_access`` (PROVIDED run_id verified — IDOR ratchet).
  * Migration 0093 mappers/columns are inspectable and the downgrade is real.

Unit-level: a smart in-memory fake DB stands in for Postgres (the ORM uses
JSONB/UUID/TSVECTOR, Postgres-only). The fake models exactly the SELECT/DELETE/
INSERT surface ``_upsert_test_case`` + ``get_or_create_canonical`` touch.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

pytest.importorskip("sqlalchemy")

from app.models.postgres import (  # noqa: E402
    CanonicalTestCase,
    TestAttachment,
    TestCase,
    TestStatus,
    TestStep,
)
from app.services import ingestion as svc  # noqa: E402
from app.services.allure_parser import parse_allure_result  # noqa: E402
from app.services.pytest_parser import parse_pytest_json  # noqa: E402


# ─────────────────────────── Parser unit tests ───────────────────────────


def test_allure_recursive_step_tree_flattening():
    raw = {
        "name": "test_checkout",
        "fullName": "shop.test_checkout",
        "status": "failed",
        "start": 1000,
        "stop": 1500,
        "statusDetails": {"message": "boom", "trace": "Traceback..."},
        "labels": [{"name": "suite", "value": "Checkout"}],
        "attachments": [
            {"name": "screenshot", "source": "abc-attachment.png", "type": "image/png"},
        ],
        "steps": [
            {
                "name": "open cart",
                "status": "passed",
                "start": 1000,
                "stop": 1100,
                "parameters": [{"name": "url", "value": "/cart"}],
                "steps": [
                    {
                        "name": "click checkout",
                        "status": "failed",
                        "start": 1100,
                        "stop": 1200,
                        "statusDetails": {"message": "no button", "trace": "tr"},
                        "attachments": [
                            {"name": "dom", "source": "d.html", "type": "text/html"},
                        ],
                    },
                ],
            },
            {"name": "verify total", "status": "skipped"},
        ],
    }
    parsed = parse_allure_result(raw, "run-1", "uploads/run-1/abc-result.json")

    assert parsed["status"] == "failed"
    assert parsed["stack_trace"] == "Traceback..."
    # Test-level attachment normalised to the common shape.
    assert parsed["attachments"] == [
        {"name": "screenshot", "source_ref": "abc-attachment.png", "media_type": "image/png"},
    ]

    steps = parsed["steps"]
    assert len(steps) == 2  # two top-level steps
    top = steps[0]
    assert top["name"] == "open cart"
    assert top["status"] == "PASSED"
    assert top["duration_ms"] == 100
    assert top["start_ms"] == 1000
    assert top["parameters"] == [{"name": "url", "value": "/cart"}]

    # Nested child carries mapped status + assertion message/trace + attachment.
    child = top["steps"][0]
    assert child["name"] == "click checkout"
    assert child["status"] == "FAILED"
    assert child["assertion_message"] == "no button"
    assert child["assertion_trace"] == "tr"
    assert child["attachments"] == [
        {"name": "dom", "source_ref": "d.html", "media_type": "text/html"},
    ]
    assert steps[1]["status"] == "SKIPPED"


def test_pytest_phase_pseudo_steps():
    payload = {
        "tests": [
            {
                "nodeid": "tests/test_x.py::test_login",
                "outcome": "failed",
                "setup": {"duration": 0.01, "outcome": "passed"},
                "call": {"duration": 1.20, "outcome": "failed", "longrepr": "assert 1 == 2"},
                "teardown": {"duration": 0.0, "outcome": "passed"},
            }
        ]
    }
    import json
    cases = parse_pytest_json(json.dumps(payload), "run-1")
    assert len(cases) == 1
    steps = cases[0]["steps"]
    assert [s["name"] for s in steps] == ["setup", "call", "teardown"]
    setup, call, teardown = steps
    assert setup["status"] == "PASSED"
    assert setup["duration_ms"] == 10
    assert call["status"] == "FAILED"
    assert call["duration_ms"] == 1200
    assert call["assertion_trace"] == "assert 1 == 2"
    assert call["keyword"] == "call"
    assert teardown["status"] == "PASSED"
    # status vocab guard — every synthesized step status is in the strict set.
    assert all(s["status"] in {"PASSED", "FAILED", "SKIPPED", "BROKEN", "UNKNOWN"} for s in steps)


def test_allure_step_tree_depth_and_node_caps():
    """Untrusted ``-result.json`` hardening: a pathologically deep/wide step tree
    must not RecursionError or emit an unbounded number of nodes."""
    from app.services import allure_parser as ap

    # Build a chain far deeper than the depth cap (would also be a recursion
    # hazard for the writer). The parser must truncate, not recurse forever.
    deep: dict = {"name": "leaf", "status": "passed", "steps": []}
    node = deep
    for i in range(ap._MAX_STEP_DEPTH + 50):
        node = {"name": f"s{i}", "status": "passed", "steps": [node]}

    raw = {"name": "t", "status": "passed", "steps": [node]}
    parsed = parse_allure_result(raw, "run-1", "uploads/run-1/x-result.json")

    def _max_depth(steps, d=0):
        if not steps:
            return d
        return max(_max_depth(s["steps"], d + 1) for s in steps)

    assert _max_depth(parsed["steps"]) <= ap._MAX_STEP_DEPTH + 1

    # Wide tree: far more siblings than the node cap → emitted count is bounded.
    wide = {
        "name": "t",
        "status": "passed",
        "steps": [
            {"name": f"n{i}", "status": "passed"}
            for i in range(ap._MAX_STEP_NODES + 500)
        ],
    }
    parsed_wide = parse_allure_result(wide, "run-1", "uploads/run-1/y-result.json")

    def _count(steps):
        return sum(1 + _count(s["steps"]) for s in steps)

    assert _count(parsed_wide["steps"]) <= ap._MAX_STEP_NODES


# ─────────────────────────── Fake DB harness ───────────────────────────


class _FakeDB:
    """Models the SELECT/DELETE/INSERT surface the snapshot path exercises."""

    def __init__(self, project_id, *, default_suite_id=None):
        self.project_id = project_id
        self.default_suite_id = default_suite_id or uuid.uuid4()
        self.canonicals: list[CanonicalTestCase] = []
        self.steps: list[TestStep] = []
        self.attachments: list[TestAttachment] = []
        self.history: list = []
        self._pending: list = []

    # begin_nested() is used by get_or_create_canonical / suite helpers.
    def begin_nested(self):
        db = self

        class _Ctx:
            async def __aenter__(self_inner):
                return self_inner

            async def __aexit__(self_inner, *exc):
                return False

        return _Ctx()

    def add(self, obj):
        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()
        if isinstance(obj, CanonicalTestCase):
            self.canonicals.append(obj)
        elif isinstance(obj, TestStep):
            self.steps.append(obj)
        elif isinstance(obj, TestAttachment):
            self.attachments.append(obj)
        else:
            self.history.append(obj)

    async def flush(self):
        return None

    async def execute(self, stmt):
        sql = str(stmt).lower()
        res = MagicMock()

        if sql.startswith("delete"):
            if "test_attachments" in sql:
                self.attachments.clear()
            elif "test_steps" in sql:
                self.steps.clear()
            return res

        if "canonical_test_cases" in sql:
            row = self.canonicals[0] if self.canonicals else None
            res.scalar_one_or_none = MagicMock(return_value=row)
            res.scalar_one = MagicMock(return_value=row)
            return res

        if "from projects" in sql or "projects." in sql:
            proj = SimpleNamespace(id=self.project_id, name="Proj")
            res.scalar_one_or_none = MagicMock(return_value=proj)
            return res

        if "test_suites" in sql:
            suite = SimpleNamespace(id=self.default_suite_id, name="Checkout")
            res.scalar_one_or_none = MagicMock(return_value=suite)
            res.scalar_one = MagicMock(return_value=suite)
            return res

        if "test_case_history" in sql:
            res.scalar_one_or_none = MagicMock(return_value=None)
            return res

        # test_cases existence probe — caller passes existing= so unused.
        res.scalar_one_or_none = MagicMock(return_value=None)
        return res


def _allure_case_with_steps():
    return {
        "test_name": "test_checkout",
        "class_name": "Checkout",
        "suite_name": "Checkout",
        "status": "failed",
        "duration_ms": 500,
        "error_message": "boom",
        "stack_trace": "Traceback",
        "attachments": [
            {"name": "shot", "source_ref": "a.png", "media_type": "image/png"},
        ],
        "steps": [
            {
                "name": "open cart", "keyword": None, "status": "PASSED",
                "start_ms": 1000, "duration_ms": 100,
                "assertion_message": None, "assertion_trace": None,
                "expected": None, "actual": None, "parameters": [],
                "attachments": [],
                "steps": [
                    {
                        "name": "click", "keyword": None, "status": "FAILED",
                        "start_ms": 1100, "duration_ms": 50,
                        "assertion_message": "no button", "assertion_trace": "tr",
                        "expected": None, "actual": None, "parameters": [],
                        "attachments": [
                            {"name": "dom", "source_ref": "d.html", "media_type": "text/html"},
                        ],
                        "steps": [],
                    },
                ],
            },
        ],
    }


# ─────────────────────────── Ingestion snapshot ───────────────────────────


@pytest.mark.asyncio
async def test_snapshot_inserts_nested_steps_and_attachments():
    project_id = uuid.uuid4()
    run = SimpleNamespace(id=uuid.uuid4(), project_id=project_id)
    db = _FakeDB(project_id)
    tc = TestCase(test_run_id=run.id, test_fingerprint="fp1", test_name="test_checkout")

    with patch(
        "app.services.privacy_service.sanitize_for_persistence",
        side_effect=lambda s: s,
    ):
        await svc._upsert_test_case(
            db, _allure_case_with_steps(), run, existing=tc, fingerprint="fp1",
        )

    # Two steps (parent + nested child), depth tracked.
    assert len(db.steps) == 2
    by_name = {s.name: s for s in db.steps}
    assert by_name["open cart"].depth == 0
    assert by_name["click"].depth == 1
    assert by_name["click"].parent_step_id == by_name["open cart"].id
    assert by_name["click"].status == "FAILED"
    # Ordinals are a stable pre-order sequence.
    assert {s.ordinal for s in db.steps} == {0, 1}

    # One test-level + one step-level attachment.
    test_level = [a for a in db.attachments if a.test_step_id is None]
    step_level = [a for a in db.attachments if a.test_step_id is not None]
    assert len(test_level) == 1 and test_level[0].name == "shot"
    assert len(step_level) == 1 and step_level[0].source_ref == "d.html"

    # Per-run metadata columns set on the TestCase row.
    assert tc.step_count == 1  # one top-level step
    assert tc.stack_trace == "Traceback"


@pytest.mark.asyncio
async def test_upsert_links_canonical_id_on_sentinel_path():
    """The per-run TestCase must be linked to its canonical anchor at WRITE time.

    The read path (get_test_steps_tree) resolves steps via
    ``tc.canonical_test_case_id``. On the MinIO/sentinel ingest path
    (``process_sentinel``) ``sync_canonical_test_cases`` never runs, so without
    setting the link in ``_persist_step_snapshot`` the column stays NULL and the
    steps endpoint returns empty forever. Assert ``_upsert_test_case`` populates
    it (and that it equals the canonical the snapshot is keyed on).
    """
    project_id = uuid.uuid4()
    run = SimpleNamespace(id=uuid.uuid4(), project_id=project_id)
    db = _FakeDB(project_id)
    tc = TestCase(test_run_id=run.id, test_fingerprint="fp1", test_name="test_checkout")

    with patch(
        "app.services.privacy_service.sanitize_for_persistence",
        side_effect=lambda s: s,
    ):
        await svc._upsert_test_case(
            db, _allure_case_with_steps(), run, existing=tc, fingerprint="fp1",
        )

    assert len(db.canonicals) == 1
    assert tc.canonical_test_case_id is not None
    assert tc.canonical_test_case_id == db.canonicals[0].id
    # And every persisted step/attachment is keyed on that same anchor — so the
    # read path that fetches by tc.canonical_test_case_id finds them.
    assert all(s.canonical_test_case_id == tc.canonical_test_case_id for s in db.steps)
    assert all(a.canonical_test_case_id == tc.canonical_test_case_id for a in db.attachments)


@pytest.mark.asyncio
async def test_latest_run_only_delete_then_insert_idempotent():
    project_id = uuid.uuid4()
    run = SimpleNamespace(id=uuid.uuid4(), project_id=project_id)
    db = _FakeDB(project_id)
    tc = TestCase(test_run_id=run.id, test_fingerprint="fp1", test_name="test_checkout")

    with patch(
        "app.services.privacy_service.sanitize_for_persistence",
        side_effect=lambda s: s,
    ):
        await svc._upsert_test_case(
            db, _allure_case_with_steps(), run, existing=tc, fingerprint="fp1",
        )
        steps_after_first = len(db.steps)
        atts_after_first = len(db.attachments)
        canonicals_after_first = len(db.canonicals)

        # Re-ingest the SAME run/test → delete-then-insert must overwrite, not
        # accumulate, and must reuse the existing canonical anchor.
        await svc._upsert_test_case(
            db, _allure_case_with_steps(), run, existing=tc, fingerprint="fp1",
        )

    assert len(db.steps) == steps_after_first == 2
    assert len(db.attachments) == atts_after_first == 2
    assert len(db.canonicals) == canonicals_after_first == 1


@pytest.mark.asyncio
async def test_step_parameters_expected_actual_redacted():
    """PII boundary: ``parameters`` (test input fixtures), ``expected``/``actual``
    (often echo response bodies) and attachment name/source_ref must be redacted
    at persist, like assertion_message/trace already are."""
    project_id = uuid.uuid4()
    run = SimpleNamespace(id=uuid.uuid4(), project_id=project_id)
    db = _FakeDB(project_id)
    tc = TestCase(test_run_id=run.id, test_fingerprint="fp1", test_name="t")

    case = {
        "test_name": "t", "class_name": "C", "suite_name": "S",
        "status": "failed", "duration_ms": 5,
        "attachments": [],
        "steps": [
            {
                "name": "login", "keyword": None, "status": "FAILED",
                "start_ms": 0, "duration_ms": 1,
                "assertion_message": None, "assertion_trace": None,
                "expected": "user@example.com",
                "actual": "contact me at admin@secret.org",
                "parameters": [
                    {"password": "hunter2"},
                    {"email": "leak@example.com"},
                    {"note": "ping me at dev@example.com"},
                ],
                "attachments": [],
                "steps": [],
            },
        ],
    }

    # No sanitize patch here — exercise the REAL redaction path.
    await svc._upsert_test_case(db, case, run, existing=tc, fingerprint="fp1")

    assert len(db.steps) == 1
    step = db.steps[0]
    # Emails in expected/actual are scrubbed.
    assert "user@example.com" not in (step.expected_value or "")
    assert "admin@secret.org" not in (step.actual_value or "")
    # parameters dicts run through redact_dict — sensitive KEYS redacted, and
    # email-shaped values pattern-scrubbed even under non-sensitive keys.
    flat = str(step.parameters)
    assert "hunter2" not in flat          # key "password" → [REDACTED]
    assert "leak@example.com" not in flat  # key "email" → [REDACTED]
    assert "dev@example.com" not in flat   # value pattern-scrubbed under "note"


@pytest.mark.asyncio
async def test_snapshot_skipped_when_no_steps_or_attachments():
    """A case with no granular detail must not create a canonical or touch steps
    (keeps the legacy no-steps path identical)."""
    project_id = uuid.uuid4()
    run = SimpleNamespace(id=uuid.uuid4(), project_id=project_id)
    db = _FakeDB(project_id)
    tc = TestCase(test_run_id=run.id, test_fingerprint="fp1", test_name="t")

    with patch(
        "app.services.privacy_service.sanitize_for_persistence",
        side_effect=lambda s: s,
    ):
        await svc._upsert_test_case(
            db,
            {"test_name": "t", "class_name": "C", "status": "passed",
             "duration_ms": 5, "steps": [], "attachments": []},
            run, existing=tc, fingerprint="fp1",
        )

    assert db.steps == []
    assert db.attachments == []
    assert db.canonicals == []
    assert tc.step_count == 0  # "steps" key present but empty


# ─────────────────────────── Read-endpoint service ───────────────────────────


@pytest.mark.asyncio
async def test_get_test_steps_tree_builds_nested_ordered_tree():
    from app.services.runs_service import get_test_steps_tree

    run_id = uuid.uuid4()
    test_id = uuid.uuid4()
    canonical_id = uuid.uuid4()

    parent = TestStep(
        id=uuid.uuid4(), canonical_test_case_id=canonical_id, parent_step_id=None,
        ordinal=0, depth=0, name="open cart", status="PASSED",
    )
    parent.created_at = None
    child = TestStep(
        id=uuid.uuid4(), canonical_test_case_id=canonical_id, parent_step_id=parent.id,
        ordinal=1, depth=1, name="click", status="FAILED",
        assertion_message="no button",
    )
    child.created_at = None
    att = TestAttachment(
        id=uuid.uuid4(), canonical_test_case_id=canonical_id, test_step_id=child.id,
        name="dom", source_ref="d.html", media_type="text/html",
    )
    att.created_at = None
    test_level_att = TestAttachment(
        id=uuid.uuid4(), canonical_test_case_id=canonical_id, test_step_id=None,
        name="shot", source_ref="a.png", media_type="image/png",
    )
    test_level_att.created_at = None

    tc = TestCase(
        id=test_id, test_run_id=run_id, test_fingerprint="fp1",
        test_name="test_checkout", status="FAILED",
    )
    tc.canonical_test_case_id = canonical_id
    tc.step_count = 1
    tc.retry_count = 0
    tc.is_flaky_run = False
    tc.stack_trace = "Traceback"

    class _ReadDB:
        async def execute(self, stmt):
            sql = str(stmt).lower()
            res = MagicMock()
            if "from test_cases" in sql or "test_cases." in sql:
                res.scalar_one_or_none = MagicMock(return_value=tc)
            elif "test_steps" in sql:
                scal = MagicMock()
                scal.all = MagicMock(return_value=[parent, child])
                res.scalars = MagicMock(return_value=scal)
            elif "test_attachments" in sql:
                scal = MagicMock()
                scal.all = MagicMock(return_value=[att, test_level_att])
                res.scalars = MagicMock(return_value=scal)
            else:
                res.scalar_one_or_none = MagicMock(return_value=None)
            return res

    tree = await get_test_steps_tree(_ReadDB(), run_id, test_id)
    assert tree is not None
    assert tree["test_name"] == "test_checkout"
    assert tree["step_count"] == 1
    assert len(tree["steps"]) == 1            # one root
    root = tree["steps"][0]
    assert root["name"] == "open cart"
    assert len(root["steps"]) == 1            # nested child
    assert root["steps"][0]["name"] == "click"
    assert root["steps"][0]["status"] == "FAILED"
    assert root["steps"][0]["attachments"][0]["source_ref"] == "d.html"
    # Test-level attachment surfaces at the top.
    assert tree["attachments"][0]["name"] == "shot"


@pytest.mark.asyncio
async def test_get_test_steps_tree_returns_none_for_cross_run_test():
    """IDOR-adjacent: a test_id not belonging to the run yields None → 404."""
    from app.services.runs_service import get_test_steps_tree

    class _ReadDB:
        async def execute(self, stmt):
            res = MagicMock()
            res.scalar_one_or_none = MagicMock(return_value=None)
            return res

    tree = await get_test_steps_tree(_ReadDB(), uuid.uuid4(), uuid.uuid4())
    assert tree is None


# ─────────────────────────── Router wiring / IDOR ───────────────────────────


def test_steps_endpoint_guarded_by_require_run_access():
    from app.routers import runs as runs_router

    route = next(
        r for r in runs_router.router.routes
        if getattr(r, "path", "") == "/api/v1/runs/{run_id}/tests/{test_id}/steps"
    )
    dep_calls = [d.call for d in route.dependant.dependencies]
    # require_run_access() returns the inner _check closure named "_check".
    assert any(getattr(c, "__name__", "") == "_check" for c in dep_calls), (
        "steps endpoint must depend on require_run_access() (verifies the "
        "provided run_id — IDOR ratchet)"
    )


class _AccessDB:
    """Models the two SELECTs ``require_run_access._check`` issues:
    ``SELECT project_id FROM test_runs WHERE id=:run`` then
    ``SELECT id FROM project_members WHERE user_id=:u AND project_id=:p``.

    ``run_project`` is the project that owns the run (None → run not found);
    ``member_project_ids`` is the set of projects the caller belongs to.
    """

    def __init__(self, run_project, member_project_ids):
        self.run_project = run_project
        self.member_project_ids = set(member_project_ids)

    async def execute(self, stmt):
        sql = str(stmt).lower()
        res = MagicMock()
        if "from test_runs" in sql or "test_runs." in sql:
            res.scalar_one_or_none = MagicMock(return_value=self.run_project)
            return res
        if "project_members" in sql:
            # The membership probe selects project_members.id; return a sentinel
            # only when the run's owning project is in the caller's set.
            hit = self.run_project in self.member_project_ids
            res.scalar_one_or_none = MagicMock(return_value=(uuid.uuid4() if hit else None))
            return res
        res.scalar_one_or_none = MagicMock(return_value=None)
        return res


def _fake_request(run_id):
    return SimpleNamespace(path_params={"run_id": str(run_id)})


def _member_user(role="QA_ENGINEER"):
    from app.models.postgres import UserRole

    return SimpleNamespace(id=uuid.uuid4(), role=UserRole(role))


@pytest.mark.asyncio
async def test_require_run_access_rejects_inaccessible_run():
    """IDOR enforcement: a non-admin who is NOT a member of the run's owning
    project must be denied (403) even with a valid, existing run_id — the guard
    verifies the PROVIDED run_id, not merely the None path."""
    from app.core.deps import require_run_access

    owning_project = uuid.uuid4()
    run_id = uuid.uuid4()
    check = require_run_access()
    db = _AccessDB(run_project=owning_project, member_project_ids=set())  # not a member
    user = _member_user()

    with pytest.raises(HTTPException) as ei:
        await check(_fake_request(run_id), db=db, current_user=user)
    assert ei.value.status_code == 403


@pytest.mark.asyncio
async def test_require_run_access_404_for_unknown_run():
    """A run_id with no TestRun row → 404 (not a silent pass)."""
    from app.core.deps import require_run_access

    check = require_run_access()
    db = _AccessDB(run_project=None, member_project_ids=set())  # run not found
    user = _member_user()

    with pytest.raises(HTTPException) as ei:
        await check(_fake_request(uuid.uuid4()), db=db, current_user=user)
    assert ei.value.status_code == 404


@pytest.mark.asyncio
async def test_require_run_access_allows_member():
    """A member of the run's owning project is allowed through (returns the user)."""
    from app.core.deps import require_run_access

    owning_project = uuid.uuid4()
    check = require_run_access()
    db = _AccessDB(run_project=owning_project, member_project_ids={owning_project})
    user = _member_user()

    out = await check(_fake_request(uuid.uuid4()), db=db, current_user=user)
    assert out is user


# ─────────────────────── Recursive TestStepResponse serialization ───────────────────────


def test_teststepresponse_serializes_nested_tree():
    """The recursive Pydantic response model round-trips the nested dict the
    read service emits — child ``steps`` and per-step ``attachments`` survive
    validation + JSON serialization (model_rebuild() resolved the forward ref)."""
    from datetime import datetime, timezone

    from app.models.schemas import TestStepResponse

    now = datetime.now(timezone.utc)
    child_id = uuid.uuid4()
    parent_id = uuid.uuid4()
    tree = {
        "id": parent_id,
        "parent_step_id": None,
        "ordinal": 0,
        "depth": 0,
        "name": "open cart",
        "keyword": None,
        "status": "PASSED",
        "duration_ms": 100,
        "start_ms": 1000,
        "assertion_message": None,
        "assertion_trace": None,
        "expected_value": None,
        "actual_value": None,
        "parameters": {"url": "/cart"},
        "created_at": now,
        "attachments": [],
        "steps": [
            {
                "id": child_id,
                "parent_step_id": parent_id,
                "ordinal": 1,
                "depth": 1,
                "name": "click checkout",
                "keyword": None,
                "status": "FAILED",
                "duration_ms": 50,
                "start_ms": 1100,
                "assertion_message": "no button",
                "assertion_trace": "tr",
                "expected_value": None,
                "actual_value": None,
                "parameters": None,
                "created_at": now,
                "steps": [],
                "attachments": [
                    {
                        "id": uuid.uuid4(),
                        "test_step_id": child_id,
                        "name": "dom",
                        "source_ref": "d.html",
                        "media_type": "text/html",
                        "created_at": now,
                    }
                ],
            }
        ],
    }

    model = TestStepResponse.model_validate(tree)
    assert model.name == "open cart"
    assert len(model.steps) == 1
    nested = model.steps[0]
    assert isinstance(nested, TestStepResponse)  # recursive type, not a dict
    assert nested.status == TestStatus.FAILED
    assert nested.assertion_message == "no button"
    assert nested.attachments[0].source_ref == "d.html"

    # Round-trips through JSON without raising (recursive forward ref resolved).
    dumped = model.model_dump(mode="json")
    assert dumped["steps"][0]["attachments"][0]["media_type"] == "text/html"
    assert model.model_dump_json()  # serializable end-to-end


def test_teststepresponse_rejects_out_of_vocab_status():
    """A step status outside the strict PASSED/FAILED/SKIPPED/BROKEN/UNKNOWN
    vocab is rejected at validation — the producer (parsers) and consumer
    (this model) must agree on vocabulary or the read endpoint 422s."""
    from datetime import datetime, timezone

    from pydantic import ValidationError

    from app.models.schemas import TestStepResponse

    with pytest.raises(ValidationError):
        TestStepResponse.model_validate({
            "id": uuid.uuid4(),
            "ordinal": 0,
            "depth": 0,
            "name": "x",
            "status": "errored",  # not in TestStatus
            "created_at": datetime.now(timezone.utc),
        })


@pytest.mark.asyncio
async def test_get_test_steps_tree_output_validates_against_response_model():
    """End-to-end: the dict get_test_steps_tree emits for each step must
    validate against the recursive TestStepResponse model (contract alignment
    between the service shape and the API schema)."""
    from datetime import datetime, timezone

    from app.models.schemas import TestStepResponse

    now = datetime.now(timezone.utc)
    canonical_id = uuid.uuid4()
    parent = TestStep(
        id=uuid.uuid4(), canonical_test_case_id=canonical_id, parent_step_id=None,
        ordinal=0, depth=0, name="open cart", status="PASSED",
    )
    parent.created_at = now
    child = TestStep(
        id=uuid.uuid4(), canonical_test_case_id=canonical_id, parent_step_id=parent.id,
        ordinal=1, depth=1, name="click", status="FAILED", assertion_message="no button",
    )
    child.created_at = now
    att = TestAttachment(
        id=uuid.uuid4(), canonical_test_case_id=canonical_id, test_step_id=child.id,
        name="dom", source_ref="d.html", media_type="text/html",
    )
    att.created_at = now

    tc = TestCase(
        id=uuid.uuid4(), test_run_id=uuid.uuid4(), test_fingerprint="fp1",
        test_name="t", status="FAILED",
    )
    tc.canonical_test_case_id = canonical_id
    tc.step_count = 1
    tc.retry_count = 0
    tc.is_flaky_run = False
    tc.stack_trace = None

    class _ReadDB:
        async def execute(self, stmt):
            sql = str(stmt).lower()
            res = MagicMock()
            if "from test_cases" in sql or "test_cases." in sql:
                res.scalar_one_or_none = MagicMock(return_value=tc)
            elif "test_steps" in sql:
                scal = MagicMock(); scal.all = MagicMock(return_value=[parent, child])
                res.scalars = MagicMock(return_value=scal)
            elif "test_attachments" in sql:
                scal = MagicMock(); scal.all = MagicMock(return_value=[att])
                res.scalars = MagicMock(return_value=scal)
            else:
                res.scalar_one_or_none = MagicMock(return_value=None)
            return res

    from app.services.runs_service import get_test_steps_tree

    tree = await get_test_steps_tree(_ReadDB(), tc.test_run_id, tc.id)
    # Each root step dict validates against the recursive response model.
    models = [TestStepResponse.model_validate(s) for s in tree["steps"]]
    assert models[0].steps[0].attachments[0].source_ref == "d.html"


# ─────────────────────────── Migration / mapper inspection ───────────────────────────


def test_migration_0093_columns_and_downgrade_present():
    import importlib

    mod = importlib.import_module("migrations.versions.0093_add_test_steps_and_attachments")
    assert mod.revision == "0093"
    assert mod.down_revision == "0092"
    assert callable(mod.upgrade) and callable(mod.downgrade)

    import inspect
    down_src = inspect.getsource(mod.downgrade)
    # Real downgrade drops both new tables + the four test_cases columns.
    assert "test_steps" in down_src
    assert "test_attachments" in down_src
    assert "step_count" in down_src


def test_orm_mappers_expose_snapshot_relationships():
    from sqlalchemy import inspect as sa_inspect

    ctc = sa_inspect(CanonicalTestCase)
    assert "steps" in ctc.relationships
    assert "attachments" in ctc.relationships

    step = sa_inspect(TestStep)
    assert "children" in step.relationships
    assert "parent" in step.relationships
    assert "attachments" in step.relationships

    # The four per-run metadata columns land on test_cases.
    cols = {c.name for c in sa_inspect(TestCase).columns}
    assert {"retry_count", "is_flaky_run", "stack_trace", "step_count"} <= cols
