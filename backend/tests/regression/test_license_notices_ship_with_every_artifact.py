"""The license notices ship with every artefact, and the trust documents are true.

Launch-readiness review, 2026-11-04 (gaps A-01, A-02, A-03, A-04, A-05, A-06,
A-13, D-02, I-09, I-19). The frontend image redistributed ~340 npm packages
with none of their copyright notices (the bundler strips every ``@license``
banner); no image carried LICENSE or NOTICE; the Java ``-all`` jar had lost its
``META-INF/LICENSE`` to a shade transformer; THIRD_PARTY_NOTICES.md was stale
and nothing checked it; THREAT_MODEL.md named the wrong key and the wrong
datastores; CONTRIBUTING.md described a DCO check that did not exist; and a
user guide claimed a legal review that never happened. Every assertion here
fails on the tree before the fix.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
GENERATOR = REPO_ROOT / "scripts" / "generate_third_party_notices.py"
IMAGE_CONTEXTS = ("backend", "frontend", "mcp")
NOTICE_FILES = ("LICENSE", "NOTICE", "THIRD_PARTY_NOTICES.md")


def _read(rel: str) -> str:
    return (REPO_ROOT / rel).read_text(encoding="utf-8")


def test_the_generated_notices_and_staged_copies_are_current() -> None:
    proc = subprocess.run(
        [sys.executable, str(GENERATOR), "--check"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


@pytest.mark.parametrize("context", IMAGE_CONTEXTS)
def test_each_image_build_context_stages_the_root_notice_files(context: str) -> None:
    for name in NOTICE_FILES:
        staged = REPO_ROOT / context / "licenses" / name
        assert staged.exists(), f"{context}/licenses/{name} is missing"
        assert staged.read_bytes() == (REPO_ROOT / name).read_bytes(), (
            f"{context}/licenses/{name} differs from the root copy"
        )


@pytest.mark.parametrize("context", IMAGE_CONTEXTS)
def test_every_image_carries_licenses_and_the_oci_license_label(context: str) -> None:
    text = _read(f"{context}/Dockerfile")
    assert re.search(r"^COPY .*licenses/? /licenses/?\s*$", text, re.M), (
        f"{context}/Dockerfile does not copy licenses/ to /licenses"
    )
    label = 'LABEL org.opencontainers.image.licenses="Apache-2.0"'
    assert label in text, f"{context}/Dockerfile has no OCI license label"
    # In the final (production) stage, not only in a builder stage.
    last_from = max(m.start() for m in re.finditer(r"^FROM ", text, re.M))
    assert text.index(label) > last_from, f"{context}/Dockerfile labels a stage that is not shipped"
    # The ignore file must not drop the staged copies from the build context.
    assert "!licenses/" in _read(f"{context}/.dockerignore")


def test_the_frontend_build_writes_the_notices_and_ci_checks_them() -> None:
    scripts = json.loads(_read("frontend/package.json"))["scripts"]
    assert scripts["build"].endswith("node scripts/generate-third-party-licenses.mjs")
    assert scripts["check:licenses"] == "node scripts/generate-third-party-licenses.mjs --check"
    generator = _read("frontend/scripts/generate-third-party-licenses.mjs")
    assert "THIRD_PARTY_LICENSES.txt" in generator
    assert "package-lock.json" in generator
    ci = _read(".github/workflows/ci.yml")
    assert "npm run check:licenses" in ci
    assert "generate_third_party_notices.py --check" in ci


def test_the_java_all_jar_keeps_the_project_license() -> None:
    pom = _read("client/java/pom.xml")
    assert "IncludeResourceTransformer" in pom
    assert "<resource>META-INF/LICENSE</resource>" in pom


def test_third_party_notices_name_the_mpl_components_and_carry_the_source_offer() -> None:
    text = _read("THIRD_PARTY_NOTICES.md")
    for name in ("certifi", "pathspec", "tqdm"):
        assert f"`{name}`" in text, f"MPL-2.0 component {name} is not named"
    assert "Written offer" in text
    assert "three years" in text
    # The claim that was false: no release carried an SBOM.
    assert "SBOM attached to each release" not in text
    assert "/licenses" in text


def test_the_threat_model_describes_the_system_that_ships() -> None:
    model = _read("THREAT_MODEL.md")
    assert "derived from `JWT_SECRET_KEY`" not in model
    assert "APP_SECRET_KEY" in model
    assert "APP_SECRET_KEY_PREVIOUS" in model
    for component in ("Redis", "MinIO"):
        assert component not in model, f"THREAT_MODEL.md still names {component}"
    lower = model.lower()
    for topic in ("valkey", "versitygw", "saml", "scim", "totp", "share link", "knowledge source", "upload", "defusedxml"):
        assert topic in lower, f"THREAT_MODEL.md has no row for {topic}"


def test_the_compliance_guide_claims_no_legal_review() -> None:
    text = _read("user-guide/compliance.md")
    assert "legal sign-off" not in text
    assert "own counsel" in text


def test_contributing_describes_the_dco_check_that_exists() -> None:
    workflow = REPO_ROOT / ".github" / "workflows" / "dco.yml"
    assert workflow.exists()
    text = workflow.read_text(encoding="utf-8")
    assert "pull_request" in text
    assert "Signed-off-by" in text
    contributing = _read("CONTRIBUTING.md")
    assert ".github/workflows/dco.yml" in contributing
    assert "checks this automatically" not in contributing
