"""The PR security scans are configured to block (re-audit M23).

Parses .github/workflows/ci.yml and .trivyignore.yaml and checks the
EFFECTIVE settings: a scan step that stopped failing (exit code 0), lost its
severity threshold, or ignored the acceptance file would otherwise pass every
PR in silence, which is the state M23 found (no PR scanning at all).
"""
from __future__ import annotations

import datetime as dt
import json
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


def test_mcp_coverage_has_its_own_measured_floor() -> None:
    script = "\n".join(_runs(_jobs()["mcp-test"]))
    assert "pip install --require-hashes -r ../scripts/requirements-ci.txt" in script
    for target in (
        "client", "config", "prompts", "resources", "review_notice",
        "server", "token_verifier", "tools",
    ):
        assert f"--cov={target}" in script
    assert "--cov-report=xml:coverage.xml" in script
    assert "--cov-fail-under=41" in script


def test_cli_coverage_has_an_independent_measured_floor() -> None:
    script = "\n".join(_runs(_jobs()["sdk-cli-test"]))
    assert "pip install --require-hashes -r scripts/requirements-sdk-test.txt" in script
    assert "pip check" in script
    assert "python -m pytest client/tests -q" in script
    assert "python -m pytest cli/tests -q" in script
    assert "--cov=testlookup_cli" in script
    assert "--cov-report=xml:cli-coverage.xml" in script
    assert "--cov-fail-under=62" in script
    assert "--cov=client" not in script


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


ACCEPTED: set[str] = set()
# W43 (langchain 1.x) removed CVE-2026-34070 (langchain-core 0.3.86 -> 1.7.6, fix 1.2.22) and
# CVE-2026-104873 (langgraph-sdk 0.2.15 -> 0.4.14, fix 0.4.4). PR-2 (2026-11-12 supply chain) removed
# the last ten: starlette CVE-2026-54283 / CVE-2026-48818 / CVE-2025-62727 (starlette 1.10.0) and the
# seven util-linux CVEs (Alpine 3.24 ships util-linux 2.42.3-r1; the nginx stage runs apk upgrade).

# PR #375: the HIGH findings Trivy reported inside the Cypress 16.3.0 binary of the e2e-runner image
# (CI run 43053849052). Every one is scoped to that binary's directory, never accepted repo-wide.
CYPRESS_BUNDLE = {
    "CVE-2026-44728", "CVE-2026-104852", "CVE-2026-59892", "CVE-2026-34601", "CVE-2026-41672",
    "CVE-2026-41673", "CVE-2026-41674", "CVE-2026-41675", "CVE-2026-83605", "CVE-2026-83607", "CVE-2026-83608",
    "CVE-2026-83613", "CVE-2026-83614", "CVE-2026-83615", "CVE-2026-83616", "CVE-2026-83619",
    "CVE-2026-102282", "CVE-2026-39244", "CVE-2026-77301", "GHSA-8238-w5pm-2374", "GHSA-rcw4-f5rp-g42v",
    "CVE-2021-3807", "CVE-2026-102990", "CVE-2026-41324", "CVE-2026-44240", "CVE-2026-111181",
    "CVE-2026-102276", "CVE-2026-102278", "CVE-2026-13149", "CVE-2026-14257", "CVE-2026-69152",
    "CVE-2024-4068", "CVE-2026-73088", "CVE-2026-73089", "CVE-2026-87776", "CVE-2026-118664", "CVE-2026-40345",
    "CVE-2026-102599", "CVE-2026-13676", "CVE-2026-16221", "CVE-2026-18446", "CVE-2026-6321", "CVE-2026-6322",
    "CVE-2026-75975", "CVE-2026-76172", "CVE-2026-84292", "CVE-2026-111755", "CVE-2026-111756",
    "CVE-2026-116689", "CVE-2025-71329", "CVE-2025-71330", "CVE-2026-69192", "CVE-2026-59869",
    "CVE-2026-84375", "GHSA-5p4m-2wfm-xmqj", "CVE-2026-112942", "CVE-2026-4800", "CVE-2026-27904",
    "CVE-2026-4867", "CVE-2026-33671", "CVE-2026-69185", "CVE-2025-59343", "CVE-2026-76844", "CVE-2026-48779",
}
CYPRESS_LOCK = ROOT / "e2e-runner" / "reporters" / "js" / "package-lock.json"


def test_the_accepted_findings_are_exactly_the_triaged_ones() -> None:
    doc = yaml.safe_load(IGNORE.read_text(encoding="utf-8"))
    ids = {entry["id"] for entry in doc["vulnerabilities"]}
    assert ids == ACCEPTED | CYPRESS_BUNDLE, ids ^ (ACCEPTED | CYPRESS_BUNDLE)
    for entry in doc["vulnerabilities"]:
        assert entry["statement"].startswith(("MITIGATED.", "NOT REACHABLE.")), entry["id"]


def test_the_cypress_bundle_acceptances_cover_only_the_installed_cypress_binary() -> None:
    """A Cypress bump moves the binary's directory, so it fails here until the entries are re-triaged or
    deleted, and none of these ids is accepted outside the binary (a lockfile, another image)."""
    lock = json.loads(CYPRESS_LOCK.read_text(encoding="utf-8"))
    installed = lock["packages"]["node_modules/cypress"]["version"]
    doc = yaml.safe_load(IGNORE.read_text(encoding="utf-8"))
    entries = [entry for entry in doc["vulnerabilities"] if entry["id"] in CYPRESS_BUNDLE]
    assert len(entries) == len(CYPRESS_BUNDLE)
    for entry in entries:
        assert entry.get("paths") == [f"opt/cypress/{installed}/Cypress/resources/app/**"], entry["id"]
        assert f"Cypress {installed} binary" in entry["statement"], entry["id"]
    for entry in doc["vulnerabilities"]:
        if entry["id"] not in CYPRESS_BUNDLE:
            assert not any("cypress" in path.lower() for path in entry.get("paths") or []), entry["id"]


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


def test_the_debian_runtime_images_take_security_updates() -> None:
    """The backend and MCP images are python:3.14-slim (Debian). Installing a
    few packages does not upgrade the ones the base already carries, so a fix
    Debian publishes for gzip / pcre2 / sqlite / perl-base never reaches the
    image until the base tag is rebuilt upstream -- and Trivy fails every PR
    that touches an image input in the meantime (PR #56: 12 HIGH/CRITICAL, all
    with a fixed version). The frontend already takes Alpine's updates; this is
    the same rule for Debian."""
    for dockerfile in ("backend/Dockerfile", "mcp/Dockerfile"):
        steps = _joined(_final_stage(dockerfile))
        assert steps[0].startswith("FROM python:3.14-slim"), dockerfile
        runs = [s for s in steps if s.startswith("RUN ") and "apt-get upgrade -y" in s]
        assert len(runs) == 1, f"{dockerfile}: the runtime stage must run apt-get upgrade once"
        run = runs[0]
        at = steps.index(run)
        assert run.index("apt-get update") < run.index("apt-get upgrade -y"), dockerfile
        assert "rm -rf /var/lib/apt/lists/*" in run, f"{dockerfile}: apt lists left in the image"
        assert not any(
            s.startswith("RUN ") and "apt-get install" in s for s in steps[at + 1:]
        ), f"{dockerfile}: a later install would pull packages the upgrade never saw"
        end = next(i for i, s in enumerate(steps) if s.startswith(("CMD", "ENTRYPOINT")))
        assert at < end, dockerfile


