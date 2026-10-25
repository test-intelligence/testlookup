"""``services/workflow_default_service`` (W14 AGT-08) over an in-memory world.

The REAL service functions and the REAL ``workflow_definition_service``
statements run against ``_World``: a session stand-in that evaluates each
statement's equality / IN criteria, its ``ORDER BY … DESC`` and ``LIMIT`` over
planted ORM objects (and applies the upsert / delete), so "this project only",
"the newest PUBLISHED version" and the statement counts are the code's own.
The SQL semantics themselves (``ON CONFLICT``, the cascade) are proven on
PostgreSQL in ``tests/integration/test_workflow_default_service_postgres.py``.

* ``resolve_run_workflow``: no row -> builtin; a pinned published row -> that
  version; an unpinned row -> the newest PUBLISHED version (a newer draft
  ignored); a row with no published version / a missing workflow / a base
  mismatch / a corrupt stored body -> ``default_unavailable`` + the sentence
  and the built-in; it never raises for a bad default.
* ``set_default``: another project's workflow id -> 404 (the IDOR); a
  draft-only fork -> 422 ``DEFAULT_NOT_PUBLISHED``; a deep fork as the offline
  default -> 422 ``DEFAULT_BASE_MISMATCH``; a definition failing semantic
  validation -> 422 with ``errors``; a bad base -> 422; the upsert replaces
  (one row per base) and records the previous ref.
* ``read_defaults``: both bases always, ≤ 4 statements, ``model_stages`` from
  the project's agent configs, the updater's name.
* ``clear_default`` deletes and answers the row, or None; nothing commits.
"""
from __future__ import annotations

import ast
import copy
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.sql import operators

from app.core.agent_ops_vocab import (
    DEFAULT_BASE_INVALID,
    DEFAULT_BASE_MISMATCH,
    DEFAULT_NOT_PUBLISHED,
    WORKFLOW_DEFAULT_BASES,
)
from app.models.postgres import AgentConfig, ProjectWorkflowDefault, User, WorkflowDefinition
from app.models.schemas import WorkflowDefaultUpdate
from app.services import workflow_default_service as svc
from app.services import workflow_definition_service as definition_svc
from app.services.agent_config_service import default_config


class _Result:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None

    def scalar_one(self):
        assert len(self._rows) == 1, self._rows
        return self._rows[0]

    def scalars(self):
        return SimpleNamespace(all=lambda: list(self._rows))


def _value(right) -> Any:
    return right.effective_value if hasattr(right, "effective_value") else right.value


class _World:
    """Planted ORM objects + a session stand-in for the statements the service sends."""

    def __init__(self, *rows: Any) -> None:
        self.rows: list[Any] = list(rows)
        self.statements: list[Any] = []
        self.flushes = 0

    # -- the session surface the service uses (no commit / rollback on purpose) --
    async def execute(self, statement, *args, **kwargs):
        self.statements.append(statement)
        if getattr(statement, "is_insert", False):
            return self._upsert(statement)
        description = statement.column_descriptions[0]
        entity, expr = description["entity"], description["expr"]
        rows = [row for row in self.rows if isinstance(row, entity) and self._matches(row, statement)]
        for clause in reversed(statement._order_by_clauses):
            key = getattr(clause, "element", clause).key
            rows.sort(key=lambda r: getattr(r, key), reverse=getattr(clause, "modifier", None) is operators.desc_op)
        if statement._limit is not None:
            rows = rows[: statement._limit]
        if expr is not entity:   # a single-column select: answer the column's values
            rows = [getattr(row, expr.key) for row in rows]
        return _Result(rows)

    async def flush(self) -> None:
        self.flushes += 1

    async def delete(self, row) -> None:
        self.rows.remove(row)

    @staticmethod
    def _matches(row, statement) -> bool:
        for criterion in statement._where_criteria:
            attribute, value = getattr(row, criterion.left.key), _value(criterion.right)
            if criterion.operator is operators.eq and attribute != value:
                return False
            if criterion.operator is operators.in_op and attribute not in value:
                return False
            if criterion.operator not in (operators.eq, operators.in_op):
                raise AssertionError(f"unexpected operator {criterion.operator}")
        return True

    def _upsert(self, statement):
        assert statement.table.name == "project_workflow_defaults"
        params = statement.compile(dialect=postgresql.dialect()).params
        values = {key: params[key] for key in ("project_id", "base", "workflow_id", "workflow_version", "updated_by")}
        for row in self.rows:
            if isinstance(row, ProjectWorkflowDefault) and (row.project_id, row.base) == (
                values["project_id"], values["base"],
            ):
                row.workflow_id, row.workflow_version = values["workflow_id"], values["workflow_version"]
                row.updated_by, row.updated_at = values["updated_by"], datetime.now(timezone.utc)
                return _Result([row])
        row = ProjectWorkflowDefault(**values, updated_at=datetime.now(timezone.utc))
        self.rows.append(row)
        return _Result([row])

    def defaults(self) -> list[ProjectWorkflowDefault]:
        return [row for row in self.rows if isinstance(row, ProjectWorkflowDefault)]


