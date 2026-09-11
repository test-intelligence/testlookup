"""The PR security scans are configured to block (re-audit M23).

Parses .github/workflows/ci.yml and .trivyignore.yaml and checks the
EFFECTIVE settings: a scan step that stopped failing (exit code 0), lost its
severity threshold, or ignored the acceptance file would otherwise pass every
PR in silence, which is the state M23 found (no PR scanning at all).
"""
from __future__ import annotations

import datetime as dt
import re
import shlex
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CI = ROOT / ".github" / "workflows" / "ci.yml"
RELEASE = ROOT / ".github" / "workflows" / "release.yml"
IGNORE = ROOT / ".trivyignore.yaml"
MAX_ACCEPTANCE_DAYS = 90


def _jobs() -> dict:
    return yaml.safe_load(CI.read_text(encoding="utf-8"))["jobs"]


def _runs(job: dict) -> list[str]:
    return [step["run"] for step in job["steps"] if "run" in step]


def _trivy_commands(job: dict) -> list[list[str]]:
    commands = []
    for script in _runs(job):
        joined = script.replace("\\\n", " ")
        for line in joined.splitlines():
            line = line.strip()
            if line.startswith("trivy ") and " --version" not in line:
                commands.append(shlex.split(line))
    return commands


def _flag(argv: list[str], name: str) -> str | None:
    for i, token in enumerate(argv):
        if token == name and i + 1 < len(argv):
            return argv[i + 1]
        if token.startswith(name + "="):
            return token.split("=", 1)[1]
    return None


def test_both_scan_jobs_exist_and_run_on_pull_requests() -> None:
    jobs = _jobs()
    assert {"security-deps", "security-images"} <= set(jobs)
    on = yaml.safe_load(CI.read_text(encoding="utf-8"))[True]  # YAML 1.1: `on` -> True
    assert "pull_request" in on


def test_every_trivy_scan_blocks_at_high_and_critical_with_the_acceptance_file() -> None:
    jobs = _jobs()
    scans = [argv for name in ("security-deps", "security-images") for argv in _trivy_commands(jobs[name])]
    assert len(scans) >= 2, scans
    for argv in scans:
        assert argv[1] in ("fs", "image"), argv
        assert _flag(argv, "--severity") == "HIGH,CRITICAL", argv
        assert "--ignore-unfixed" in argv, argv
        assert _flag(argv, "--exit-code") == "1", argv
        assert _flag(argv, "--ignorefile") == ".trivyignore.yaml", argv
        assert _flag(argv, "--scanners") in (None, "vuln"), argv


def test_the_dependency_scan_covers_the_whole_tree_except_the_samples() -> None:
    [argv] = [a for a in _trivy_commands(_jobs()["security-deps"]) if a[1] == "fs"]
    assert argv[-1] == "."
    assert "client/examples" in (_flag(argv, "--skip-dirs") or "")


def _install_pin(text: str) -> tuple[str, str]:
    version = re.search(r"trivy/releases/download/v([0-9.]+)/", text)
    digest = re.search(r"TRIVY_ARCHIVE_SHA256:\s*([0-9a-f]{64})", text)
    assert version and digest, "no checksum-verified Trivy install found"
    return version.group(1), digest.group(1)


def test_trivy_is_the_same_checksum_pinned_build_as_the_release_scan() -> None:
    ci = CI.read_text(encoding="utf-8")
    assert _install_pin(ci) == _install_pin(RELEASE.read_text(encoding="utf-8"))
    assert "sha256sum --check --strict" in ci


def test_the_image_scan_matrix_is_every_dockerfile_in_the_tree() -> None:
    tracked = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True,
                             check=True).stdout.split()
    dockerfiles = {p for p in tracked if p.endswith("Dockerfile") and not p.startswith("client/examples/")}
    matrix = _jobs()["security-images"]["strategy"]["matrix"]["include"]
    assert {entry["dockerfile"] for entry in matrix} == dockerfiles


def test_publishing_waits_for_the_dependency_scan() -> None:
    assert "security-deps" in _jobs()["build-images"]["needs"]


def test_every_accepted_finding_says_why_and_expires() -> None:
    doc = yaml.safe_load(IGNORE.read_text(encoding="utf-8"))
    assert set(doc) == {"vulnerabilities"}
    today = dt.datetime.now(dt.timezone.utc).date()
    for entry in doc["vulnerabilities"] or []:
        assert re.fullmatch(r"(CVE-\d{4}-\d{4,}|GHSA(-[23456789cfghjmpqrvwx]{4}){3})", entry["id"]), entry
        assert len(str(entry.get("statement", "")).strip()) >= 20, f"{entry['id']}: say WHY"
        expires = entry["expired_at"]
        expires = expires if isinstance(expires, dt.date) else dt.date.fromisoformat(str(expires))
        assert expires <= today + dt.timedelta(days=MAX_ACCEPTANCE_DAYS), (
            f"{entry['id']}: acceptance longer than {MAX_ACCEPTANCE_DAYS} days"
        )
