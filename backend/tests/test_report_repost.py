"""W23c AGT-10 R-1 ... R-6 — the PR / MR comment re-post after a person accepts a run's AI report.

* staging: which reviews and runs stage which kind, on the accept's own
  transaction (a double accept stages one row per kind; a staging failure
  never fails the accept);
* the executor: every skip reason in the contract's order, the race rule, the
  offline skip with no HTTP, ``timed_out`` (never ``failed``) retried by the
  outbox and recorded once at the end, a host refusal, the SSRF block;
* the edit: through an ASGI double of the GitHub issue-comments API behind the
  ``_request`` seam -- ONE PATCH of the marker comment found on page 3, never a
  POST, the body carrying the reviewed line and the labels and no reviewer;
* the staged payload binds to the REAL task signatures.
"""
from __future__ import annotations

import inspect
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import structlog
from fastapi import HTTPException
from sqlalchemy.dialects import postgresql
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

import app.db.postgres as pg
from app.core.config import settings
from app.core.deps import CREDENTIAL_KIND_JWT, _bind_credential_kind
from app.services import github_checks_service, gitlab_integration_service
from app.services import github_pr_comment_service as gh
from app.services import report_repost_service as repost
from app.services import review_request_service, run_downstream_outbox
from app.services.review_envelope import ReviewEnvelope
from app.services.review_gate_vocab import REPOST_SENTENCES

REVIEWER_SENTINEL = "Zq-reviewer-xyz"


@pytest.fixture(autouse=True)
def _online(monkeypatch):
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", False)


def _review(**kw):
    base = dict(
        id=uuid.uuid4(), project_id=uuid.uuid4(), kind="report", subject_type="pipeline_run",
        subject_id="", pipeline_run_id=uuid.uuid4(), test_run_id=uuid.uuid4(),
        workflow_type="offline", state="pending_review", requested_by=None,
        reviewed_by=None, reviewed_at=None, reason_code=None, notes=None,
        evidence_bundle_sha256=None, superseded_by=None,
        created_at=datetime.now(timezone.utc),
        ai_disclaimer_version=review_request_service.AI_DISCLAIMER_VERSION,
    )
    base.update(kw)
    review = SimpleNamespace(**base)
    review.subject_id = review.subject_id or str(review.pipeline_run_id)
    return review


class _StagingDB:
    """The accept's session as staging sees it: one TestRun row read."""

    def __init__(self, run_row) -> None:
        self.run_row = run_row
        self.statements: list[str] = []

    async def execute(self, statement, *args, **kwargs):
        self.statements.append(str(statement.compile(dialect=postgresql.dialect())))
        return SimpleNamespace(first=lambda: self.run_row)


class _Outbox:
    """``stage_downstream_operation`` with the unique constraint's ON CONFLICT DO NOTHING."""

    def __init__(self) -> None:
        self.rows: dict[tuple, dict] = {}

    async def stage(self, db, *, run_id, project_id, operation, input_version, payload,
                    queue, priority=5, initial_status="pending"):
        assert operation in run_downstream_outbox._OPERATIONS
        assert len(input_version) == 64
        key = (run_id, operation, input_version)
        if key in self.rows:
            return False
        self.rows[key] = {"payload": payload, "queue": queue, "priority": priority,
                          "project_id": project_id}
        return True


def _integrations(monkeypatch, *, github: bool, gitlab: bool):
    monkeypatch.setattr(
        github_checks_service, "get_integration",
        AsyncMock(return_value=SimpleNamespace(pr_comment_mode="failures_only") if github else None),
    )
    monkeypatch.setattr(
        gitlab_integration_service, "get_integration",
        AsyncMock(return_value=SimpleNamespace(mr_comment_mode="failures_only") if gitlab else None),
    )


