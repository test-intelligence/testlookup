"""
Tests for PII & Test Data Redaction (PR-1 through PR-8).

Covers:
  - PII pattern detection: email, phone, SSN, credit card, IP
  - Secret pattern preservation (existing behavior)
  - Privacy service policy modes
  - Sensitive key expansion
  - Dict redaction with PII in nested values
"""
from app.services.redaction_service import (
    redact_text,
    redact_dict,
    redact_value,
    SENSITIVE_KEYS,
    REDACTED,
)
from app.services.privacy_service import (
    sanitize_for_persistence,
    sanitize_for_logging,
    sanitize_for_llm,
    sanitize_for_report,
    sanitize_dict_for_persistence,
)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# PR-2: PII Pattern Detection
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestEmailRedaction:
    def test_simple_email(self):
        result = redact_text("Contact john.doe@example.com for details")
        assert "[REDACTED_EMAIL]" in result
        assert "john.doe@example.com" not in result

    def test_multiple_emails(self):
        result = redact_text("From alice@test.org to bob@corp.io")
        assert result.count("[REDACTED_EMAIL]") == 2

    def test_email_in_error_message(self):
        result = redact_text("User admin@internal.dev failed authentication at 2026-04-06")
        assert "admin@internal.dev" not in result


class TestPhoneRedaction:
    def test_us_phone_with_dashes(self):
        result = redact_text("Call 555-123-4567 for support")
        assert "[REDACTED_PHONE]" in result
        assert "555-123-4567" not in result

    def test_us_phone_with_parens(self):
        result = redact_text("Phone: (555) 123-4567")
        assert "[REDACTED_PHONE]" in result

    def test_international_prefix(self):
        result = redact_text("Contact +1-555-123-4567")
        assert "[REDACTED_PHONE]" in result


class TestSSNRedaction:
    def test_ssn_with_dashes(self):
        result = redact_text("SSN: 123-45-6789")
        assert "[REDACTED_SSN]" in result
        assert "123-45-6789" not in result

    def test_ssn_with_spaces(self):
        result = redact_text("Social: 123 45 6789")
        assert "[REDACTED_SSN]" in result


class TestCreditCardRedaction:
    def test_cc_with_spaces(self):
        result = redact_text("Card: 4111 1111 1111 1111")
        assert "[REDACTED_CC]" in result
        assert "4111" not in result

    def test_cc_with_dashes(self):
        result = redact_text("CC: 4111-1111-1111-1111")
        assert "[REDACTED_CC]" in result


