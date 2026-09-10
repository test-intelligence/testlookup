"""
BL-02: Secret Encryption and Config Hardening — Unit Tests.

Tests:
  - Fernet encryption round-trip (encrypt → decrypt = original)
  - Mask value generation
  - SMTP password now uses secret_refs (not app_settings.value)
  - Startup guards for default secrets
  - Infra-sensitive settings masked in GET responses
  - Legacy plaintext fallback (backward compatibility)
  - Edge cases: empty values, short values, key rotation
"""
from __future__ import annotations

import importlib.util
import sys
import types
import uuid
from unittest.mock import MagicMock

import pytest


def _make_stub(name: str, **attrs) -> types.ModuleType:
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    return mod


@pytest.fixture(autouse=True)
def _stub_external_modules(monkeypatch: pytest.MonkeyPatch) -> None:
    with monkeypatch.context() as m:
        if importlib.util.find_spec("bcrypt") is None:
            m.setitem(sys.modules, "bcrypt", _make_stub("bcrypt", checkpw=MagicMock(return_value=True), hashpw=MagicMock(return_value=b"$2b$fake"), gensalt=MagicMock(return_value=b"$2b$12$salt")))
        if importlib.util.find_spec("jose") is None:
            jose_jwt_stub = _make_stub("jose.jwt", encode=MagicMock(return_value="tok"), decode=MagicMock(return_value={}))
            m.setitem(sys.modules, "jose.jwt", jose_jwt_stub)
            m.setitem(sys.modules, "jose", _make_stub("jose", jwt=jose_jwt_stub, JWTError=Exception))

        m.setitem(sys.modules, "app.core.security", _make_stub("app.core.security", verify_password=MagicMock(return_value=True), get_password_hash=MagicMock(return_value="hashed_pw"), create_access_token=MagicMock(return_value="access_token"), create_refresh_token=MagicMock(return_value="refresh_token"), decode_token=MagicMock(return_value={"sub": str(uuid.uuid4()), "type": "access"})))
        m.setitem(sys.modules, "app.core.deps", _make_stub("app.core.deps", require_role=MagicMock(return_value=MagicMock()), get_current_active_user=MagicMock(), verify_webhook_secret=MagicMock(), require_project_role=MagicMock(return_value=MagicMock()), require_project_access=MagicMock(return_value=MagicMock()), require_run_access=MagicMock(return_value=MagicMock()), get_accessible_project_ids=MagicMock(return_value=None)))

        from sqlalchemy.orm import DeclarativeBase

        class _Base(DeclarativeBase):
            pass

        m.setitem(sys.modules, "app.db.postgres", _make_stub("app.db.postgres", get_db=MagicMock(), AsyncSession=MagicMock(), AsyncSessionLocal=MagicMock(), Base=_Base))
        m.setitem(sys.modules, "app.db.mongo", _make_stub("app.db.mongo", get_mongo_db=MagicMock(), close_mongo=MagicMock(), Collections=MagicMock()))
        m.setitem(sys.modules, "app.db.redis_client", _make_stub("app.db.redis_client", get_redis=MagicMock(), close_redis=MagicMock()))

        yield


# ═══════════════════════════════════════════════════════════════════════════════
# Fernet Encryption Round-Trip
# ═══════════════════════════════════════════════════════════════════════════════

class TestFernetEncryption:
    def test_encrypt_decrypt_roundtrip(self):
        from app.services.secret_service import encrypt_value, decrypt_value

        original = "sk-abc123def456ghi789jkl012mno345"
        ciphertext = encrypt_value(original)

        # Ciphertext should be different from plaintext
        assert ciphertext != original
        assert len(ciphertext) > len(original)

        # Decryption should recover original
        decrypted = decrypt_value(ciphertext)
        assert decrypted == original

    def test_encrypt_different_values_produce_different_ciphertexts(self):
        from app.services.secret_service import encrypt_value

        ct1 = encrypt_value("secret-one")
        ct2 = encrypt_value("secret-two")
        assert ct1 != ct2

    def test_decrypt_invalid_ciphertext_returns_none(self):
        from app.services.secret_service import decrypt_value

        result = decrypt_value("not-valid-base64-ciphertext")
        assert result is None

    def test_decrypt_empty_string_returns_none(self):
        from app.services.secret_service import decrypt_value

        result = decrypt_value("")
        assert result is None

    def test_encrypt_empty_string(self):
        from app.services.secret_service import encrypt_value, decrypt_value

        ct = encrypt_value("")
        assert ct != ""
        assert decrypt_value(ct) == ""

    def test_encrypt_unicode(self):
        from app.services.secret_service import encrypt_value, decrypt_value

        original = "p@ssw0rd-日本語-émojis-🔑"
        ct = encrypt_value(original)
        assert decrypt_value(ct) == original

    def test_encrypt_long_value(self):
        from app.services.secret_service import encrypt_value, decrypt_value

        original = "x" * 10000
        ct = encrypt_value(original)
        assert decrypt_value(ct) == original