def test_the_nginx_image_takes_alpine_security_updates() -> None:
    steps = _joined(_final_stage("frontend/Dockerfile"))
    assert re.fullmatch(r"FROM nginx:\d+\.\d+\.\d+-alpine[\d.]*@sha256:[0-9a-f]{64} AS production", steps[0]), (
        "pin the nginx stage by version and digest; the floating nginx:alpine changed under every build"
    )
    upgrade = [i for i, s in enumerate(steps) if s.startswith("RUN ") and "apk upgrade --no-cache" in s]
    assert upgrade and upgrade[0] < next(i for i, s in enumerate(steps) if s.startswith("CMD"))


def test_the_os_upgrade_layer_is_rebuilt_daily_in_the_ci_scan() -> None:
    """PR #342: the image scan builds from the GitHub Actions layer cache, which replayed an `apt-get upgrade`
    layer from before Debian shipped a zlib fix, so Trivy failed on a CVE whose fix the Dockerfile already takes.
    Each runtime stage declares OS_PATCH_DAY right before its upgrade step (an ARG whose value changes misses the
    cache of the RUN after it), and the scan build passes the UTC day, computed BEFORE the build."""
    upgrades = {"backend/Dockerfile": "apt-get upgrade -y", "mcp/Dockerfile": "apt-get upgrade -y",
                "frontend/Dockerfile": "apk upgrade --no-cache"}
    for dockerfile, marker in upgrades.items():
        steps = _joined(_final_stage(dockerfile))
        at = next(i for i, s in enumerate(steps) if s.startswith("RUN ") and marker in s)
        args = [i for i, s in enumerate(steps) if s.startswith("ARG OS_PATCH_DAY")]
        assert len(args) == 1 and args[0] < at, f"{dockerfile}: ARG OS_PATCH_DAY must come right before the upgrade"
        assert not any(s.startswith("RUN ") for s in steps[args[0] + 1:at]), dockerfile
    steps = _jobs()["security-images"]["steps"]
    names = [step.get("name", "") for step in steps]
    day = next(i for i, step in enumerate(steps) if step.get("id") == "day")
    build = names.index("Build the image (not pushed)")
    assert day < build, "the day stamp must exist before the image build"
    assert "OS_PATCH_DAY=${{ steps.day.outputs.day }}" in steps[build]["with"].get("build-args", "")


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


def _first_stage(dockerfile: str) -> list[str]:
    lines = (ROOT / dockerfile).read_text(encoding="utf-8").splitlines()
    froms = [i for i, line in enumerate(lines) if line.startswith("FROM ")]
    return lines[froms[0]:froms[1]]


def test_the_frontend_image_installs_exactly_the_lockfile() -> None:
    """``npm install`` skipped rolldown's optional linux-musl binding inside
    node:alpine (npm/cli#4828) although package-lock.json lists it, and
    ``vite build`` then failed in Build & Push Images. ``npm ci`` installs what
    the lockfile records, platform binaries included, and refuses a lockfile
    that disagrees with package.json instead of quietly re-resolving."""
    steps = _joined(_first_stage("frontend/Dockerfile"))
    installs = [s for s in steps if s.startswith("RUN ") and re.search(r"\bnpm (ci|install|i)\b", s)]
    assert len(installs) == 1, installs
    [run] = installs
    assert "npm ci --include=dev" in run
    assert not re.search(r"\bnpm (install|i)\b", run), "a fallback `npm install` re-resolves the tree"
    at = steps.index(run)
    assert steps.index("COPY package*.json ./") < at
    # The interception-CA trust still comes first, inside the same step.
    assert run.index("NODE_EXTRA_CA_CERTS") < run.index("npm ci")


def test_the_frontend_lockfile_matches_package_json() -> None:
    """``npm ci`` fails the image build when the two disagree, so catch it here."""
    package = json.loads((ROOT / "frontend" / "package.json").read_text(encoding="utf-8"))
    lock = json.loads((ROOT / "frontend" / "package-lock.json").read_text(encoding="utf-8"))
    assert lock["lockfileVersion"] >= 2
    root = lock["packages"][""]
    assert root.get("name") == package.get("name")
    for field in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies"):
        assert root.get(field, {}) == package.get(field, {}), f"package-lock.json {field} is stale"
    # Every declared dependency is actually resolved in the lockfile.
    for name in {**package.get("dependencies", {}), **package.get("devDependencies", {})}:
        assert f"node_modules/{name}" in lock["packages"], f"{name} is not in package-lock.json"
    # The musl binding the node:alpine build stage needs is recorded.
    if "node_modules/rolldown" in lock["packages"]:
        assert "node_modules/@rolldown/binding-linux-x64-musl" in lock["packages"]


# ── 2026-11-12 open-source review, PR-2 (supply chain) ───────────────────────

WORKFLOWS = ROOT / ".github" / "workflows"
SECRET_SCAN = WORKFLOWS / "secret-scan.yml"
_USES = re.compile(r"^\s*(?:-\s+)?uses:\s*(\S+)(.*)$", re.MULTILINE)


