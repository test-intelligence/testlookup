"""Regression: Tier-3 security hardening (audit items S7, S8, S11).

S7 — the auth rate-limit middleware (`_AUTH_RATE_LIMITS`) covered /login and
     /register but NOT /refresh, leaving the token-mint endpoint open to
     refresh-token grinding. Now covered.
S8 — the knowledge domain-allowlist accepted arbitrary strings (`*`, IPs,
     scheme/path/port). The allowlist is the domain gate that complements the
     url_connector SSRF guard, so entries must be real FQDNs. (Rejecting `*`
     etc. is behaviour-preserving: the `hostname == d or endswith('.'+d)`
     matcher never honoured them anyway.)
S11 — `validate_production_secrets` now warns when CORS_ORIGINS contains a
     wildcard in production/staging (credentialed any-origin access).
"""
from __future__ import annotations

import pytest

pytest.importorskip("pydantic")

pytestmark = pytest.mark.regression


# ── S7: /refresh is rate-limited ─────────────────────────────────────────────

def test_refresh_endpoint_is_rate_limited():
    # app.main pulls in the full app graph (llm_factory etc.), which isn't
    # importable in every local env — skip rather than fail there; CI imports it.
    try:
        from app.main import _AUTH_RATE_LIMITS
    except Exception as exc:  # pragma: no cover - env-dependent
        pytest.skip(f"app.main not importable here: {exc}")

    assert "/api/v1/auth/refresh" in _AUTH_RATE_LIMITS
    limit, msg = _AUTH_RATE_LIMITS["/api/v1/auth/refresh"]
    assert limit.endswith("/minute") and msg
    # the previously-covered endpoints are still present (no regression)
    assert "/api/v1/auth/login" in _AUTH_RATE_LIMITS
    assert "/api/v1/auth/register" in _AUTH_RATE_LIMITS


# ── S8: domain-allowlist validation ──────────────────────────────────────────

@pytest.mark.parametrize(
    "bad",
    [
        "*",                       # wildcard
        "*.corp.com",              # wildcard subdomain
        "https://jira.corp.com",   # scheme
        "jira.corp.com/path",      # path
        "jira.corp.com:8080",      # port
        "10.0.0.5",                # IPv4 literal
        "169.254.169.254",         # metadata IP
        "localhost",               # no dot / not FQDN
        "jira corp com",           # whitespace
    ],
)
def test_allowlist_rejects_non_fqdn_entries(bad):
    from pydantic import ValidationError
    from app.models.schemas import KnowledgeDomainAllowlistUpdate

    with pytest.raises(ValidationError):
        KnowledgeDomainAllowlistUpdate(domains=[bad])


def test_allowlist_accepts_valid_fqdns_and_normalizes():
    from app.models.schemas import KnowledgeDomainAllowlistUpdate

    out = KnowledgeDomainAllowlistUpdate(
        domains=["  Jira.Corp.com ", "confluence.example.co.uk"]
    )
    assert out.domains == ["jira.corp.com", "confluence.example.co.uk"]


def test_allowlist_empty_is_allowed_clear_semantics():
    # An empty / all-blank list clears the allowlist (permissive) — preserved.
    from app.models.schemas import KnowledgeDomainAllowlistUpdate

    assert KnowledgeDomainAllowlistUpdate(domains=[]).domains == []
    assert KnowledgeDomainAllowlistUpdate(domains=["  ", ""]).domains == []


# ── S11: CORS wildcard warning in production ─────────────────────────────────

def test_cors_wildcard_warns_in_production():
    from app.core.config import Settings

    s = Settings(APP_ENV="production", CORS_ORIGINS='["*"]')
    warnings = s.validate_production_secrets()
    assert any("CORS_ORIGINS contains a wildcard" in w for w in warnings)


def test_cors_explicit_origins_no_warning():
    from app.core.config import Settings

    s = Settings(APP_ENV="production", CORS_ORIGINS='["https://app.example.com"]')
    warnings = s.validate_production_secrets()
    assert not any("CORS_ORIGINS contains a wildcard" in w for w in warnings)