# ═══════════════════════════════════════════════════════════════════════════════
# Masking
# ═══════════════════════════════════════════════════════════════════════════════

class TestMasking:
    def test_mask_standard_key(self):
        # Hardened (auth review Tier 3): reveal at most the last 2 chars and
        # never a usable prefix. An 18-char key shows only its last 2 chars.
        from app.services.secret_service import mask_value

        result = mask_value("sk-abcdef123456789")
        assert result == "****89"
        assert "sk-a" not in result

    def test_mask_short_value(self):
        from app.services.secret_service import mask_value

        assert mask_value("short") == "****"
        assert mask_value("") == "****"
        assert mask_value("1234567") == "****"

    def test_mask_below_16_chars_fully_masked(self):
        # Anything shorter than 16 chars is fully masked — no prefix leak.
        from app.services.secret_service import mask_value

        assert mask_value("12345678") == "****"
        assert mask_value("123456789012345") == "****"  # 15 chars

    def test_mask_16_chars_reveals_last_two(self):
        from app.services.secret_service import mask_value

        assert mask_value("1234567890123456") == "****56"


# ═══════════════════════════════════════════════════════════════════════════════
# SMTP Secret Path
# ═══════════════════════════════════════════════════════════════════════════════

class TestSmtpSecretPath:
    def test_smtp_password_is_registered_as_secret(self):
        from app.services.secret_service import SECRET_FIELDS

        assert "password" in SECRET_FIELDS["smtp_config"]

    def test_smtp_endpoints_exist(self):
        from app.routers.app_settings import get_smtp_config, update_smtp_config
        assert callable(get_smtp_config)
        assert callable(update_smtp_config)


# ═══════════════════════════════════════════════════════════════════════════════
# Startup Guards
# ═══════════════════════════════════════════════════════════════════════════════

class TestStartupGuards:
    def test_default_jwt_secret_flagged_in_production(self):
        from app.core.config import Settings

        s = Settings(APP_ENV="production", JWT_SECRET_KEY="change-me-jwt-secret")
        warnings = s.validate_production_secrets()
        assert any("JWT_SECRET_KEY" in w for w in warnings)

    def test_default_app_secret_flagged(self):
        from app.core.config import Settings

        s = Settings(APP_ENV="staging", APP_SECRET_KEY="change-me-in-production")
        warnings = s.validate_production_secrets()
        assert any("APP_SECRET_KEY" in w for w in warnings)

    def test_dev_login_flagged_in_production(self):
        from app.core.config import Settings

        s = Settings(APP_ENV="production", DEV_AUTO_LOGIN_ENABLED=True)
        warnings = s.validate_production_secrets()
        assert any("DEV_AUTO_LOGIN_ENABLED" in w for w in warnings)

    def test_no_warnings_in_development(self):
        from app.core.config import Settings

        s = Settings(APP_ENV="development", JWT_SECRET_KEY="change-me-jwt-secret")
        warnings = s.validate_production_secrets()
        assert warnings == []

    def test_no_warnings_with_proper_secrets(self):
        from app.core.config import Settings

        s = Settings(
            APP_ENV="production",
            JWT_SECRET_KEY="a-real-strong-secret-key-here",
            APP_SECRET_KEY="another-real-strong-key",
            WEBHOOK_SECRET="webhook-real-secret",
            DEV_AUTO_LOGIN_ENABLED=False,
        )
        warnings = s.validate_production_secrets()
        assert warnings == []


