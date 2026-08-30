"""Six routes that were role-gated but never scope-gated.

Two were the last known holes from the sweep; the other four came out of
triaging the widened ratchet's backlog rather than baselining it — every one
of the three entries parked there as "suspected" turned out to be real, and
chasing them surfaced a fourth the scan itself had missed.

**`POST /deep-investigate/defects/{defect_id}/review`** ran on
`require_role(UserRole.QA_LEAD)` alone, and `approve_action` / `reject_action`
update by id:

    update(Defect).where(Defect.id == record_id,
                         Defect.approval_status == PENDING_REVIEW)

So a QA lead of project A could flip project B's pending defect to APPROVED or
REJECTED, with their own name recorded as the approver — and use the 200-vs-404
split as an existence oracle for defect ids. The sibling `list_pending_defects`
in the same file already documents this exact class ("``require_role(QA_LEAD)``
gates by ROLE, not by project membership") and was fixed for **reads**; the
endpoint that *writes* those rows was not.

**`POST /feedback/{analysis_id}`** was worse: no role gate at all, just
`get_current_active_user`. `AIAnalysis` carries no project of its own, and the
service selected it by primary key. A `rating=INCORRECT` submission overwrites
another tenant's `failure_category` and `root_cause_summary`, clears their
`requires_human_review` flag, and evicts their semantic-cache entry — so the
next reader is served the attacker's verdict. The join that resolves the owning
project (`TestCase -> TestRun.project_id`) already existed one function away, in
`_invalidate_analysis_cache_for`.

Both now resolve the owning project and call `resolve_project_scope`, which
raises 403 for a non-member and passes ADMIN through.
"""
from __future__ import annotations

import inspect
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.routers import agents as agents_router
from app.routers import deep_investigation as di_router
from app.routers import integrations as integrations_router
from app.routers import reports as reports_router
from app.services import feedback_service

_MINE = uuid.uuid4()
_THEIRS = uuid.uuid4()


def _user(role="QA_LEAD"):
    u = MagicMock()
    u.id = uuid.uuid4()
    u.role = role
    u.username = "zzprobeidor"
    return u


def _db_returning_scalar(value):
    db = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=value)
    db.execute = AsyncMock(return_value=result)
    return db


# ── POST /feedback/{analysis_id} ────────────────────────────────────────────

class TestFeedbackIsProjectScoped:
    @pytest.mark.asyncio
    async def test_another_tenants_analysis_is_refused(self):
        analysis = MagicMock()
        analysis.test_case_id = uuid.uuid4()
        db = _db_returning_scalar(_THEIRS)  # the run's project

        with patch(
            "app.core.deps.get_accessible_project_ids",
            AsyncMock(return_value={_MINE}),
        ):
            with pytest.raises(HTTPException) as excinfo:
                await feedback_service._require_analysis_access(db, analysis, _user())
        assert excinfo.value.status_code == 403

    @pytest.mark.asyncio
    async def test_an_unreachable_analysis_fails_closed(self):
        """A purged run leaves no owner to check against — deny, don't allow."""
        analysis = MagicMock()
        analysis.test_case_id = uuid.uuid4()
        db = _db_returning_scalar(None)

        with patch(
            "app.core.deps.get_accessible_project_ids",
            AsyncMock(return_value={_MINE}),
        ):
            with pytest.raises(HTTPException) as excinfo:
                await feedback_service._require_analysis_access(db, analysis, _user())
        assert excinfo.value.status_code == 404

    @pytest.mark.asyncio
    async def test_a_member_is_allowed(self):
        analysis = MagicMock()
        analysis.test_case_id = uuid.uuid4()
        db = _db_returning_scalar(_MINE)

        with patch(
            "app.core.deps.get_accessible_project_ids",
            AsyncMock(return_value={_MINE}),
        ):
            await feedback_service._require_analysis_access(db, analysis, _user())

    @pytest.mark.asyncio
    async def test_an_admin_is_allowed(self):
        analysis = MagicMock()
        analysis.test_case_id = uuid.uuid4()
        db = _db_returning_scalar(_THEIRS)

        with patch(
            "app.core.deps.get_accessible_project_ids", AsyncMock(return_value=None)
        ):
            await feedback_service._require_analysis_access(db, analysis, _user("ADMIN"))

    def test_submit_feedback_checks_before_it_mutates(self):
        """A check after the overwrite still overwrote the row."""
        src = inspect.getsource(feedback_service.submit_feedback)
        assert "_require_analysis_access" in src, (
            "submit_feedback no longer verifies project access -- it is a "
            "cross-tenant write behind a bare authenticated session"
        )
        guard_at = src.index("_require_analysis_access")
        assert src.index("analysis.failure_category =") > guard_at, (
            "the analysis is overwritten before access is verified"
        )

    def test_the_join_resolves_the_run_not_the_analysis(self):
        """AIAnalysis has no project_id; scoping it by one would be a no-op."""
        src = inspect.getsource(feedback_service._require_analysis_access)
        assert "TestRun.project_id" in src and "TestCase.test_run_id" in src, (
            "the owning project must come from TestCase -> TestRun"
        )


# ── POST /deep-investigate/defects/{defect_id}/review ───────────────────────