def _workflow(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_every_action_is_pinned_to_a_full_commit_sha() -> None:
    """M-4: a moved or hijacked tag (the tj-actions incident) ran with the job's
    token. The build-images job pushes the public images with packages: write
    and read the Docker Hub secret, yet ran docker/* actions by tag. A SHA pin
    also lets the owner turn on "require actions to be pinned to a full-length
    commit SHA", which fails every unpinned workflow. Dependabot's
    github-actions updates move the SHA and the version comment together."""
    seen = 0
    for path in sorted(WORKFLOWS.glob("*.y*ml")):
        for ref, rest in _USES.findall(path.read_text(encoding="utf-8")):
            if ref.startswith("./"):
                continue                       # a reusable workflow in this repo
            seen += 1
            assert re.fullmatch(r"[\w.-]+/[\w.-]+(/[\w./-]+)?@[0-9a-f]{40}", ref), (
                f"{path.name}: {ref} is not pinned to a commit SHA"
            )
            assert re.fullmatch(r"\s*#\s*v\d+(\.\d+){0,2}\s*", rest), (
                f"{path.name}: {ref} needs a `# vX.Y.Z` comment naming the release it pins"
            )
    assert seen >= 80, f"only {seen} action references found; the scan cannot have looked"


def test_delivery_policy_checks_gate_the_existing_backend_job() -> None:
    jobs = _workflow(WORKFLOWS / "ci.yml")["jobs"]
    steps = {step.get("name"): step for step in jobs["backend-test"]["steps"]}
    for name, script in (
        ("Enforce targeted security source policy", "scripts/security/source_policy.py"),
        ("Check rendered infrastructure profiles", "scripts/security/render_reference_profiles.py"),
    ):
        step = steps[name]
        assert script in step["run"] and '--source-sha "$GITHUB_SHA"' in step["run"]
        assert step["working-directory"] == "." and not step.get("if") and not step.get("continue-on-error")
    assert "backend-test" in jobs["build-images"]["needs"]
    dev = jobs["deploy-dev"]
    assert dev["needs"] == ["build-images"] and dev["permissions"] == {"contents": "read", "packages": "read"}
    by_name = {step.get("name"): step for step in dev["steps"]}
    assert "verified-images-${{ github.sha }}" == by_name["Download verified image manifest"]["with"]["name"]
    pins = by_name["Pin the verified image digests in kustomization"]
    assert set(pins["env"]) == {"BACKEND_DIGEST", "FRONTEND_DIGEST", "MCP_DIGEST"}
    assert 'manifest["images"][component]["source_ref"] != expected' in pins["run"]
    verified = by_name["Verify all application roles and running digests"]["run"]
    assert 'verify_kubernetes_rollout.py' in verified and '--inactive-role beat' in verified


def test_opted_in_signal_evidence_gates_the_release_manifest() -> None:
    workflow = _workflow(WORKFLOWS / "release-e2e.yml")
    triggers = workflow.get("on", workflow.get(True))
    for event in ("workflow_dispatch", "workflow_call"):
        signal_input = triggers[event]["inputs"]["verify_signals"]
        assert signal_input["type"] == "boolean" and signal_input["default"] is False
    steps = workflow["jobs"]["regression"]["steps"]
    run = next(step for step in steps if "e2e-runner/monitored_ephemeral.py" in step.get("run", ""))
    attest = next(step for step in steps if step.get("id") == "attest")
    assert run["env"]["VERIFY_SIGNALS"] == attest["env"]["VERIFY_SIGNALS"] == "${{ inputs.verify_signals }}"
    assert steps.index(run) < steps.index(attest)
    assert 'signal.get("cleanup") != "completed"' in attest["run"]
    assert 'signal.get("source_sha") != manifest["source_sha"]' in attest["run"]
    assert 'signal.get("manifest_sha256") != digest' in attest["run"]
    jobs = _workflow(RELEASE)["jobs"]
    assert "vars.REQUIRE_RELEASE_SIGNAL_GATE" in jobs["functional-verification"]["with"]["verify_signals"]
    assert "functional-verification" in jobs["promote"]["needs"]


def test_crane_is_checksum_verified_before_it_runs_with_packages_write() -> None:
    promote = _workflow(RELEASE)["jobs"]["promote"]
    assert promote["permissions"].get("packages") == "write"
    [step] = [s for s in promote["steps"] if "crane" in s.get("name", "").lower() and "run" in s]
    assert re.fullmatch(r"[0-9a-f]{64}", step["env"]["CRANE_ARCHIVE_SHA256"])
    script = step["run"]
    assert 'sha256sum --check --strict' in script
    assert script.index("sha256sum") < script.index("tar ")
    assert "| tar" not in script, "piping the download into tar skips the checksum"


def test_ci_scans_and_builds_the_committed_frontend_lockfile() -> None:
    """A-06: CI ran `npm install --package-lock-only` before the dependency scan
    and before building the published frontend image, so both could see a tree
    that no PR reviewed. `npm ci` in the frontend job and in the Dockerfile
    already refuses a lockfile that disagrees with package.json."""
    for path in sorted(WORKFLOWS.glob("*.y*ml")):
        for job in (_workflow(path).get("jobs") or {}).values():
            for step in job.get("steps") or []:
                assert "--package-lock-only" not in step.get("run", ""), (
                    f"{path.name}: {step.get('name')!r} rewrites a committed lockfile"
                )


def test_python_jobs_install_the_hashed_locks() -> None:
    jobs = _jobs()
    for name, directory in (("backend-test", "backend"), ("mcp-test", "mcp")):
        installs = [r for r in _runs(jobs[name]) if "pip install" in r and "-r " in r]
        assert installs, name
        assert all("--require-hashes -r requirements-lock.txt" in r for r in installs), (
            f"{name} must test the packages the {directory} image ships: {installs}"
        )
        setup = next(s for s in jobs[name]["steps"] if "setup-python" in s.get("uses", ""))
        assert setup["with"]["cache-dependency-path"] == f"{directory}/requirements-lock.txt"


def test_published_images_carry_signed_build_provenance() -> None:
    job = _jobs()["build-images"]
    assert job["permissions"] == {
        "contents": "read", "packages": "write",
        "id-token": "write", "attestations": "write", "artifact-metadata": "write",
    }
    steps = job["steps"]
    attests = [s for s in steps if "attest-build-provenance" in s.get("uses", "")]
    subjects = {s["with"]["subject-digest"] for s in attests}
    assert subjects == {
        "${{ steps.build-backend.outputs.digest }}",
        "${{ steps.build-frontend.outputs.digest }}",
        "${{ steps.build-mcp.outputs.digest }}",
    }
    for step in attests:
        assert str(step["with"]["push-to-registry"]).lower() == "true"
        assert step["with"]["subject-name"].startswith("${{ env.REGISTRY }}/${{ env.IMAGE_PREFIX }}/")
    # After the manifest upload, so an attestation outage cannot cost the manifest.
    upload = max(i for i, s in enumerate(steps) if "upload-artifact" in s.get("uses", ""))
    assert all(steps.index(s) > upload for s in attests)


def test_the_secret_scan_runs_on_every_change_and_blocks() -> None:
    """M-9: no general secret scanner guarded the repo. ci.yml skips Markdown-only
    changes, so the scan is its own workflow with no path filter."""
    doc = _workflow(SECRET_SCAN)
    triggers = doc.get("on") or doc.get(True)       # YAML 1.1 reads a bare `on` as True
    for event in ("pull_request", "push"):
        assert event in triggers
        assert not {"paths", "paths-ignore"} & set(triggers[event] or {}), f"{event} must see every path"
    assert doc["permissions"] == {"contents": "read"}
    steps = doc["jobs"]["gitleaks"]["steps"]
    checkout = steps[0]
    assert checkout["with"]["fetch-depth"] == 0
    install = next(s for s in steps if "gitleaks" in s.get("name", "").lower() and "curl" in s.get("run", ""))
    assert re.fullmatch(r"[0-9a-f]{64}", install["env"]["GITLEAKS_ARCHIVE_SHA256"])
    assert install["run"].index("sha256sum --check --strict") < install["run"].index("tar ")
    scan = next(step for step in steps if step.get("name") == "Scan the added commits")
    assert "gitleaks git --config .gitleaks.toml" in scan["run"]
    assert "--exit-code 1" in scan["run"]
    assert "${{" not in scan["run"], "pass event values through env:, never into the script"
    version = re.search(r"/download/v([\d.]+)/", install["run"]).group(1)
    push_check = (ROOT / "scripts" / "push_check.py").read_text(encoding="utf-8")
    assert f"gitleaks/v8@v{version}" in push_check, "push_check.py installs a different gitleaks"
    snapshot = next(step for step in steps if step.get("name") == "Scan the full tracked candidate snapshot")
    assert snapshot["if"] == "github.event_name == 'push'"
    assert 'scan_secret_snapshot.py' in snapshot["run"]
    assert '--source-sha "$GITHUB_SHA"' in snapshot["run"]
    assert '--gitleaks /usr/local/bin/gitleaks' in snapshot["run"]
    assert not snapshot.get("continue-on-error")
    proof = next(step for step in steps if step.get("name") == "Prove full candidate coverage and raw-blob isolation")
    assert proof["env"]["GITLEAKS_TEST_BINARY"] == "/usr/local/bin/gitleaks"


def test_the_gitleaks_rules_add_the_tokens_the_defaults_missed() -> None:
    import tomllib

    config = tomllib.loads((ROOT / ".gitleaks.toml").read_text(encoding="utf-8"))
    assert config["extend"]["useDefault"] is True
    rules = {rule["id"]: re.compile(rule["regex"]) for rule in config["rules"]}
    assert set(rules) == {"testlookup-ingest-key", "k3s-node-token", "cloudflare-tunnel-token"}
    # Built here so no token-shaped literal sits in the tree.
    assert rules["testlookup-ingest-key"].search("qai_" + "Z" * 32)
    assert rules["k3s-node-token"].search("K10" + "0f" * 21 + "::server:" + "Abc123")
    assert rules["cloudflare-tunnel-token"].search("eyJhIjoi" + "Q" * 48)
    for allow in config["allowlists"]:
        assert len(allow["description"]) >= 20, "say why the allowlist entry is safe"


THIRD_PARTY = WORKFLOWS / "third-party-images.yml"
MANIFEST = ROOT / "deploy" / "images.manifest.txt"


def _infra_images() -> list[str]:
    """The `infra` rows of the image manifest, read the way the workflow's awk does."""
    rows = []
    for line in MANIFEST.read_text(encoding="utf-8").splitlines():
        fields = line.split()
        if fields and not fields[0].startswith("#") and len(fields) >= 2 and fields[1] == "infra":
            rows.append(fields[0])
    return rows


def test_third_party_images_are_scanned_weekly_and_when_the_manifest_changes() -> None:
    """H-4 (OSS review 2026-11-12): Ollama, the data stores and the monitoring images
    were in no scan. The workflow reports their HIGH/CRITICAL findings with a fix."""
    workflow = _workflow(THIRD_PARTY)
    triggers = workflow.get("on") or workflow.get(True)  # YAML 1.1 reads a bare `on` as True
    assert triggers["schedule"], "no weekly run"
    assert "deploy/images.manifest.txt" in triggers["push"]["paths"]
    assert "docker-compose.monitoring.yml" in triggers["push"]["paths"]
    assert workflow["permissions"] == {"contents": "read"}


def test_the_third_party_scan_uses_the_pinned_trivy_and_covers_every_infra_row() -> None:
    text = THIRD_PARTY.read_text(encoding="utf-8")
    assert _install_pin(text) == _install_pin(CI.read_text(encoding="utf-8"))
    assert "sha256sum --check --strict" in text
    assert '$2 == "infra"' in text and "deploy/images.manifest.txt" in text
    images = _infra_images()
    assert len(images) >= 8 and any(i.startswith("ollama/ollama:") for i in images), images
    [scan] = [line for line in text.replace("\\\n", " ").splitlines() if line.strip().startswith("if trivy image")]
    assert "--severity HIGH,CRITICAL" in scan and "--ignore-unfixed" in scan, scan


# ── 2026-11-18 OpenSSF Scorecard hardening ───────────────────────────────────
# Scorecard v5.6.0 (api.securityscorecards.dev) scored main 90f6baf3 at 5.5/10.
# These pin what the fix PR changed, so each signal fails here before it can
# quietly regress on the public score.

OFFLINE_BUNDLE = WORKFLOWS / "offline-bundle.yml"
_LOCK_BLOCK = re.compile(r"^[A-Za-z0-9][^\n]*(?:\n[ \t]+[^\n]*)*", re.MULTILINE)
_LOCK_PIN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s;\\]+)")
_REQ_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")
_COMMAND_SPLIT = re.compile(r"&&|\|\||;|\|")
_LEADING_KEYWORD = re.compile(r"^(?:RUN|then|do|else)\s+")
_PYTHON = re.compile(r"python(?:3(?:\.\d+)?)?")
_PIP = re.compile(r"pip(?:3(?:\.\d+)?)?")
_FAST_CHECK = re.compile(
    r"""(from\s+['"](fast-check|@fast-check/(ava|jest|vitest))['"]"""
    r"""|require\(\s*['"](fast-check|@fast-check/(ava|jest|vitest))['"]\s*\))"""
)


