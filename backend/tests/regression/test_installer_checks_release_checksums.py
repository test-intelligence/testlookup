"""Installing a release authenticates its files and immutable application images.

``install.sh`` downloaded the Compose file, the env template and two scripts
and ran one of them with no integrity check, although every release publishes
``SHA256SUMS`` for exactly those files (the "Installer checksums" step of
``.github/workflows/offline-bundle.yml``, attached to the GitHub release).

The rule: when the ref being installed is a release tag (``vX.Y.Z`` in
``TL_REF`` or ``TESTLOOKUP_VERSION``), the installer authenticates that
release's ``SHA256SUMS`` and immutable image manifest, compares every file it
fetched, verifies the complete application-and-infrastructure digest set, and
generates a Compose override BEFORE it writes into the install directory or runs anything it
downloaded. A mismatch or missing release asset stops the install. For an untagged ref
(``TL_REF=main``, the unreleased tip, only when asked for) there is nothing to
compare with, and it says so. With no ref and no version given, the installer
looks up the latest published release and installs that one, checked the same
way; when the lookup fails it stops.

No network: the fake release is a directory read through ``file://`` URLs
(``TL_RAW_BASE`` / ``TL_RELEASE_BASE``), and ``docker`` is a stub on ``PATH``.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from tests.shell_utils import bash_environment

pytestmark = pytest.mark.regression

ROOT = Path(__file__).resolve().parents[3]
INSTALLER = ROOT / "install.sh"
TAG = "v9.9.9"
FILES = ("docker-compose.release.yml", ".env.example", "scripts/gen-dev-env.sh", "scripts/init-db.sql")
SOURCE_SHA = "a" * 40
APP_COMPONENTS = ("backend", "frontend", "mcp")
INFRA_NAMES = {
    "postgres": "postgres:16-alpine",
    "valkey": "valkey/valkey:8.1-alpine",
    "mongo": "mongo:7",
    "versitygw": "ghcr.io/versity/versitygw:v1.8.0",
    "busybox": "busybox:1.36",
    "ollama": "ollama/ollama:0.40.10",
    "chroma": "chromadb/chroma:1.5.9",
}
COMPONENTS = APP_COMPONENTS + tuple(INFRA_NAMES)
DIGESTS = {component: format(index, "x") * 64 for index, component in enumerate(COMPONENTS, start=1)}


def _image(component: str) -> str:
    name = (
        f"ghcr.io/test-intelligence/testlookup/{component}"
        if component in APP_COMPONENTS
        else INFRA_NAMES[component]
    )
    return f"{name}@sha256:{DIGESTS[component]}"


#: Stands in for the real generator: proves whether a downloaded script ran.
FAKE_GENERATOR = '#!/usr/bin/env bash\ncd "$(dirname "$0")/.."\necho "MARKER=the-downloaded-script-ran" > .env\n'
_TIMEOUT_S = 300  # a hang guard, not a speed bound: process start-up is slow on Windows


def _bash() -> str | None:
    candidates = [r"C:\Program Files\Git\bin\bash.exe", r"C:\Program Files\Git\usr\bin\bash.exe"] if os.name == "nt" else []
    candidates.append(shutil.which("bash") or "")
    for candidate in candidates:
        if not candidate or not Path(candidate).exists():
            continue
        try:
            probe = subprocess.run([candidate, "-c", "command -v curl >/dev/null 2>&1"], capture_output=True,
                                   timeout=60, env=bash_environment(candidate))
        except (OSError, subprocess.SubprocessError):
            continue
        if probe.returncode == 0:
            return candidate
    return None


_BASH = _bash()


def _release(tmp_path: Path, ref: str, *, sums: bool = True, tamper: str | None = None) -> dict[str, str]:
    """A fake repository + release under tmp_path; returns the installer's environment."""
    raw = tmp_path / "served" / "raw" / ref
    lines = []
    for index, relative in enumerate(FILES):
        target = raw / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        body = FAKE_GENERATOR if relative.endswith("gen-dev-env.sh") else f"# released file {index}: {relative}\n"
        target.write_bytes(body.encode())
        lines.append(f"{hashlib.sha256(body.encode()).hexdigest()}  {relative}\n")
        if tamper == relative:
            target.write_bytes(body.encode() + b"# changed after the release\n")
    if sums:
        published = tmp_path / "served" / "releases" / ref
        published.mkdir(parents=True)
        (published / "SHA256SUMS").write_bytes("".join(lines).encode())
        manifest = {
            "schema_version": 2,
            "source_sha": SOURCE_SHA,
            "version": ref,
            "images": {
                component: {"source_ref": _image(component)}
                for component in COMPONENTS
            },
        }
        (published / "release-manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        (published / f"testlookup-offline-{ref.removeprefix('v')}.intoto.jsonl").write_text(
            '{"fake":"signed provenance bundle"}\n', encoding="utf-8"
        )
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    (stubs / "docker").write_bytes(b"#!/usr/bin/env bash\nexit 0\n")
    (stubs / "docker").chmod(0o755)
    (stubs / "gh").write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$*\" >> \"$FAKE_GH_LOG\"\n"
        "if [ -n \"${FAKE_GH_FAIL_ON:-}\" ] && [[ \"$*\" == *\"$FAKE_GH_FAIL_ON\"* ]]; then exit 1; fi\n",
        encoding="utf-8",
    )
    (stubs / "gh").chmod(0o755)
    env = bash_environment(_BASH)
    env["PATH"] = str(stubs) + os.pathsep + env.get("PATH", "")
    env.update(
        TL_RAW_BASE=(tmp_path / "served" / "raw").as_uri(),
        TL_RELEASE_BASE=(tmp_path / "served" / "releases").as_uri(),
        TL_DIR="install",
        TL_NO_UP="1",
        FAKE_GH_LOG=str(tmp_path / "gh.log"),
    )
    for name in ("TL_REF", "TESTLOOKUP_VERSION", "TESTLOOKUP_IMAGE", "TL_PROFILE"):
        env.pop(name, None)
    return env


