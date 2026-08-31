"""``deploy-homelab.sh --skip-build`` must resolve a tag instead of aborting.

The flag exists for exactly one situation: a deploy whose images built and
pushed fine but whose apply phase died partway, so you want to resume without
paying for a 15-minute rebuild. It never worked.

``_existing_common_tag`` intersected the three registry tag lists with::

    comm -12 <(... | sort -ru) <(comm -12 <(... | sort -ru) <(... | sort -ru))

``comm`` requires **ascending** input. Given descending input it prints
"input is not in sorted order" on stderr and **exits 1** — while still writing
the correct answer to stdout. Measured against the live registry (97 backend /
128 frontend / 98 mcp tags)::

    comm OWN exit = 1
    stdout lines  = 21, first = build-20260807-231731     <-- the right answer

Under the script's ``set -euo pipefail`` that exit status propagated out of the
``BUILD_TAG=$(_existing_common_tag)`` command substitution and killed the run.
Two consequences, both confirmed by emulating the script:

  1. ``--skip-build`` aborted every time, on a correct answer.
  2. The author's own "no build-YYYYMMDD-HHMMSS tag exists for ALL THREE
     images" diagnostic was **unreachable** — the script died before evaluating
     ``[ -z "$BUILD_TAG" ]``, so an operator saw only ``comm: file 1 is not in
     sorted order``.

That is why this is worth a test rather than a one-line edit: the failure mode
is "correct result, non-zero status", which reads as a data problem and sends
you looking at the registry.

The selection step is now ``_newest_common_tag``, taking the three lists as
arguments so it can be exercised with no registry at all. These tests run it
under the same ``set -euo pipefail`` the script uses — the status is the point,
not just the value.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

from tests.shell_utils import bash_environment

REPO_ROOT = Path(__file__).resolve().parents[3]
DEPLOY_SCRIPT = REPO_ROOT / "homelabsetup" / "deploy-homelab.sh"


def _find_bash() -> str | None:
    """Locate a POSIX bash, avoiding the Windows WSL launcher.

    On Windows ``shutil.which("bash")`` finds ``C:\\Windows\\System32\\bash.exe``
    — the WSL launcher, not a shell. Where WSL isn't provisioned it fails with
    ``HCS_E_CONNECTION_TIMEOUT`` and writes the error in UTF-16, which looks
    like corrupted output rather than a missing interpreter. Prefer Git for
    Windows' bash, which is what actually runs the deploy script here.
    """
    candidates = [
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files\Git\usr\bin\bash.exe",
    ]
    for candidate in candidates:
        if Path(candidate).is_file():
            return candidate
    found = shutil.which("bash")
    if found and Path(found).name.lower() == "bash.exe" and "system32" in found.lower():
        return None  # WSL launcher — not a usable shell here
    return found


BASH = _find_bash()

pytestmark = pytest.mark.skipif(
    BASH is None or not DEPLOY_SCRIPT.is_file(),
    reason="needs a POSIX bash and homelabsetup/deploy-homelab.sh",
)


def _extract_function(name: str) -> str:
    """Pull one function's source out of the deploy script.

    Sourcing the whole script would execute a deploy, so the function is
    lifted by text. It ends at the first line that is exactly its closing
    brace at the definition's indentation.
    """
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    # ``[ \t]*`` not ``\s*``: ``\s`` matches newlines, so with re.M the group
    # greedily absorbed the preceding line break and the computed indent was
    # "\n  " — which never equals a line, so the closing brace was never found.
    match = re.search(rf"^([ \t]*){re.escape(name)}\(\) \{{$", text, re.M)
    assert match, f"{name}() not found in {DEPLOY_SCRIPT.name} — was it renamed?"

    indent = match.group(1)
    lines = text[match.start():].splitlines()
    for index, line in enumerate(lines[1:], start=1):
        if line == f"{indent}}}":
            return "\n".join(lines[: index + 1])
    raise AssertionError(f"no closing brace found for {name}()")


def _run(backend: str, frontend: str, mcp: str, tmp_path: Path) -> subprocess.CompletedProcess:
    """Run ``_newest_common_tag`` under the script's own shell options.

    The tag lists go in via heredocs in a temp script rather than through
    argv, so multi-line values can't be reinterpreted by any shell in the
    chain. ``stdin`` is closed because nothing here should ever read it — a
    hang would otherwise look like a slow test.
    """
    runner = tmp_path / "run_newest_common_tag.sh"
    runner.write_text(
        "set -euo pipefail\n"
        f"{textwrap.dedent(_extract_function('_newest_common_tag'))}\n"
        f"backend=$(cat <<'__TL_BACKEND__'\n{backend}\n__TL_BACKEND__\n)\n"
        f"frontend=$(cat <<'__TL_FRONTEND__'\n{frontend}\n__TL_FRONTEND__\n)\n"
        f"mcp=$(cat <<'__TL_MCP__'\n{mcp}\n__TL_MCP__\n)\n"
        '_newest_common_tag "$backend" "$frontend" "$mcp"\n',
        encoding="utf-8",
        newline="\n",
    )
    assert BASH is not None  # guarded by pytestmark
    return subprocess.run(
        [BASH, str(runner)],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        env=bash_environment(BASH),
        timeout=60,
    )


TAGS = [
    "build-20260807-231731",
    "build-20260807-221334",
    "build-20260807-215829",
    "build-20260806-101500",
]


def _joined(*tags: str) -> str:
    return "\n".join(tags)


# ── Fixture choice is load-bearing ──────────────────────────────────────────
#
# ``comm`` reports disorder only when the merge actually has to advance one
# side past the other. Three IDENTICAL lists walk in lockstep, so the broken
# implementation exits 0 on them and a test built from identical lists cannot
# fail. Measured against the pre-fix code:
#
#     identical x3 (4 tags)   rc=0   <-- bug invisible
#     one list shorter        rc=1   comm: file 2 is not in sorted order
#     two lists differ        rc=1   comm: file 1 is not in sorted order
#     40/55/47 generated      rc=1   comm: file 2 is not in sorted order
#
# The real registry had 97 / 128 / 98 tags, so it always differed. Every test
# below therefore uses lists that DIFFER — otherwise it proves nothing.
_BACKEND = _joined(*TAGS)
_FRONTEND = _joined(*TAGS[:3])          # one build not yet pushed here
_MCP = _joined(TAGS[0], TAGS[2], TAGS[3])  # a different gap again


class TestItSurvivesItsOwnShellOptions:
    def test_exits_zero(self, tmp_path):
        """The actual bug: right answer, non-zero status, dead script."""
        result = _run(_BACKEND, _FRONTEND, _MCP, tmp_path)
        assert result.returncode == 0, (
            "non-zero exit kills BUILD_TAG=$(...) under set -euo pipefail, so "
            f"--skip-build aborts. stderr:\n{result.stderr}"
        )

    def test_emits_no_sorted_order_warning(self, tmp_path):
        result = _run(_BACKEND, _FRONTEND, _MCP, tmp_path)
        assert "not in sorted order" not in result.stderr, (
            "comm is still being fed descending input; the exit status that "
            "breaks the script comes with this warning"
        )

    def test_survives_catalogs_the_size_of_the_real_registry(self, tmp_path):
        """97 / 128 / 98 tags is what the live registry actually held."""
        base = [f"build-2026080{d}-{h:02d}0000" for d in range(1, 8) for h in range(24)]
        result = _run(
            _joined(*base[:97]), _joined(*base[:128]), _joined(*base[:98]), tmp_path
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == base[96]


class TestItPicksTheRightTag:
    def test_newest_tag_common_to_all_three(self, tmp_path):
        result = _run(_BACKEND, _FRONTEND, _MCP, tmp_path)
        assert result.stdout.strip() == "build-20260807-231731"

    def test_skips_a_tag_missing_from_one_image(self, tmp_path):
        """A partial push must not be selected — that is ImagePullBackOff."""
        newest, second, *rest = TAGS
        result = _run(
            _joined(newest, second, *rest),
            _joined(newest, second, *rest),
            _joined(second, *rest),  # mcp never got the newest tag
            tmp_path,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == second

    def test_ordering_of_the_input_does_not_matter(self, tmp_path):
        """Registry catalogs are not required to return tags in any order."""
        result = _run(
            _joined(*TAGS),
            _joined(*reversed(TAGS)),
            _joined(TAGS[2], TAGS[0], TAGS[3], TAGS[1]),
            tmp_path,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "build-20260807-231731"


class TestTheNoCommonTagPathIsReachable:
    """The script's helpful diagnostic depends on getting an empty string
    back rather than a dead shell."""

    def test_returns_empty_when_nothing_is_shared(self, tmp_path):
        result = _run(_joined("build-20260807-231731"), _joined("build-20260806-101500"), _joined("build-20260805-090000"), tmp_path)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == ""

    def test_returns_empty_when_a_catalog_is_empty(self, tmp_path):
        result = _run(_joined(*TAGS), "", _joined(*TAGS), tmp_path)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == ""


def test_the_caller_still_uses_this_helper():
    """Anchors the tests to the code path that actually runs."""
    assert "_newest_common_tag" in _extract_function("_existing_common_tag"), (
        "_existing_common_tag no longer delegates to _newest_common_tag, so "
        "these tests are exercising dead code"
    )
