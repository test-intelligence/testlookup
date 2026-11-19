"""Rule: ownership resolve answers only from the project named in the path.

``GET /projects/{project_id}/ownership/resolve/{cluster_id}`` takes a cluster
LABEL such as ``cl_001``. The label is unique per pipeline run only, so the same
label exists in many runs and many projects. The access guard checks the path
project, so the cluster and its member tests must come from that project too:

* the cluster is looked up through its run, in the path project only;
* when several runs of the project carry the label, the newest cluster wins,
  so the answer does not depend on row order;
* a project with no cluster of that label answers 404;
* the service loads member tests of the given project only, so no other caller
  of it can resolve over another project's tests;
* a cluster whose tests have no owner at all answers the documented fallback
  (``confidence: "none"``), never ``confidence: "high"`` with every field empty.

Before this rule held, a member of one project could receive a resolution
computed from another project's tests: that project's ``owner`` label came back
as ``team_name``, and a rule written in the caller's own project worked as a
yes/no probe for the other project's suite names.

The fake session answers each statement from the rows below using the
statement's own bound conditions, the way the database would: a statement with
no project condition gets rows from every project.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.models.postgres import FailureCluster, Project, ServiceOwnershipRule, TestCase
from app.routers import ownership as ownership_router
from app.services import ownership_resolver_service as resolver

pytestmark = pytest.mark.regression

T0 = datetime(2026, 11, 1, tzinfo=timezone.utc)


def _test_case(run_id, *, suite, owner=None):
    return SimpleNamespace(
        id=uuid.uuid4(), test_run_id=run_id, suite_name=suite, class_name=None,
        package_name=None, full_name=None, feature=None, epic=None, owner=owner,
    )


def _rule(project_id, pattern, team):
    return SimpleNamespace(
        id=uuid.uuid4(), project_id=project_id, is_active=True, priority=0,
        match_type="suite_name", match_pattern=pattern, service_name=f"{team}-svc",
        team_name=team, team_contact=None,
    )


class _Result:
    def __init__(self, rows):
        self._rows = list(rows)

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _World:
    """Projects, runs, clusters and tests, plus a session that queries them."""

    def __init__(self):
        self.run_project: dict[uuid.UUID, uuid.UUID] = {}
        self.clusters: list = []
        self.tests: list = []
        self.rules: list = []
        self.statements: list[str] = []

    def add_cluster(self, project_id, label, tests_spec, *, created_at=T0):
        run_id = uuid.uuid4()
        self.run_project[run_id] = project_id
        tests = [_test_case(run_id, **spec) for spec in tests_spec]
        self.tests.extend(tests)
        cluster = SimpleNamespace(
            id=uuid.uuid4(), test_run_id=run_id, cluster_id=label,
            member_test_ids=[str(t.id) for t in tests], created_at=created_at,
        )
        self.clusters.append(cluster)
        return cluster

    # ── the session ──────────────────────────────────────────────────────────

    async def execute(self, stmt, *args, **kwargs):
        sql = " ".join(str(stmt).split())
        self.statements.append(sql)
        params = stmt.compile().params
        project = next((v for k, v in params.items() if k.startswith("project_id")), None)
        entity = stmt.column_descriptions[0]["entity"]

        if entity is FailureCluster:
            rows = [c for c in self.clusters if c.cluster_id == params["cluster_id_1"]]
            if project is not None:
                rows = [c for c in rows if self.run_project[c.test_run_id] == project]
            if "ORDER BY failure_clusters.created_at DESC" in sql:
                rows.sort(key=lambda c: c.created_at, reverse=True)
            return _Result(rows[:1])
        if entity is TestCase:
            wanted = {str(v) for k, vs in params.items() if k.startswith("id") for v in vs}
            rows = [t for t in self.tests if str(t.id) in wanted]
            if project is not None:
                rows = [t for t in rows if self.run_project[t.test_run_id] == project]
            return _Result(rows)
        if entity is ServiceOwnershipRule:
            return _Result([r for r in self.rules if r.project_id == project])
        if entity is Project:
            return _Result([SimpleNamespace(component_owner_map=None)])
        raise AssertionError(f"unexpected statement: {sql}")


PROJECT_A = uuid.uuid4()
PROJECT_B = uuid.uuid4()
PROJECT_EMPTY = uuid.uuid4()
PROJECT_UNOWNED = uuid.uuid4()
PROJECT_TWO_RUNS = uuid.uuid4()


@pytest.fixture
def world(monkeypatch):
    async def _no_memory(db, project_id, member_test_ids):
        return None

    # Canonical memory is a separate source; these tests are about the rows.
    monkeypatch.setattr(resolver, "_resolve_cluster_ownership_from_memory", _no_memory)

    w = _World()
    # B's cluster is stored FIRST: a lookup by label alone returns it to anyone.
    w.cluster_b = w.add_cluster(
        PROJECT_B, "cl_001", [{"suite": "SuiteB", "owner": "team-b"}] * 3
    )
    w.cluster_a = w.add_cluster(PROJECT_A, "cl_001", [{"suite": "SuiteA"}] * 3)
    w.add_cluster(PROJECT_UNOWNED, "cl_777", [{"suite": "Orphans"}] * 2)
    w.add_cluster(
        PROJECT_TWO_RUNS, "cl_002", [{"suite": "Old", "owner": "team-old"}], created_at=T0
    )
    w.add_cluster(
        PROJECT_TWO_RUNS, "cl_002", [{"suite": "New", "owner": "team-new"}],
        created_at=T0 + timedelta(days=1),
    )
    return w


async def _resolve(world, project_id, label):
    return await ownership_router.resolve_cluster_ownership_endpoint(
        project_id=project_id, cluster_id=label, db=world, current_user=SimpleNamespace()
    )


# ── Refusals ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_member_of_a_never_receives_bs_owner_label(world):
    out = await _resolve(world, PROJECT_A, "cl_001")

    assert out.team_name != "team-b"
    assert out.match_source != "test_owner_label"
    assert out.team_name is None  # A's own cluster has no owner


@pytest.mark.asyncio
async def test_a_rule_in_a_cannot_probe_bs_suite_names(world):
    probe = _rule(PROJECT_A, "SuiteB", "probe-team")
    world.rules.append(probe)

    out = await _resolve(world, PROJECT_A, "cl_001")

    assert out.matched_rule_id != str(probe.id), "A's rule matched a suite of project B"
    assert out.team_name != "probe-team"


@pytest.mark.asyncio
async def test_a_project_with_no_such_cluster_answers_not_found(world):
    with pytest.raises(HTTPException) as exc:
        await _resolve(world, PROJECT_EMPTY, "cl_001")

    assert exc.value.status_code == 404
    assert exc.value.detail == "Cluster not found"


@pytest.mark.asyncio
async def test_the_service_ignores_member_tests_of_another_project(world):
    """A second caller handing over foreign ids still learns nothing from them."""
    world.rules.append(_rule(PROJECT_A, "SuiteB", "probe-team"))

    result = await resolver.resolve_cluster_ownership(
        world, PROJECT_A, list(world.cluster_b.member_test_ids)
    )

    assert result.team_name is None
    assert result.matched_rule_id is None
    assert result.confidence == "none"


@pytest.mark.asyncio
async def test_a_cluster_with_no_owner_at_all_is_not_high_confidence(world):
    out = await _resolve(world, PROJECT_UNOWNED, "cl_777")

    assert out.confidence == "none"
    assert out.team_name is None and out.service_name is None
    assert out.fallback_reason and "No ownership rule matched" in out.fallback_reason


# ── The legitimate path still works ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_member_of_b_gets_bs_owner_label(world):
    out = await _resolve(world, PROJECT_B, "cl_001")

    assert out.team_name == "team-b"
    assert out.match_source == "test_owner_label"


@pytest.mark.asyncio
async def test_a_rule_in_b_matches_bs_own_suite(world):
    rule = _rule(PROJECT_B, "SuiteB", "payments")
    world.rules.append(rule)

    out = await _resolve(world, PROJECT_B, "cl_001")

    assert out.matched_rule_id == str(rule.id)
    assert out.team_name == "payments"
    assert out.match_source == "suite_name"
    assert out.confidence == "high"


@pytest.mark.asyncio
async def test_the_newest_cluster_of_the_project_answers_when_the_label_repeats(world):
    out = await _resolve(world, PROJECT_TWO_RUNS, "cl_002")

    assert out.team_name == "team-new"
    cluster_sql = next(s for s in world.statements if s.startswith("SELECT failure_clusters."))
    # The id breaks a tie between two clusters created in the same instant.
    assert "ORDER BY failure_clusters.created_at DESC, failure_clusters.id DESC" in cluster_sql