# ── R-1 staging rules ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("github", "gitlab", "pr_number", "ci_repo", "provider", "expected"),
    [
        (False, False, None, None, None, []),  # nothing to re-post: the common case
        (False, False, 7, "acme/payments", "github_actions", ["pr_comment_repost"]),
        (False, False, 7, "group/payments", "gitlab_ci", ["mr_note_repost"]),
        (False, False, 7, None, "github_actions", []),  # a PR number without a repo is no PR context
        (True, False, None, None, None, ["pr_comment_repost"]),  # an integration, no PR: staged, skipped later
        (False, True, None, None, "jenkins", ["mr_note_repost"]),
        (True, True, None, None, None, ["pr_comment_repost", "mr_note_repost"]),
        (True, False, 7, "group/payments", "gitlab_ci", ["pr_comment_repost", "mr_note_repost"]),
        (False, True, 7, "acme/payments", "GitHub_Actions", ["pr_comment_repost", "mr_note_repost"]),
    ],
)
def test_which_kinds_are_staged(github, gitlab, pr_number, ci_repo, provider, expected):
    assert repost.repost_kinds_for(
        pr_number=pr_number, ci_repo=ci_repo, ci_provider=provider,
        has_github=github, has_gitlab=gitlab,
    ) == expected


@pytest.mark.parametrize(
    "overrides",
    [
        {"kind": "eval_drift"},
        {"subject_type": "invocation"},  # an inline invocation's review never re-posts
        {"subject_type": "capability"},
        {"subject_type": "decision_report"},
        {"workflow_type": "investigation"},
        {"test_run_id": None},
    ],
)
@pytest.mark.asyncio
async def test_reviews_that_are_not_a_runs_report_stage_nothing(monkeypatch, overrides):
    _integrations(monkeypatch, github=True, gitlab=True)
    outbox = _Outbox()
    monkeypatch.setattr(run_downstream_outbox, "stage_downstream_operation", outbox.stage)
    review = _review(**overrides)
    db = _StagingDB((review.test_run_id, review.project_id, 7, "acme/payments", "github_actions"))
    assert await repost.stage_report_reposts(db, review=review) == []
    assert outbox.rows == {} and db.statements == []


@pytest.mark.asyncio
async def test_staging_reads_the_run_of_the_reviews_own_project_and_binds_the_real_tasks(monkeypatch):
    _integrations(monkeypatch, github=True, gitlab=True)
    outbox = _Outbox()
    monkeypatch.setattr(run_downstream_outbox, "stage_downstream_operation", outbox.stage)
    review = _review(workflow_type="deep")
    db = _StagingDB((review.test_run_id, review.project_id, None, None, None))
    assert await repost.stage_report_reposts(db, review=review) == ["pr_comment_repost", "mr_note_repost"]
    assert "test_runs.id = " in db.statements[0] and "test_runs.project_id = " in db.statements[0]
    tasks = run_downstream_outbox.downstream_tasks()
    for (run_id, operation, version), row in outbox.rows.items():
        assert row["payload"] == {"test_run_id": str(review.test_run_id), "review_id": str(review.id)}
        assert (row["queue"], row["priority"]) == ("default", 6)
        assert version == run_downstream_outbox.operation_input_version(operation, row["payload"])
        inspect.signature(tasks[operation].run).bind(**row["payload"])  # the mocked-apply_async lesson
    # A run of another project is not read as this review's run.
    assert await repost.stage_report_reposts(_StagingDB(None), review=_review()) == []


# ── R-1 through the accept handler ──────────────────────────────────────────


class _Savepoint:
    def __init__(self, db) -> None:
        self.db = db

    async def __aenter__(self):
        self.db.savepoints += 1
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if exc_type is not None:
            self.db.savepoint_rollbacks += 1
        return False


class _AcceptDB:
    """The accept's request session: the review row for every review read, the
    run row for staging's read, a savepoint, and a commit counter."""

    def __init__(self, review, run_row) -> None:
        self.review = review
        self.run_row = run_row
        self.commits = 0
        self.savepoints = 0
        self.savepoint_rollbacks = 0

    async def execute(self, statement, *args, **kwargs):
        return SimpleNamespace(
            scalar_one_or_none=lambda: self.review,
            scalars=lambda: SimpleNamespace(all=lambda: []),
            first=lambda: self.run_row,
        )

    async def get(self, _model, _row_id):
        return SimpleNamespace(execution_metadata={})

    async def flush(self):
        return None

    async def commit(self):
        self.commits += 1

    def begin_nested(self):
        return _Savepoint(self)


