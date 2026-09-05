"""Network MCP has no shared account; generic service accounts remain safe."""

from __future__ import annotations

import pathlib
import re
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest


REPO = pathlib.Path(__file__).resolve().parents[3]
BACKEND = REPO / "backend"
PROJECTS_ROUTER = BACKEND / "app" / "routers" / "projects.py"
PROVISION = REPO / "scripts" / "createServiceAccount.py"
RETIRE = REPO / "scripts" / "retireLegacyMcpServiceAccount.py"
RETIRE_SERVICE = BACKEND / "app" / "services" / "legacy_mcp_retirement.py"


def test_network_deploys_do_not_publish_or_provision_shared_mcp_credentials():
    """A deployed network server must use the bearer token of each caller."""
    paths = [
        REPO / "docker-compose.yml",
        REPO / "docker-compose.release.yml",
        REPO / "k8s" / "base" / "mcp-deployment.yaml",
        REPO / "k8s" / "base" / "secrets.yaml",
    ]
    forbidden = (
        "MCP_USERNAME",
        "MCP_PASSWORD",
        "TESTLOOKUP_USERNAME",
        "TESTLOOKUP_PASSWORD",
    )
    for path in paths:
        source = path.read_text(encoding="utf-8")
        for name in forbidden:
            assert name not in source, f"{path} still carries shared {name}"

    for path in (
        REPO / "scripts" / "deploy-k8s.sh",
        REPO / "homelabsetup" / "deploy-homelab.sh",
        REPO / "openshiftsetup" / "deploy-openshift-artifactory.sh",
    ):
        source = path.read_text(encoding="utf-8")
        assert "--from-literal=MCP_USERNAME" not in source, path
        assert "--from-literal=MCP_PASSWORD" not in source, path
        assert "createServiceAccount.py" not in source, path

    docs = (
        REPO / "installation.md",
        REPO / "deploymentsteps.md",
        REPO / "homelabsetup" / "DEPLOY_TESTLOOKUP.md",
        REPO / "infra" / "cloudrun" / "mcp.env.example",
    )
    for path in docs:
        source = path.read_text(encoding="utf-8")
        assert "MCP_USERNAME" not in source, path
        assert "MCP_PASSWORD" not in source, path
        assert "mcp_service" not in source, path


def test_mcp_server_enables_bearer_verification():
    source = (REPO / "mcp" / "server.py").read_text(encoding="utf-8")
    assert "token_verifier=TestLookupTokenVerifier()" in source
    assert "auth=build_auth_settings()" in source


def test_generic_provisioning_refuses_to_invent_a_password():
    src = PROVISION.read_text(encoding="utf-8")
    assert re.search(r"if not password:", src)
    assert "refusing to create a service" in src


def test_generic_provisioning_marks_new_and_existing_accounts():
    src = PROVISION.read_text(encoding="utf-8")
    create_branch = src.split("if existing is None:")[1].split("changes = []")[0]
    converge_branch = src.split("changes = []")[1]
    assert "is_service_account=True" in create_branch
    assert "is_service_account = True" in converge_branch


def test_generic_provisioning_enrolls_and_invalidates_membership_cache():
    src = PROVISION.read_text(encoding="utf-8")
    assert "_enroll_in_all_projects" in src
    assert "invalidate_membership_cache" in src


def test_upgrade_retires_only_the_secret_named_machine_account():
    retire = RETIRE_SERVICE.read_text(encoding="utf-8")
    assert "User.username == username" in retire
    assert "if not account.is_service_account" in retire
    assert "account.is_active = False" in retire
    assert "delete(ProjectMember)" in retire
    assert "_revoke_family" in retire
    wrapper = RETIRE.read_text(encoding="utf-8")
    assert "await db.commit()" not in retire
    assert "await db.commit()" in wrapper
    assert "revoke_all_user_tokens" in wrapper

    for path in (
        REPO / "scripts" / "deploy-k8s.sh",
        REPO / "homelabsetup" / "deploy-homelab.sh",
        REPO / "openshiftsetup" / "deploy-openshift-artifactory.sh",
    ):
        deploy = path.read_text(encoding="utf-8")
        assert "LEGACY_MCP_USER=" in deploy
        assert "KEEP_LEGACY_MCP_SERVICE_ACCOUNT" in deploy
        assert "retireLegacyMcpServiceAccount.py" in deploy
        assert '--type=merge' in deploy
        assert '"MCP_USERNAME":null,"MCP_PASSWORD":null' in deploy
        rollout = re.search(r"rollout status[^\n]*testlookup-mcp", deploy)
        assert rollout is not None, f"{path} does not gate retirement on MCP rollout"
        retirement = deploy.index("retireLegacyMcpServiceAccount.py")
        assert rollout.start() < retirement, f"{path} retires credentials before MCP is ready"


