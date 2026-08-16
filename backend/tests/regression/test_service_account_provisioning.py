"""The MCP service account must exist, and must be able to see projects.

Found on the homelab after fixing the NetworkPolicy that blocked MCP from the
backend. With connectivity restored, tool calls failed differently::

    tools/call health_check -> "Backend unreachable at http://testlookup-backend:8000:
                                Client error '401 Unauthorized' for .../auth/login"

    MCP pod env: TESTLOOKUP_USERNAME=mcp_service   (secretKeyRef MCP_USERNAME)
    psql: select count(*) from users where username='mcp_service'  ->  0

`deploy-homelab.sh` generates `MCP_PASS`, writes `MCP_USERNAME`/`MCP_PASSWORD`
into `testlookup-secrets`, prints them under a **"SAVE THESE CREDENTIALS —
SHOWN ONLY ONCE"** banner and saves them to `.homelab-credentials` — but never
created the account. The deploy handed the operator working-looking credentials
for a user that did not exist.

Creating it was not sufficient either. Non-admin users only see projects they
are a member of, so a fresh QA_LEAD service account authenticated and then
answered "No active projects found." for every listing tool while five projects
existed, and `get_quarantine_stats` reported all zeros. It needs membership —
and needs it for projects created *later*, which is why the flag exists and why
project creation enrols them.

Two guards, two classes:

  * **a credential a deploy publishes must correspond to an account that
    deploy creates** — otherwise the deploy is handing out a key to nothing;
  * **a service account must be able to see projects created after it** —
    otherwise it goes quietly blind as the deployment grows.
"""
from __future__ import annotations

import pathlib
import re
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

REPO = pathlib.Path(__file__).resolve().parents[3]
BACKEND = REPO / "backend"
DEPLOY = REPO / "homelabsetup" / "deploy-homelab.sh"
PROVISION = REPO / "scripts" / "createServiceAccount.py"
PROJECTS_ROUTER = BACKEND / "app" / "routers" / "projects.py"


# ── The deploy must create what it publishes ────────────────────────────────


def test_the_deploy_provisions_the_account_whose_credentials_it_prints():
    """The regression. The secret was written; the user never was."""
    script = DEPLOY.read_text(encoding="utf-8")
    assert "MCP_USERNAME" in script, "deploy no longer writes MCP credentials"
    # Must actually EXECUTE the script. Asserting the mere presence of the
    # filename passed even with the invocation replaced by `true`, because the
    # name also appears in the failure-help text — a vacuous guard.
    assert re.search(
        r"python\s*<\s*\"\$REPO_ROOT/scripts/createServiceAccount\.py\"", script
    ), (
        "deploy-homelab.sh writes MCP credentials into testlookup-secrets but "
        "never runs createServiceAccount.py — every MCP tool 401s"
    )


def test_the_deploy_reads_the_password_from_the_secret_not_a_shell_var():
    """Step 4 is skipped when the secret already exists, so $MCP_PASS is unset
    on every re-run — which is precisely when the account still needs
    converging. Reading the secret back is what makes the step idempotent."""
    script = DEPLOY.read_text(encoding="utf-8")
    # "Step 10b" appears in both the section comment and the header() call, so
    # take everything after the LAST occurrence — splitting on the first only
    # yields the comment block and the check passes/fails for the wrong reason.
    block = script.split("Step 10b")[-1]
    assert "jsonpath='{.data.MCP_PASSWORD}'" in block, (
        "the provisioning step does not read the password from the secret"
    )
    assert "$MCP_PASS\"" not in block, (
        "reading $MCP_PASS makes the step a no-op on re-runs, when Step 4 is "
        "skipped and the variable is unset"
    )


def test_provisioning_refuses_to_invent_a_password():
    """A defaulted password on an account that can quarantine tests and trigger
    analysis is worse than no account."""
    src = PROVISION.read_text(encoding="utf-8")
    assert re.search(r"if not password:", src)
    assert "refusing to create a service" in src


def test_provisioning_marks_the_account_as_a_service_account():
    """The flag is what lets project creation find it later.

    Both branches are checked separately. A single `x or y` assertion passed
    with the flag deleted from the create branch, because the converge branch
    still matched — so a freshly provisioned account would never be enrolled in
    any project created after it.
    """
    src = PROVISION.read_text(encoding="utf-8")
    create_branch = src.split("if existing is None:")[1].split("changes = []")[0]
    assert "is_service_account=True" in create_branch, (
        "a newly created service account is not flagged, so project creation "
        "will never enrol it"
    )
    converge_branch = src.split("changes = []")[1]
    assert "is_service_account = True" in converge_branch, (
        "an account created before this flag existed is never back-filled"
    )