def _tracked_files() -> list[str]:
    return subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True,
                          check=True).stdout.splitlines()


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _shell_commands(script: str) -> list[list[str]]:
    """Simple commands in a run: / RUN script, split the way a shell would chain them."""
    commands = []
    for line in script.replace("\\\n", " ").splitlines():
        for segment in _COMMAND_SPLIT.split(line):
            segment = _LEADING_KEYWORD.sub("", segment.strip())
            if "pip" in segment:
                commands.append(_shell_words(segment))
    return commands


def _shell_words(segment: str) -> list[str]:
    """Split on unquoted whitespace and KEEP the quotes, so `"$X"/w.whl` stays one
    word and _scorecard_word can tell which words Scorecard's parser keeps.
    (shlex drops the quotes, and its non-POSIX mode splits after a closing quote.)"""
    words: list[str] = []
    current: list[str] = []
    quote = ""
    started = False
    for ch in segment:
        if quote:
            current.append(ch)
            if ch == quote:
                quote = ""
        elif ch in "'\"":
            quote, started = ch, True
            current.append(ch)
        elif ch.isspace():
            if started:
                words.append("".join(current))
                current, started = [], False
        else:
            current.append(ch)
            started = True
    if started:
        words.append("".join(current))
    return words


def _scorecard_word(token: str) -> str | None:
    """The word as Scorecard's extractCommand sees it, or None when it drops it.

    It keeps a word only when the shell parses it as ONE part: a plain literal,
    or a single- or double-quoted literal (kept with its quotes). A word holding
    a variable or command substitution, or mixing quoted and unquoted text, is
    dropped, so `pip install --no-deps "$RUNNER_TEMP"/w/*.whl` reads as a bare
    `pip install --no-deps` and counts as unpinned (alert #199 on 86e53ba6)."""
    if len(token) >= 2 and token[0] == token[-1] == "'" and "'" not in token[1:-1]:
        return token
    if (len(token) >= 2 and token[0] == token[-1] == '"' and '"' not in token[1:-1]
            and "$" not in token and "`" not in token):
        return token
    if any(c in token for c in "'\"$`"):
        return None
    return token


def _command_name(word: str) -> str:
    """The program a shell word runs: quotes and directories dropped, so
    `"$RUNNER_TEMP/tl-e2e/bin/pip"` and `/opt/venv/bin/pip3` both read as pip."""
    return word.replace('"', "").replace("'", "").rsplit("/", 1)[-1]


def _pip_subcommand(argv: list[str]) -> list[str] | None:
    """`install ...` / `wheel ...` / `check` of a pip call, however pip is named:
    `pip`, `pip3`, a pip called by path, or `<python> -m pip`."""
    if not argv:
        return None
    name = _command_name(argv[0])
    if _PIP.fullmatch(name):
        return argv[1:]
    if argv[1:3] == ["-m", "pip"] and (_PYTHON.fullmatch(name) or "$" in argv[0]):
        return argv[3:]
    return None


