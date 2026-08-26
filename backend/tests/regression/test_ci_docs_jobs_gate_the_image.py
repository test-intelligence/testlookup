"""The documentation checks must gate the image build.

They are cheap, and they guard something no test suite can see. ``mermaid-check``
asserts every diagram parses; ``docs-diagram-render`` asserts the diagrams
actually DRAW and that their labels are not clipped by their own node boxes —
a geometry question that every text assertion in this repository answered
wrongly, because ``textContent`` still holds the whole label when it is visually
cut off.

Both defects reached the deployment. An image that ships unreadable
documentation is a broken image, so ``build-images`` waits for them.

The second assertion is the durable one: it is not a list of two job ids, it is
"every documentation job gates the image". A third docs job added later has to
make that decision explicitly instead of quietly running alongside a build that
ignores it.
"""
from __future__ import annotations

import pathlib

import pytest
import yaml

REPO = pathlib.Path(__file__).resolve().parents[3]
WORKFLOW = REPO / ".github" / "workflows" / "ci.yml"

pytestmark = pytest.mark.skipif(
    not WORKFLOW.is_file(), reason="workflow not present in this checkout"
)


def _jobs() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]


def _docs_jobs(jobs: dict) -> dict:
    """Jobs whose display name marks them as documentation checks."""
    return {jid: spec for jid, spec in jobs.items() if str(spec.get("name", "")).startswith("Docs")}


def test_the_image_build_waits_for_every_documentation_job():
    jobs = _jobs()
    assert "build-images" in jobs, "the image build job was renamed — this guard is now blind"

    docs = _docs_jobs(jobs)
    assert len(docs) >= 2, (
        f"expected the documentation checks to be present; found {sorted(docs)} — "
        "if they were renamed this guard is measuring nothing"
    )

    needs = jobs["build-images"].get("needs") or []
    ungated = sorted(jid for jid in docs if jid not in needs)
    assert not ungated, (
        "these documentation jobs do not gate the image build, so a broken diagram "
        f"can still ship: {ungated}"
    )


def test_the_two_known_documentation_checks_are_the_ones_gating():
    """Named explicitly, so a rename that also drops the gate cannot pass by
    making `_docs_jobs` return an empty set."""
    needs = _jobs()["build-images"].get("needs") or []
    for job in ("mermaid-check", "docs-diagram-render"):
        assert job in needs, f"{job} must gate build-images"


def test_every_dependency_names_a_real_job():
    """A typo in `needs` is not a weaker gate — GitHub refuses to run the
    workflow at all, which looks like an infrastructure failure rather than an
    edit mistake."""
    jobs = _jobs()
    dangling = {
        jid: [n for n in (spec.get("needs") or []) if n not in jobs]
        for jid, spec in jobs.items()
        if [n for n in (spec.get("needs") or []) if n not in jobs]
    }
    assert not dangling, f"`needs` entries that name no job: {dangling}"