def _install(tmp_path: Path, env: dict[str, str]) -> subprocess.CompletedProcess:
    if _BASH is None:
        pytest.skip("no bash with curl on this machine")
    script = INSTALLER.read_text(encoding="utf-8")
    assert "TL_RAW_BASE" in script and "TL_RELEASE_BASE" in script, (
        "install.sh cannot be pointed at a local copy, so this test will not run it "
        "(it would download from the network)"
    )
    shutil.copy(INSTALLER, tmp_path / "install.sh")
    return subprocess.run([_BASH, "install.sh"], cwd=tmp_path, env=env, capture_output=True,
                          text=True, timeout=_TIMEOUT_S)


def test_a_release_install_checks_every_file_then_runs(tmp_path):
    """Positive case, through TESTLOOKUP_VERSION alone: files and images follow the tag."""
    env = _release(tmp_path, TAG)
    env["TESTLOOKUP_VERSION"] = TAG
    result = _install(tmp_path, env)
    assert result.returncode == 0, result.stderr
    assert f"Checked 4 downloaded files against the {TAG} SHA256SUMS" in result.stdout
    installed = tmp_path / "install"
    for relative in FILES:
        assert (installed / relative).is_file(), relative
    assert "the-downloaded-script-ran" in (installed / ".env").read_text(encoding="utf-8")
    assert "docker compose -f docker-compose.release.yml -f docker-compose.images.yml" in result.stdout


def test_a_release_uses_only_authenticated_digest_refs_for_every_release_service(tmp_path):
    env = _release(tmp_path, TAG)
    env["TL_REF"] = TAG
    result = _install(tmp_path, env)
    assert result.returncode == 0, result.stderr

    override = yaml.safe_load((tmp_path / "install" / "docker-compose.images.yml").read_text(encoding="utf-8"))
    release = yaml.safe_load((ROOT / "docker-compose.release.yml").read_text(encoding="utf-8"))
    expected_components = {}
    for service, definition in release["services"].items():
        image_ref = definition.get("image", "")
        for component in APP_COMPONENTS:
            if f"/{component}:${{TESTLOOKUP_VERSION:-latest}}" in image_ref:
                expected_components[service] = component
    expected_components.update({
        "postgres": "postgres",
        "mongo": "mongo",
        "redis": "valkey",
        "minio": "versitygw",
        "ollama": "ollama",
        "chromadb": "chroma",
    })
    assert set(override["services"]) == set(expected_components), (
        "every tag-addressed release service in the Compose file needs a digest override"
    )
    for service, component in expected_components.items():
        assert override["services"][service]["image"] == _image(component)
    assert all("@sha256:" in service["image"] for service in override["services"].values())

    checks = (tmp_path / "gh.log").read_text(encoding="utf-8")
    assert "attestation verify" in checks
    assert "SHA256SUMS" in checks and "release-manifest.json" in checks
    for component in APP_COMPONENTS:
        assert f"oci://ghcr.io/test-intelligence/testlookup/{component}@sha256:{DIGESTS[component]}" in checks
    assert f"--source-digest {SOURCE_SHA}" in checks


