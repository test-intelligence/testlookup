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


# ── remediation of the first PR scans (PR #26) ───────────────────────────────


ACCEPTED = {
    "CVE-2026-54283", "CVE-2025-62727", "CVE-2026-48818", "CVE-2026-34070",
    "CVE-2026-53613", "CVE-2026-53614", "CVE-2026-76642", "CVE-2026-78410",
    # util-linux fixes not yet on the Alpine mirror (c9fe5d71 homelab build)
    "CVE-2026-53612", "CVE-2026-78408", "CVE-2026-78409",
}


def test_the_accepted_findings_are_exactly_the_triaged_ones() -> None:
    doc = yaml.safe_load(IGNORE.read_text(encoding="utf-8"))
    ids = {entry["id"] for entry in doc["vulnerabilities"]}
    assert ids == ACCEPTED, ids ^ ACCEPTED
    for entry in doc["vulnerabilities"]:
        assert entry["statement"].startswith(("MITIGATED.", "NOT REACHABLE.")), entry["id"]


def _final_stage(dockerfile: str) -> list[str]:
    lines = (ROOT / dockerfile).read_text(encoding="utf-8").splitlines()
    last_from = max(i for i, line in enumerate(lines) if line.startswith("FROM "))
    return lines[last_from:]


def _joined(lines: list[str]) -> list[str]:
    out, current = [], ""
    for line in lines:
        current += line.rstrip("\\").rstrip() + " " if line.rstrip().endswith("\\") else line
        if not line.rstrip().endswith("\\"):
            out.append(current.strip())
            current = ""
    return out


def test_python_runtime_images_carry_no_packaging_toolchain() -> None:
    for dockerfile in ("backend/Dockerfile", "mcp/Dockerfile"):
        steps = _joined(_final_stage(dockerfile))
        strip = [i for i, s in enumerate(steps) if s.startswith("RUN rm -rf") and "site-packages/setuptools" in s]
        assert len(strip) == 1, dockerfile
        step = steps[strip[0]]
        for target in ("site-packages/pip ", "site-packages/setuptools ", "site-packages/pkg_resources",
                       "site-packages/wheel ", "/usr/local/bin/pip "):
            assert target in step, (dockerfile, target)
        assert "importlib.util.find_spec" in step and "sys.exit" in step, dockerfile
        installs = [i for i, s in enumerate(steps) if "pip install" in s or s.startswith("COPY --from=base")]
        assert all(i < strip[0] for i in installs), f"{dockerfile}: something installs after the strip"
        assert not any("pip " in s for s in steps[strip[0] + 1:]), dockerfile


def test_the_nginx_image_takes_alpine_security_updates() -> None:
    steps = _joined(_final_stage("frontend/Dockerfile"))
    assert steps[0].startswith("FROM nginx:alpine")
    upgrade = [i for i, s in enumerate(steps) if s.startswith("RUN ") and "apk upgrade --no-cache" in s]
    assert upgrade and upgrade[0] < next(i for i, s in enumerate(steps) if s.startswith("CMD"))


def test_the_nginx_stage_trusts_the_staged_ca_before_apk_goes_online() -> None:
    """R-B45-T-2: apk upgrade reaches the Alpine mirror over HTTPS; behind a TLS
    interceptor (INSTALL_EXTRA_CA=1) the staged CA must be trusted first."""
    steps = _joined(_final_stage("frontend/Dockerfile"))
    [run] = [s for s in steps if "apk upgrade --no-cache" in s]
    at = steps.index(run)
    assert steps.index("ARG INSTALL_EXTRA_CA=0") < at
    assert steps.index("COPY certs/ /opt/extra-ca/") < at
    gate = run.index('"$INSTALL_EXTRA_CA" = "1"')
    trust = run.index("cat /opt/extra-ca/*.crt >> /etc/ssl/certs/ca-certificates.crt")
    assert gate < trust < run.index("apk upgrade --no-cache")
    assert not any(s.startswith("RUN ") and "apk " in s for s in steps[:at]), (
        "apk goes online before the CA step"
    )