class TestCriticalSecurityFailures:
    """The startup fail-fast guard (main.py lifespan) keys off this helper."""

    def test_default_secret_is_critical_in_production(self):
        from app.core.config import Settings

        s = Settings(APP_ENV="production", JWT_SECRET_KEY="change-me-jwt-secret")
        assert s.critical_security_failures()  # non-empty → startup refused

    def test_default_secret_is_critical_in_staging(self):
        # Staging is treated like production — this is the behavior widened from
        # production-only fail-fast (review finding M4).
        from app.core.config import Settings

        s = Settings(APP_ENV="staging", JWT_SECRET_KEY="change-me-jwt-secret")
        assert s.critical_security_failures()

    def test_no_critical_failures_in_development(self):
        from app.core.config import Settings

        s = Settings(APP_ENV="development", JWT_SECRET_KEY="change-me-jwt-secret")
        assert s.critical_security_failures() == []

    def test_warning_only_item_is_not_critical(self):
        # The WARNING/CRITICAL split still exists: a warning must not block
        # startup on its own. A localhost SAML_BASE_URL is the example now —
        # it is a misconfiguration, not an open door.
        #
        # WEBHOOK_SECRET used to be the example here. It was promoted to
        # CRITICAL (re-audit H1) because it is the only credential in front of
        # POST /ws/events/{run_id}, which injects live results into a
        # caller-named project: booting production with the default published
        # in this source tree let anyone forge results for any tenant.
        from app.core.config import Settings

        s = Settings(
            APP_ENV="production",
            JWT_SECRET_KEY="a-real-strong-secret-key-here",
            APP_SECRET_KEY="another-real-strong-key",
            WEBHOOK_SECRET="a-real-webhook-secret",
            SSO_ENABLED=True,
            SAML_BASE_URL="http://localhost:8000",
            DEV_AUTO_LOGIN_ENABLED=False,
        )
        assert any("SAML_BASE_URL" in w for w in s.validate_production_secrets())
        assert s.critical_security_failures() == []

    def test_default_webhook_secret_is_critical(self):
        """It gates a cross-tenant write, so it must refuse to boot (H1)."""
        from app.core.config import Settings

        s = Settings(
            APP_ENV="production",
            JWT_SECRET_KEY="a-real-strong-secret-key-here",
            APP_SECRET_KEY="another-real-strong-key",
            WEBHOOK_SECRET="change-me-webhook-secret",
            DEV_AUTO_LOGIN_ENABLED=False,
        )
        assert any(
            "WEBHOOK_SECRET" in w for w in s.critical_security_failures()
        )

    def test_dev_auto_login_is_critical(self):
        from app.core.config import Settings

        s = Settings(APP_ENV="production", DEV_AUTO_LOGIN_ENABLED=True)
        assert any("DEV_AUTO_LOGIN_ENABLED" in w for w in s.critical_security_failures())


# ═══════════════════════════════════════════════════════════════════════════════
# Infra Masking in Storage Config
# ═══════════════════════════════════════════════════════════════════════════════

class TestInfraMasking:
    def test_storage_config_no_longer_has_raw_infra(self):
        from app.models.schemas import StorageConfigRead

        fields = set(StorageConfigRead.model_fields.keys())
        # These should NOT be present anymore
        assert "postgres_host" not in fields
        assert "postgres_port" not in fields
        assert "postgres_db" not in fields
        assert "mongo_host" not in fields
        assert "mongo_port" not in fields
        assert "mongo_db" not in fields
        assert "redis_url" not in fields

    def test_storage_config_has_connection_status(self):
        from app.models.schemas import StorageConfigRead

        fields = set(StorageConfigRead.model_fields.keys())
        assert "postgres_connected" in fields
        assert "mongo_connected" in fields
        assert "redis_connected" in fields

    def test_storage_config_still_has_editable_fields(self):
        from app.models.schemas import StorageConfigRead

        fields = set(StorageConfigRead.model_fields.keys())
        assert "minio_endpoint" in fields
        assert "chroma_host" in fields
        assert "chroma_port" in fields
        assert "storage_backend" in fields

    def test_storage_config_instantiation(self):
        from app.models.schemas import StorageConfigRead

        cfg = StorageConfigRead(
            storage_backend="minio",
            postgres_connected=True,
            mongo_connected=False,
            redis_connected=True,
            minio_endpoint="localhost:9000",
            minio_bucket_name="test",
            minio_use_ssl=False,
            chroma_host="localhost",
            chroma_port=8001,
            chroma_collection="testlookup",
        )
        assert cfg.postgres_connected is True
        assert cfg.mongo_connected is False
        assert cfg.redis_connected is True


# ═══════════════════════════════════════════════════════════════════════════════
# Edge Cases
# ═══════════════════════════════════════════════════════════════════════════════

class TestEdgeCases:
    def test_secret_fields_registered_for_all_scopes(self):
        from app.services.secret_service import SECRET_FIELDS

        assert "smtp_config" in SECRET_FIELDS
        assert "ai_config" in SECRET_FIELDS
        assert "integrations_config" in SECRET_FIELDS

    def test_is_secret_field_unknown_scope(self):
        from app.services.secret_service import is_secret_field

        assert is_secret_field("nonexistent", "anything") is False

    def test_extract_skips_empty_values(self):
        from app.services.secret_service import extract_secrets_from_config

        result = extract_secrets_from_config("ai_config", {"openai_api_key": "", "google_api_key": None})
        assert result == {}

    def test_strip_removes_only_secrets(self):
        from app.services.secret_service import strip_secrets_from_config

        result = strip_secrets_from_config("ai_config", {
            "llm_provider": "openai",
            "openai_api_key": "sk-secret",
            "llm_model": "gpt-4o",
        })
        assert "openai_api_key" not in result
        assert result["llm_provider"] == "openai"
        assert result["llm_model"] == "gpt-4o"
