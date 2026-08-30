"""A link in a shipped doc must point at a file that is actually in the repo.

`docs/` and `ROADMAP.md` are gitignored **on purpose** -- they are local working
notes, not shipped documentation. But the shipped docs linked into them anyway,
and the failure is invisible on the machine that wrote it: the files exist
locally, so every link resolves for the author and 404s for everyone who clones.

Eight distinct links were dead on GitHub:

* ``ROADMAP.md`` -- linked from ``README.md``, ``CONTRIBUTING.md``,
  ``GETTING_STARTED.md`` and ``ARCHITECTURE.md``. ``CONTRIBUTING.md`` opened
  with *"step 1: Read the roadmap"*, so a new contributor's very first
  instruction was a 404. This one could never self-heal: the file is ignored at
  ``.gitignore:125``, so writing it does not fix the link.
* ``docs/features/FEATURE_FLAG_INVENTORY.md``, ``docs/deployment/README.md``,
  ``docs/cloud-run-cloud-sql.md``, ``docs/JENKINS_PIPELINE.md``,
  ``docs/MULTI_CLOUD_DEPLOYMENT_STRATEGY.md``,
  ``docs/TESTLOOKUP_USER_GUIDE_TIER_0_2.md``
* ``CLAUDE.md`` -- an agent-instruction file, gitignored, linked from
  ``README.md``, ``README_FULL.md`` and ``ARCHITECTURE.md``

This test asks git what is tracked rather than the filesystem what exists,
because the filesystem is exactly what made the bug invisible.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]

#: The docs a person actually lands on -- the ones whose links must resolve for
#: someone who has only ever cloned the repo.
SHIPPED_DOCS = [
    "README.md",
    "README_FULL.md",
    "GETTING_STARTED.md",
    "CONTRIBUTING.md",
    "ARCHITECTURE.md",
    "installation.md",
    "deploymentsteps.md",
    "SECURITY.md",
    "CODE_OF_CONDUCT.md",
    "THREAT_MODEL.md",
]

#: ``[text](target)`` -- markdown inline links.
_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")


def _tracked_paths() -> set[str]:
    out = subprocess.run(
        ["git", "ls-files"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    ).stdout
    return {line.strip() for line in out.splitlines() if line.strip()}


def _present_docs() -> list[str]:
    return [d for d in SHIPPED_DOCS if (REPO_ROOT / d).is_file()]


def _relative_link_targets(doc: str) -> list[tuple[str, int]]:
    """Repo-relative link targets in ``doc``, with line numbers."""
    targets: list[tuple[str, int]] = []
    text = (REPO_ROOT / doc).read_text(encoding="utf-8", errors="ignore")
    for i, line in enumerate(text.splitlines(), 1):
        for raw in _LINK_RE.findall(line):
            target = raw.split()[0].split("#")[0].strip()
            if not target:
                continue
            if target.startswith(("http://", "https://", "mailto:", "#", "<")):
                continue
            # NB: not ``lstrip("./")`` -- that strips a character *set* and
            # would turn ``.env.example`` into ``env.example``.
            if target.startswith("./"):
                target = target[2:]
            targets.append((target, i))
    return targets


def test_the_scan_reads_real_docs_and_finds_real_links():
    """A glob that matched nothing would make this suite pass forever."""
    docs = _present_docs()
    assert len(docs) >= 6, f"expected the shipped docs on disk, found {docs}"
    total = sum(len(_relative_link_targets(d)) for d in docs)
    assert total >= 20, f"expected many relative links, found {total}"
    assert len(_tracked_paths()) > 100, "git ls-files returned implausibly little"


@pytest.mark.parametrize("doc", _present_docs())
def test_every_relative_link_points_at_a_tracked_file(doc: str):
    tracked = _tracked_paths()
    # A link to a directory resolves on GitHub if anything under it is tracked.
    tracked_dirs = {str(Path(p).parent).replace("\\", "/") for p in tracked}
    tracked_dirs |= {
        "/".join(p.split("/")[:i])
        for p in tracked
        for i in range(1, len(p.split("/")))
    }

    offenders: list[str] = []
    for target, line_no in _relative_link_targets(doc):
        normalised = target.rstrip("/")
        if normalised in tracked or normalised in tracked_dirs:
            continue
        exists_locally = (REPO_ROOT / normalised).exists()
        why = (
            "exists locally but is NOT tracked -- 404 for everyone who clones"
            if exists_locally
            else "does not exist"
        )
        offenders.append(f"{doc}:{line_no} -> {target} ({why})")

    assert not offenders, (
        "a shipped doc links to something that is not in the repository:\n  "
        + "\n  ".join(offenders)
    )
