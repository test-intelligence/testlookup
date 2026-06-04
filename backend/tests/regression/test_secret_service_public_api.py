"""Regression: secret_service public API must stay importable.

Commit e6c0348 ("Align secret masking implementation with tests") replaced the
whole module with a masking-only stub, deleting store_secret / read_secret /
has_secret / extract_secrets_from_config / strip_secrets_from_config /
SECRET_FIELDS and renaming mask_value → mask_secret. Because app/routers/
app_settings.py imports those names at module top, and bootstrap.py imports
app_settings, the entire app failed to import — pytest collection errored out
on test_architectural_authorization, test_llm_connectivity, test_route_ordering
(CI exit 2), and the app couldn't start.

This pins the public surface every caller (app_settings, ai_config_resolver,
github_checks_service, webhook_service) and the existing tests rely on, plus
the mask_value contract, so a future stub/rename breaks here loudly instead of
taking down the whole import graph.
"""
from __future__ import annotations

import pytest

pytest.importorskip("cryptography")  # core dep (via python-jose); guards odd envs

pytestmark = pytest.mark.regression


def test_public_names_are_importable():
    # The exact surface app_settings.py + the other callers import at module top.
    from app.services.secret_service import (  # noqa: F401
        SECRET_FIELDS,
        decrypt_value,
        encrypt_value,
        extract_secrets_from_config,
        get_masked,
        has_secret,
        is_secret_field,
        mask_value,
        read_secret,
        store_secret,
        strip_secrets_from_config,
    )


def test_mask_value_contract():
    from app.services.secret_service import mask_value

    # < 16 chars → fully masked; >= 16 → reveal at most the last 2.
    assert mask_value("") == "****"
    assert mask_value("short") == "****"
    assert mask_value("123456789012345") == "****"      # 15
    assert mask_value("1234567890123456") == "****56"   # 16
    assert mask_value("sk-supersecretvalue99") == "****99"


def test_secret_fields_registry():
    from app.services.secret_service import SECRET_FIELDS, is_secret_field

    assert "anthropic_api_key" in SECRET_FIELDS["ai_config"]
    assert "openai_api_key" in SECRET_FIELDS["ai_config"]
    assert "google_api_key" in SECRET_FIELDS["ai_config"]
    assert SECRET_FIELDS["integrations_config"]
    assert is_secret_field("ai_config", "openai_api_key") is True
    assert is_secret_field("ai_config", "base_url") is False


def test_extract_and_strip_round_trip():
    from app.services.secret_service import (
        extract_secrets_from_config,
        strip_secrets_from_config,
    )

    cfg = {"openai_api_key": "sk-secret", "base_url": "https://x", "model": "gpt"}
    secrets = extract_secrets_from_config("ai_config", cfg)
    stripped = strip_secrets_from_config("ai_config", cfg)

    assert secrets == {"openai_api_key": "sk-secret"}
    assert "openai_api_key" not in stripped
    assert stripped == {"base_url": "https://x", "model": "gpt"}


def test_encrypt_decrypt_round_trip(monkeypatch):
    # A strong key so the fail-closed guard is satisfied, then verify crypto round-trips.
    from app.core.config import settings
    from app.services import secret_service

    monkeypatch.setattr(settings, "APP_SECRET_KEY", "x" * 32, raising=False)
    monkeypatch.setattr(settings, "APP_ENV", "development", raising=False)
    secret_service.reset_fernet_cache()
    try:
        ct = secret_service.encrypt_value("hunter2")
        assert ct != "hunter2"
        assert secret_service.decrypt_value(ct) == "hunter2"
        # tampered / non-Fernet ciphertext → None (not an exception)
        assert secret_service.decrypt_value("not-a-fernet-token") is None
    finally:
        secret_service.reset_fernet_cache()