@pytest.fixture
def accept_world(monkeypatch):
    from app.routers import reviews

    async def _guarded(db, run_id, *, expected, to, error=None, **_kw):
        return to

    monkeypatch.setattr("app.services.workflow_run_state.guarded_transition", _guarded)
    monkeypatch.setattr(reviews, "log_access_change", AsyncMock())
    monkeypatch.setattr(reviews, "record_activity", AsyncMock())
    monkeypatch.setattr("app.services.pipeline_event_log.emit_event", AsyncMock())
    outbox = _Outbox()
    monkeypatch.setattr(run_downstream_outbox, "stage_downstream_operation", outbox.stage)
    return outbox


def _qa_lead():
    user = SimpleNamespace(id=uuid.uuid4(), role="QA_LEAD", is_synthetic=False, username=REVIEWER_SENTINEL)
    return _bind_credential_kind(user, CREDENTIAL_KIND_JWT)


@pytest.mark.asyncio
async def test_a_double_accept_stages_exactly_one_row_per_kind(monkeypatch, accept_world):
    from app.routers.reviews import accept_review

    _integrations(monkeypatch, github=True, gitlab=False)
    review = _review()
    db = _AcceptDB(review, (review.test_run_id, review.project_id, 7, "acme/payments", "github_actions"))
    out = await accept_review(review.id, None, db=db, current_user=_qa_lead())
    assert out.state == "accepted" and db.commits == 1 and db.savepoints == 1
    assert [key[1] for key in accept_world.rows] == ["pr_comment_repost"]

    with pytest.raises(HTTPException) as refused:  # the second accept is today's 409
        await accept_review(review.id, None, db=db, current_user=_qa_lead())
    assert refused.value.status_code == 409
    assert refused.value.detail["code"] == "review_not_pending"
    assert db.commits == 1 and db.savepoints == 1
    # A replayed staging of the same review hits the unique constraint.
    assert await repost.stage_report_reposts(db, review=review) == []
    assert len(accept_world.rows) == 1


@pytest.mark.asyncio
async def test_a_staging_failure_never_fails_the_accept(monkeypatch, accept_world):
    from app.routers import reviews
    from app.routers.reviews import accept_review

    warnings: list[tuple] = []
    monkeypatch.setattr(reviews, "logger", SimpleNamespace(
        warning=lambda *args, **kwargs: warnings.append(args),
    ))
    monkeypatch.setattr(github_checks_service, "get_integration",
                        AsyncMock(side_effect=RuntimeError("integration table unreadable")))
    review = _review()
    db = _AcceptDB(review, (review.test_run_id, review.project_id, 7, "acme/payments", "github_actions"))
    out = await accept_review(review.id, None, db=db, current_user=_qa_lead())
    assert out.state == "accepted" and db.commits == 1
    assert (db.savepoints, db.savepoint_rollbacks) == (1, 1)
    assert accept_world.rows == {}
    assert len(warnings) == 1 and warnings[0][0].startswith("report_repost_not_staged")
    assert str(review.id) in [str(arg) for arg in warnings[0][1:]]


@pytest.mark.asyncio
async def test_an_inline_invocation_review_accept_stages_nothing(monkeypatch, accept_world):
    from app.routers.reviews import accept_review

    _integrations(monkeypatch, github=True, gitlab=True)
    review = _review(subject_type="invocation", subject_id=str(uuid.uuid4()))
    db = _AcceptDB(review, (review.test_run_id, review.project_id, 7, "acme/payments", "github_actions"))
    await accept_review(review.id, None, db=db, current_user=_qa_lead())
    assert accept_world.rows == {} and db.commits == 1


# ── R-3 the executor ────────────────────────────────────────────────────────

from app.services.github_target import github_target_status as _REAL_TARGET_STATUS  # noqa: E402

_REAL_PR_REPOST = gh.repost_pr_summary_for_run
_REAL_MR_REPOST = gitlab_integration_service.repost_mr_note_for_run


