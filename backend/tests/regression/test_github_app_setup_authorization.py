"""Defensive authorization checks for binding a project to a GitHub App installation.

The setup state is a hashed, expiring, single-use server transaction.  It is
bound to the initiating administrator and the repository they approved, and
the callback verifies the installation's repository access before committing.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.models.postgres import UserRole
from app.models.schemas import GitHubAppSetupCallback
from app.routers import github_app
from app.services import github_app_setup_state, github_auth, github_checks_service, settings_audit_service

PROJECT_ID = uuid.uuid4()
ADMIN_ID = uuid.uuid4()
LEAD_ID = uuid.uuid4()
APP = {"app_id": "4242", "slug": "testlookup-demo", "private_key": "private"}


class _Redis:
    def __init__(self):
        self.data: dict[str, str] = {}
        self.expiries: list[int] = []

    async def set(self, key, value, *, ex, nx):
        assert nx is True
        if key in self.data:
            return False
        self.data[key] = value
        self.expiries.append(ex)
        return True

    async def getdel(self, key):
        return self.data.pop(key, None)


class _DB:
    def __init__(self, timeline: list[str] | None = None):
        self.commits = 0
        self.timeline = timeline
        self.added = []

    def add(self, value):
        self.added.append(value)

    async def flush(self):
        return None

    async def commit(self):
        self.commits += 1
        if self.timeline is not None:
            self.timeline.append("commit")


def _user(user_id: uuid.UUID, role: UserRole):
    return SimpleNamespace(id=user_id, role=role, username=role.value.lower(), full_name=role.value)


def _transaction(user_id: uuid.UUID = ADMIN_ID):
    return github_app_setup_state.SetupTransaction(
        project_id=PROJECT_ID,
        user_id=user_id,
        app_id="4242",
        api_base_url="https://api.github.com",
        repo_owner="acme",
        repo_name="payments",
    )


async def test_setup_transaction_is_hashed_expiring_and_single_use(monkeypatch):
    redis = _Redis()
    monkeypatch.setattr(github_app_setup_state, "get_redis", lambda: redis)

    state = await github_app_setup_state.issue_setup_transaction(_transaction())
    stored = "".join(redis.data.keys()) + "".join(redis.data.values())
    assert state not in stored
    assert redis.expiries == [github_app_setup_state.STATE_TTL_SECONDS]
    assert await github_app_setup_state.consume_setup_transaction(state) == _transaction()
    assert await github_app_setup_state.consume_setup_transaction(state) is None


async def test_qa_lead_cannot_bind_an_arbitrary_installation(monkeypatch):
    async def consume(_state):
        return _transaction(LEAD_ID)

    scope = False

    async def resolve(*_args):
        nonlocal scope
        scope = True

    monkeypatch.setattr(github_app_setup_state, "consume_setup_transaction", consume)
    monkeypatch.setattr(github_app, "resolve_project_scope", resolve)
    db = _DB()
    with pytest.raises(HTTPException) as exc:
        await github_app.github_app_setup_callback(
            GitHubAppSetupCallback(installation_id=999999, state="x" * 43),
            db=db,
            current_user=_user(LEAD_ID, UserRole.QA_LEAD),
        )
    assert exc.value.status_code == 403
    assert scope is False and db.commits == 0


async def test_forwarded_administrator_state_is_burned_and_refused(monkeypatch):
    consumed: list[str] = []

    async def consume(state):
        consumed.append(state)
        return _transaction(ADMIN_ID)

    monkeypatch.setattr(github_app_setup_state, "consume_setup_transaction", consume)
    with pytest.raises(HTTPException) as exc:
        await github_app.github_app_setup_callback(
            GitHubAppSetupCallback(installation_id=777, state="y" * 43),
            db=_DB(),
            current_user=_user(LEAD_ID, UserRole.QA_LEAD),
        )
    assert exc.value.status_code == 400
    assert consumed == ["y" * 43]


async def _authorize_admin_callback(monkeypatch, *, row, timeline: list[str]):
    async def consume(_state):
        return _transaction()

    async def resolve(*_args):
        return None

    async def get_integration(_db, _project_id, *, for_update=False):
        if for_update:
            timeline.append("lock")
        return row

    async def load_app(_db):
        return APP

    async def verify(api_base, installation_id, app, owner, repo):
        assert (api_base, installation_id, app, owner, repo) == (
            "https://api.github.com", 777, APP, "acme", "payments",
        )
        timeline.append("verify")

    async def audit(*_args, **_kwargs):
        timeline.append("audit")

    async def activity(*_args, **_kwargs):
        timeline.append("activity")

    async def release(_db):
        timeline.append("release")
        return True

    monkeypatch.setattr(github_app_setup_state, "consume_setup_transaction", consume)
    monkeypatch.setattr(github_app, "resolve_project_scope", resolve)
    monkeypatch.setattr(github_checks_service, "get_integration", get_integration)
    monkeypatch.setattr(github_auth, "load_app_config", load_app)
    monkeypatch.setattr(github_auth, "verify_installation_repository", verify)
    monkeypatch.setattr(settings_audit_service, "log_settings_change", audit)
    monkeypatch.setattr(github_app, "record_activity", activity)
    monkeypatch.setattr(github_app, "release_read_connection", release)
    monkeypatch.setattr(github_app.settings, "GITHUB_APP_API_BASE_URL", "https://api.github.com")

    db = _DB(timeline)
    result = await github_app.github_app_setup_callback(
        GitHubAppSetupCallback(installation_id=777, state="z" * 43),
        db=db,
        current_user=_user(ADMIN_ID, UserRole.ADMIN),
    )
    return db, result


async def test_administrator_binding_verifies_repository_before_commit(monkeypatch):
    timeline: list[str] = []
    row = SimpleNamespace(
        id=uuid.uuid4(),
        repo_owner="acme",
        repo_name="payments",
        api_base_url="https://api.github.com",
        auth_kind="pat",
        installation_id=None,
        updated_by_user_id=None,
    )
    db, result = await _authorize_admin_callback(monkeypatch, row=row, timeline=timeline)
    assert timeline == ["release", "verify", "lock", "audit", "activity", "commit"]
    assert db.commits == 1 and result.installation_id == 777 and result.auth_kind == "app"
    assert row.installation_id == 777 and row.auth_kind == "app" and row.updated_by_user_id == ADMIN_ID


async def test_repository_change_requires_a_fresh_transaction(monkeypatch):
    async def consume(_state):
        return _transaction()

    async def resolve(*_args):
        return None

    row = SimpleNamespace(
        repo_owner="acme",
        repo_name="changed-after-approval",
        api_base_url="https://api.github.com",
        auth_kind="pat",
        installation_id=None,
    )

    async def get_integration(_db, _project_id, *, for_update=False):
        assert for_update is False
        return row

    async def load_app(_db):
        return APP

    verified = False

    async def verify(*_args):
        nonlocal verified
        verified = True

    monkeypatch.setattr(github_app_setup_state, "consume_setup_transaction", consume)
    monkeypatch.setattr(github_app, "resolve_project_scope", resolve)
    monkeypatch.setattr(github_checks_service, "get_integration", get_integration)
    monkeypatch.setattr(github_auth, "load_app_config", load_app)
    monkeypatch.setattr(github_auth, "verify_installation_repository", verify)
    monkeypatch.setattr(github_app.settings, "GITHUB_APP_API_BASE_URL", "https://api.github.com")

    db = _DB()
    with pytest.raises(HTTPException) as exc:
        await github_app.github_app_setup_callback(
            GitHubAppSetupCallback(installation_id=777, state="z" * 43),
            db=db,
            current_user=_user(ADMIN_ID, UserRole.ADMIN),
        )
    assert exc.value.status_code == 409
    assert verified is False and db.commits == 0 and row.installation_id is None and row.auth_kind == "pat"


async def test_project_editor_cannot_reuse_approval_for_another_repository(monkeypatch):
    row = SimpleNamespace(
        repo_owner="acme",
        repo_name="payments",
        api_base_url="https://api.github.com",
        enabled=True,
        has_pat=False,
        auth_kind="app",
        installation_id=777,
        pr_comment_mode="failures_only",
        updated_by_user_id=ADMIN_ID,
        updated_at=None,
    )
    locked: list[bool] = []

    async def get_integration(_db, _project_id, *, for_update=False):
        locked.append(for_update)
        return row

    monkeypatch.setattr(github_checks_service, "get_integration", get_integration)
    result = await github_checks_service.upsert_integration(
        _DB(),
        project_id=PROJECT_ID,
        actor=_user(LEAD_ID, UserRole.QA_LEAD),
        enabled=True,
        repo_owner="acme",
        repo_name="other",
        api_base_url="https://api.github.com",
        pat=None,
        auth_kind="app",
    )
    assert locked == [True]
    assert result.repo_name == "other" and result.auth_kind == "app" and result.installation_id is None