P, Q = uuid.uuid4(), uuid.uuid4()
LEAD = uuid.uuid4()


def _definition(project_id, workflow_id, version, *, base="offline", status="published", body=None):
    document = copy.deepcopy(body or definition_svc.builtin(base)["definition"])
    document.update({"workflow_id": workflow_id, "version": version, "project_id": str(project_id)})
    return WorkflowDefinition(
        id=uuid.uuid4(), project_id=project_id, workflow_id=workflow_id, version=version,
        name=f"{workflow_id} v{version}", description=None, base=document["base"], definition=document,
        status=status, published_at=datetime(2026, 10, 1, tzinfo=timezone.utc) if status == "published" else None,
        eval_regression_accepted=False,
    )


def _default(base="offline", workflow_id="wf.my-fork", version=None, project_id=P, updated_by=None):
    return ProjectWorkflowDefault(
        project_id=project_id, base=base, workflow_id=workflow_id, workflow_version=version,
        updated_by=updated_by, updated_at=datetime(2026, 10, 2, tzinfo=timezone.utc),
    )


# ── resolve_run_workflow ─────────────────────────────────────────────────────


@pytest.mark.parametrize("base", WORKFLOW_DEFAULT_BASES)
async def test_no_row_runs_the_builtin(base):
    world = _World(_definition(P, "wf.my-fork", 1, base=base))
    resolution = await svc.resolve_run_workflow(world, P, base)
    assert resolution == svc.DefaultResolution(base, None, "builtin", None)


async def test_a_base_outside_the_defaults_sends_no_statement():
    world = _World()
    assert (await svc.resolve_run_workflow(world, P, "live")).source == "builtin"
    assert world.statements == []


async def test_a_pinned_published_row_runs_that_version():
    world = _World(
        _default(version=1),
        _definition(P, "wf.my-fork", 1), _definition(P, "wf.my-fork", 2),
    )
    resolution = await svc.resolve_run_workflow(world, P, "offline")
    assert resolution == svc.DefaultResolution("wf.my-fork", 1, "project_default", None)


async def test_an_unpinned_row_runs_the_newest_published_version_never_a_newer_draft():
    world = _World(
        _default(version=None),
        _definition(P, "wf.my-fork", 1), _definition(P, "wf.my-fork", 2),
        _definition(P, "wf.my-fork", 3, status="draft"),
        _definition(Q, "wf.my-fork", 9),   # another project's newer version is invisible here
    )
    resolution = await svc.resolve_run_workflow(world, P, "offline")
    assert resolution == svc.DefaultResolution("wf.my-fork", 2, "project_default", None)


async def test_a_row_without_a_published_version_falls_back_with_the_sentence():
    world = _World(_default(version=None), _definition(P, "wf.my-fork", 1, status="draft"))
    resolution = await svc.resolve_run_workflow(world, P, "offline")
    assert resolution.source == "default_unavailable"
    assert (resolution.workflow_id, resolution.workflow_version) == ("offline", None)
    assert resolution.reason == (
        "The default wf.my-fork no longer resolves in this project. The built-in offline workflow ran instead."
    )


async def test_a_pinned_version_that_is_gone_falls_back():
    world = _World(_default(version=4), _definition(P, "wf.my-fork", 1))
    resolution = await svc.resolve_run_workflow(world, P, "offline")
    assert resolution.source == "default_unavailable"
    assert resolution.reason.startswith("The default wf.my-fork@4 no longer resolves in this project.")


