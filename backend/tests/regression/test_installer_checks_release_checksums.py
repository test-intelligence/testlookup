"""Installing a release checks the downloaded files against that release's SHA256SUMS.

``install.sh`` downloaded the Compose file, the env template and two scripts
and ran one of them with no integrity check, although every release publishes
``SHA256SUMS`` for exactly those files (the "Installer checksums" step of
``.github/workflows/offline-bundle.yml``, attached to the GitHub release).

The rule: when the ref being installed is a release tag (``vX.Y.Z`` in
``TL_REF`` or ``TESTLOOKUP_VERSION``), the installer downloads that release's
``SHA256SUMS`` and compares every file it fetched BEFORE it writes into the
install directory or runs anything it downloaded. A mismatch, a file the sums
do not list, or a missing sums file stops the install. For an untagged ref
(the default, ``main``) there is nothing to compare with, and it says so.

No network: the fake release is a directory read through ``file://`` URLs
(``TL_RAW_BASE`` / ``TL_RELEASE_BASE``), and ``docker`` is a stub on ``PATH``.
"""
from __future__ import annotations

import hashlib
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
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    (stubs / "docker").write_bytes(b"#!/usr/bin/env bash\nexit 0\n")
    (stubs / "docker").chmod(0o755)
    env = bash_environment(_BASH)
    env["PATH"] = str(stubs) + os.pathsep + env.get("PATH", "")
    env.update(
        TL_RAW_BASE=(tmp_path / "served" / "raw").as_uri(),
        TL_RELEASE_BASE=(tmp_path / "served" / "releases").as_uri(),
        TL_DIR="install",
        TL_NO_UP="1",
    )
    for name in ("TL_REF", "TESTLOOKUP_VERSION", "TL_PROFILE"):
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
    assert f"TESTLOOKUP_VERSION={TAG} " in result.stdout, "the start command must pin the release's images"


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


def test_the_default_ref_installs_and_says_it_is_not_verified(tmp_path):
    env = _release(tmp_path, "main", sums=False)
    result = _install(tmp_path, env)
    assert result.returncode == 0, result.stderr
    assert "no checksum is available for an untagged ref (main)" in result.stderr
    assert (tmp_path / "install" / ".env").exists()
    assert "TESTLOOKUP_VERSION=latest " in result.stdout


def test_the_installer_checks_the_files_the_release_publishes_checksums_for():
    """The names in install.sh and in the workflow's SHA256SUMS step are the same list."""
    workflow = yaml.safe_load((ROOT / ".github/workflows/offline-bundle.yml").read_text(encoding="utf-8"))
    steps = [step for job in workflow["jobs"].values() for step in job.get("steps", [])]
    sums_step = next(step["run"] for step in steps if "> dist/SHA256SUMS" in step.get("run", ""))
    for relative in FILES:
        assert relative in sums_step, f"the release does not publish a checksum for {relative}"
    script = INSTALLER.read_text(encoding="utf-8")
    assert 'FILES=("$COMPOSE_FILE" ".env.example" "scripts/gen-dev-env.sh" "scripts/init-db.sql")' in script
    assert 'REF="${TL_REF:-main}"' in script, "main stays the default ref"
