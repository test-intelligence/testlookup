"""Regression: an IN_PROGRESS defect still blocks the release gate (W06 DEF-01, contract §0.5).

``IN_PROGRESS`` existed before W06 -- ``scripts/seed_data.py`` seeds 6 of its 18
demo defects that way -- yet every gate reader asked for ``resolution_status ==
"OPEN"``, so an in-progress CRITICAL was invisible to the P0 cap, to the
release gate's defect criterion, to the council's open counts and to the
risk agent. An in-progress bug is not a fixed one. Every live-defect reader now
asks for ``defect_status.ACTIVE_STATUSES`` (``OPEN``, ``IN_PROGRESS``).

Behavioural half: a session fake that FILTERS its rows by the statuses the
statement actually binds (so a reader that reverts to ``== "OPEN"`` sees no
in-progress row and the count drops to zero). Static half: no live-defect
reader module compares ``resolution_status`` to a single literal again.
"""
from __future__ import annotations

import ast
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import postgresql

from app.services import metrics_service
from app.services import release_defect_service as defect_svc
from app.services.defect_status import ACTIVE_STATUSES

pytestmark = pytest.mark.regression

APP = Path(__file__).resolve().parents[2] / "app"
PROJECT = uuid.uuid4()
RELEASE = uuid.uuid4()


def _bound_statuses(stmt) -> set[str]:
    """The ``resolution_status`` values a statement binds (``=`` or ``IN``)."""
    compiled = stmt.compile(dialect=postgresql.dialect())
    out: set[str] = set()
    for key, value in compiled.params.items():
        if key.startswith("resolution_status"):
            out |= set(value) if isinstance(value, (list, tuple)) else {value}
    return out


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)

    def scalar(self):
        return len(self._rows)

    def scalar_one(self):
        return len(self._rows)

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None


class _FilteringSession:
    """Returns only the defects whose status the statement admits."""

    def __init__(self, defects, release=None):
        self.defects = defects
        self.release = release
        self.statements: list = []

    async def execute(self, stmt, *args, **kwargs):
        self.statements.append(stmt)
        froms = {getattr(t, "name", None) for t in stmt.get_final_froms()} if hasattr(stmt, "get_final_froms") else set()
        if "releases" in froms:
            return _Result([self.release] if self.release is not None else [])
        statuses = _bound_statuses(stmt)
        return _Result([d for d in self.defects if d.resolution_status in statuses])


def _defect(status, *, severity="CRITICAL", title="Checkout 500s"):
    return SimpleNamespace(
        id=uuid.uuid4(), title=title, severity=severity, resolution_status=status,
        release_id=RELEASE, affects_releases=None, project_id=PROJECT, component="payments",
    )


@pytest.mark.asyncio
async def test_the_p0_cap_counts_an_in_progress_critical_project_wide():
    session = _FilteringSession([_defect("IN_PROGRESS"), _defect("RESOLVED")])
    assert await metrics_service.count_open_critical_defects(session, PROJECT) == 1
    assert _bound_statuses(session.statements[0]) == set(ACTIVE_STATUSES)


@pytest.mark.asyncio
async def test_the_p0_cap_counts_an_in_progress_critical_for_a_release():
    session = _FilteringSession([_defect("IN_PROGRESS"), _defect("INVALID")])
    assert await metrics_service.count_open_critical_defects(session, PROJECT, RELEASE) == 1
    # Several releases take the union path; still counted.
    session = _FilteringSession([_defect("IN_PROGRESS")])
    assert await metrics_service.count_open_critical_defects(session, PROJECT, [str(RELEASE), str(uuid.uuid4())]) == 1


@pytest.mark.asyncio
async def test_blocking_defects_lists_an_in_progress_defect():
    in_progress = _defect("IN_PROGRESS")
    session = _FilteringSession([in_progress, _defect("RESOLVED")])
    assert await defect_svc.blocking_defects(session, RELEASE, PROJECT) == [in_progress]
    assert defect_svc.OPEN_STATUSES == ACTIVE_STATUSES


@pytest.mark.asyncio
async def test_the_release_gate_stays_no_go_with_the_same_reason(monkeypatch):
    from app.services import release_gate_service as gate

    async def _rollup(db, release_id):
        return SimpleNamespace(denominator=10, evidence_count=10, run_ids=[], status_counts={},
                               attribution_mix={}, incomplete_runs=[])

    monkeypatch.setattr(gate.rollup_svc, "build_rollup", _rollup)
    monkeypatch.setattr(gate.rollup_svc, "decide", lambda rollup: ("GO", []))
    monkeypatch.setattr(gate.rollup_svc, "summarise", lambda rollup: {})
    release = SimpleNamespace(id=RELEASE, project_id=PROJECT, baseline_release_id=None)
    session = _FilteringSession([_defect("IN_PROGRESS", title="Checkout 500s")], release=release)

    result = await gate.evaluate_release(session, RELEASE, record=False)

    assert result["verdict"] == "NO_GO"
    assert "CRITICAL defect open: Checkout 500s" in result["blocking_reasons"]
    assert result["defects"]["open_total"] == 1


@pytest.mark.asyncio
async def test_the_risk_agent_counts_an_in_progress_defect():
    from app.agents.release_risk_agent import ReleaseRiskAgent

    session = _FilteringSession([_defect("IN_PROGRESS"), _defect("OPEN"), _defect("RESOLVED")])
    assert await ReleaseRiskAgent._count_open_defects_from_db(session, str(PROJECT)) == 2


# ── Static half: no live-defect reader goes back to a single literal ────────

#: The §0.5 reader modules (G-A's half; G-B owns defect_jira_service / defect_promotion_service).
READERS = (
    "services/metrics_service.py",
    "services/release_defect_service.py",
    "services/release_council_service.py",
    "agents/release_risk_agent.py",
    "agents/triage_agent.py",
    "services/cluster_service.py",
    "routers/integrations.py",
)
#: triage_agent reads the row the OPEN-only unique index refused: OPEN on purpose.
ALLOWED_SINGLE_OPEN = {("agents/triage_agent.py", "Defect.test_case_id == tc_id")}


def _single_status_comparisons(rel: str) -> list[str]:
    source = (APP / rel).read_text(encoding="utf-8")
    tree = ast.parse(source)
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare) or len(node.ops) != 1:
            continue
        left = node.left
        if not (isinstance(left, ast.Attribute) and left.attr == "resolution_status"):
            continue
        if isinstance(node.ops[0], (ast.Eq, ast.NotEq)):
            found.append(node)
    out = []
    for node in found:
        parent_line = source.splitlines()[node.lineno - 2] if node.lineno > 1 else ""
        if any(rel == path and marker in parent_line for path, marker in ALLOWED_SINGLE_OPEN):
            continue
        out.append(f"{rel}:{node.lineno}")
    return out


def test_no_reader_compares_the_status_to_a_single_word():
    offenders = [hit for rel in READERS for hit in _single_status_comparisons(rel)]
    assert not offenders, (
        "a live-defect reader compares resolution_status to one word again; use "
        ".in_(ACTIVE_STATUSES) so an IN_PROGRESS defect still counts: " + ", ".join(offenders)
    )


def test_the_scan_sees_the_one_allowed_open_read():
    source = (APP / "agents" / "triage_agent.py").read_text(encoding="utf-8")
    assert 'Defect.resolution_status == "OPEN"' in source, (
        "triage_agent's conflict read (the row the OPEN-only index refused) moved: re-check ALLOWED_SINGLE_OPEN"
    )
