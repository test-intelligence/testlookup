"""Self-test for scripts/clean_scratch.sh.

The script's job is deletion, so the properties worth pinning are the ones that
stop it deleting the wrong thing:

  * it removes every ``--basetemp`` spelling the repo actually uses, including
    the HYPHENATED one that ``.gitignore`` missed until 2026-09-20;
  * it never removes a path that holds a git-tracked file, whatever the
    directory is called;
  * it never descends into a dependency or build tree (node_modules, venvs,
    .uv-cache, dist/build/target) — those are expensive to rebuild and are not
    scratch;
  * the repo-root ``.tmp`` (run evidence) survives while a component's own
    ``backend/.tmp`` (pytest basetemp output) is cleaned.

Every case runs against a throwaway git repo in ``tmp_path``, never the real
checkout — a test that cleaned its own working tree would be both flaky and
rude.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent / "clean_scratch.sh"


def _find_bash() -> str | None:
    """A bash that actually runs, not merely one that is on PATH.

    On Windows ``shutil.which("bash")`` finds C:\\Windows\\System32\\bash.exe —
    the WSL launcher, which fails with
    ``execvpe(/bin/bash) failed: No such file or directory`` when no distro is
    installed. Git for Windows ships the real thing further down PATH. Probing
    by execution rather than by existence is what tells them apart; on Linux CI
    the first candidate answers immediately.
    """
    candidates = [
        shutil.which("bash"),
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files (x86)\Git\bin\bash.exe",
    ]
    for candidate in candidates:
        if not candidate or not Path(candidate).exists():
            continue
        try:
            probe = subprocess.run(
                [candidate, "-c", "echo ok"],
                capture_output=True, text=True, timeout=30, check=False,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if probe.returncode == 0 and probe.stdout.strip() == "ok":
            return candidate
    return None


BASH = _find_bash()

pytestmark = pytest.mark.skipif(BASH is None, reason="clean_scratch.sh needs bash")


def _run(repo: Path, *, dry_run: bool = False) -> subprocess.CompletedProcess[str]:
    assert BASH is not None
    env = {"DRY_RUN": "1"} if dry_run else {}
    return subprocess.run(
        [BASH, str(SCRIPT)],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
        env={**_base_env(), **env},
    )


def _base_env() -> dict[str, str]:
    import os

    # Keep PATH (bash, git, mktemp) but drop an inherited DRY_RUN.
    env = dict(os.environ)
    env.pop("DRY_RUN", None)
    return env


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@e.st"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    # A tracked file so the repo has an index worth querying.
    (repo / "README.md").write_text("x\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)
    return repo


def _touch(repo: Path, rel: str) -> Path:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x\n", encoding="utf-8")
    return path


# Exactly the spellings tracked callers pass: scripts/mutation_check_e2_1.py
# uses `backend/.pytest-tmp-e21-mutation`, EXPLORATORY_RUNBOOK.md uses
# `.pytest-tmp-exploratory-*`, backend/CLAUDE.md uses `.pytest_tmp`, and
# `.pytest_cache_t0` is the spelling that broke the mcp image build.
@pytest.mark.parametrize(
    "scratch",
    [
        ".pytest_tmp/x.txt",
        ".pytest_tmp_ui003_full/x.txt",
        ".pytest-tmp-exploratory-cli/x.txt",
        "backend/.pytest-tmp-e21-mutation/x.txt",
        ".pytest_cache_t0/x.txt",
        ".pytest-cache-m10/x.txt",
        "backend/.pytest_cache/x.txt",
        "backend/app/__pycache__/m.pyc",
        "backend/.mypy_cache/x.txt",
        "backend/.ruff_cache/x.txt",
        "backend/.hypothesis/x.txt",
        "backend/htmlcov/index.html",
        "backend/.coverage",
        "backend/coverage.json",
        "mcp/coverage.xml",
        "backend/celerybeat-schedule",
    ],
)
def test_removes_every_scratch_spelling(tmp_path: Path, scratch: str) -> None:
    repo = _make_repo(tmp_path)
    target = _touch(repo, scratch)

    result = _run(repo)

    assert result.returncode == 0, result.stdout + result.stderr
    assert not target.exists(), f"{scratch} survived: {result.stdout}"


def test_never_removes_a_path_holding_a_tracked_file(tmp_path: Path) -> None:
    """The name says scratch; the index says otherwise. The index wins."""
    repo = _make_repo(tmp_path)
    tracked = _touch(repo, "backend/.pytest_tmp_fixture/keep.py")
    subprocess.run(
        ["git", "add", "-f", "backend/.pytest_tmp_fixture/keep.py"],
        cwd=repo, check=True,
    )

    result = _run(repo)

    assert result.returncode == 0, result.stdout + result.stderr
    assert tracked.exists(), "a tracked file was deleted"
    assert "skip (tracked)" in result.stdout


def test_leaves_dependency_and_build_trees_alone(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    survivors = [
        _touch(repo, "frontend/node_modules/pkg/__pycache__/m.pyc"),
        _touch(repo, "venv/lib/__pycache__/m.pyc"),
        _touch(repo, ".venv311/lib/__pycache__/m.pyc"),
        _touch(repo, "backend/.uv-cache/x/__pycache__/m.pyc"),
        _touch(repo, "frontend/dist/__pycache__/m.pyc"),
        _touch(repo, "client/java/target/__pycache__/m.pyc"),
        _touch(repo, "client/reporter.egg-info/__pycache__/m.pyc"),
    ]

    result = _run(repo)

    assert result.returncode == 0, result.stdout + result.stderr
    for path in survivors:
        assert path.exists(), f"{path} was removed but is not scratch"


def test_root_tmp_is_evidence_but_a_component_tmp_is_scratch(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    evidence = _touch(repo, ".tmp/h06-evidence/report.md")
    component_scratch = _touch(repo, "backend/.tmp/pytest-run-1/x.txt")

    result = _run(repo)

    assert result.returncode == 0, result.stdout + result.stderr
    assert evidence.exists(), "repo-root .tmp holds run evidence and must survive"
    assert not component_scratch.exists(), "backend/.tmp is basetemp output"


def test_dry_run_reports_but_removes_nothing(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    scratch = _touch(repo, "backend/.pytest-tmp-e22/x.txt")

    result = _run(repo, dry_run=True)

    assert result.returncode == 0, result.stdout + result.stderr
    assert scratch.exists(), "DRY_RUN deleted something"
    assert "would remove: backend/.pytest-tmp-e22" in result.stdout