def _pip_install_args(argv: list[str]) -> list[str] | None:
    """The arguments of a pip call that downloads and runs code from the package
    index. `pip wheel` is one: it builds in an isolated environment, for which it
    downloads the newest build backend (hatchling, setuptools) with no pin and no
    hash, and `--no-deps` does not cover that. Only `--no-build-isolation`, which
    builds with what is already installed, makes `pip wheel` download nothing."""
    rest = _pip_subcommand(argv)
    if not rest:
        return None
    if rest[0] == "install":
        return rest[1:]
    if rest[0] == "wheel" and "--no-build-isolation" not in rest:
        return rest[1:]
    return None


def _scorecard_reads_as_pinned(args: list[str]) -> bool:
    """Scorecard's Pinned-Dependencies rule for one `pip install` (its
    checks/raw/shell_download_validate.go): --require-hashes, only .whl files, or
    `--no-deps -e <local path>`. A `-r <file>` without --require-hashes is
    unpinned even when the file carries hashes: Scorecard does not open it."""
    hashes = no_deps = editable = wheel = False
    others: list[str] = []
    editable_sources: list[str] = []
    take_source = False
    for arg in (w for w in map(_scorecard_word, args) if w is not None):
        if take_source:
            editable_sources.append(arg)
            take_source = False
        elif arg == "--require-hashes":
            hashes = True
            break
        elif arg == "--no-deps":
            no_deps = True
        elif arg in ("-e", "--editable"):
            editable = take_source = True
        elif arg.startswith("-"):
            continue
        elif arg.endswith(".whl"):
            wheel = True
        else:
            others.append(arg)
    if editable:
        remote = any(s.startswith(("git", "svn", "hg", "bzr")) for s in editable_sources)
        return no_deps and not remote and not others
    return hashes or (wheel and not others)


def _pip_installs() -> list[tuple[str, list[str]]]:
    """Every `pip install` Scorecard parses: workflow run: steps and Dockerfile RUNs."""
    found = []
    for path in sorted(WORKFLOWS.glob("*.y*ml")):
        for job in (_workflow(path).get("jobs") or {}).values():
            for step in job.get("steps") or []:
                for argv in _shell_commands(step.get("run", "")):
                    args = _pip_install_args(argv)
                    if args is not None:
                        found.append((f"{path.name}: {step.get('name') or ' '.join(argv)}", args))
    for rel in _tracked_files():
        name = rel.rsplit("/", 1)[-1]
        if rel.startswith("client/examples/") or not (name == "Dockerfile" or name.startswith("Dockerfile.")):
            continue
        if name.endswith(".dockerignore"):
            continue
        text = (ROOT / rel).read_text(encoding="utf-8").replace("\\\n", " ")
        for line in text.splitlines():
            if line.lstrip().startswith("RUN "):
                for argv in _shell_commands(line.strip()):
                    args = _pip_install_args(argv)
                    if args is not None:
                        found.append((rel, args))
    return found


def test_every_pip_install_is_hash_pinned_the_way_scorecard_reads_it() -> None:
    """Scorecard Pinned-Dependencies (8/10 on 2026-11-18): ten installs in ci.yml,
    backend/Dockerfile and e2e-runner/Dockerfile took whatever PyPI served. They
    now read hashed locks (`make lock-python`) or install local wheels --no-deps."""
    installs = _pip_installs()
    assert len(installs) >= 12, f"only {len(installs)} pip installs found; the scan cannot have looked"
    unpinned = [f"{where}: pip install {' '.join(args)}" for where, args in installs
                if not _scorecard_reads_as_pinned(args)]
    assert not unpinned, "pip installs Scorecard reads as unpinned:\n  " + "\n  ".join(unpinned)


def test_the_pin_rule_matches_scorecards_reading() -> None:
    """The helper above must agree with Scorecard on the shapes this repo uses,
    or the previous test proves nothing."""
    pinned = [
        ["--require-hashes", "-r", "requirements-lock.txt"],
        ["--no-cache-dir", "--require-hashes", "-r", "a.txt", "-r", "b.txt"],
        ["--no-deps", "-e", "../cli"],
        ["--no-deps", "/tmp/sdk-wheels/*.whl"],
    ]
    unpinned = [
        ["pytest"],
        ["-r", "requirements-lock.txt"],  # hashes in the file are invisible to Scorecard
        ["-e", "../cli"],
        ["--no-deps", "./cli"],
        ["-c", "constraints.txt", "-r", "requirements.txt", "./cli"],
        ["--no-deps", "-e", "git+https://example.invalid/x.git"],
        [],
        # Words with a variable are dropped, leaving a bare `pip install --no-deps`.
        ["--no-deps", '"$RUNNER_TEMP"/sdk-wheels/*.whl'],
        ["--no-deps", "$RUNNER_TEMP/sdk-wheels/*.whl"],
        # A quoted literal keeps its quotes, so it no longer ends in .whl.
        ["--no-deps", '"/tmp/sdk-wheels/x.whl"'],
    ]
    assert all(_scorecard_reads_as_pinned(a) for a in pinned)
    assert not any(_scorecard_reads_as_pinned(a) for a in unpinned)
    # The line Scorecard flagged on 86e53ba6 (alert #199), end to end.
    [argv] = _shell_commands('pip install --no-deps "$RUNNER_TEMP"/sdk-wheels/*.whl')
    assert not _scorecard_reads_as_pinned(_pip_install_args(argv) or [])
    assert _pip_install_args(["python", "-m", "pip", "install", "x"]) == ["x"]
    # A pip called by path is still pip (release-e2e.yml installed PyYAML that way, unhashed).
    [argv] = _shell_commands('"$RUNNER_TEMP/tl-e2e/bin/pip" install PyYAML==6.0.3')
    assert _pip_install_args(argv) == ["PyYAML==6.0.3"]
    assert _pip_install_args(["/opt/venv/bin/pip3", "install", "x"]) == ["x"]
    assert _pip_install_args(['"$CLI_PY"', "-m", "pip", "install", "x"]) == ["x"]
    assert _pip_install_args(["pip", "check"]) is None
    assert _pip_install_args(["python3", "-m", "venv", "/tmp/pip-env"]) is None
    # `pip wheel` downloads its build backend unless build isolation is off.
    assert _pip_install_args(["pip", "wheel", "--no-deps", "./cli"]) == ["--no-deps", "./cli"]
    assert not _scorecard_reads_as_pinned(["--no-deps", "--wheel-dir", "/tmp/w", "./cli"])
    assert _pip_install_args(["python3", "-m", "pip", "wheel", "--no-deps", "--quiet", "./cli"]) is not None
    assert _pip_install_args(["pip", "wheel", "--no-build-isolation", "--no-deps", "./cli"]) is None


BUILD_LOCK = "scripts/requirements-build.txt"
# Wheel builds that still download their build backend. Each is fixed in the
# pull request named beside it; the test below fails once the file is fixed, so
# the entry is removed and the rule covers the file from then on.
_WHEEL_BUILDS_PENDING: dict[str, str] = {}