def _first(row):
    return SimpleNamespace(first=lambda: row)


def _scalar(value):
    return SimpleNamespace(scalar_one_or_none=lambda: value)


class _ExecSessions:
    """``AsyncSessionLocal`` for the executor's short read sessions."""

    def __init__(self, world) -> None:
        self.world = world

    def __call__(self):
        return self

    async def __aenter__(self):
        self.world.opened += 1
        return self

    async def __aexit__(self, *exc):
        self.world.closed += 1
        return False

    async def execute(self, statement, *args, **kwargs):
        sql = str(statement.compile(dialect=postgresql.dialect()))
        self.world.statements.append(sql)
        if "FROM run_downstream_outbox" in sql:
            return _scalar(self.world.attempts)
        if "FROM review_requests" in sql:
            return _first(self.world.review_row)
        if "FROM test_runs" in sql and "ORDER BY" in sql:
            return _scalar(self.world.latest)
        if "FROM test_runs" in sql:
            return _first(self.world.run_row)
        raise AssertionError(f"unexpected statement: {sql}")


@pytest.fixture
def world(monkeypatch):
    run_id, review_id, project_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    w = SimpleNamespace(
        run_id=run_id, review_id=review_id, project_id=project_id,
        run_row=SimpleNamespace(
            id=run_id, project_id=project_id, build_number="1042", pr_number=7, ci_repo="acme/payments",
        ),
        review_row=SimpleNamespace(
            project_id=project_id,
            reviewed_at=datetime.now(timezone.utc).replace(second=0, microsecond=0) - timedelta(minutes=3),
            username=REVIEWER_SENTINEL, email=f"{REVIEWER_SENTINEL}@example.invalid",
        ),
        latest=run_id, attempts=1, opened=0, closed=0, statements=[],
        envelope=ReviewEnvelope(True, "accepted", "", review_id=str(review_id)),
    )
    monkeypatch.setattr(pg, "AsyncSessionLocal", _ExecSessions(w), raising=False)
    monkeypatch.setattr(
        "app.services.review_envelope.review_envelope_for_run",
        AsyncMock(side_effect=lambda db, run, **kw: w.envelope),
    )
    w.gh_status = AsyncMock(return_value=(None, "https://api.github.test"))
    monkeypatch.setattr("app.services.github_target.github_target_status", w.gh_status)
    _integrations(monkeypatch, github=True, gitlab=True)
    w.activity = AsyncMock()
    monkeypatch.setattr(repost, "record_activity", w.activity)
    w.pr_repost = AsyncMock(return_value={"outcome": "updated", "reason": None, "status_code": 200, "error_type": None})
    w.mr_repost = AsyncMock(return_value={"outcome": "updated", "reason": None, "status_code": 200, "error_type": None})
    monkeypatch.setattr(gh, "repost_pr_summary_for_run", w.pr_repost)
    monkeypatch.setattr(gitlab_integration_service, "repost_mr_note_for_run", w.mr_repost)
    return w


async def _go(w, kind="pr_comment_repost", outbox_id=None):
    return await repost.execute_repost(
        kind, test_run_id=str(w.run_id), review_id=str(w.review_id), outbox_id=outbox_id,
    )


def _row(w) -> dict:
    assert w.activity.await_count == 1, w.activity.await_args_list
    call = w.activity.await_args
    assert call.args == (None,)  # attempt mode: the activity service owns the write
    return call.kwargs


def _skip(host: str, reason: str, provider: str = "GitHub") -> str:
    return REPOST_SENTENCES[f"skipped:{reason}"].format(host=host, provider=provider)


def _no_pr(w):
    w.run_row.pr_number = None


def _mode_off(w, monkeypatch):
    monkeypatch.setattr(github_checks_service, "get_integration",
                        AsyncMock(return_value=SimpleNamespace(pr_comment_mode="off")))