class TestDefectReviewIsProjectScoped:
    def test_the_handler_resolves_scope_before_approving(self):
        src = inspect.getsource(di_router.review_defect)
        assert "resolve_project_scope" in src, (
            "review_defect is role-gated only -- a QA lead of one project can "
            "approve or reject another project's defect"
        )
        guard_at = src.index("resolve_project_scope")
        for writer in ("approve_action(", "reject_action("):
            assert src.index(writer) > guard_at, (
                f"review_defect calls {writer} before verifying access"
            )

    def test_the_scope_comes_from_the_defect_being_reviewed(self):
        """Checking any other project id would read as a fix and be one."""
        src = inspect.getsource(di_router.review_defect)
        assert "str(target.project_id)" in src, (
            "review_defect must scope on the project of the defect it loaded"
        )

    def test_the_sibling_read_endpoint_stays_scoped(self):
        """Guards the reference implementation this fix was modelled on."""
        src = inspect.getsource(di_router.list_pending_defects)
        assert "get_accessible_project_ids" in src
        assert "Defect.project_id.in_(accessible)" in src


def test_neither_endpoint_relies_on_a_role_check_alone():
    """A role check answers "what is the caller", never "what may they touch"."""
    for fn in (di_router.review_defect, feedback_service.submit_feedback):
        src = inspect.getsource(fn)
        assert "resolve_project_scope" in src or "_require_analysis_access" in src, (
            f"{fn.__name__} has no membership check"
        )


# ══════════════════════════════════════════════════════════════════════════
# The four the widened ratchet's backlog turned up
# ══════════════════════════════════════════════════════════════════════════
#
# When the ratchet was widened to cover ids arriving outside the path, three
# routes were parked in its backlog as "suspected, needs triage" rather than
# baselined as approved. Triaging them found all three were real -- and turned
# up a fourth the scan had missed, because ``run_ids`` (plural) was not in
# ``SCOPED_IDS``. That gap is now closed too.
#
# * ``POST /reports/email-trends`` -- the worst of the four. The caller supplies
#   BOTH ``project_id`` and ``recipient_email`` and nothing checked either, so
#   any authenticated user could have another tenant's trend report mailed to an
#   address of their choosing. The data leaves the system, so no later access
#   control can contain it.
# * ``POST /agents/pipelines/trigger`` -- role-gated with only an existence
#   check on the run: starts the agent pipeline on another tenant's run,
#   spending their LLM budget and writing agent results into their project.
# * ``POST /agents/pipelines/bulk-trigger`` -- the same, for up to 2000 run ids
#   in one call, filtered on none of them.
# * ``POST /integrations/jira`` -- fetches a ``TestCase`` by primary key and
#   files its failure text and AI analysis into a Jira project the caller names.


class TestPipelineTriggersAreScoped:
    def test_single_trigger_resolves_the_runs_project(self):
        src = inspect.getsource(agents_router.trigger_pipeline)
        assert "resolve_project_scope" in src, (
            "trigger_pipeline is role-gated only -- it starts the pipeline on "
            "any run id the caller names"
        )
        assert "str(run.project_id)" in src, (
            "the scope must come from the run that was just loaded"
        )
        assert src.index("resolve_project_scope") < src.index("run_agent_pipeline"), (
            "the pipeline is queued before access is verified"
        )

    def test_bulk_trigger_filters_by_accessible_projects(self):
        """2000 ids per call, filtered on none of them."""
        src = inspect.getsource(agents_router.bulk_trigger_pipelines)
        assert "get_accessible_project_ids" in src, (
            "bulk_trigger_pipelines queues pipelines for run ids it never scoped"
        )
        assert "TestRun.project_id.in_(accessible)" in src, (
            "the accessible-project set must actually filter the query"
        )
        assert src.index("get_accessible_project_ids") < src.index("apply_async"), (
            "runs are fanned out before the scope is resolved"
        )

    def test_bulk_trigger_does_not_leak_existence(self):
        """An inaccessible run must look identical to a nonexistent one."""
        src = inspect.getsource(agents_router.bulk_trigger_pipelines)
        assert "not_found_ids" in src
        assert "403" not in src, (
            "distinguishing forbidden from not-found turns the response into an "
            "existence oracle for run ids"
        )


class TestEmailTrendsIsScoped:
    def test_the_project_is_verified_before_the_report_is_built(self):
        src = inspect.getsource(reports_router.email_trends_report)
        assert "resolve_project_scope" in src, (
            "email_trends_report mails a project's data to a caller-supplied "
            "address with no membership check"
        )
        assert src.index("resolve_project_scope") < src.index(
            "report_service.email_trends_report"
        ), "the report is generated and sent before access is verified"

    def test_the_scope_uses_the_requested_project(self):
        src = inspect.getsource(reports_router.email_trends_report)
        assert "body.project_id" in src


class TestJiraCreationIsScoped:
    def test_the_test_cases_owning_project_is_verified(self):
        src = inspect.getsource(integrations_router.create_jira_defect)
        assert "resolve_project_scope" in src, (
            "create_jira_defect files another tenant's failure text into a "
            "Jira project the caller names"
        )
        assert "TestRun.project_id" in src, (
            "TestCase has no project of its own -- resolve through TestRun"
        )
        assert src.index("resolve_project_scope") < src.index("create_jira_issue"), (
            "the Jira ticket is created before access is verified"
        )


def test_the_ratchet_now_knows_about_plural_ids():
    """The gap that hid two of these four from the scan that exists to find them."""
    from tests.test_architectural_authorization import SCOPED_IDS

    for name in ("run_ids", "test_case_id", "test_run_ids"):
        assert name in SCOPED_IDS, (
            f"{name!r} missing from SCOPED_IDS -- a route taking it as a body "
            "field would be invisible to the non-path authorization scan"
        )
