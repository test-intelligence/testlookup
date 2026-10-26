"""W17b ADM-01 / ADM-02: the new unauthenticated routes are throttled, and the mails escape.

Accepting an invitation creates an account, and a reset link replaces a
password — both without a session. A per-IP ceiling on each (and the
per-mailbox Redis counter on ``/password/forgot``) is mandatory, not polish:
this pins all four paths in ``_AUTH_RATE_LIMITS``. The admin-side routes stay
ADMIN-only with no project-key access. The mail bodies HTML-escape every value.
"""
from __future__ import annotations

import inspect

import pytest

PUBLIC_PATHS = (
    "/api/v1/auth/invitations/preview",
    "/api/v1/auth/invitations/accept",
    "/api/v1/auth/password/forgot",
    "/api/v1/auth/password/reset",
)


@pytest.mark.parametrize("path", PUBLIC_PATHS)
def test_every_public_link_route_is_rate_limited(path):
    from app.main import _AUTH_RATE_LIMITS, app

    assert path in _AUTH_RATE_LIMITS
    limit, message = _AUTH_RATE_LIMITS[path]
    assert limit.endswith("/minute") and int(limit.split("/")[0]) <= 10 and message
    assert any(getattr(r, "path", "") == path for r in app.routes), f"{path} is not mounted"


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/users/invitations",
        "/api/v1/users/invitations/{invitation_id}/revoke",
        "/api/v1/users/invitations/{invitation_id}/resend",
        "/api/v1/users/{user_id}/password-reset-link",
        "/api/v1/users/invite",
    ],
)
def test_admin_routes_require_admin_and_refuse_a_project_key(path):
    from app.main import app

    route = next(r for r in app.routes if getattr(r, "path", "") == path)
    source = inspect.getsource(route.endpoint)
    assert "require_role(UserRole.ADMIN)" in source and "allow_project_key" not in source


def test_the_invitations_list_is_not_swallowed_by_the_user_id_route():
    from app.main import app

    paths = [getattr(r, "path", "") for r in app.routes if "GET" in getattr(r, "methods", set())]
    assert paths.index("/api/v1/users/invitations") < paths.index("/api/v1/users/{user_id}")


def test_the_mails_escape_every_value():
    from app.services.notification.email_templates import render_invitation_email, render_password_reset_email

    html = render_invitation_email("<script>x</script>", "QA <b>", "https://h/accept-invite#token=a\"b", "<now>")
    assert "<script>" not in html and "&lt;script&gt;" in html and 'token=a"b' not in html
    reset = render_password_reset_email('https://h/r#token="><img>', 60, True)
    assert "<img>" not in reset


def test_links_carry_the_token_in_the_fragment_only():
    from app.services.one_time_tokens import fragment_link, hash_token, new_token

    raw, digest = new_token()
    assert digest == hash_token(raw) and len(digest) == 64 and raw not in digest
    link = fragment_link("/accept-invite", raw)
    assert link.endswith(f"/accept-invite#token={raw}") and "?" not in link