def test_a_manifest_without_trusted_provenance_stops_before_installing_or_running(tmp_path):
    env = _release(tmp_path, TAG)
    env["TL_REF"] = TAG
    env["FAKE_GH_FAIL_ON"] = "release-manifest.json"
    result = _install(tmp_path, env)
    assert result.returncode != 0
    assert "image manifest does not have trusted TestLookup release provenance" in result.stderr
    assert not (tmp_path / "install" / ".env").exists()
    assert not (tmp_path / "install" / "scripts").exists()
    assert not (tmp_path / "install" / "docker-compose.images.yml").exists()


def test_an_incomplete_signed_image_set_stops_before_installing_or_running(tmp_path):
    env = _release(tmp_path, TAG)
    env["TL_REF"] = TAG
    manifest_path = tmp_path / "served" / "releases" / TAG / "release-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["images"].pop("postgres")
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    result = _install(tmp_path, env)

    assert result.returncode != 0
    assert "exactly ten application and infrastructure image references" in result.stderr
    assert not (tmp_path / "install" / ".env").exists()
    assert not (tmp_path / "install" / "docker-compose.images.yml").exists()


def test_a_custom_registry_keeps_the_authenticated_digests(tmp_path):
    env = _release(tmp_path, TAG)
    env["TL_REF"] = TAG
    env["TESTLOOKUP_IMAGE"] = "registry.example:5000/team/testlookup"
    result = _install(tmp_path, env)
    assert result.returncode == 0, result.stderr

    services = yaml.safe_load(
        (tmp_path / "install" / "docker-compose.images.yml").read_text(encoding="utf-8")
    )["services"]
    assert services["backend"]["image"] == (
        f"registry.example:5000/team/testlookup/backend@sha256:{DIGESTS['backend']}"
    )
    assert services["frontend"]["image"].endswith(f"/frontend@sha256:{DIGESTS['frontend']}")
    assert services["mcp"]["image"].endswith(f"/mcp@sha256:{DIGESTS['mcp']}")
    for service, component in (("postgres", "postgres"), ("mongo", "mongo"), ("redis", "valkey"),
                               ("minio", "versitygw"), ("ollama", "ollama"), ("chromadb", "chroma")):
        assert services[service]["image"] == _image(component)


def test_release_files_and_requested_image_release_must_match(tmp_path):
    env = _release(tmp_path, TAG)
    env["TL_REF"] = TAG
    env["TESTLOOKUP_VERSION"] = "v9.9.8"
    result = _install(tmp_path, env)
    assert result.returncode != 0
    assert "must match the release files" in result.stderr
    assert not (tmp_path / "install").exists()


def test_a_changed_file_stops_the_install_before_anything_runs(tmp_path):
    env = _release(tmp_path, TAG, tamper="scripts/gen-dev-env.sh")
    env["TL_REF"] = TAG
    earlier = tmp_path / "install" / "docker-compose.release.yml"
    earlier.parent.mkdir()
    earlier.write_text("# an earlier install\n", encoding="utf-8")
    result = _install(tmp_path, env)
    assert result.returncode != 0
    assert "checksum mismatch for scripts/gen-dev-env.sh" in result.stderr
    assert not (tmp_path / "install" / ".env").exists(), "the downloaded script ran before the check"
    assert not (tmp_path / "install" / "scripts").exists(), "an unverified file was installed"
    assert earlier.read_text(encoding="utf-8") == "# an earlier install\n", "the earlier install was overwritten"


def test_a_release_without_published_checksums_is_refused(tmp_path):
    env = _release(tmp_path, TAG, sums=False)
    env["TL_REF"] = TAG
    result = _install(tmp_path, env)
    assert result.returncode != 0
    assert "could not download the checksums" in result.stderr
    assert not (tmp_path / "install" / ".env").exists()
    assert not (tmp_path / "install" / "scripts").exists()


