"""W31 STRAT-12 (E-10): an erased account's placeholder address is never mailed.

The homelab's SMTP is a LIVE relay, so the guard is proven ONLINE (``AI_OFFLINE_MODE``
false, the relay a public host): each of the three senders of
``services/notification/email_service.py`` refuses a ``…@erased.invalid``
recipient inside ``_assert_smtp_allowed`` — before ``aiosmtplib.send``, which is
patched with an ``AsyncMock`` and asserted NEVER awaited — while an ordinary
recipient on the same relay still reaches the (patched) send, so the guard is
the refusal, not the setup. The guard logs nothing (CodeQL: no log line after a
mail call in the guard).
"""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.core.config import settings
from app.services.notification import email_service
from app.services.notification.egress import OfflineEgressBlocked
from app.services.subject_rights_vocab import ERASED_EMAIL_DOMAIN

CFG = {"enabled": True, "host": "smtp.gmail.com", "port": 587, "tls": False, "from_address": "qa@example.com"}
ERASED_TO = "erased-0a1b2c3d@" + ERASED_EMAIL_DOMAIN
EMAIL_SERVICE_PY = Path(email_service.__file__)


@pytest.fixture
def send(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    monkeypatch.setattr(settings, "AI_OFFLINE_MODE", False)
    mock = AsyncMock(return_value=None)
    monkeypatch.setattr(email_service.aiosmtplib, "send", mock)
    return mock


async def _notification(to: str) -> None:
    await email_service.send_notification(to=to, title="t", body="b", event_type="test_failed", smtp_cfg=CFG)


async def _html(to: str) -> None:
    await email_service.send_html_email(to, "subject", "<p>x</p>", smtp_cfg=CFG)


async def _attachments(to: str) -> None:
    await email_service.send_html_email_with_attachments(
        to, "subject", "<p>x</p>", attachments=[("report.html", "<p>r</p>", "text/html")], smtp_cfg=CFG,
    )


SENDERS = [_notification, _html, _attachments]


@pytest.mark.parametrize("sender", SENDERS, ids=lambda fn: fn.__name__)
@pytest.mark.parametrize(
    "to", [ERASED_TO, "Erased User <ERASED-0A1B2C3D@ERASED.INVALID>", "x@mail.erased.invalid"],
)
async def test_every_sender_refuses_an_erased_recipient_before_the_send(send: AsyncMock, sender: Any, to: str) -> None:
    with pytest.raises(OfflineEgressBlocked) as refused:
        await sender(to)
    send.assert_not_awaited()
    assert "0a1b2c3d" not in str(refused.value).lower()   # the refusal never carries the address


@pytest.mark.parametrize("sender", SENDERS, ids=lambda fn: fn.__name__)
async def test_an_ordinary_recipient_still_reaches_the_send(send: AsyncMock, sender: Any) -> None:
    await sender("qa.person@example.com")
    send.assert_awaited_once()


async def test_a_mixed_recipient_list_is_refused_whole(send: AsyncMock) -> None:
    with pytest.raises(OfflineEgressBlocked):
        await email_service._assert_smtp_allowed("smtp.gmail.com", ["qa@example.com", ERASED_TO])
    await email_service._assert_smtp_allowed("smtp.gmail.com", ["qa@example.com", "erased.invalid@example.com"])
    send.assert_not_awaited()


def test_the_guard_logs_nothing_and_runs_before_the_residency_check() -> None:
    tree = ast.parse(EMAIL_SERVICE_PY.read_text(encoding="utf-8"))
    functions = {node.name: node for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef)}
    guard = functions["_refuse_erased_recipients"]
    called = [getattr(node.func, "attr", getattr(node.func, "id", None))
              for node in ast.walk(guard) if isinstance(node, ast.Call)]
    assert not {"info", "warning", "error", "debug", "exception"} & set(called)
    gate = functions["_assert_smtp_allowed"]
    order = [getattr(node.func, "attr", getattr(node.func, "id", None))
             for node in ast.walk(gate) if isinstance(node, ast.Call)]
    assert order.index("_refuse_erased_recipients") < order.index("assert_delivery_allowed_async")