def test_provisioning_enrolls_and_invalidates_the_membership_cache():
    """The accessible-project set is cached for five minutes. An account
    enrolled but not invalidated reports "no projects" — indistinguishable from
    enrolment having failed. (This cost real debugging time when done by hand.)"""
    src = PROVISION.read_text(encoding="utf-8")
    assert "_enroll_in_all_projects" in src
    assert "invalidate_membership_cache" in src


# ── Projects created later must be visible ──────────────────────────────────


@pytest.mark.asyncio
async def test_new_projects_enroll_every_active_service_account():
    """Enrolling once at deploy time is not enough: without this, a project
    created afterwards is invisible to the MCP server forever."""
    from app.services.service_account_service import enroll_service_accounts_in_project

    svc_id = uuid.uuid4()
    account = MagicMock(id=svc_id, role="QA_LEAD")
    project_id = uuid.uuid4()

    added = []
    db = MagicMock()
    db.add = MagicMock(side_effect=lambda obj: added.append(obj))

    accounts_result = MagicMock()
    accounts_result.scalars.return_value.all.return_value = [account]
    members_result = MagicMock()
    members_result.scalars.return_value.all.return_value = []
    db.execute = AsyncMock(side_effect=[accounts_result, members_result])

    enrolled = await enroll_service_accounts_in_project(db, project_id)

    assert enrolled == [svc_id]
    assert len(added) == 1
    assert added[0].user_id == svc_id
    assert added[0].project_id == project_id
    assert added[0].role == "QA_LEAD", "service account lost its configured role"


@pytest.mark.asyncio
async def test_enrolment_is_idempotent():
    """Re-running must not create duplicate memberships — there is a unique
    constraint on (user_id, project_id) and hitting it would 500 the create."""
    from app.services.service_account_service import enroll_service_accounts_in_project

    svc_id = uuid.uuid4()
    account = MagicMock(id=svc_id, role="QA_LEAD")

    db = MagicMock()
    added = []
    db.add = MagicMock(side_effect=lambda obj: added.append(obj))
    accounts_result = MagicMock()
    accounts_result.scalars.return_value.all.return_value = [account]
    members_result = MagicMock()
    members_result.scalars.return_value.all.return_value = [svc_id]  # already a member
    db.execute = AsyncMock(side_effect=[accounts_result, members_result])

    enrolled = await enroll_service_accounts_in_project(db, uuid.uuid4())
    assert enrolled == []
    assert added == []


@pytest.mark.asyncio
async def test_no_service_accounts_is_not_an_error():
    """A deployment that never provisioned one must still create projects."""
    from app.services.service_account_service import enroll_service_accounts_in_project

    db = MagicMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = []
    db.execute = AsyncMock(return_value=result)
    assert await enroll_service_accounts_in_project(db, uuid.uuid4()) == []


def test_project_creation_wires_enrolment_and_cache_invalidation():
    """The service stages; the router commits and then invalidates. Enrolling
    without invalidating leaves the account blind for up to five minutes, which
    is the same symptom as not enrolling at all."""
    src = PROJECTS_ROUTER.read_text(encoding="utf-8")
    body = src.split("async def create_project")[1].split("\n@router.")[0]
    assert "enroll_service_accounts_in_project" in body
    assert re.search(
        r"for account_id in enrolled_service_accounts:\s*\n\s*await invalidate_membership_cache\(account_id\)",
        body,
    ), "enrolled service accounts never have their membership cache invalidated"
    # Enrolment must be staged BEFORE the commit so it lands in the same
    # transaction as the project itself.
    assert body.index("enroll_service_accounts_in_project(db") < body.index("await db.commit()")


def test_service_accounts_are_not_given_admin():
    """The operator picks the role. Granting ADMIN would bypass per-project
    authorisation entirely and make the membership model meaningless for the
    one account that talks to every project."""
    src = (BACKEND / "app" / "services" / "service_account_service.py").read_text(
        encoding="utf-8"
    )
    assert "UserRole.ADMIN" not in src
    assert "role=account.role" in src, "enrolment must preserve the configured role"