def _ordered_pip_commands(script: str) -> list[list[str]]:
    return [argv for argv in _shell_commands(script) if _pip_subcommand(argv) is not None]


def _wheel_build_scripts() -> list[tuple[str, str]]:
    """(where, script) for every script that can run `pip wheel`: workflow run
    steps, Dockerfile RUN lines and the tracked shell scripts."""
    found = []
    for path in sorted(WORKFLOWS.glob("*.y*ml")):
        for job_name, job in (_workflow(path).get("jobs") or {}).items():
            for step in job.get("steps") or []:
                if "run" in step:
                    found.append((f"{path.name}: {job_name}: {step.get('name', '')}", step["run"]))
    for rel in _tracked_files():
        name = rel.rsplit("/", 1)[-1]
        if rel.startswith("client/examples/"):
            continue
        if name == "Dockerfile" or (name.startswith("Dockerfile.") and not name.endswith(".dockerignore")):
            text = (ROOT / rel).read_text(encoding="utf-8").replace("\\\n", " ")
            found.extend((rel, line.strip()) for line in text.splitlines() if line.lstrip().startswith("RUN "))
        elif name.endswith(".sh"):
            found.append((rel, (ROOT / rel).read_text(encoding="utf-8")))
    return found


def _unlocked_wheel_builds(script: str) -> list[str]:
    """Every `pip wheel` in the script that could download a build backend: it
    lacks --no-build-isolation, or the same pip did not first install the hashed
    build lock (so the backend it builds with came from somewhere else)."""
    problems = []
    locked: set[str] = set()                   # the pip words that installed the build lock
    for argv in _ordered_pip_commands(script):
        rest = _pip_subcommand(argv) or []
        if not rest:
            continue
        pip_word = " ".join(argv[: len(argv) - len(rest)])
        if rest[0] == "install":
            files = [rest[i + 1] for i, word in enumerate(rest[:-1]) if word == "-r"]
            if "--require-hashes" in rest and any(_command_name(f) == "requirements-build.txt" for f in files):
                locked.add(pip_word)
        elif rest[0] == "wheel":
            if "--no-build-isolation" not in rest:
                problems.append(f"{' '.join(argv)}: no --no-build-isolation")
            elif pip_word not in locked:
                problems.append(f"{' '.join(argv)}: {pip_word} did not install {BUILD_LOCK} with --require-hashes first")
    return problems


def test_every_wheel_build_uses_the_hashed_build_lock() -> None:
    """Security audit 2026-11-18: `pip wheel --no-deps ./cli` ran in build-images
    after the registry logins, with packages: write and id-token: write, and
    downloaded the newest hatchling to build with. Every wheel build now installs
    scripts/requirements-build.txt with --require-hashes and builds with
    --no-build-isolation."""
    builds = 0
    for where, script in _wheel_build_scripts():
        wheels = [argv for argv in _ordered_pip_commands(script) if (_pip_subcommand(argv) or [""])[0] == "wheel"]
        if not wheels:
            continue
        pending = _WHEEL_BUILDS_PENDING.get(where)
        problems = _unlocked_wheel_builds(script)
        if pending:
            assert problems, (
                f"{where} now builds from the hashed lock: delete it from _WHEEL_BUILDS_PENDING"
            )
            continue
        builds += len(wheels)
        assert not problems, f"{where}:\n  " + "\n  ".join(problems)
    assert builds >= 4, f"only {builds} wheel builds found; the scan cannot have looked"


def test_the_wheel_build_rule_reads_the_shapes_this_repo_uses() -> None:
    good = (
        'python3 -m venv "$RUNNER_TEMP/wheel-build"\n'
        '"$RUNNER_TEMP/wheel-build/bin/pip" install --require-hashes -r scripts/requirements-build.txt\n'
        '"$RUNNER_TEMP/wheel-build/bin/pip" wheel --no-build-isolation --no-deps --wheel-dir /tmp/w ./cli\n'
    )
    assert _unlocked_wheel_builds(good) == []
    assert _unlocked_wheel_builds(
        "RUN /tmp/b/bin/pip install --no-cache-dir --require-hashes -r scripts/requirements-build.txt"
        " && /tmp/b/bin/pip wheel --no-build-isolation --no-deps ./cli"
    ) == []
    # Isolation left on: pip downloads the backend whatever is installed.
    assert _unlocked_wheel_builds(good.replace("--no-build-isolation ", ""))
    # The lock installed without hashes, not installed, or installed by another pip.
    assert _unlocked_wheel_builds(good.replace("--require-hashes ", ""))
    assert _unlocked_wheel_builds(good.splitlines()[2])
    assert _unlocked_wheel_builds(good.replace('"$RUNNER_TEMP/wheel-build/bin/pip" wheel', "pip wheel"))
    assert _unlocked_wheel_builds('"$CLI_PY" -m pip wheel --no-deps --quiet --wheel-dir "$S/cli" "$R/cli"')


def test_the_build_lock_holds_every_declared_build_requirement() -> None:
    """--no-build-isolation builds with what the lock installed and nothing else,
    so a build requirement added to either pyproject.toml must be in the lock."""
    import tomllib

    pins = _lock_pins(ROOT / BUILD_LOCK)
    for project in ("cli", "client"):
        requires = tomllib.loads((ROOT / project / "pyproject.toml").read_text(encoding="utf-8"))["build-system"]["requires"]
        wanted = {_norm(_REQ_NAME.match(r).group(1)) for r in requires}
        assert wanted and wanted <= set(pins), f"{project}: {wanted - set(pins)} missing from {BUILD_LOCK}"
    # The release runner image reads the lock from its own (allow-list) build context.
    ignore = (ROOT / "e2e-runner" / "Dockerfile.dockerignore").read_text(encoding="utf-8").splitlines()
    assert f"!{BUILD_LOCK}" in ignore
    assert ignore.index(f"!{BUILD_LOCK}") > ignore.index("scripts/**")


def test_build_images_runs_no_package_manager_after_a_registry_login() -> None:
    """Once docker/login-action has run, the job's Docker config holds registry
    credentials with packages: write. Nothing after that point may download and
    run code from a package index; the SDK staging step (which builds the CLI
    wheel) therefore comes before the logins."""
    steps = _jobs()["build-images"]["steps"]
    logins = [i for i, s in enumerate(steps) if "docker/login-action" in s.get("uses", "")]
    assert len(logins) == 2
    stage = next(i for i, s in enumerate(steps) if s.get("name") == "Stage client SDKs into backend context")
    assert stage < logins[0]
    assert "pip" in steps[stage]["run"], "the staging step no longer builds the CLI wheel"
    for step in steps[logins[0]:]:
        match = re.search(r"\b(pip3?|npm|npx|pnpm|yarn|uv|go (?:install|get)|cargo|gem|curl|wget)\b", step.get("run", ""))
        assert not match, f"{step.get('name')!r} runs {match.group(0)} after the registry login"


def _lock_pins(path: Path) -> dict[str, str]:
    pins = {}
    for block in _LOCK_BLOCK.findall(path.read_text(encoding="utf-8")):
        match = _LOCK_PIN.match(block)
        assert match, f"{path.name}: {block.splitlines()[0]!r} is not pinned with =="
        assert "--hash=sha256:" in block, f"{path.name}: {match.group(1)} has no hash"
        pins[_norm(match.group(1))] = match.group(2)
    assert pins, f"{path.name} holds no pins"
    return pins