async def test_a_workflow_of_another_project_never_resolves():
    world = _World(_default(version=None), _definition(Q, "wf.my-fork", 1))
    resolution = await svc.resolve_run_workflow(world, P, "offline")
    assert (resolution.workflow_id, resolution.source) == ("offline", "default_unavailable")


async def test_a_base_mismatch_falls_back():
    world = _World(_default(base="offline", version=1), _definition(P, "wf.my-fork", 1, base="deep"))
    resolution = await svc.resolve_run_workflow(world, P, "offline")
    assert resolution == svc.DefaultResolution(
        "offline", None, "default_unavailable",
        "The default wf.my-fork@1 is no longer built on offline. The built-in offline workflow ran instead.",
    )


async def test_a_corrupt_stored_definition_falls_back_and_never_raises():
    broken = _definition(P, "wf.my-fork", 1)
    broken.definition = {**broken.definition, "steps": []}       # min_length=1: no longer parses
    world = _World(_default(version=1), broken)
    resolution = await svc.resolve_run_workflow(world, P, "offline")
    assert resolution.source == "default_unavailable"
    assert resolution.reason.startswith("The default wf.my-fork@1 no longer passes validation: steps")
    assert resolution.reason.endswith(" The built-in offline workflow ran instead.")


# ── set_default ──────────────────────────────────────────────────────────────

ACTOR = SimpleNamespace(id=LEAD)


async def _rejected(world, base, body) -> svc.DefaultRejected:
    with pytest.raises(svc.DefaultRejected) as caught:
        await svc.set_default(world, P, base, body, ACTOR)
    assert world.defaults() == [] or all(row.workflow_id != body.workflow_id for row in world.defaults())
    return caught.value


async def test_another_projects_workflow_is_not_found_here():
    world = _World(_definition(Q, "wf.theirs", 1))
    refused = await _rejected(world, "offline", WorkflowDefaultUpdate(workflow_id="wf.theirs"))
    assert (refused.status_code, refused.detail) == (404, "Workflow definition not found")


async def test_a_draft_only_fork_is_refused():
    world = _World(_definition(P, "wf.draft", 1, status="draft"))
    refused = await _rejected(world, "offline", WorkflowDefaultUpdate(workflow_id="wf.draft"))
    assert (refused.status_code, refused.detail) == (422, DEFAULT_NOT_PUBLISHED.format(workflow_id="wf.draft"))
    assert refused.detail == "Only a published workflow can be the default: publish wf.draft first."


async def test_a_deep_fork_cannot_be_the_offline_default():
    world = _World(_definition(P, "wf.deep-fork", 1, base="deep"))
    refused = await _rejected(world, "offline", WorkflowDefaultUpdate(workflow_id="wf.deep-fork"))
    assert refused.status_code == 422
    assert refused.detail == DEFAULT_BASE_MISMATCH.format(workflow_id="wf.deep-fork", actual="deep")
    assert refused.detail == "wf.deep-fork is built on deep: it can only be the deep default."


async def test_a_definition_failing_semantic_validation_is_refused_with_its_errors():
    body = copy.deepcopy(definition_svc.builtin("offline")["definition"])
    body["steps"][0]["agent_id"] = "agent.nope.v1"
    world = _World(_definition(P, "wf.bad", 1, body=body))
    refused = await _rejected(world, "offline", WorkflowDefaultUpdate(workflow_id="wf.bad"))
    assert refused.status_code == 422
    assert refused.detail["message"] == "Workflow definition failed semantic validation"
    assert any("unknown capability 'agent.nope.v1'" in error for error in refused.detail["errors"])


@pytest.mark.parametrize("base", ["live", "Offline", "", "deep "])
async def test_a_bad_base_is_refused_before_any_statement(base):
    world = _World(_definition(P, "wf.my-fork", 1))
    refused = await _rejected(world, base, WorkflowDefaultUpdate(workflow_id="wf.my-fork"))
    assert (refused.status_code, refused.detail) == (422, DEFAULT_BASE_INVALID)
    assert world.statements == []


