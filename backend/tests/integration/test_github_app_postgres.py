"""W19 INT-06 routes against a real database: the GitHub App settings, the install URL and the setup callback.

* PUT stores the App id / slug and the private key in ``secret_service``; GET
  says ``private_key_set`` and never returns the key (nor does any log line);
  a key that is not an RSA PEM is a 422;
* the install URL names the App's slug and carries a state the callback accepts
  only from the same user;
* the callback records the installation and switches the project to ``app``;
  switching back to ``pat`` through the integration PUT keeps the installation id.

Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` and a database at head; skips otherwise.
"""
from __future__ import annotations

import logging
import os
import uuid
from urllib.parse import parse_qs, urlsplit

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytest.importorskip("asyncpg")
pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

PEM = rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()


def _dsn() -> str:
    value = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


@pytest.fixture
async def world():
    from app.models.postgres import AppSetting, GitHubIntegration, Project, SecretRef, SettingsAuditLog, User, UserRole

    engine = create_async_engine(_dsn(), pool_size=1, max_overflow=0)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    marker = "zqx" + uuid.uuid4().hex[:8].replace("a", "q")
    admin = User(id=uuid.uuid4(), email=f"a-{marker}@testlookup.dev", username=f"a-{marker}", full_name="Admin",
                 hashed_password="x", role=UserRole.ADMIN, is_active=True)
    lead = User(id=uuid.uuid4(), email=f"l-{marker}@testlookup.dev", username=f"l-{marker}", full_name="Lead",
                hashed_password="x", role=UserRole.QA_LEAD, is_active=True)
    project = Project(id=uuid.uuid4(), name=f"Payments {marker}", slug=f"payments-{marker}", is_active=True)
    async with factory() as db:
        before = (await db.execute(select(AppSetting).where(AppSetting.key == "github_app"))).scalar_one_or_none()
        saved = dict(before.value) if before is not None and before.value else None
        db.add_all([admin, lead, project])
        await db.flush()
        db.add(GitHubIntegration(project_id=project.id, repo_owner="acme", repo_name="payments", enabled=True,
                                 api_base_url="https://api.github.com"))
        await db.commit()
    try:
        async with factory() as db:
            yield {"db": db, "admin": admin, "lead": lead, "project": project}
    finally:
        async with factory() as db:
            await db.execute(delete(GitHubIntegration).where(GitHubIntegration.project_id == project.id))
            await db.execute(delete(SettingsAuditLog).where(SettingsAuditLog.actor_id.in_([admin.id, lead.id])))
            await db.execute(delete(Project).where(Project.id == project.id))
            await db.execute(delete(User).where(User.id.in_([admin.id, lead.id])))
            if saved is None:
                await db.execute(delete(AppSetting).where(AppSetting.key == "github_app"))
                await db.execute(delete(SecretRef).where(SecretRef.scope == "github_app"))
            await db.commit()
        await engine.dispose()


async def test_settings_store_the_key_and_never_return_it(world, caplog):
    from app.models.schemas import GitHubAppSettingsWrite
    from app.routers.github_app import get_github_app_settings, put_github_app_settings

    caplog.set_level(logging.DEBUG)
    db, admin = world["db"], world["admin"]
    out = await put_github_app_settings(GitHubAppSettingsWrite(app_id="4242", slug="testlookup-demo", private_key=PEM,
                                                               webhook_secret="hook-secret-x"), db=db, current_user=admin)
    assert out.configured and out.private_key_set and out.webhook_secret_set
    read = await get_github_app_settings(db=db, current_user=admin)
    dump = read.model_dump_json()
    assert "PRIVATE KEY" not in dump and "hook-secret-x" not in dump and read.slug == "testlookup-demo"
    assert "PRIVATE KEY" not in caplog.text and "hook-secret-x" not in caplog.text
    with pytest.raises(HTTPException) as exc:
        await put_github_app_settings(GitHubAppSettingsWrite(app_id="4242", slug="x", private_key="not a key"), db=db, current_user=admin)
    assert exc.value.status_code == 422


async def test_install_url_and_callback_record_the_installation(world):
    from app.models.postgres import GitHubIntegration
    from app.models.schemas import GitHubAppSettingsWrite, GitHubAppSetupCallback, GitHubIntegrationWrite
    from app.routers.github_app import github_app_install_url, github_app_setup_callback, put_github_app_settings
    from app.routers.github_integration import upsert_github_integration

    db, admin, lead, project = world["db"], world["admin"], world["lead"], world["project"]
    await put_github_app_settings(GitHubAppSettingsWrite(app_id="4242", slug="testlookup-demo", private_key=PEM),
                                  db=db, current_user=admin)
    url = await github_app_install_url(project_id=project.id, db=db, current_user=admin)
    parts = urlsplit(url.url)
    assert parts.netloc == "github.com" and parts.path == "/apps/testlookup-demo/installations/new"
    state = parse_qs(parts.query)["state"][0]

    with pytest.raises(HTTPException) as exc:  # the link forwarded to another user
        await github_app_setup_callback(GitHubAppSetupCallback(installation_id=777, state=state), db=db, current_user=lead)
    assert exc.value.status_code == 400

    done = await github_app_setup_callback(GitHubAppSetupCallback(installation_id=777, state=state), db=db, current_user=admin)
    assert done.installation_id == 777 and done.auth_kind == "app"
    db.expire_all()
    row = (await db.execute(select(GitHubIntegration).where(GitHubIntegration.project_id == project.id))).scalar_one()
    assert row.auth_kind == "app" and row.installation_id == 777

    back = await upsert_github_integration(project.id, GitHubIntegrationWrite(repo_owner="acme", repo_name="payments",
                                                                              auth_kind="pat"), db=db, current_user=admin)
    await db.commit()
    assert back.auth_kind == "pat" and back.installation_id == 777