@pytest.mark.asyncio
async def test_legacy_retirement_is_idempotent_and_leaves_other_accounts(monkeypatch):
    from app.services import legacy_mcp_retirement as retirement

    legacy = MagicMock(id=uuid.uuid4(), username="mcp_service", is_service_account=True)
    unrelated = MagicMock(id=uuid.uuid4(), username="ci_agent", is_active=True)
    selected = MagicMock()
    selected.scalar_one_or_none.return_value = legacy
    deleted = MagicMock()
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[selected, deleted, selected, deleted])
    db.commit = AsyncMock()
    revoke_refresh = AsyncMock()
    monkeypatch.setattr(retirement, "_revoke_family", revoke_refresh)

    first = await retirement.stage_legacy_account_retirement(db, "mcp_service")
    second = await retirement.stage_legacy_account_retirement(db, "mcp_service")

    assert first == second == ("retired", legacy.id)
    assert legacy.is_active is False
    assert unrelated.is_active is True
    assert revoke_refresh.await_count == 2
    statement = str(db.execute.await_args_list[0].args[0])
    assert "users.username" in statement


@pytest.mark.asyncio
async def test_legacy_retirement_refuses_a_human_account(monkeypatch):
    from app.services import legacy_mcp_retirement as retirement

    human = MagicMock(id=uuid.uuid4(), username="mcp_service", is_service_account=False)
    selected = MagicMock()
    selected.scalar_one_or_none.return_value = human
    db = MagicMock()
    db.execute = AsyncMock(return_value=selected)

    with pytest.raises(RuntimeError, match="not marked as a service account"):
        await retirement.stage_legacy_account_retirement(db, "mcp_service")


# Generic machine accounts are still supported for integrations other than
# network MCP and must keep their project-membership invariants.


@pytest.mark.asyncio
async def test_new_projects_enroll_every_active_service_account():
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
    from app.services.service_account_service import enroll_service_accounts_in_project

    svc_id = uuid.uuid4()
    account = MagicMock(id=svc_id, role="QA_LEAD")

    db = MagicMock()
    added = []
    db.add = MagicMock(side_effect=lambda obj: added.append(obj))
    accounts_result = MagicMock()
    accounts_result.scalars.return_value.all.return_value = [account]
    members_result = MagicMock()
    members_result.scalars.return_value.all.return_value = [svc_id]
    db.execute = AsyncMock(side_effect=[accounts_result, members_result])

    enrolled = await enroll_service_accounts_in_project(db, uuid.uuid4())
    assert enrolled == []
    assert added == []


@pytest.mark.asyncio
async def test_no_service_accounts_is_not_an_error():
    from app.services.service_account_service import enroll_service_accounts_in_project

    db = MagicMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = []
    db.execute = AsyncMock(return_value=result)
    assert await enroll_service_accounts_in_project(db, uuid.uuid4()) == []


def test_project_creation_wires_enrolment_and_cache_invalidation():
    src = PROJECTS_ROUTER.read_text(encoding="utf-8")
    body = src.split("async def create_project")[1].split("\n@router.")[0]
    assert "enroll_service_accounts_in_project" in body
    assert re.search(
        r"for account_id in enrolled_service_accounts:\s*\n\s*await invalidate_membership_cache\(account_id\)",
        body,
    ), "enrolled service accounts never have their membership cache invalidated"
    assert body.index("enroll_service_accounts_in_project(db") < body.index("await db.commit()")


def test_service_accounts_are_not_given_admin():
    src = (BACKEND / "app" / "services" / "service_account_service.py").read_text(
        encoding="utf-8"
    )
    assert "UserRole.ADMIN" not in src
    assert "role=account.role" in src, "enrolment must preserve the configured role"
