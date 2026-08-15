"""The AI-degradation flag must report what actually happened.

Found on the homelab immediately after the deterministic summary fallback was
made reachable. The very first run to use it stored:

```
summary_provenance.fallback_used = True      <- the truth
fallback_used                    = False     <- what the API reports
executive_summary = "...This summary was generated from stored pipeline
                     evidence because the configured LLM was unavailable."
```

Cause, in ``SummaryAgent._store_summary``:

```python
fallback_used = structured.get("_fallback_used", False)
```

``_fallback_used`` is written **nowhere**. Grepping ``backend/app`` and
``backend/tests`` returned that single line as the only occurrence in the
codebase. The value ``run()`` actually records is
``structured["_provenance"]["fallback_used"]``. So the expression was a
constant ``False`` dressed up as a lookup — the wrong-name/dead-branch class,
the same shape as ``PerformanceBaseline``/``PerfBaseline`` and
``oc_namespace``/``ocp_namespace``.

Across all 57 stored summaries at the time: ``fallback_used == True`` appeared
**zero** times, while ``summary_provenance.fallback_used == True`` appeared
once — the single summary generated since the fallback became reachable.

This is not a cosmetic boolean. ``fallback_used`` is the flag a consumer checks
to learn that AI output is degraded; it propagates to ``provenance.fallback_used``
on the run-intelligence contract and to the ``run_intelligence_snapshots.
fallback_used`` column. A summary that says in prose that the LLM was
unavailable while reporting itself as not-degraded is exactly the failure the
AI-trust work exists to prevent.

The guards are the CLASS: **a flag reported to consumers is read from where it
is written**, and **no summary may claim to be undegraded while saying it was**.
"""
from __future__ import annotations

import pathlib
import re
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.summary_agent import SummaryAgent

BACKEND = pathlib.Path(__file__).resolve().parents[2]

DEGRADED_TEXT = (
    "Build 12 on branch main completed with 4 failing tests out of 20 and a 80.0% "
    "pass rate. This summary was generated from stored pipeline evidence because "
    "the configured LLM was unavailable."
)


def _structured(fallback: bool, layer1: str = "All good."):
    return {
        "layer1_executive_summary": layer1,
        "layer2_incident_view": {"what_failed": "4 tests"},
        "layer3_evidence_pack": {"citations": [], "data_sources_used": ["stack_trace"]},
        "layer4_action_plan": {"immediate_mitigation": "look at it"},
        "executive_panel": {},
        "_provenance": {"fallback_used": fallback, "context_sha256": "abc"},
    }


async def _store(structured):
    """Run _store_summary against a captured Mongo, return the stored document."""
    captured = {}

    async def _update_one(_filter, update, **_kw):
        captured.update(update["$set"])
        return MagicMock()

    collection = MagicMock()
    collection.update_one = AsyncMock(side_effect=_update_one)
    mongo = MagicMock()
    mongo.__getitem__ = MagicMock(return_value=collection)

    with patch("app.agents.summary_agent.get_mongo_db", MagicMock(return_value=mongo)):
        await SummaryAgent()._store_summary("run-1", structured, {"project_id": "p"})
    return captured


# ── The regression ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_degraded_summary_is_stored_as_degraded():
    """The bug: this stored False."""
    doc = await _store(_structured(fallback=True, layer1=DEGRADED_TEXT))
    assert doc["fallback_used"] is True


@pytest.mark.asyncio
async def test_a_normal_summary_is_not_flagged_degraded():
    """The other direction — the fix must not simply hardcode True."""
    doc = await _store(_structured(fallback=False))
    assert doc["fallback_used"] is False


@pytest.mark.asyncio
async def test_the_stored_flag_agrees_with_the_provenance_it_ships_beside():
    """Both live in the same document. They disagreeing is the whole defect."""
    for fallback in (True, False):
        doc = await _store(_structured(fallback=fallback))
        assert doc["fallback_used"] == doc["summary_provenance"]["fallback_used"], (
            f"stored flag {doc['fallback_used']} contradicts provenance "
            f"{doc['summary_provenance']['fallback_used']}"
        )


@pytest.mark.asyncio
async def test_a_summary_cannot_say_it_was_degraded_and_report_otherwise():
    """The user-facing honesty invariant, stated independently of which key
    the implementation happens to read."""
    doc = await _store(_structured(fallback=True, layer1=DEGRADED_TEXT))
    says_degraded = "llm was unavailable" in str(doc["executive_summary"]).lower()
    assert says_degraded, "fixture no longer exercises the degraded wording"
    assert doc["fallback_used"] is True, (
        "summary states the LLM was unavailable but reports itself undegraded"
    )


@pytest.mark.asyncio
async def test_a_missing_provenance_block_is_not_silently_undegraded():
    """Absent provenance means unknown, and the honest default for "was this
    degraded?" is the non-alarming one — but it must come from a real lookup,
    not from a key that never exists."""
    structured = _structured(fallback=False)
    structured.pop("_provenance")
    doc = await _store(structured)
    assert doc["fallback_used"] is False


# ── The class ratchet: no reading a key nobody writes ───────────────────────


def test_the_dead_key_is_gone_and_stays_gone():
    """``_fallback_used`` was read in exactly one place and written in none.
    Pinning the name stops it being reintroduced by a revert or a merge."""
    # The dead key is a dict lookup of the exact literal "_fallback_used".
    # Matching the bare substring would also flag unrelated names that merely
    # end in it — a log event ("run_compare_ai_report_fallback_used") and an
    # f-string flag template — and a ratchet that cries wolf gets deleted.
    dead_key = re.compile(r"""['"]_fallback_used['"]""")
    hits = []
    for path in (BACKEND / "app").rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        for i, line in enumerate(text.splitlines(), 1):
            if dead_key.search(line):
                hits.append(f"{path.relative_to(BACKEND)}:{i}: {line.strip()}")
    assert not hits, "the dead key is back: " + "; ".join(hits)


def test_the_dead_key_ratchet_would_actually_fire():
    """A regex that matched nothing would make the test above vacuous."""
    dead_key = re.compile(r"""['"]_fallback_used['"]""")
    assert dead_key.search('fallback_used = structured.get("_fallback_used", False)')
    assert not dead_key.search('logger.warning("run_compare_ai_report_fallback_used")')
    assert not dead_key.search('flags.append(_flag(f"{key}_fallback_used", "x"))')


def test_store_summary_reads_the_flag_from_provenance():
    """Names the source of truth, so a future edit that reaches for some other
    dict has to justify itself against this test."""
    src = (BACKEND / "app" / "agents" / "summary_agent.py").read_text(encoding="utf-8")
    body = src.split("async def _store_summary")[1].split("\n    async def ")[0]
    assign = re.search(r"fallback_used\s*=\s*(.+)", body)
    assert assign, "_store_summary no longer assigns fallback_used"
    assert "summary_provenance" in assign.group(1), (
        f"fallback_used is read from {assign.group(1)!r}, not from the provenance "
        "block that run() actually populates"
    )