def test_the_unreleased_tip_installs_only_when_named_and_says_it_is_not_verified(tmp_path):
    env = _release(tmp_path, "main", sums=False)
    env["TL_REF"] = "main"
    result = _install(tmp_path, env)
    assert result.returncode == 0, result.stderr
    assert "no checksum is available for an untagged ref (main)" in result.stderr
    assert (tmp_path / "install" / ".env").exists()
    assert "TESTLOOKUP_VERSION=latest " in result.stdout


# Security review 2026-11-20, TL-06: with nothing set, the installer took its
# files from the tip of main, unchecked, and ran the `latest` images. The
# default is now the latest published release, files and images together.


def _latest(tmp_path: Path, env: dict[str, str], answer: str | None) -> None:
    """Point the latest-release lookup at a local file (or at nothing)."""
    api = tmp_path / "served" / "latest.json"
    if answer is not None:
        api.write_text(answer, encoding="utf-8")
    env["TL_LATEST_RELEASE_URL"] = api.as_uri()


def test_the_default_install_is_the_latest_release_checked_and_pinned(tmp_path):
    env = _release(tmp_path, TAG)
    _latest(tmp_path, env, '{\n  "url": "x",\n  "tag_name": "%s",\n  "name": "TestLookup"\n}\n' % TAG)
    result = _install(tmp_path, env)
    assert result.returncode == 0, result.stderr
    assert f"Latest release: {TAG}" in result.stdout
    assert f"Checked 4 downloaded files against the {TAG} SHA256SUMS" in result.stdout
    assert "docker compose -f docker-compose.release.yml -f docker-compose.images.yml" in result.stdout
    assert f"TESTLOOKUP_VERSION={TAG} " not in result.stdout, "release startup must use digests, not the tag"
    assert "untagged ref" not in result.stderr


@pytest.mark.parametrize("answer", [
    None,                                         # the lookup fails (offline, rate limited)
    '{"message": "API rate limit exceeded"}',     # an answer without a release
    '{"tag_name": "main"}',                       # an answer that is not a release tag
    '{"tag_name": "v1.2.3; rm -rf ~"}',
], ids=["lookup-fails", "no-tag", "branch-name", "not-a-version"])
def test_without_a_known_latest_release_nothing_is_installed(tmp_path, answer):
    """It stops; it does not fall back to main by itself."""
    env = _release(tmp_path, "main", sums=False)
    _latest(tmp_path, env, answer)
    result = _install(tmp_path, env)
    assert result.returncode != 0
    assert "could not find the latest TestLookup release" in result.stderr
    assert "TL_REF=main" in result.stderr, "the message must say how to install the tip on purpose"
    assert not (tmp_path / "install" / ".env").exists()
    assert not (tmp_path / "install" / "scripts").exists()


def test_an_image_tag_that_is_not_a_release_keeps_the_unverified_files_of_main(tmp_path):
    env = _release(tmp_path, "main", sums=False)
    env["TESTLOOKUP_VERSION"] = "0123abc"
    _latest(tmp_path, env, None)  # must not be asked: a failing lookup would stop the install
    result = _install(tmp_path, env)
    assert result.returncode == 0, result.stderr
    assert "no checksum is available for an untagged ref (main)" in result.stderr
    assert "TESTLOOKUP_VERSION=0123abc " in result.stdout


def test_the_installer_checks_the_files_the_release_publishes_checksums_for():
    """The names in install.sh and in the workflow's SHA256SUMS step are the same list."""
    workflow = yaml.safe_load((ROOT / ".github/workflows/offline-bundle.yml").read_text(encoding="utf-8"))
    steps = [step for job in workflow["jobs"].values() for step in job.get("steps", [])]
    sums_step = next(step["run"] for step in steps if "> dist/SHA256SUMS" in step.get("run", ""))
    for relative in FILES:
        assert relative in sums_step, f"the release does not publish a checksum for {relative}"
    script = INSTALLER.read_text(encoding="utf-8")
    assert 'FILES=("$COMPOSE_FILE" ".env.example" "scripts/gen-dev-env.sh" "scripts/init-db.sql")' in script
    assert 'REF="${TL_REF:-}"' in script and 'REF="$(latest_release_tag)"' in script, (
        "the default ref is the latest published release, never a branch"
    )