class TestIPRedaction:
    def test_ipv4(self):
        result = redact_text("Server at 192.168.1.100 is down")
        assert "[REDACTED_IP]" in result
        assert "192.168.1.100" not in result

    def test_multiple_ips(self):
        result = redact_text("From 10.0.0.1 to 10.0.0.2")
        assert result.count("[REDACTED_IP]") == 2


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# PR-2: Secret Pattern Preservation (Existing Behavior)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestSecretPatterns:
    def test_bearer_token(self):
        result = redact_text("Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.abc.def")
        assert "eyJ" not in result

    def test_api_key(self):
        result = redact_text("api_key=sk_live_abcdefghijklmnop")
        assert "sk_live" not in result

    def test_password(self):
        result = redact_text("password=MyS3cr3tP@ss!")
        assert "MyS3cr3t" not in result

    def test_connection_string(self):
        result = redact_text("postgresql://admin:supersecret@db.example.com:5432/mydb")
        assert "supersecret" not in result


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# PR-2: Sensitive Key Expansion
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestSensitiveKeys:
    def test_pii_keys_in_sensitive_set(self):
        pii_keys = {"email", "phone", "ssn", "credit_card", "date_of_birth", "address"}
        assert pii_keys.issubset(SENSITIVE_KEYS)

    def test_secret_keys_still_present(self):
        secret_keys = {"password", "api_key", "bearer", "jwt", "smtp_password"}
        assert secret_keys.issubset(SENSITIVE_KEYS)

    def test_redact_value_pii_key(self):
        assert redact_value("email", "john@example.com") == REDACTED
        assert redact_value("phone", "555-123-4567") == REDACTED
        assert redact_value("ssn", "123-45-6789") == REDACTED


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# PR-2: Dict Redaction with PII
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestDictRedaction:
    def test_nested_pii_keys(self):
        data = {"user": {"email": "test@corp.com", "name": "John"}}
        result = redact_dict(data)
        assert result["user"]["email"] == REDACTED
        assert result["user"]["name"] == "John"

    def test_pii_in_string_values(self):
        data = {"error": "Failed for user john@example.com at 192.168.1.1"}
        result = redact_dict(data)
        assert "john@example.com" not in result["error"]
        assert "192.168.1.1" not in result["error"]
        assert "[REDACTED_EMAIL]" in result["error"]
        assert "[REDACTED_IP]" in result["error"]

    def test_list_values_redacted(self):
        data = {"logs": ["User john@x.com logged in", "Normal event"]}
        result = redact_dict(data)
        assert "john@x.com" not in result["logs"][0]
        assert "[REDACTED_EMAIL]" in result["logs"][0]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# PR-2: Privacy Service Policy Modes
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestPrivacyServiceModes:
    def test_persistence_redacts_email(self):
        result = sanitize_for_persistence("Error for user@test.com")
        assert "[REDACTED_EMAIL]" in result

    def test_logging_redacts_ip(self):
        result = sanitize_for_logging("Connection from 10.0.0.1 failed")
        assert "[REDACTED_IP]" in result

    def test_llm_redacts_phone(self):
        result = sanitize_for_llm("Contact 555-123-4567")
        assert "[REDACTED_PHONE]" in result

    def test_report_redacts_ssn(self):
        result = sanitize_for_report("SSN: 123-45-6789")
        assert "[REDACTED_SSN]" in result

    def test_empty_input_safe(self):
        assert sanitize_for_persistence("") == ""
        assert sanitize_for_logging("") == ""
        assert sanitize_for_llm("") == ""
        assert sanitize_for_report("") == ""

    def test_dict_persistence(self):
        data = {"email": "a@b.com", "error": "timeout at 10.0.0.1"}
        result = sanitize_dict_for_persistence(data)
        assert result["email"] == REDACTED
        assert "[REDACTED_IP]" in result["error"]

    def test_none_input_safe(self):
        assert sanitize_dict_for_persistence(None) is None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# PR-3: Mixed PII + Secret in Error Message
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestMixedRedaction:
    def test_error_with_email_and_bearer(self):
        msg = "Auth failed for admin@corp.com with Bearer eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.payload.sig"
        result = redact_text(msg)
        assert "admin@corp.com" not in result
        assert "eyJ" not in result
        assert "[REDACTED_EMAIL]" in result

    def test_error_with_ip_and_password(self):
        msg = "Failed to connect to 192.168.50.10 with password=Admin123!"
        result = redact_text(msg)
        assert "192.168.50.10" not in result
        assert "Admin123" not in result


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Edge cases: empty input, non-string non-sensitive values, deep recursion
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestRedactionEdgeCases:
    def test_redact_text_empty_returns_input(self):
        # Empty string short-circuits — no work, returns "" unchanged.
        assert redact_text("") == ""

    def test_redact_text_none_safe(self):
        # ``redact_text(None)`` is exercised via ``redact_value`` callers; the
        # public API is typed as str but the empty-falsy guard means None is
        # also safe (returned unchanged via the truthy check).
        assert redact_text(None) is None  # type: ignore[arg-type]

    def test_redact_value_none_returns_none(self):
        assert redact_value("password", None) is None

    def test_redact_value_int_with_non_sensitive_key_passes_through(self):
        # ints, bools, etc. with a non-sensitive key are returned unchanged
        # because the function only pattern-scrubs strings.
        assert redact_value("count", 42) == 42
        assert redact_value("active", True) is True

    def test_redact_value_int_with_sensitive_key_fully_redacted(self):
        # Even non-string values hit the redaction path when the KEY is
        # sensitive — defends against logging structured tokens by accident.
        assert redact_value("api_key", 12345) == REDACTED

    def test_redact_value_normalizes_dashes_in_key(self):
        # The function lowercases and converts dashes to underscores so a
        # header-style ``Api-Key`` matches the canonical ``api_key`` entry
        # in SENSITIVE_KEYS. (The full ``X-API-Key`` header form maps to
        # ``x_api_key`` — not in the set on purpose; callers normalize
        # before passing it in.)
        assert redact_value("Api-Key", "secret-token-value-12345") == REDACTED
        assert redact_value("REFRESH-TOKEN", 12345) == REDACTED

    def test_redact_dict_passes_through_non_string_non_sensitive_values(self):
        result = redact_dict({"counts": [1, 2, 3], "active": True, "score": 4.2})
        assert result == {"counts": [1, 2, 3], "active": True, "score": 4.2}

    def test_redact_dict_recurses_into_lists_of_strings_and_dicts(self):
        result = redact_dict({
            "logs": [
                "user a@b.com tried to log in",
                {"password": "hunter2", "kept": "safe"},
                42,
            ],
        })
        assert "[REDACTED_EMAIL]" in result["logs"][0]
        assert result["logs"][1]["password"] == REDACTED
        assert result["logs"][1]["kept"] == "safe"
        assert result["logs"][2] == 42  # non-string non-dict pass-through

    def test_redact_dict_max_recursion_depth_fails_closed(self):
        """Past the depth limit, the complete subtree is redacted.

        Depth bounding must not turn deeply nested input into a route around
        the persistence/privacy boundary.
        """
        # Build an 11-deep nesting (exceeds _MAX_RECURSION_DEPTH = 10).
        deep: dict = {"password": "leaked"}
        for _ in range(11):
            deep = {"nested": deep}
        result = redact_dict(deep)
        # Walk down the outer levels — they are processed normally — until the
        # value stops being a dict, which is where the depth cap kicked in.
        cursor: object = result
        while isinstance(cursor, dict):
            assert "password" not in cursor  # the leaf never survived intact
            cursor = cursor["nested"]
        assert cursor == REDACTED
        assert "leaked" not in str(result)

    def test_redact_dict_none_passthrough(self):
        # ``not data`` covers None and {} — both return as-is so callers
        # don't need to special-case absent payloads.
        assert redact_dict(None) is None
        assert redact_dict({}) == {}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# redact_for_llm convenience wrapper
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestLlmConvenience:
    def test_llm_wrapper_redacts_email(self):
        from app.services.redaction_service import redact_for_llm
        out = redact_for_llm("error from a@b.com")
        assert "a@b.com" not in out
        assert "[REDACTED_EMAIL]" in out

    def test_llm_wrapper_preserves_safe_text(self):
        from app.services.redaction_service import redact_for_llm
        assert redact_for_llm("plain technical message") == "plain technical message"
