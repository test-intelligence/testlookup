"""Regression guard: the /docs pipeline table must match the real stage list.

The defect
----------
``DocsPage`` documented a six-stage pipeline under "How the AI agents work":

    ingestion, anomaly_detection, root_cause_analysis,
    failure_clustering, summary, triage

The code has two lists, and that matched **neither**:

* ``_PIPELINE_STAGES``      — 5 stages, **no** ``failure_clustering``
* ``_DEEP_PIPELINE_STAGES`` — 19 stages, including ``failure_clustering``
  and thirteen more the page never mentioned

So the page described a pipeline that never runs. Confirmed on the live
deployment: across **901** recorded pipeline runs, ``agent_stage_results``
contained ``ingestion``, ``anomaly_detection``, ``root_cause_analysis``,
``summary`` and ``triage`` — 901 of each — and ``failure_clustering``
**zero times**.

A reader following the docs and then watching a run's Intelligence page would
count five stages against a documented six, with no way to tell which was
wrong.

Why this guard lives in the backend suite
-----------------------------------------
The stage list is backend truth (``agents/workflow.py``); the page is
frontend prose. Something has to compare them, and the side that owns the
truth is the side that should fail when they diverge.
"""
from __future__ import annotations

import pathlib
import re

import pytest

pytestmark = pytest.mark.regression

DOCS_PAGE = (
    pathlib.Path(__file__).resolve().parents[3]
    / "frontend" / "src" / "pages" / "DocsPage.tsx"
)


def _documented_stages() -> list[str]:
    """Stage names from the ``['stage', 'description']`` table rows."""
    src = DOCS_PAGE.read_text(encoding="utf-8")
    head = src.index("head={['Stage', 'What the agent does']}")
    table = src[head : head + 1200]
    return re.findall(r"\['([a-z_]+)', '", table)


@pytest.mark.skipif(not DOCS_PAGE.exists(), reason="frontend tree not present")
def test_the_guard_can_read_the_docs_table():
    """Fail-open check: an empty parse must not read as agreement."""
    stages = _documented_stages()
    assert len(stages) >= 4, (
        f"parsed too few stages from the docs table: {stages}. The table's "
        "shape probably changed and the comparison below would be vacuous."
    )


@pytest.mark.skipif(not DOCS_PAGE.exists(), reason="frontend tree not present")
def test_the_documented_table_is_exactly_the_standard_pipeline():
    from app.agents.workflow import _PIPELINE_STAGES

    documented = _documented_stages()
    assert documented == list(_PIPELINE_STAGES), (
        "the /docs pipeline table no longer matches _PIPELINE_STAGES.\n"
        f"  documented: {documented}\n"
        f"  actual    : {list(_PIPELINE_STAGES)}\n"
        "The table describes the STANDARD pipeline; deep-only stages belong in "
        "the prose beneath it, not in the table, or readers count a different "
        "number of stages than the product runs."
    )


@pytest.mark.skipif(not DOCS_PAGE.exists(), reason="frontend tree not present")
def test_deep_only_stages_are_not_presented_as_standard():
    """``failure_clustering`` is the one that was wrong; pin the whole class.

    Any stage that exists only in the deep chain must stay out of the table.
    """
    from app.agents.workflow import _DEEP_PIPELINE_STAGES, _PIPELINE_STAGES

    deep_only = set(_DEEP_PIPELINE_STAGES) - set(_PIPELINE_STAGES)
    assert deep_only, "no deep-only stages found — the guard is checking nothing"
    leaked = sorted(deep_only & set(_documented_stages()))
    assert not leaked, (
        f"deep-only stages are listed as standard pipeline stages: {leaked}. "
        "A standard run never executes them, so the documented count will not "
        "match what the Intelligence page shows."
    )


def test_the_two_stage_lists_are_still_distinct():
    """If the lists ever merge, the distinction the docs draw stops being real
    and this guard's premise needs revisiting rather than silently passing."""
    from app.agents.workflow import _DEEP_PIPELINE_STAGES, _PIPELINE_STAGES

    assert "failure_clustering" not in _PIPELINE_STAGES
    assert "failure_clustering" in _DEEP_PIPELINE_STAGES
