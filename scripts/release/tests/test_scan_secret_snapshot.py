"""Snapshot coverage survives an unrelated push after a failed secret scan.

Pure unittest also runs in the secret-scan job without installing packages.
Set GITLEAKS_TEST_BINARY to the checksum-verified scanner for live CLI proofs.
All Git writes and synthetic canaries stay in disposable fixture repositories.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import scan_secret_snapshot as snapshot  # noqa: E402


SCANNER = os.environ.get("GITLEAKS_TEST_BINARY")
ROOT = Path(__file__).resolve().parents[3]
POLICY = '''[extend]
useDefault = true
[[rules]]
id = "fixture-ingest-key"
description = "Synthetic snapshot canary"
regex = "qai_[A-Za-z0-9_]{30,}"
keywords = ["qai_"]
'''


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="snapshot-fixture-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.repo = self.root / "repository"
        self.repo.mkdir()
        # Do not inherit a developer's hooks, signing, filters or Git overrides.
        self.environment = {name: value for name, value in os.environ.items() if not name.startswith("GIT_")}
        self.environment.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
        self.git("init", "--template=", "--initial-branch=main")
        self.git("config", "user.name", "Snapshot fixture")
        self.git("config", "user.email", "snapshot@example.invalid")
        self.git("config", "core.autocrlf", "false")
        (self.repo / ".gitleaks.toml").write_text(POLICY, encoding="utf-8")

    def git(self, *arguments, input=None):
        result = subprocess.run(
            ["git", "-C", str(self.repo), *arguments],
            input=input, capture_output=True, env=self.environment, check=True,
        )
        return result.stdout

    def commit(self, *, stage=True):
        if stage:
            self.git("add", "-A")
        self.git("commit", "-m", "Synthetic snapshot fixture")
        return self.git("rev-parse", "HEAD").decode().strip()

    def test_only_the_requested_tracked_tree_is_staged(self):
        (self.repo / "config.txt").write_text("tracked-safe", encoding="utf-8")
        sha = self.commit()
        (self.repo / "config.txt").write_text("uncommitted-different", encoding="utf-8")
        (self.repo / ".env").write_text("untracked-local", encoding="utf-8")
        destination = self.root / "snapshot"
        self.assertEqual(snapshot.stage_snapshot(self.repo, sha, destination), 2)
        self.assertEqual((destination / "config.txt").read_text(), "tracked-safe")
        self.assertFalse((destination / ".env").exists())
        self.assertFalse((destination / ".git").exists())

    def test_export_ignore_does_not_hide_a_tracked_blob(self):
        (self.repo / ".gitattributes").write_text("hidden.txt export-ignore\n", encoding="utf-8")
        (self.repo / "hidden.txt").write_text("tracked", encoding="utf-8")
        sha = self.commit()
        destination = self.root / "snapshot"
        self.assertEqual(snapshot.stage_snapshot(self.repo, sha, destination), 3)
        self.assertEqual((destination / "hidden.txt").read_text(), "tracked")

    def test_export_subst_does_not_replace_candidate_bytes(self):
        (self.repo / ".gitattributes").write_text("substitute.txt export-subst\n", encoding="utf-8")
        (self.repo / "substitute.txt").write_text("$Format:%H$", encoding="utf-8")
        sha = self.commit()
        destination = self.root / "snapshot"
        self.assertEqual(snapshot.stage_snapshot(self.repo, sha, destination), 3)
        self.assertEqual((destination / "substitute.txt").read_text(), "$Format:%H$")

    def test_checkout_eol_and_encoding_attributes_do_not_transform_raw_blobs(self):
        (self.repo / ".gitattributes").write_text(
            "*.ps1 text eol=crlf\nencoded.txt working-tree-encoding=UTF-16LE\n", encoding="utf-8"
        )
        (self.repo / "script.ps1").write_bytes(b"Write-Output 'fixture'\r\n")
        (self.repo / "encoded.txt").write_bytes("Encoding fixture\n".encode("utf-16-le"))
        sha = self.commit()
        destination = self.root / "snapshot"
        self.assertEqual(snapshot.stage_snapshot(self.repo, sha, destination), 4)
        self.assertEqual((destination / "script.ps1").read_bytes(), b"Write-Output 'fixture'\n")
        self.assertEqual((destination / "encoded.txt").read_bytes(), b"Encoding fixture\n")

    def test_symlink_blob_is_scanned_as_text_without_following_its_target(self):
        self.commit()
        blob = self.git("hash-object", "-w", "--stdin", input=b"../../outside-private-file").decode().strip()
        self.git("update-index", "--add", "--cacheinfo", f"120000,{blob},link")
        sha = self.commit(stage=False)
        destination = self.root / "snapshot"
        snapshot.stage_snapshot(self.repo, sha, destination)
        self.assertFalse((destination / "link").is_symlink())
        self.assertEqual((destination / "link").read_text(), "../../outside-private-file")

    def test_unsafe_paths_and_invalid_revision_are_refused(self):
        for name in ("../outside", "/absolute", "a/../b", "a\\b", "C:/outside", "a//b"):
            with self.subTest(name=name), self.assertRaises(snapshot.SnapshotScanError):
                snapshot._safe_path(name)
        with self.assertRaises(snapshot.SnapshotScanError):
            snapshot.stage_snapshot(self.repo, "main", self.root / "snapshot")

    def test_submodule_content_is_not_silently_omitted(self):
        previous = self.commit()
        self.git("update-index", "--add", "--cacheinfo", f"160000,{previous},submodule")
        sha = self.commit(stage=False)
        with self.assertRaisesRegex(snapshot.SnapshotScanError, "unsupported"):
            snapshot.stage_snapshot(self.repo, sha, self.root / "snapshot")

    def test_scanner_gets_only_verified_snapshot_policy_and_redacted_output(self):
        (self.repo / "safe.txt").write_text("safe", encoding="utf-8")
        sha = self.commit()
        calls = []
        original = subprocess.run
        executable = str(Path(sys.executable).resolve())

        def run(command, **options):
            if command[0] == executable:
                calls.append((command, options))
                self.assertEqual((options["cwd"] / "safe.txt").read_text(), "safe")
                self.assertTrue((options["cwd"] / ".gitleaks.toml").is_file())
                return subprocess.CompletedProcess(command, 0)
            return original(command, **options)

        # setup-python's Linux executable can be a symlink. Also exercise a
        # noncanonical spelling on platforms whose Python path is already real.
        alias = os.path.join(str(Path(sys.executable).parent), ".", Path(sys.executable).name)
        for scanner in (sys.executable, alias):
            with self.subTest(scanner_alias=scanner != executable), patch.object(snapshot.subprocess, "run", side_effect=run):
                self.assertEqual(snapshot.scan_snapshot(self.repo, sha, gitleaks=scanner), 2)
        self.assertEqual(len(calls), 2)
        command, options = calls[0]
        self.assertEqual(command[1:5], ["dir", ".", "--config", ".gitleaks.toml"])
        self.assertIn("--redact", command)
        self.assertEqual(options["timeout"], 310)

    def test_scanner_failure_and_timeout_both_fail_closed(self):
        sha = self.commit()
        original = subprocess.run
        executable = str(Path(sys.executable).resolve())
        for fail_with_timeout in (False, True):
            calls = []

            def run(command, **options):
                if command[0] == executable:
                    calls.append(command)
                    if fail_with_timeout:
                        raise subprocess.TimeoutExpired(command, options["timeout"])
                    return subprocess.CompletedProcess(command, 7)
                return original(command, **options)

            with self.subTest(timeout=fail_with_timeout), patch.object(snapshot.subprocess, "run", side_effect=run):
                with self.assertRaises(snapshot.SnapshotScanError):
                    snapshot.scan_snapshot(self.repo, sha, gitleaks=sys.executable)
            self.assertEqual(len(calls), 1)

    @unittest.skipUnless(SCANNER, "set GITLEAKS_TEST_BINARY to the pinned scanner")
    def test_failed_A_then_unrelated_B_still_blocks_the_unchanged_canary(self):
        # A tracked path must remain covered even when .gitignore matches it.
        (self.repo / ".gitignore").write_text("credential.txt\n", encoding="utf-8")
        self.commit()
        # Constructed synthetic data cannot accidentally be a real credential.
        canary = "qai_" + "SnapshotCanary7" * 4
        (self.repo / "credential.txt").write_text("API_KEY=" + canary + "\n", encoding="utf-8")
        self.git("add", "-f", "credential.txt")
        failed_a = self.commit()
        (self.repo / "unrelated.txt").write_text("unrelated change", encoding="utf-8")
        candidate_b = self.commit()
        incremental = subprocess.run(
            [SCANNER, "git", "--config", ".gitleaks.toml", "--redact", "--no-banner",
             "--log-opts=" + failed_a + ".." + candidate_b, "."],
            cwd=self.repo, capture_output=True, check=False,
        )
        self.assertEqual(incremental.returncode, 0, "the old immediate-push range omits A")
        with self.assertRaisesRegex(snapshot.SnapshotScanError, "did not pass"):
            snapshot.scan_snapshot(self.repo, candidate_b, gitleaks=SCANNER)
        (self.repo / "credential.txt").unlink()
        repaired = self.commit()
        self.assertEqual(snapshot.scan_snapshot(self.repo, repaired, gitleaks=SCANNER), 3)

    @unittest.skipUnless(SCANNER, "set GITLEAKS_TEST_BINARY to the pinned scanner")
    def test_narrow_fixture_exceptions_do_not_allow_another_secret_in_those_files(self):
        (self.repo / ".gitleaks.toml").write_text((ROOT / ".gitleaks.toml").read_text(), encoding="utf-8")
        for path in (
            "backend/tests/integration/golden/analytics_parity_characterisation.json",
            "sdk-vectors/http_exchange.json",
        ):
            with self.subTest(path=path):
                fixture = self.repo / path
                fixture.parent.mkdir(parents=True, exist_ok=True)
                fixture.write_text('{"api_key":"' + "OtherFixtureCanary7" * 4 + '"}', encoding="utf-8") # gitleaks:allow -- generated same-detector negative fixture
                candidate = self.commit()
                detected = subprocess.run(
                    [SCANNER, "dir", ".", "--config", ".gitleaks.toml", "--redact",
                     "--no-banner", "--report-format", "json", "--report-path", "-"],
                    cwd=self.repo, capture_output=True, check=False,
                )
                self.assertEqual(detected.returncode, 1)
                self.assertIn("generic-api-key", {finding["RuleID"] for finding in json.loads(detected.stdout)})
                with self.assertRaisesRegex(snapshot.SnapshotScanError, "did not pass"):
                    snapshot.scan_snapshot(self.repo, candidate, gitleaks=SCANNER)
                fixture.unlink()


if __name__ == "__main__":
    unittest.main()
