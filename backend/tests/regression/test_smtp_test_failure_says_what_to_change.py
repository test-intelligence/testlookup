"""Owner report 2026-10-10: "unable to save the SMTP password".

It had saved. Gmail on port 587 with implicit TLS failed the test send with
``ssl.SSLError: [SSL: WRONG_VERSION_NUMBER]`` and the page said only "Failed
to send test email. Please verify the SMTP configuration", which read as the
password not taking. The test result now names the change to make, and never
includes a credential.
"""
from __future__ import annotations

import pytest

from app.routers.app_settings import smtp_failure_hint


class _SMTPException(Exception):
    pass


class SMTPConnectError(_SMTPException, ConnectionError):
    """Same name and bases as aiosmtplib's; the hint matches by class name, so
    this test holds when another suite has stubbed ``sys.modules["aiosmtplib"]``."""


class SMTPServerDisconnected(_SMTPException, ConnectionError):
    pass


class SMTPAuthenticationError(_SMTPException):
    pass


aiosmtplib = type("aiosmtplib", (), {
    "SMTPConnectError": SMTPConnectError,
    "SMTPServerDisconnected": SMTPServerDisconnected,
    "SMTPAuthenticationError": lambda code, msg: SMTPAuthenticationError(f"({code}, {msg!r})"),
})

pytestmark = pytest.mark.regression


def test_implicit_tls_on_587_says_switch_to_starttls():
    exc = aiosmtplib.SMTPConnectError(
        "Error connecting to smtp.gmail.com on port 587: [SSL: WRONG_VERSION_NUMBER] wrong version number"
    )
    msg = smtp_failure_hint(exc, port=587, implicit_tls=True)
    assert "STARTTLS" in msg and "turn implicit TLS off" in msg


def test_starttls_on_465_says_switch_to_implicit_tls():
    exc = aiosmtplib.SMTPServerDisconnected("Connection lost")
    assert "turn implicit TLS on" in smtp_failure_hint(exc, port=465, implicit_tls=False)


def test_a_rejected_login_says_so_and_mentions_app_passwords():
    exc = aiosmtplib.SMTPAuthenticationError(535, "5.7.8 Username and Password not accepted")
    msg = smtp_failure_hint(exc, port=587, implicit_tls=False)
    assert "rejected the username or password" in msg and "app password" in msg


def test_an_unreachable_server_names_the_port():
    exc = aiosmtplib.SMTPConnectError("Error connecting to mail.example.com on port 2525: refused")
    assert "port 2525" in smtp_failure_hint(exc, port=2525, implicit_tls=False)


def test_anything_else_keeps_the_generic_text():
    assert smtp_failure_hint(ValueError("x"), port=2525, implicit_tls=False).startswith("Failed to send test email")