def _declared(pyproject: Path) -> set[str]:
    import tomllib

    deps = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["dependencies"]
    return {_norm(_REQ_NAME.match(d).group(1)) for d in deps}


def _required(requirements: Path) -> set[str]:
    names = set()
    for line in requirements.read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.lstrip().startswith(("#", "-")):
            names.add(_norm(_REQ_NAME.match(line).group(1)))
    return names


def test_the_ci_locks_hold_what_their_jobs_install() -> None:
    """The new locks replace `pip install ./cli` style resolution, so each must
    hold every dependency its job relies on (`pip check` re-checks it in CI)."""
    cli = _declared(ROOT / "cli" / "pyproject.toml")
    client = _declared(ROOT / "client" / "pyproject.toml")

    sdk = _lock_pins(ROOT / "scripts" / "requirements-sdk-test.txt")
    assert cli | client | {"pytest", "pytest-cov"} <= set(sdk), (cli | client) - set(sdk)

    tools = _lock_pins(ROOT / "scripts" / "requirements-ci.txt")
    assert {"pytest", "pytest-cov", "pyyaml"} <= set(tools)

    runner = _lock_pins(ROOT / "e2e-runner" / "requirements-lock.txt")
    wanted = cli | _required(ROOT / "e2e-runner" / "requirements.txt") | _required(ROOT / "mcp" / "requirements.txt")
    assert wanted <= set(runner), wanted - set(runner)

    _lock_pins(ROOT / "backend" / "requirements-ragas-lock.txt")


def test_the_backend_cli_extras_agree_with_the_backend_lock() -> None:
    """backend-test installs requirements-lock.txt and requirements-cli-test.txt
    in ONE pip call; a package both pin must be pinned to the same version, or
    the job fails to resolve. `make lock-python` keeps them in step."""
    backend = _lock_pins(ROOT / "backend" / "requirements-lock.txt")
    extras = _lock_pins(ROOT / "backend" / "requirements-cli-test.txt")
    drift = {name: (backend[name], version) for name, version in extras.items()
             if name in backend and backend[name] != version}
    assert not drift, f"re-run `make lock-python`: {drift}"
    missing = _declared(ROOT / "cli" / "pyproject.toml") - set(backend) - set(extras)
    assert not missing, missing


def test_the_security_policy_links_the_private_reporting_form() -> None:
    """Scorecard Security-Policy (4/10 on 2026-11-18) gives 6 of its points to a
    URL or e-mail address for reports; the policy only described the button."""
    text = (ROOT / "SECURITY.md").read_text(encoding="utf-8")
    assert "https://github.com/test-intelligence/testlookup/security/advisories/new" in text


def test_a_typescript_property_test_keeps_the_fuzzing_signal() -> None:
    """Scorecard Fuzzing (0/10 on 2026-11-18) recognises fast-check imports in a
    TypeScript file. The property tests fuzz the open-redirect and javascript:
    URL guards; the first run found a tab that turned a saved path into
    `//host` (returnPath.ts)."""
    package = json.loads((ROOT / "frontend" / "package.json").read_text(encoding="utf-8"))
    assert "fast-check" in package["devDependencies"]
    tests = [p for p in _tracked_files() if p.startswith("frontend/src/") and p.endswith((".test.ts", ".test.tsx"))]
    fuzzed = {p for p in tests if _FAST_CHECK.search((ROOT / p).read_text(encoding="utf-8"))}
    assert {
        "frontend/src/utils/returnPath.property.test.ts",
        "frontend/src/utils/safeUrl.property.test.ts",
    } <= fuzzed, fuzzed


# A release run of offline-bundle.yml: dispatched by hand, from main, with a
# version. RELEASING.md step 6 (`gh workflow run offline-bundle.yml -f
# version=X.Y.Z`) starts it on the default branch, so it meets all three.
_RELEASE_BUNDLE_RUN = (
    "env.RELEASE_TAG != '' && github.ref == 'refs/heads/main' && github.event_name == 'workflow_dispatch'"
)
_SIGNER = "test-intelligence/testlookup/.github/workflows/"
_VERIFY_DOCS = (
    "RELEASING.md",
    "README.md",
    "user-guide/air-gapped-install.md",
    ".github/workflows/ci.yml",
    ".github/workflows/offline-bundle.yml",
)


def _attestation_verify_commands(text: str) -> list[str]:
    """Every `gh attestation verify ...` in a document or workflow comment, with
    its backslash-continued lines joined and comment markers dropped."""
    commands = []
    lines = text.splitlines()
    for at, line in enumerate(lines):
        start = line.find("gh attestation verify")
        if start < 0:
            continue
        command = line[start:]
        while command.rstrip().endswith("\\") and at + 1 < len(lines):
            at += 1
            command = command.rstrip()[:-1].rstrip() + " " + lines[at].strip().lstrip("#").strip()
        commands.append(command.split("`")[0].strip())
    return commands


def test_the_bundle_is_signed_only_on_a_release_run_dispatched_from_main() -> None:
    """Security audit 2026-11-18: the attest step ran on any run that was given a
    version, including one dispatched from a branch whose workflow file had been
    changed, and the documented check accepted that signature. A run from another
    ref still builds and checks the bundle; it is just not signed."""
    workflow = _workflow(OFFLINE_BUNDLE)
    assert workflow["env"]["RELEASE_TAG"] == "${{ inputs.version && format('v{0}', inputs.version) || '' }}"
    triggers = workflow.get("on") or workflow.get(True)
    assert "version" in triggers["workflow_dispatch"]["inputs"]
    steps = workflow["jobs"]["build-bundle"]["steps"]
    signing = [s for s in steps if "attest" in s.get("uses", "") or "steps.attest." in str(s.get("env", ""))]
    assert len(signing) == 2
    for step in signing:
        condition = step["if"]
        assert "github.ref == 'refs/heads/main'" in condition, step.get("name")
        assert "github.event_name == 'workflow_dispatch'" in condition, step.get("name")
        assert "env.RELEASE_TAG != ''" in condition, step.get("name")
        assert "||" not in condition, "an `or` would let another kind of run sign"


def test_the_documented_attestation_checks_name_the_signing_workflow_and_main() -> None:
    """`gh attestation verify --repo ...` alone accepts a signature from ANY
    workflow run in the repository, including one started from a branch. The
    documented commands also bind the workflow file and the source ref."""
    seen = 0
    for rel in _VERIFY_DOCS:
        commands = _attestation_verify_commands((ROOT / rel).read_text(encoding="utf-8"))
        assert commands, f"{rel} documents no `gh attestation verify` command"
        for command in commands:
            seen += 1
            signer = _SIGNER + ("ci.yml" if "oci://" in command else "offline-bundle.yml")
            assert f"--signer-workflow {signer}" in command, f"{rel}: {command}"
            assert "--source-ref refs/heads/main" in command, f"{rel}: {command}"
            assert "--repo test-intelligence/testlookup" in command, f"{rel}: {command}"
    assert seen >= 6, f"only {seen} commands found; the scan cannot have looked"
    # The helper reads a continued command in a workflow comment as one command.
    assert _attestation_verify_commands("  #   gh attestation verify a.tar.gz \\\n  #     --repo o/r \\\n  #     --bundle b\n  # next") == [
        "gh attestation verify a.tar.gz --repo o/r --bundle b"
    ]