@pytest.mark.parametrize(
    ("scenario", "reason"),
    [
        ("review_pending", "review_no_longer_accepted"),
        ("another_review_accepted", "review_no_longer_accepted"),
        ("no_integration_and_no_pr", "no_integration"),
        ("github_checks_off", "github_checks_off"),
        ("no_token", "no_token"),
        ("no_pr", "no_pr_for_run"),
        ("mode_off", "mode_off"),
        ("newer_run", "superseded_by_newer_run"),
    ],
)
@pytest.mark.asyncio
async def test_every_skip_reason_in_order_with_its_sentence(monkeypatch, world, scenario, reason):
    w = world
    if scenario == "review_pending":
        w.envelope = ReviewEnvelope(True, "pending_review", "", review_id=str(w.review_id))
    elif scenario == "another_review_accepted":
        w.envelope = ReviewEnvelope(True, "accepted", "", review_id=str(uuid.uuid4()))
    elif scenario == "no_integration_and_no_pr":
        w.gh_status.return_value = ("no_integration", None)
        _no_pr(w)  # step 3 decides before step 4
    elif scenario in ("github_checks_off", "no_token"):
        w.gh_status.return_value = (scenario, "https://api.github.test")
    elif scenario == "no_pr":
        _no_pr(w)
    elif scenario == "mode_off":
        _mode_off(w, monkeypatch)
    elif scenario == "newer_run":
        w.latest = uuid.uuid4()
    result = await _go(w)
    assert result == {"outcome": "skipped", "reason": reason, "status_code": None}
    w.pr_repost.assert_not_awaited()  # no HTTP for any skip
    row = _row(w)
    assert row["event_type"] == "integration.comment_reposted"
    assert row["project_id"] == w.project_id and row["entity_id"] == w.run_id
    assert row["entity_label"] == "Build 1042"
    assert row["actor"].actor_type == "system"
    assert row["context"] == {
        "sentence": _skip("PR comment", reason), "host": "github", "outcome": "skipped",
        "reason": reason, "review_id": str(w.review_id), "status_code": None,
    }
    assert REVIEWER_SENTINEL not in repr(row)
    assert w.opened == w.closed  # the read session is closed


@pytest.mark.asyncio
async def test_a_missing_run_or_mismatched_ids_skip_as_run_not_found(world):
    w = world
    w.run_row = None
    assert (await _go(w))["reason"] == "run_not_found"
    row = _row(w)
    assert row["project_id"] == w.project_id  # recorded under the review's project
    assert row["context"]["sentence"] == _skip("PR comment", "run_not_found")

    w.activity.reset_mock()
    w.run_row = SimpleNamespace(id=w.run_id, project_id=uuid.uuid4(), build_number="9",
                                pr_number=7, ci_repo="acme/payments")
    assert (await _go(w))["reason"] == "run_not_found"
    w.activity.assert_not_awaited()  # ids that disagree are recorded in no project


@pytest.mark.asyncio
async def test_offline_skips_with_no_http_and_its_sentence_verbatim(monkeypatch, world):
    w = world
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", True)
    monkeypatch.setattr("app.services.github_target.github_target_status", _REAL_TARGET_STATUS)
    monkeypatch.setattr(gh, "repost_pr_summary_for_run", _REAL_PR_REPOST)
    monkeypatch.setattr(gitlab_integration_service, "repost_mr_note_for_run", _REAL_MR_REPOST)
    http = AsyncMock(side_effect=AssertionError("no HTTP while offline"))
    monkeypatch.setattr(gh, "_request", http)
    monkeypatch.setattr(gitlab_integration_service, "_request", http)

    assert await _go(w) == {"outcome": "skipped", "reason": "offline_mode", "status_code": None}
    row = _row(w)
    assert row["context"]["sentence"] == "PR comment re-post skipped: offline mode (AI_OFFLINE_MODE=true)."
    assert f"{row['context']['sentence']} — {row['entity_label']}" == (
        "PR comment re-post skipped: offline mode (AI_OFFLINE_MODE=true). — Build 1042"
    )
    w.activity.reset_mock()
    assert (await _go(w, "mr_note_repost"))["reason"] == "offline_mode"
    assert _row(w)["context"]["sentence"] == "MR note re-post skipped: offline mode (AI_OFFLINE_MODE=true)."
    http.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_gitlab_gate_order(monkeypatch, world):
    w = world
    target = AsyncMock(return_value=gitlab_integration_service.GitLabTargetResolution(
        str(w.project_id), "https://gitlab.test", None, reason="no_project_path"))
    monkeypatch.setattr(gitlab_integration_service, "load_gitlab_target", target)
    assert (await _go(w, "mr_note_repost"))["reason"] == "no_project_path"
    assert _row(w)["context"]["sentence"] == _skip("MR note", "no_project_path", "GitLab")

    w.activity.reset_mock()
    target.return_value = gitlab_integration_service.GitLabTargetResolution(
        str(w.project_id), "https://gitlab.test", "group/payments", pat="glpat-" + "Zq7Lm" * 4)
    monkeypatch.setattr(gitlab_integration_service, "_post_allowed", AsyncMock(return_value=False))
    assert (await _go(w, "mr_note_repost"))["reason"] == "gitlab_off"
    assert _row(w)["context"]["host"] == "gitlab"
    w.mr_repost.assert_not_awaited()


