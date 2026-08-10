"""A share link must carry a hostname its recipient can actually open.

``POST /api/v1/reports/runs/{run_id}/share`` built its URL from an **SSO**
setting::

    base_url = settings.SAML_BASE_URL  # reuse the base URL setting

``SAML_BASE_URL`` defaults to ``http://localhost:8000`` and has no reason to be
set on a deployment that doesn't use SAML. Worse, the config's own
localhost warning for it is gated on ``SSO_ENABLED``, so on a non-SSO
deployment nothing ever flagged the default.

Measured against the live homelab, which sets ``PUBLIC_BASE_URL`` correctly:

============================================  ===========================
                                              result
============================================  ===========================
``PUBLIC_BASE_URL`` (configured)              ``http://testlookup.local``
``settings.public_base_url`` would resolve    ``http://testlookup.local``
``SAML_BASE_URL`` (default, SSO off)          ``http://localhost:8000``
issued share_url, followed verbatim           **connection failed**
same token on the real ingress                **HTTP 200, the report**
============================================  ===========================

So the deployment was configured correctly and the feature still emitted a dead
link — this was never a misconfiguration. The link was otherwise valid; only
the host was wrong. A share link's entire purpose is to be sent to someone
else, and both the CLI (``testlookup reports share``) and the UI's
copy-to-clipboard on the release-gate page handed out the localhost URL.

``settings.public_base_url`` is the property documented for this
(``PUBLIC_BASE_URL`` → first ``CORS_ORIGINS`` entry → localhost), already used
by the GitHub checks, GitHub PR-comment and GitLab integrations.
"""
from __future__ import annotations

import inspect

import pytest

pytest.importorskip("sqlalchemy")

from app.core.config import Settings  # noqa: E402
from app.routers import reports  # noqa: E402

pytestmark = pytest.mark.regression

SOURCE = inspect.getsource(reports)

#: Comments are stripped so the check reads *code*, not prose — the fix's own
#: comment names the old setting to explain the bug, and matching that would
#: fail the test on its own documentation.
CODE = "\n".join(
    line.split("#", 1)[0] for line in SOURCE.splitlines()
)


class TestTheShareUrlIsBuiltFromThePublicBaseUrl:
    def test_it_does_not_use_the_saml_setting(self):
        assert "SAML_BASE_URL" not in CODE, (
            "the share link is built from an SSO setting that defaults to "
            "localhost:8000 — recipients get a URL that does not resolve"
        )

    def test_it_uses_the_documented_public_base_url(self):
        assert "settings.public_base_url" in SOURCE, (
            "share links must use the externally-reachable base URL"
        )


class TestTheResolverItself:
    """Pins the fallback chain the fix now depends on."""

    def test_an_explicit_public_base_url_wins(self):
        s = Settings(
            PUBLIC_BASE_URL="https://testlookup.example.com",
            CORS_ORIGINS_RAW="http://ignored.local",
        )
        assert s.public_base_url == "https://testlookup.example.com"

    def test_a_trailing_slash_is_stripped(self):
        """Callers append ``/api/v1/...`` directly — a double slash would
        produce a subtly wrong link rather than an obviously broken one."""
        s = Settings(PUBLIC_BASE_URL="https://testlookup.example.com/")
        assert s.public_base_url == "https://testlookup.example.com"

    def test_it_falls_back_to_the_first_cors_origin(self):
        """The homelab case: an ingress hostname is known even when
        PUBLIC_BASE_URL is not set explicitly."""
        s = Settings(PUBLIC_BASE_URL="", CORS_ORIGINS_RAW="http://testlookup.local")
        assert s.public_base_url == "http://testlookup.local"

    def test_it_never_returns_the_saml_default(self):
        """Whatever the fallback, it must not be the port-8000 API default that
        caused this — that host is only reachable on the server itself."""
        s = Settings(PUBLIC_BASE_URL="", CORS_ORIGINS_RAW="")
        assert s.public_base_url != "http://localhost:8000"
