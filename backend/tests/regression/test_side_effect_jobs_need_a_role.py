"""Routes that start jobs or act on the whole deployment need a matching role.

Four places checked project membership only, or a project-level role for an
action that covers every project:

* the duplicate-detection sweep (writes candidate rows, spends compute) ran
  for a VIEWER, while dismissing or merging a candidate needed QA_ENGINEER;
* live-run recovery (queues a worker job that writes test rows) ran for a
  VIEWER, while writing test results anywhere else needs QA_ENGINEER;
* the training export and the fine-tune request cover every project's
  feedback, but a QA lead of one project could queue them;
* the on-demand integration probe made the server dial every
  deployment-wide integration for a QA lead.

The rule this file pins: QA_ENGINEER for the sweep and for recovery; ADMIN for
the training export and fine-tune; ADMIN for an integration probe that names
no project (a per-project probe stays at QA_LEAD). A refused caller starts no
work. The two instance-wide count reads stay open to every signed-in user and
say so in their docstrings.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = pytest.mark.regression

PROJECT = uuid.UUID("11111111-1111-4111-8111-111111111111")
RUN = uuid.UUID("22222222-2222-4222-8222-222222222222")
PROBE_NEEDS_ADMIN = (
    "Probing the deployment-wide integrations requires the ADMIN role. To probe one "
    "project's GitHub or GitLab integration, pass project_id with provider=github or "
    "provider=gitlab."
)


def _user(role: str, *, bound_to: uuid.UUID | None = None):
    user = SimpleNamespace(id=uuid.uuid4(), role=role, is_active=True, username=role.lower())
    if bound_to is not None:
        # How deps marks a project-bound API key (``_API_KEY_BOUND_PROJECT_ATTR``).
        setattr(user, "_testlookup_api_key_project_id", bound_to)
    return user


def _scalar(value):
    return MagicMock(scalar_one_or_none=MagicMock(return_value=value))


def _client(router, state, db):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.core.deps import get_current_active_user
    from app.db.postgres import get_db

    async def _db():
        yield db

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_active_user] = lambda: state.user
    return TestClient(app, raise_server_exceptions=False)


# ── duplicate-detection sweep: QA_ENGINEER ───────────────────────────────────


@pytest.fixture
def sweep(monkeypatch):
    from app.routers import duplicates

    state = SimpleNamespace(user=_user("VIEWER"))
    state.detect = AsyncMock(return_value={
        "project_id": PROJECT, "candidates_created": 1, "candidates_total": 1,
        "cases_scanned": 14, "sampled": False, "note": None,
    })
    monkeypatch.setattr(duplicates.dup_svc, "detect_duplicates_for_project", state.detect)
    db = MagicMock()
    db.execute = AsyncMock(return_value=_scalar(uuid.uuid4()))  # the caller is a member
    db.commit = AsyncMock()
    state.db = db
    state.http = _client(duplicates.router, state, db)
    state.path = f"/api/v1/projects/{PROJECT}/duplicate-candidates/detect?enable_semantic=false"
    return state


@pytest.mark.parametrize("role", ["VIEWER", "TESTER"])
def test_a_member_below_qa_engineer_cannot_run_the_duplicate_sweep(sweep, role):
    sweep.user = _user(role)

    resp = sweep.http.post(sweep.path)

    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"] == "Requires at least QA_ENGINEER role"
    sweep.detect.assert_not_awaited()
    sweep.db.commit.assert_not_awaited()


def test_a_qa_engineer_runs_the_duplicate_sweep(sweep):
    sweep.user = _user("QA_ENGINEER")

    resp = sweep.http.post(sweep.path)

    assert resp.status_code == 200, resp.text
    assert resp.json()["candidates_created"] == 1
    sweep.detect.assert_awaited_once()


# ── live-run recovery: QA_ENGINEER ───────────────────────────────────────────


@pytest.fixture
def recovery():
    from app.routers import runs

    state = SimpleNamespace(user=_user("VIEWER"), run_loaded=0)

    async def _execute(statement, *args, **kwargs):
        text = " ".join(str(statement).lower().split())
        if "project_members" in text:
            return _scalar(uuid.uuid4())  # the caller is a member
        if text.startswith("select test_runs.project_id from"):
            return _scalar(PROJECT)  # the run guard
        if "from test_runs" in text:
            state.run_loaded += 1  # the handler's own read of the run
            return _scalar(SimpleNamespace(id=RUN, project_id=PROJECT, trigger_source="ci_upload"))
        raise AssertionError(f"unexpected query: {text}")

    db = MagicMock()
    db.execute = AsyncMock(side_effect=_execute)
    db.commit = AsyncMock()
    state.db = db
    state.http = _client(runs.router, state, db)
    state.path = f"/api/v1/runs/{RUN}/recover-live"
    return state


@pytest.mark.parametrize("role", ["VIEWER", "TESTER"])
def test_a_member_below_qa_engineer_cannot_start_live_recovery(recovery, role):
    recovery.user = _user(role)

    resp = recovery.http.post(recovery.path)

    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"] == "Requires at least QA_ENGINEER role"
    assert recovery.run_loaded == 0, "the handler ran for a refused caller"
    recovery.db.commit.assert_not_awaited()


def test_a_qa_engineer_reaches_live_recovery(recovery):
    """The run here is not a live-stream run, so the handler's own 422 is the
    proof the caller got past the role floor and the run guard."""
    recovery.user = _user("QA_ENGINEER")

    resp = recovery.http.post(recovery.path)

    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"] == "Recovery is only available for live-stream runs."
    assert recovery.run_loaded == 1


# ── training export and fine-tune: ADMIN ─────────────────────────────────────

TRAINING = [
    ("/api/v1/training/export", None, "trigger_export"),
    ("/api/v1/training/finetune", {"track": "classifier"}, "trigger_finetune"),
]


@pytest.fixture
def training(monkeypatch):
    from app.routers import feedback

    state = SimpleNamespace(user=_user("QA_LEAD"))
    state.trigger_export = MagicMock(return_value={"message": "Training data export queued", "task_id": "t-1"})
    state.trigger_finetune = MagicMock(return_value={"message": "Fine-tuning queued", "task_id": "t-2"})
    state.stats = AsyncMock(return_value={"total": 3})
    state.status = AsyncMock(return_value={"finetune_enabled": False})
    monkeypatch.setattr(feedback.feedback_service, "trigger_export", state.trigger_export)
    monkeypatch.setattr(feedback.feedback_service, "trigger_finetune", state.trigger_finetune)
    monkeypatch.setattr(feedback.feedback_service, "get_feedback_stats", state.stats)
    monkeypatch.setattr(feedback.feedback_service, "get_training_status", state.status)
    state.http = _client(feedback.router, state, MagicMock())
    return state


def _post(http, path, body):
    return http.post(path) if body is None else http.post(path, json=body)


@pytest.mark.parametrize("path, body, service", TRAINING, ids=["export", "finetune"])
@pytest.mark.parametrize("role", ["VIEWER", "TESTER", "QA_ENGINEER", "QA_LEAD"])
def test_only_an_admin_queues_instance_wide_training_work(training, path, body, service, role):
    training.user = _user(role)

    resp = _post(training.http, path, body)

    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"] == "Requires at least ADMIN role"
    getattr(training, service).assert_not_called()


@pytest.mark.parametrize("path, body, service", TRAINING, ids=["export", "finetune"])
def test_an_admin_queues_training_work(training, path, body, service):
    training.user = _user("ADMIN")

    resp = _post(training.http, path, body)

    assert resp.status_code == 202, resp.text
    getattr(training, service).assert_called_once()


@pytest.mark.parametrize("path, body, service", TRAINING, ids=["export", "finetune"])
def test_an_admins_project_bound_key_does_not_queue_training_work(training, path, body, service):
    """The work covers every project; a key bound to one project is refused."""
    training.user = _user("ADMIN", bound_to=PROJECT)

    resp = _post(training.http, path, body)

    assert resp.status_code == 403, resp.text
    getattr(training, service).assert_not_called()


@pytest.mark.parametrize("path, handler", [
    ("/api/v1/feedback/stats", "get_feedback_stats"),
    ("/api/v1/training/status", "get_training_status"),
])
def test_the_instance_wide_counts_stay_open_and_say_so(training, path, handler):
    from app.routers import feedback

    training.user = _user("VIEWER")

    resp = training.http.get(path)

    assert resp.status_code == 200, resp.text
    doc = " ".join((getattr(feedback, handler).__doc__ or "").split())
    assert "instance-wide" in doc and "every signed-in user" in doc, doc


# ── on-demand integration probe: ADMIN unless a project is named ─────────────


@pytest.fixture
def probe(monkeypatch):
    from app.routers import integration_health
    from app.services import integration_config_service
    from app.services import integration_probe_service as probes

    state = SimpleNamespace(user=_user("QA_LEAD"))
    state.jira = AsyncMock(return_value=probes.ProbeResult("jira", "healthy"))
    state.run_all = AsyncMock(return_value=[probes.ProbeResult("jira", "healthy")])
    state.run_project = AsyncMock(return_value=[probes.ProbeResult("github", "healthy")])
    state.persist = AsyncMock()
    monkeypatch.setattr(probes, "ALL_PROBES", {"jira": state.jira})
    monkeypatch.setattr(probes, "run_all_probes", state.run_all)
    monkeypatch.setattr(probes, "run_project_probes", state.run_project)
    monkeypatch.setattr(probes, "persist_probe_results", state.persist)
    monkeypatch.setattr(integration_config_service, "resolve_global_integrations", AsyncMock(return_value={}))
    monkeypatch.setattr(integration_health, "resolve_project_scope", AsyncMock(return_value=(PROJECT, None)))
    monkeypatch.setattr(integration_health, "get_accessible_project_ids", AsyncMock(return_value=None))
    state.http = _client(integration_health.router, state, SimpleNamespace())
    state.path = "/api/v1/integration-health/probe"
    return state


@pytest.mark.parametrize("query", ["", "?provider=jira"], ids=["every-integration", "one-global-provider"])
def test_a_qa_lead_cannot_probe_the_deployment_wide_integrations(probe, query):
    probe.user = _user("QA_LEAD")

    resp = probe.http.post(probe.path + query)

    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"] == PROBE_NEEDS_ADMIN
    probe.jira.assert_not_awaited()
    probe.run_all.assert_not_awaited()
    probe.run_project.assert_not_awaited()
    probe.persist.assert_not_awaited()


@pytest.mark.parametrize("query, reached", [("", "run_all"), ("?provider=jira", "jira")],
                         ids=["every-integration", "one-global-provider"])
def test_an_admin_probes_the_deployment_wide_integrations(probe, query, reached):
    probe.user = _user("ADMIN")

    resp = probe.http.post(probe.path + query)

    assert resp.status_code == 200, resp.text
    getattr(probe, reached).assert_awaited_once()
    probe.persist.assert_awaited_once()


def test_a_qa_lead_still_probes_one_projects_integration(probe):
    probe.user = _user("QA_LEAD")

    resp = probe.http.post(f"{probe.path}?provider=github&project_id={PROJECT}")

    assert resp.status_code == 200, resp.text
    probe.run_project.assert_awaited_once()
    probe.run_all.assert_not_awaited()


def test_a_qa_lead_is_still_told_how_to_probe_gitlab(probe):
    """The request-shape answers come before the role answer: they name the fix."""
    probe.user = _user("QA_LEAD")

    resp = probe.http.post(f"{probe.path}?provider=gitlab")

    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"] == "GitLab is probed per project: pass project_id."


def test_a_qa_engineer_cannot_probe_at_all(probe):
    probe.user = _user("QA_ENGINEER")

    resp = probe.http.post(f"{probe.path}?provider=github&project_id={PROJECT}")

    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"] == "Requires at least QA_LEAD role"
    probe.run_project.assert_not_awaited()