def test_the_air_gap_guide_authenticates_the_bundle_before_extraction() -> None:
    """The transferred checksum is attacker-replaceable, so signed provenance
    must authenticate the tarball before any archive member can be trusted."""
    guide = (ROOT / "user-guide" / "air-gapped-install.md").read_text(encoding="utf-8")
    transfer = guide.split("## 4. Transfer and verify", 1)[1].split("\n---", 1)[0]
    [command] = _attestation_verify_commands(transfer)
    assert "--bundle testlookup-offline-X.Y.Z.intoto.jsonl" in command
    assert command.endswith("--custom-trusted-root /path/to/trusted_root.jsonl &&")
    block = transfer.split("```bash\n", 1)[1].split("\n```", 1)[0]
    assert block.split(" &&\n")[1:] == [
        "sha256sum -c testlookup-offline-X.Y.Z.tar.gz.sha256",
        "tar xzf testlookup-offline-X.Y.Z.tar.gz",
        "cd testlookup-offline-X.Y.Z",
        "./import-bundle.sh --verify-only",
    ]
    guidance = " ".join(transfer.split())
    assert "provisioned independently of this transfer" in guidance
    assert "by themselves they do not establish publisher identity" in guidance


def test_the_bundle_builder_does_not_recommend_checksum_only_transfer() -> None:
    """The custom builder must require publisher authentication before its
    generated instructions permit checksum, extraction, or import commands."""
    builder = (ROOT / "scripts" / "release" / "offline-bundle.sh").read_text(encoding="utf-8")
    assert "Transfer both files to the air-gapped site" not in builder
    assert "Quick start (after publisher authentication)" in builder
    assert "before running checksum, tar, or import commands" in builder


def test_a_release_bundle_run_attaches_signed_provenance() -> None:
    """Scorecard Signed-Releases (0/10 on 2026-11-18) reads the release assets for
    *.intoto.jsonl / *.sig files. A release run of offline-bundle.yml now attests
    the release files it publishes and keeps the Sigstore bundle beside them."""
    job = _workflow(OFFLINE_BUNDLE)["jobs"]["build-bundle"]
    assert job["permissions"] == {
        "actions": "read", "contents": "read", "id-token": "write",
        "attestations": "write", "artifact-metadata": "write",
    }
    steps = job["steps"]
    [attest] = [s for s in steps if "attest-build-provenance" in s.get("uses", "")]
    assert attest["if"] == _RELEASE_BUNDLE_RUN
    assert [p.strip() for p in attest["with"]["subject-path"].splitlines() if p.strip()] == [
        "dist/testlookup-offline-${{ env.BUNDLE_VERSION }}.tar.gz",
        "dist/testlookup-offline-${{ env.BUNDLE_VERSION }}.tar.gz.sha256",
        "dist/SHA256SUMS",
        "dist/release-manifest.json",
    ]
    [keep] = [s for s in steps if ".intoto.jsonl" in s.get("run", "")]
    assert keep["if"] == _RELEASE_BUNDLE_RUN
    assert keep["env"]["BUNDLE_PATH"] == "${{ steps.attest.outputs.bundle-path }}"
    [upload] = [s for s in steps if "upload-artifact" in s.get("uses", "")]
    assert "dist/*.intoto.jsonl" in upload["with"]["path"]
    assert "dist/release-manifest.json" in upload["with"]["path"]
    sums = next(i for i, s in enumerate(steps) if "> dist/SHA256SUMS" in s.get("run", ""))
    assert sums < steps.index(attest) < steps.index(keep) < steps.index(upload)


def test_the_online_manifest_contains_only_ci_attested_release_digests() -> None:
    """The online installer manifest is generated only after each digest is bound to CI and source."""
    job = _workflow(OFFLINE_BUNDLE)["jobs"]["build-bundle"]
    pull = next(s["run"] for s in job["steps"] if s.get("name") == "Pull the release's verified images")
    publish = next(
        s["run"] for s in job["steps"]
        if s.get("name") == "Publish the verified image manifest for online installs"
    )

    assert 'artifact_name="verified-images-$source_sha"' in pull
    assert 'gh run download "${matches[0]}" --name "$artifact_name"' in pull
    assert "release_manifest.py validate" in pull
    assert '[ "$release_digest" = "$manifest_digest" ]' in pull
    assert 'gh attestation verify "oci://$manifest_ref"' in pull
    assert "--signer-workflow test-intelligence/testlookup/.github/workflows/ci.yml" in pull
    assert "--source-ref refs/heads/main" in pull
    assert '--source-digest "$source_sha"' in pull
    assert "cp verified-images/release-manifest.json dist/release-manifest.json" in publish
    assert publish.count("sha256sum") == 2


def test_every_osv_acceptance_says_why_and_expires() -> None:
    """Scorecard Vulnerabilities runs osv-scanner, which honours an
    osv-scanner.toml beside a lockfile. Each accepted finding must say why and
    expire within 90 days (the .trivyignore.yaml rule), so it comes back."""
    import tomllib

    files = [p for p in _tracked_files() if p.rsplit("/", 1)[-1] == "osv-scanner.toml"]
    assert files, "no osv-scanner.toml found"
    today = dt.datetime.now(dt.timezone.utc).date()
    ids = set()
    for rel in files:
        doc = tomllib.loads((ROOT / rel).read_text(encoding="utf-8"))
        assert set(doc) == {"IgnoredVulns"}, f"{rel}: only IgnoredVulns entries belong here"
        for entry in doc["IgnoredVulns"]:
            assert re.fullmatch(r"(CVE-\d{4}-\d{4,}|GHSA(-[23456789cfghjmpqrvwx]{4}){3}|PYSEC-\d{4}-\d+)",
                                entry["id"]), f"{rel}: {entry}"
            assert len(str(entry.get("reason", "")).strip()) >= 20, f"{rel}: {entry['id']}: say WHY"
            until = entry["ignoreUntil"]
            until = until if isinstance(until, dt.date) else dt.date.fromisoformat(str(until))
            assert until <= today + dt.timedelta(days=MAX_ACCEPTANCE_DAYS), (
                f"{rel}: {entry['id']}: acceptance longer than {MAX_ACCEPTANCE_DAYS} days"
            )
            ids.add(entry["id"])
        siblings = {p.rsplit("/", 1)[-1] for p in _tracked_files() if p.rsplit("/", 1)[0] == rel.rsplit("/", 1)[0]}
        assert siblings & {"package-lock.json", "requirements-lock.txt", "requirements.txt"}, (
            f"{rel}: osv-scanner reads it only beside a lockfile"
        )
    # The chromadb acceptances rest on this guard; it must exist while they do.
    if ids & {"PYSEC-2026-311", "PYSEC-2026-3813", "PYSEC-2026-3814", "PYSEC-2026-3815"}:
        assert (ROOT / "backend" / "tests" / "regression" / "test_chromadb_server_code_is_never_used.py").is_file()