async def test_set_then_replace_keeps_one_row_per_base_and_names_the_previous_ref():
    world = _World(_definition(P, "wf.my-fork", 1), _definition(P, "wf.other", 1), _definition(P, "wf.other", 2))
    row, definition = await svc.set_default(world, P, "offline", WorkflowDefaultUpdate(workflow_id="wf.my-fork"), ACTOR)
    assert (row.project_id, row.base, row.workflow_id, row.workflow_version, row.updated_by) == (
        P, "offline", "wf.my-fork", None, LEAD,
    )
    assert definition["workflow_id"] == "wf.my-fork" and definition["previous_default"] == "builtin"
    assert uuid.UUID(definition["id"]) and definition["name"] == "wf.my-fork v1"
    row2, definition2 = await svc.set_default(
        world, P, "offline", WorkflowDefaultUpdate(workflow_id="wf.other", workflow_version=1), ACTOR,
    )
    assert [(r.base, r.workflow_id, r.workflow_version) for r in world.defaults()] == [("offline", "wf.other", 1)]
    assert row2 is row and definition2["previous_default"] == "wf.my-fork" and definition2["version"] == 1
    assert world.flushes == 2


# ── read_defaults ────────────────────────────────────────────────────────────


async def test_no_rows_read_both_builtins_in_two_statements():
    world = _World()
    defaults = await svc.read_defaults(world, P)
    assert [d["base"] for d in defaults] == list(WORKFLOW_DEFAULT_BASES)
    offline, deep = defaults
    assert offline == {
        "base": "offline", "source": "builtin", "workflow_id": "offline", "workflow_version": None,
        "resolved_ref": "offline@1", "resolved_name": "Offline workflow", "state": "ok", "reason": None,
        "model_stages": ["root_cause_analysis", "summary", "triage"], "updated_by": None, "updated_at": None,
    }
    assert deep["resolved_ref"] == "deep@1" and deep["source"] == "builtin"
    # defect_commander (llm) starts disabled (a mutating capability): planned off, so it calls no model.
    assert deep["model_stages"] == ["root_cause_analysis", "summary", "triage"]
    assert len(world.statements) == 2, "the rows and the agent configs only"


async def test_a_usable_and_an_unavailable_row_read_in_four_statements():
    deterministic = default_config("agent.summary.v1").model_dump(mode="json")
    deterministic["model"]["tier"] = "deterministic"
    world = _World(
        _default("offline", "wf.my-fork", None, updated_by=LEAD),
        _default("deep", "wf.gone", 3, updated_by=LEAD),
        _definition(P, "wf.my-fork", 1), _definition(P, "wf.my-fork", 2),
        User(id=LEAD, username="lead", full_name="Lea Lead"),
        AgentConfig(
            id=uuid.uuid4(), project_id=P, agent_id="agent.summary.v1", enabled=True,
            mode=deterministic["mode"], config=deterministic, config_version=1,
        ),
    )
    offline, deep = await svc.read_defaults(world, P)
    assert len(world.statements) == 4
    assert (offline["source"], offline["state"], offline["resolved_ref"], offline["resolved_name"]) == (
        "project_default", "ok", "wf.my-fork@2", "wf.my-fork v2",
    )
    assert offline["model_stages"] == ["root_cause_analysis", "triage"], "summary is deterministic in P"
    assert offline["updated_by"] == "Lea Lead" and offline["workflow_version"] is None
    assert (deep["state"], deep["resolved_ref"], deep["model_stages"]) == ("unavailable", None, [])
    assert deep["reason"] == "The default wf.gone@3 no longer resolves in this project."
    assert deep["workflow_id"] == "wf.gone" and deep["workflow_version"] == 3


# ── clear_default, commit-free ───────────────────────────────────────────────


async def test_clear_deletes_the_row_and_answers_it_then_none():
    planted = _default("deep", "wf.deep-fork", 2)
    world = _World(planted)
    assert await svc.clear_default(world, P, "deep") is planted
    assert world.defaults() == [] and world.flushes == 1
    assert await svc.clear_default(world, P, "deep") is None


async def test_clear_refuses_a_bad_base():
    with pytest.raises(svc.DefaultRejected) as caught:
        await svc.clear_default(_World(), P, "live")
    assert (caught.value.status_code, caught.value.detail) == (422, DEFAULT_BASE_INVALID)


def test_the_module_never_commits_or_rolls_back():
    tree = ast.parse(Path(svc.__file__).read_text(encoding="utf-8"))
    called = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert called.isdisjoint({"commit", "rollback"}), called