# ── R-3 step 7: a timeout is not a failure ──────────────────────────────────


@pytest.mark.asyncio
async def test_a_timeout_retries_without_a_row_then_records_timed_out_final_once(world):
    w = world
    w.pr_repost.return_value = {"outcome": "timed_out", "reason": None, "status_code": None, "error_type": None}
    outbox_id = str(uuid.uuid4())
    for attempt in (1, 2):
        w.attempts = attempt
        with pytest.raises(repost.RepostTimedOut):
            await _go(w, outbox_id=outbox_id)
        w.activity.assert_not_awaited()
    w.attempts = 3
    result = await _go(w, outbox_id=outbox_id)
    assert result == {"outcome": "timed_out", "reason": None, "status_code": None}
    row = _row(w)
    assert row["context"]["outcome"] == "timed_out"
    assert row["context"]["sentence"] == (
        "PR comment re-post timed out three times; the comment was not changed."
    )
    assert any("FROM run_downstream_outbox" in sql for sql in w.statements)


@pytest.mark.asyncio
async def test_a_direct_call_records_a_timeout_at_once(world):
    w = world
    w.pr_repost.return_value = {"outcome": "timed_out", "reason": None, "status_code": None, "error_type": None}
    assert (await _go(w, outbox_id=None))["outcome"] == "timed_out"
    assert _row(w)["context"]["outcome"] == "timed_out"


def test_the_task_hands_a_timeout_back_to_the_outbox(monkeypatch):
    from app.worker import tasks

    async def timed_out(kind, **kwargs):
        assert kwargs["outbox_id"] == "11111111-1111-4111-8111-111111111111"
        raise repost.RepostTimedOut(kind)

    monkeypatch.setattr(repost, "execute_repost", timed_out)
    retried: list = []

    class _Retry(Exception):
        pass

    def _retry(exc):
        retried.append(exc)
        return _Retry()

    task = SimpleNamespace(
        request=SimpleNamespace(
            id="t-1", retries=0,
            headers={"downstream_outbox_id": "11111111-1111-4111-8111-111111111111"},
        ),
        name="app.worker.tasks.repost_pr_comment",
        retry=_retry,
    )
    try:
        with pytest.raises(_Retry):
            tasks._run_report_repost(task, "pr_comment_repost", str(uuid.uuid4()), str(uuid.uuid4()))
    finally:
        structlog.contextvars.clear_contextvars()  # the task bound its context
    assert len(retried) == 1 and isinstance(retried[0], repost.RepostTimedOut)


def test_the_tasks_are_registered_and_bind_the_staged_payload():
    from app.worker import tasks

    payload = {"test_run_id": str(uuid.uuid4()), "review_id": str(uuid.uuid4())}
    for kind, task in (("pr_comment_repost", tasks.repost_pr_comment), ("mr_note_repost", tasks.repost_mr_note)):
        assert run_downstream_outbox.downstream_tasks()[kind] is task
        assert task.queue == "default"
        assert isinstance(task, tasks.DownstreamTrackedTask)
        inspect.signature(task.run).bind(**payload)


# ── R-3 step 6 / R-5: the edit, through an ASGI double of the GitHub comments API ──

