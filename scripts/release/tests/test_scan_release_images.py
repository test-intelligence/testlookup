"""Regression tests for the release-only immutable image scan gate."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import release_manifest  # noqa: E402
import scan_release_images as scanner  # noqa: E402


SOURCE_SHA = "a" * 40


def _manifest(path: Path) -> dict:
    document = release_manifest.create_manifest(
        source_sha=SOURCE_SHA,
        version="v1.2.3",
        images={
            component: f"ghcr.io/acme/testlookup/{component}@sha256:{str(index) * 64}"
            for index, component in enumerate(release_manifest.COMPONENTS, start=1)
        },
    )
    path.write_text(json.dumps(document), encoding="utf-8")
    return document


class FakeTrivy:
    def __init__(self, *, blocked: str | None = None, error: str | None = None):
        self.blocked = blocked
        self.error = error
        self.commands: list[tuple[str, ...]] = []

    def __call__(self, command):
        command = tuple(command)
        self.commands.append(command)
        if command[1:] == ("--version",):
            return subprocess.CompletedProcess(command, 0, "Version: 0.74.0\n", "")
        image_ref = command[-1]
        component = image_ref.split("/")[-1].split("@")[0]
        output = Path(command[command.index("--output") + 1])
        if self.error == component:
            return subprocess.CompletedProcess(
                command, 2, "", "Authorization: Bearer secret-token\nregistry unavailable"
            )
        if command[command.index("--format") + 1] == "json":
            vulnerabilities = []
            if self.blocked == component:
                vulnerabilities = [{"VulnerabilityID": "CVE-TEST", "Severity": "HIGH", "FixedVersion": "2"}]
            output.write_text(json.dumps({"Results": [{"Vulnerabilities": vulnerabilities}]}), encoding="utf-8")
            return subprocess.CompletedProcess(command, 1 if vulnerabilities else 0, "", "")
        output.write_text(json.dumps({"bomFormat": "CycloneDX"}), encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "", "")


def test_scan_uses_every_exact_digest_and_writes_complete_evidence(tmp_path: Path):
    manifest_path = tmp_path / "manifest.json"
    manifest = _manifest(manifest_path)
    output = tmp_path / "evidence"
    trivy = FakeTrivy()

    assert scanner.scan_release(manifest_path, output, runner=trivy) == 0
    image_commands = [command for command in trivy.commands if len(command) > 2]
    assert len(image_commands) == 6
    vulnerability_commands = [command for command in image_commands if command[command.index("--format") + 1] == "json"]
    assert all("--ignore-unfixed" in command for command in vulnerability_commands)
    assert all(command[command.index("--severity") + 1] == "HIGH,CRITICAL" for command in vulnerability_commands)
    assert all(command[command.index("--exit-code") + 1] == "1" for command in vulnerability_commands)
    assert all(command[command.index("--timeout") + 1] == "3m" for command in image_commands)
    for component in release_manifest.COMPONENTS:
        expected = manifest["images"][component]["source_ref"]
        assert sum(command[-1] == expected for command in image_commands) == 2
        assert (output / f"{component}.vulnerabilities.json").is_file()
        assert (output / f"{component}.cyclonedx.json").is_file()
        assert "return_code: 0" in (output / f"{component}.vulnerabilities.log").read_text()
        assert "return_code: 0" in (output / f"{component}.sbom.log").read_text()
    assert json.loads((output / "summary.json").read_text())["result"] == "passed"
    assert (output / "release-manifest.json").is_file()
    assert (output / "trivy-version.txt").is_file()


def test_one_policy_failure_still_scans_every_component(tmp_path: Path):
    manifest_path = tmp_path / "manifest.json"
    _manifest(manifest_path)
    output = tmp_path / "evidence"
    trivy = FakeTrivy(blocked="frontend")

    assert scanner.scan_release(manifest_path, output, runner=trivy) == 1
    summary = json.loads((output / "summary.json").read_text())
    assert summary["components"]["frontend"]["vulnerability_status"] == "blocked"
    assert summary["components"]["frontend"]["actionable_high_or_critical"] == 1
    assert set(summary["components"]) == set(release_manifest.COMPONENTS)
    assert len([command for command in trivy.commands if len(command) > 2]) == 6


def test_scanner_error_fails_closed_and_continues(tmp_path: Path):
    manifest_path = tmp_path / "manifest.json"
    _manifest(manifest_path)
    output = tmp_path / "evidence"
    trivy = FakeTrivy(error="backend")

    assert scanner.scan_release(manifest_path, output, runner=trivy) == 1
    summary = json.loads((output / "summary.json").read_text())
    assert summary["components"]["backend"]["vulnerability_status"] == "scanner_error"
    assert summary["components"]["backend"]["sbom_status"] == "scanner_error"
    assert summary["components"]["mcp"]["vulnerability_status"] == "passed"
    assert "registry unavailable" in (output / "backend.vulnerabilities.log").read_text()
    assert "secret-token" not in (output / "backend.vulnerabilities.log").read_text()


def test_release_workflow_gates_promotion_without_adding_pr_scan():
    root = Path(__file__).resolve().parents[3]
    release = (root / ".github/workflows/release.yml").read_text(encoding="utf-8")
    ci = (root / ".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert "  scan-verified-images:" in release
    assert "needs: [resolve-tag, verify, scan-verified-images]" in release
    assert "python3 scripts/release/scan_release_images.py" in release
    assert "if: always()" in release
    assert "if-no-files-found: error" in release
    assert "retention-days: 90" in release
    assert "timeout-minutes: 30" in release
    assert "packages: read" in release
    assert "packages: write" in release
    assert "scan_release_images.py" not in ci
