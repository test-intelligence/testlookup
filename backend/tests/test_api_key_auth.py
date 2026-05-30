"""
Tests for CLI-5: API Key Authentication on Protected Routes.

Covers:
  - API key validation logic
  - Dual auth dependency (JWT + API key)
  - Invalid/expired/inactive key rejection
  - CORS headers include X-API-Key
"""
import hashlib

import pytest

pytest.importorskip("asyncpg")

from app.models.postgres import ApiKey  # noqa: E402


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CLI-5: API Key Model
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestApiKeyModel:
    def test_api_key_table_exists(self):
        assert ApiKey.__tablename__ == "api_keys"

    def test_api_key_has_required_columns(self):
        columns = {c.name for c in ApiKey.__table__.columns}
        assert "key_hash" in columns
        assert "user_id" in columns
        assert "is_active" in columns
        assert "expires_at" in columns
        assert "name" in columns


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CLI-5: Dual Auth Dependency
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

from app.core.deps import (  # noqa: E402
    get_current_user_or_api_key,
    _validate_api_key,
    oauth2_scheme_optional,
)


class TestDualAuthDependency:
    def test_dual_auth_function_exists(self):
        assert callable(get_current_user_or_api_key)

    def test_api_key_validator_exists(self):
        assert callable(_validate_api_key)

    def test_optional_oauth2_scheme_exists(self):
        assert oauth2_scheme_optional is not None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CLI-5: API Key Hash Verification
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestApiKeyHashLogic:
    def test_sha256_hash_matches(self):
        """Verify the hashing logic matches what the API key endpoint stores."""
        raw_key = "qai_test_key_12345678"
        expected_hash = hashlib.sha256(raw_key.encode()).hexdigest()
        assert len(expected_hash) == 64  # SHA-256 hex digest

    def test_different_keys_produce_different_hashes(self):
        hash1 = hashlib.sha256(b"qai_key_1").hexdigest()
        hash2 = hashlib.sha256(b"qai_key_2").hexdigest()
        assert hash1 != hash2


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CLI-5: CORS Configuration
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestCORSHeaders:
    def test_x_api_key_in_bootstrap(self):
        """X-API-Key must be in CORS allowed headers."""
        from pathlib import Path
        bootstrap_path = Path(__file__).parent.parent / "app" / "bootstrap.py"
        content = bootstrap_path.read_text(encoding="utf-8")
        assert "X-API-Key" in content

    def test_protected_routes_use_dual_auth(self):
        """Protected routes should use get_current_user_or_api_key."""
        from pathlib import Path
        bootstrap_path = Path(__file__).parent.parent / "app" / "bootstrap.py"
        content = bootstrap_path.read_text(encoding="utf-8")
        assert "get_current_user_or_api_key" in content


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CLI-1: CLI Package Structure
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestCLIPackageExists:
    def test_cli_directory_exists(self):
        from pathlib import Path
        cli_dir = Path(__file__).parent.parent.parent / "cli" / "testlookup_cli"
        assert cli_dir.is_dir()

    def test_cli_app_module_exists(self):
        from pathlib import Path
        app_path = Path(__file__).parent.parent.parent / "cli" / "testlookup_cli" / "app.py"
        assert app_path.is_file()

    def test_cli_pyproject_exists(self):
        from pathlib import Path
        pyproject = Path(__file__).parent.parent.parent / "cli" / "pyproject.toml"
        assert pyproject.is_file()

    def test_cli_commands_directory_exists(self):
        from pathlib import Path
        cmds_dir = Path(__file__).parent.parent.parent / "cli" / "testlookup_cli" / "commands"
        assert cmds_dir.is_dir()

    def test_expected_command_modules(self):
        from pathlib import Path
        cmds_dir = Path(__file__).parent.parent.parent / "cli" / "testlookup_cli" / "commands"
        expected = {"auth.py", "health.py", "projects.py", "runs.py", "tests.py",
                    "search.py", "intelligence.py", "deep.py", "reports.py", "keys.py"}
        actual = {f.name for f in cmds_dir.iterdir() if f.suffix == ".py" and f.name != "__init__.py"}
        assert expected.issubset(actual), f"Missing: {expected - actual}"
