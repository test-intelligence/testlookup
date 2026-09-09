"""Unit tests for backend/scripts/ops_support.py (backup/restore support).

The migration-refusal classification and the manifest validation are the two
pieces of pure logic behind `make restore`'s safety gate — pinned here so the
shell scripts can stay dumb.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "ops_support.py"

spec = importlib.util.spec_from_file_location("ops_support", SCRIPT_PATH)
ops_support = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ops_support)


def _valid_manifest() -> dict:
    return {
        "schema_version": 1,
        "created_at": "2026-07-10T00:00:00Z",
        "app_version": "0.1.0",
        "git_sha": "abc123",
        "alembic_head": "0105",
        "components": [
            {"name": "postgres", "file": "postgres.dump", "method": "pg_dump -Fc", "sha256": "0" * 64},
            {"name": "mongo", "file": "mongo.archive.gz", "method": "mongodump", "sha256": "0" * 64},
            {"name": "minio", "file": "minio_data.tar.gz", "method": "volume tar", "sha256": "0" * 64},
        ],
        "excluded": [{"name": "redis", "reason": "broker"}],
    }


class TestClassifyBackupHead:
    """Ancestry classification — drives restore.sh's refusal semantics."""

    HEADS = ["0105"]
    ANCESTRY = {"0105", "0104", "0103", "0102"}

    def test_equal_head_is_safe(self):
        assert (
            ops_support.classify_backup_head("0105", self.HEADS, self.ANCESTRY)
            == ops_support.TOKEN_EQUAL
        )

    def test_backup_behind_code_means_stack_newer(self):
        # Backup taken at 0103, code now at 0105 → restoring rolls data back.
        assert (
            ops_support.classify_backup_head("0103", self.HEADS, self.ANCESTRY)
            == ops_support.TOKEN_STACK_NEWER
        )

    def test_backup_ahead_of_code_is_unknown(self):
        # Backup from a NEWER version — its head is not in the deployed tree.
        assert (
            ops_support.classify_backup_head("0106", self.HEADS, self.ANCESTRY)
            == ops_support.TOKEN_BACKUP_NEWER_OR_UNKNOWN
        )

    def test_unrelated_revision_is_unknown(self):
        assert (
            ops_support.classify_backup_head("deadbeef", self.HEADS, self.ANCESTRY)
            == ops_support.TOKEN_BACKUP_NEWER_OR_UNKNOWN
        )

    def test_empty_backup_head_is_unknown(self):
        assert (
            ops_support.classify_backup_head("", self.HEADS, self.ANCESTRY)
            == ops_support.TOKEN_BACKUP_NEWER_OR_UNKNOWN
        )

    def test_no_code_heads_is_unknown(self):
        assert (
            ops_support.classify_backup_head("0105", [], set())
            == ops_support.TOKEN_BACKUP_NEWER_OR_UNKNOWN
        )

    def test_multi_head_tree_matches_any_head(self):
        heads = ["0105a", "0105b"]
        ancestry = {"0105a", "0105b", "0104"}
        assert (
            ops_support.classify_backup_head("0105b", heads, ancestry)
            == ops_support.TOKEN_EQUAL
        )


class TestValidateManifest:
    def test_valid_manifest_passes(self):
        assert ops_support.validate_manifest(_valid_manifest()) == []

    def test_not_a_dict(self):
        assert ops_support.validate_manifest(["nope"]) == ["manifest is not a JSON object"]

    def test_wrong_schema_version(self):
        manifest = _valid_manifest()
        manifest["schema_version"] = 999
        problems = ops_support.validate_manifest(manifest)
        assert any("schema_version" in p for p in problems)

    def test_missing_alembic_head(self):
        manifest = _valid_manifest()
        manifest["alembic_head"] = ""
        problems = ops_support.validate_manifest(manifest)
        assert any("alembic_head" in p for p in problems)

    def test_missing_component(self):
        manifest = _valid_manifest()
        manifest["components"] = [c for c in manifest["components"] if c["name"] != "minio"]
        problems = ops_support.validate_manifest(manifest)
        assert any("minio" in p for p in problems)

    def test_malformed_component_entry(self):
        manifest = _valid_manifest()
        manifest["components"].append({"file": "orphan.bin"})
        problems = ops_support.validate_manifest(manifest)
        assert any("malformed component" in p for p in problems)

    def test_empty_components(self):
        manifest = _valid_manifest()
        manifest["components"] = []
        problems = ops_support.validate_manifest(manifest)
        assert any("components" in p for p in problems)


class TestCompareHeadCli:
    """compare-head against the REAL migration tree (ScriptDirectory read)."""

    def _run(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(  # noqa: S603
            [sys.executable, str(SCRIPT_PATH), *args],
            capture_output=True,
            text=True,
            # Explicit DEVNULL: inheriting a closed/invalid stdin handle makes
            # subprocess raise WinError 6 under Git Bash on Windows.
            stdin=subprocess.DEVNULL,
            cwd=str(SCRIPT_PATH.parents[1]),  # backend/ — where alembic.ini lives
            timeout=120,
        )

    def test_real_head_classifies_equal(self):
        result = self._run("compare-head", "0105")
        # If the tree has moved past 0105 by the time this runs, the answer
        # legitimately becomes stack_newer — both prove the wiring works.
        assert result.returncode == 0
        assert result.stdout.strip() in (
            ops_support.TOKEN_EQUAL,
            ops_support.TOKEN_STACK_NEWER,
        )

    def test_ancient_revision_classifies_stack_newer(self):
        result = self._run("compare-head", "0001")
        assert result.returncode == 0
        assert result.stdout.strip() == ops_support.TOKEN_STACK_NEWER

    def test_unknown_revision_classifies_unknown(self):
        result = self._run("compare-head", "not_a_revision")
        assert result.returncode == 0
        assert result.stdout.strip() == ops_support.TOKEN_BACKUP_NEWER_OR_UNKNOWN

    def test_validate_manifest_cli_ok(self):
        result = subprocess.run(  # noqa: S603
            [sys.executable, str(SCRIPT_PATH), "validate-manifest"],
            input=json.dumps(_valid_manifest()),
            capture_output=True,
            text=True,
            cwd=str(SCRIPT_PATH.parents[1]),
            timeout=60,
        )
        assert result.returncode == 0
        assert result.stdout.strip() == "ok"

    def test_validate_manifest_cli_rejects_bad(self):
        result = subprocess.run(  # noqa: S603
            [sys.executable, str(SCRIPT_PATH), "validate-manifest"],
            input="{}",
            capture_output=True,
            text=True,
            cwd=str(SCRIPT_PATH.parents[1]),
            timeout=60,
        )
        assert result.returncode == 1