PAT = "ghp_" + "Zq7Lm" * 7 + "X"  # real-looking: ghp_ + 36 alphanumerics
PR_PROJECT = uuid.uuid4()


class _GitHubComments:
    """The issue-comments contract the re-post uses: a paginated list, a PATCH
    of one comment and the POST it must never call. Records every request."""

    def __init__(self, *, marker_page: int = 3, patch_status: int = 200, with_marker: bool = True) -> None:
        self.marker = gh._marker(PR_PROJECT)
        self.marker_page = marker_page
        self.patch_status = patch_status
        self.with_marker = with_marker
        self.requests: list[tuple] = []

    async def _comments(self, request: Request):
        assert request.headers["authorization"] == f"Bearer {PAT}"
        if request.method == "POST":
            self.requests.append(("POST", request.url.path, None))
            return JSONResponse({"id": 9}, status_code=201)
        page = int(request.query_params["page"])
        self.requests.append(("GET", request.url.path, page))
        if page < self.marker_page:
            return JSONResponse([{"id": page * 1000 + i, "body": f"a person's comment {i}"} for i in range(100)])
        if page == self.marker_page and self.with_marker:
            return JSONResponse([{"id": 1, "body": "unrelated"}, {"id": 4242, "body": self.marker + "\nold"}])
        return JSONResponse([])

    async def _edit(self, request: Request):
        body = (await request.json())["body"]
        self.requests.append(("PATCH", request.url.path, body))
        return JSONResponse({"id": int(request.path_params["cid"])}, status_code=self.patch_status)

    def app(self) -> Starlette:
        return Starlette(routes=[
            Route("/repos/acme/payments/issues/7/comments", self._comments, methods=["GET", "POST"]),
            Route("/repos/acme/payments/issues/comments/{cid:int}", self._edit, methods=["PATCH"]),
        ])


def _behind_the_seam(monkeypatch, double: _GitHubComments, *, timeout_on: str | None = None):
    transport = httpx.ASGITransport(app=double.app())

    async def _request(method, url, *, headers, json_body=None, params=None):
        if timeout_on == method:
            raise httpx.ReadTimeout("slow host")
        async with httpx.AsyncClient(transport=transport) as client:
            return await client.request(method, url, headers=headers, json=json_body, params=params)

    monkeypatch.setattr(gh, "_request", _request)
    monkeypatch.setattr(gh, "_ssrf_block_reason", AsyncMock(return_value=None))
    outcomes = AsyncMock()
    monkeypatch.setattr(gh, "_record_outcome", outcomes)
    seen: list = []

    async def gather(run_id, *, reviewed_line=None):
        seen.append(reviewed_line)
        failing = SimpleNamespace(test_fingerprint="fp1", status="FAILED", test_name="checkout pays",
                                  error_message="boom", id="11111111-1111-1111-1111-111111111111")
        part = gh._partition_tests({"fp1": failing}, {}, set(), has_baseline=False,
                                   kind_labels={failing.id: "Product bug (91% conf, AI-classified)"})
        run = SimpleNamespace(id=run_id, total_tests=3, passed_tests=2, failed_tests=1,
                              broken_tests=0, skipped_tests=0, pass_rate=66.7)
        body = gh._build_comment_body(run, PR_PROJECT, "payments", part, None, reviewed_line=reviewed_line)
        return gh._PRCommentContext(
            integration_id=uuid.uuid4(), api_base="https://api.github.test", repo="acme/payments",
            pr_number=7, pat=PAT, mode="failures_only", marker=double.marker, body=body,
            has_failures=True, has_fixed=False,
        ), ""

    monkeypatch.setattr(gh, "_gather_context_or_skip", gather)
    return SimpleNamespace(outcomes=outcomes, seen=seen)


