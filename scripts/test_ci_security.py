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
    assert "pip install pytest pytest-cov pyyaml" in script
    for target in (
        "client", "config", "prompts", "resources", "review_notice",
        "server", "token_verifier", "tools",
    ):
        assert f"--cov={target}" in script
    assert "--cov-report=xml:coverage.xml" in script
    assert "--cov-fail-under=41" in script


def test_cli_coverage_has_an_independent_measured_floor() -> None:
    script = "\n".join(_runs(_jobs()["sdk-cli-test"]))
    assert "pip install pytest pytest-cov" in script
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
    scan = steps[-1]
    assert "gitleaks git --config .gitleaks.toml" in scan["run"]
    assert "--exit-code 1" in scan["run"]
    assert "${{" not in scan["run"], "pass event values through env:, never into the script"
    version = re.search(r"/download/v([\d.]+)/", install["run"]).group(1)
    push_check = (ROOT / "scripts" / "push_check.py").read_text(encoding="utf-8")
    assert f"gitleaks/v8@v{version}" in push_check, "push_check.py installs a different gitleaks"


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
