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

# The prose moved out of DocsPage.tsx into Markdown content files; the page
# component is now the shell and holds no table to read.
DOCS_PAGE = (
    pathlib.Path(__file__).resolve().parents[3]
    / "frontend" / "src" / "content" / "guide" / "ai-agents.md"
)


def _documented_stages() -> list[str]:
    """Stage names from the first column of the Markdown stage table."""
    src = DOCS_PAGE.read_text(encoding="utf-8")
    head = src.index("| Stage | What it does | Permission |")
    table = src[head:]
    blank = chr(10) + chr(10)
    table = table[: table.index(blank, table.index(chr(10)))]
    return re.findall(r"^\|\s*`([a-z_]+)`\s*\|", table, re.MULTILINE)


@pytest.mark.skipif(not DOCS_PAGE.exists(), reason="frontend tree not present")
def test_the_guard_can_read_the_docs_table():
    """Fail-open check: an empty parse must not read as agreement."""
    stages = _documented_stages()
    assert len(stages) >= 4, (
        f"parsed too few stages from the docs table: {stages}. The table's "
        "shape probably changed and the comparison below would be vacuous."
    )


@pytest.mark.skipif(not DOCS_PAGE.exists(), reason="frontend tree not present")
def test_the_documented_table_is_exactly_the_deep_pipeline():
    """The page states it documents the DEEP pipeline, so it must match that
    list exactly, in order.

    The original defect was a table matching NEITHER stage list, so a reader
    counted a different number of stages than the product ran. WHICH list the
    table describes is a choice; describing one that does not exist is not.
    """
    from app.agents.workflow import _DEEP_PIPELINE_STAGES

    documented = _documented_stages()
    assert documented == list(_DEEP_PIPELINE_STAGES), (
        "the /docs pipeline table no longer matches _DEEP_PIPELINE_STAGES. "
        f"documented={documented} actual={list(_DEEP_PIPELINE_STAGES)}"
    )


@pytest.mark.skipif(not DOCS_PAGE.exists(), reason="frontend tree not present")
def test_the_page_says_which_pipeline_the_table_describes():
    """A complete table, mislabelled, is the same defect as an incomplete one."""
    src = DOCS_PAGE.read_text(encoding="utf-8").lower()
    assert "deep" in src, "the page never says which workflow it documents"
    assert "standard" in src and "five stages" in src, (
        "the page must tell the reader a standard run is shorter than this table"
    )


@pytest.mark.skipif(not DOCS_PAGE.exists(), reason="frontend tree not present")
def test_every_deep_only_stage_is_documented():
    """The inverse of the original defect.

    ``failure_clustering`` was the stage that was wrong: documented as standard
    when a standard run never executes it. Now that the table IS the deep list,
    the risk flips -- a deep-only stage MISSING leaves a reader unable to
    account for a stage they can see on the Intelligence page.
    """
    from app.agents.workflow import _DEEP_PIPELINE_STAGES, _PIPELINE_STAGES

    deep_only = set(_DEEP_PIPELINE_STAGES) - set(_PIPELINE_STAGES)
    assert deep_only, "no deep-only stages found -- the guard is checking nothing"
    missing = sorted(deep_only - set(_documented_stages()))
    assert not missing, f"deep-only stages absent from the documented table: {missing}"


def test_the_two_stage_lists_are_still_distinct():
    """If the lists ever merge, the distinction the docs draw stops being real
    and this guard's premise needs revisiting rather than silently passing."""
    from app.agents.workflow import _DEEP_PIPELINE_STAGES, _PIPELINE_STAGES

    assert "failure_clustering" not in _PIPELINE_STAGES
    assert "failure_clustering" in _DEEP_PIPELINE_STAGES