@pytest.mark.asyncio
async def test_the_repost_patches_the_marker_comment_on_page_three_and_never_posts(monkeypatch, world):
    w = world
    monkeypatch.setattr(gh, "repost_pr_summary_for_run", _REAL_PR_REPOST)
    double = _GitHubComments()
    seam = _behind_the_seam(monkeypatch, double)
    result = await _go(w)
    assert result == {"outcome": "updated", "reason": None, "status_code": 200}
    assert [r[:2] for r in double.requests] == [
        ("GET", "/repos/acme/payments/issues/7/comments"),
        ("GET", "/repos/acme/payments/issues/7/comments"),
        ("GET", "/repos/acme/payments/issues/7/comments"),
        ("PATCH", "/repos/acme/payments/issues/comments/4242"),
    ]
    assert [r[2] for r in double.requests[:3]] == [1, 2, 3]
    body = double.requests[-1][2]
    reviewed_at = w.review_row.reviewed_at.strftime("%Y-%m-%d %H:%M UTC")
    assert seam.seen == [f"_Reviewed by a person · {reviewed_at}_"]
    lines = body.splitlines()
    assert lines[0] == double.marker
    counts = next(i for i, line in enumerate(lines) if line.startswith("**Failed:**"))
    assert lines[counts + 2] == f"_Reviewed by a person · {reviewed_at}_"  # directly under the counts line
    assert "_Product bug (91% conf, AI-classified)_" in body
    assert REVIEWER_SENTINEL not in body and "example.invalid" not in body
    seam.outcomes.assert_awaited_once()
    assert seam.outcomes.await_args.kwargs == {"error": None}
    row = _row(w)
    assert row["context"]["sentence"] == "PR comment updated with the reviewed AI labels."
    assert row["context"]["status_code"] == 200


@pytest.mark.asyncio
async def test_no_marker_comment_is_skipped_and_nothing_is_posted(monkeypatch, world):
    monkeypatch.setattr(gh, "repost_pr_summary_for_run", _REAL_PR_REPOST)
    double = _GitHubComments(with_marker=False)
    _behind_the_seam(monkeypatch, double)
    assert (await _go(world))["reason"] == "no_prior_comment"
    assert {r[0] for r in double.requests} == {"GET"}
    assert _row(world)["context"]["sentence"] == _skip("PR comment", "no_prior_comment")


@pytest.mark.asyncio
async def test_a_host_refusal_is_failed_with_its_status(monkeypatch, world):
    monkeypatch.setattr(gh, "repost_pr_summary_for_run", _REAL_PR_REPOST)
    double = _GitHubComments(patch_status=403)
    seam = _behind_the_seam(monkeypatch, double)
    assert await _go(world) == {"outcome": "failed", "reason": None, "status_code": 403}
    assert seam.outcomes.await_args.kwargs["error"].startswith("HTTP 403")
    assert _row(world)["context"]["sentence"] == (
        "PR comment re-post failed: the git host refused it (HTTP 403)."
    )


@pytest.mark.parametrize("slow", ["GET", "PATCH"])
@pytest.mark.asyncio
async def test_a_host_timeout_is_timed_out_and_never_written_as_an_error(monkeypatch, world, slow):
    monkeypatch.setattr(gh, "repost_pr_summary_for_run", _REAL_PR_REPOST)
    seam = _behind_the_seam(monkeypatch, _GitHubComments(marker_page=1), timeout_on=slow)
    result = await _go(world)  # a direct call: the last attempt
    assert result["outcome"] == "timed_out"
    seam.outcomes.assert_not_awaited()  # Integration Health's last_error is for failures
    assert _row(world)["context"]["outcome"] == "timed_out"


@pytest.mark.asyncio
async def test_an_unsafe_target_is_skipped_and_recorded_for_health(monkeypatch, world):
    monkeypatch.setattr(gh, "repost_pr_summary_for_run", _REAL_PR_REPOST)
    double = _GitHubComments()
    seam = _behind_the_seam(monkeypatch, double)
    monkeypatch.setattr(gh, "_ssrf_block_reason", AsyncMock(return_value="private address"))
    assert (await _go(world))["reason"] == "blocked_unsafe_target"
    assert double.requests == []
    assert seam.outcomes.await_args.kwargs == {"error": "blocked unsafe target: private address"}
    assert _row(world)["context"]["sentence"] == _skip("PR comment", "blocked_unsafe_target")
