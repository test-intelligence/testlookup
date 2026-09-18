"""SMTP password keep/replace/clear semantics stay consistent end to end."""
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.modules.setdefault("aiosmtplib", MagicMock())

from app.routers import app_settings as router  # noqa: E402
from app.models.schemas import SmtpConfigUpdate  # noqa: E402
from app.services import secret_service  # noqa: E402
from app.services.notification import email_service  # noqa: E402


def _payload(password):
    return SmtpConfigUpdate(
        enabled=True,
        host="smtp.example.test",
        port=587,
        user="mailer",
        password=password,
        from_address="qa@example.test",
        implicit_tls=False,
    )


def _db():
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    return SimpleNamespace(
        execute=AsyncMock(return_value=result),
        add=MagicMock(),
        commit=AsyncMock(),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("password", "initially_set", "expected_set", "store_calls", "expire_calls", "audits_password"),
    [
        (None, True, True, 0, 0, False),
        ("replacement", False, True, 1, 0, True),
        ("", True, False, 0, 1, True),
    ],
)
async def test_smtp_secret_tristate(
    password, initially_set, expected_set, store_calls, expire_calls, audits_password,
):
    db = _db()
    actor = SimpleNamespace(id="actor-id")
    store = AsyncMock()
    expire = AsyncMock(return_value=initially_set)
    has_secret = AsyncMock(return_value=initially_set)
    audit = AsyncMock()

    with patch.object(router, "store_secret", new=store), patch(
        "app.services.secret_service.expire_secret", new=expire,
    ), patch(
        "app.services.secret_service.has_secret", new=has_secret,
    ), patch.object(router, "log_settings_change", new=audit), patch.object(
        router.settings, "SMTP_PASSWORD", "",
    ):
        response = await router.update_smtp_config(
            _payload(password), current_user=actor, db=db,
        )

    assert response.password_set is expected_set
    assert store.await_count == store_calls
    assert expire.await_count == expire_calls
    changed_fields = audit.await_args.kwargs["changed_fields"]
    assert ("password" in changed_fields) is audits_password


@pytest.mark.asyncio
async def test_runtime_smtp_resolver_reads_encrypted_secret_ref(monkeypatch):
    """The delivery resolver must use the same saved password as Test SMTP."""
    row = SimpleNamespace(
        value={
            "enabled": True,
            "host": "smtp.saved.test",
            "port": 587,
            "user": "mailer",
            "from_address": "qa@example.test",
            "tls": False,
        }
    )
    result = MagicMock()
    result.scalar_one_or_none.return_value = row

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def execute(self, *_args, **_kwargs):
            return result

    import app.db.postgres as postgres

    read_secret = AsyncMock(return_value="saved-encrypted-password")
    monkeypatch.setattr(postgres, "get_session_factory", lambda: _Session)
    monkeypatch.setattr(secret_service, "read_secret", read_secret)
    monkeypatch.setattr(
        email_service.settings, "SMTP_PASSWORD", "stale-environment-password"
    )

    resolved = await email_service._get_smtp_cfg()

    assert resolved["password"] == "saved-encrypted-password"
    read_secret.assert_awaited_once()
    assert read_secret.await_args.args[1:] == ("smtp_config", "password")
