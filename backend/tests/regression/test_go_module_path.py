"""The Go reporter's module path must be where the code lives.

``client/go/go.mod`` declared ``module github.com/testlookup/testlookup-go``.
Nothing served that path, so ``go get`` could not fetch the module, and the
``github.com/testlookup`` namespace was unclaimed: anyone could register it and
serve their own code under the import path the README documented (OSS review
2026-11-12, H-5). The module is now
``github.com/test-intelligence/testlookup/client/go``, which ``go get`` resolves
from this repository.

These tests pin the module path to the repository and directory, and refuse the
unclaimed namespace anywhere but the two upgrade notes that explain the rename.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.regression

ROOT = Path(__file__).resolve().parents[3]
REPOSITORY = "github.com/test-intelligence/testlookup"
GO_DIR = "client/go"
MODULE = f"{REPOSITORY}/{GO_DIR}"
UNCLAIMED = "github.com/testlookup/"
# The rename notes name the old path so users can find it in their go.mod, and
# this file names it to refuse it.
MAY_NAME_THE_OLD_PATH = {"CHANGELOG.md", "UPGRADING.md", Path(__file__).resolve().relative_to(ROOT).as_posix()}


def _tracked() -> list[str]:
    proc = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=False)
    if proc.returncode != 0:  # pragma: no cover - not a git checkout
        pytest.skip("git unavailable")
    return [rel for rel in proc.stdout.decode("utf-8").split("\x00") if rel]


def _module(go_mod: Path) -> str:
    match = re.search(r"^module\s+(\S+)", go_mod.read_text(encoding="utf-8"), re.M)
    assert match, f"{go_mod} declares no module"
    return match.group(1)


def test_the_module_path_is_this_repository_and_directory():
    assert _module(ROOT / GO_DIR / "go.mod") == MODULE


def test_every_go_mod_that_uses_the_reporter_names_its_real_path():
    users = [rel for rel in _tracked()
             if rel.endswith("go.mod") and rel != f"{GO_DIR}/go.mod"
             and "testlookup" in (ROOT / rel).read_text(encoding="utf-8")]
    assert users, "expected a go.mod that uses the reporter (e2e-runner/reporters/go)"
    wrong = [rel for rel in users if f"require {MODULE} " not in (ROOT / rel).read_text(encoding="utf-8")]
    assert not wrong, f"these go.mod files do not require {MODULE}: {wrong}"


def test_no_tracked_file_points_at_the_unclaimed_namespace():
    offenders = []
    for rel in _tracked():
        if rel in MAY_NAME_THE_OLD_PATH:
            continue
        try:
            text = (ROOT / rel).read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue  # binary, or listed but absent (a sparse or partial checkout)
        if UNCLAIMED in text:
            offenders.append(rel)
    assert not offenders, f"{UNCLAIMED} is an unclaimed GitHub namespace; use {MODULE}: " + ", ".join(offenders)
