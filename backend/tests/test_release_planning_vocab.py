"""W11: ``app.core.release_planning``'s vocabularies agree with the frontend's.

The portfolio trend (REL-03) restates the release card's ``mapStage`` and
``mapPhaseState`` in Python, and the phase routes (REL-05 / REL-07) validate
the New-phase form's types; two copies drift unless something reads both.
This reads the frontend as TEXT (no node, no build):

* ``components/releases/mapping.ts`` ``mapStage``: every ``lower === '<x>'``
  literal per returned stage -> ``STAGE_RELEASED`` / ``STAGE_CANCELLED`` /
  ``STAGE_IN_PROGRESS`` (anything else is ``planning``, on both sides);
* ``mapPhaseState``'s ``'failed'`` branch -> ``PHASE_FAILED_STATUSES``;
* ``pages/ReleasesPage.tsx`` ``PHASE_TYPES`` values -> ``PHASE_TYPES``, and the
  ORM comment on ``ReleasePhase.phase_type`` names the same list;
* ``release_stage`` behaves like ``mapStage`` (case, ``None``, unknown).

Plus the module's own promises: it imports nothing from ``app``, and every
sentence is the contract's text.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from app.core import release_planning as rp

BACKEND = Path(__file__).resolve().parents[1]
FRONTEND = BACKEND.parent / "frontend" / "src"
MAPPING = FRONTEND / "components" / "releases" / "mapping.ts"
RELEASES_PAGE = FRONTEND / "pages" / "ReleasesPage.tsx"


def _source(path: Path) -> str:
    if not path.exists():  # pragma: no cover - a backend-only checkout
        pytest.skip(f"{path.name} not present in this checkout")
    return path.read_text(encoding="utf-8")


def _function_body(source: str, name: str) -> str:
    match = re.search(rf"function {name}\(.*?\n}}\n", source, re.S)
    assert match, f"{name} not found"
    return match.group(0)


def _branches(body: str, variable: str) -> dict[str, set[str]]:
    """``if (<var> === 'a' || <var> === 'b') return '<stage>'`` -> {stage: {a, b}}."""
    out: dict[str, set[str]] = {}
    for condition, stage in re.findall(r"if \((.*?)\) return '([a-z_]+)'", body):
        literals = set(re.findall(rf"{variable} === '([^']+)'", condition))
        assert literals, condition
        out.setdefault(stage, set()).update(literals)
    return out


def test_the_stage_vocabulary_is_the_cards_map_stage():
    stages = _branches(_function_body(_source(MAPPING), "mapStage"), "lower")
    assert stages == {
        "released": set(rp.STAGE_RELEASED),
        "cancelled": set(rp.STAGE_CANCELLED),
        "in_progress": set(rp.STAGE_IN_PROGRESS),
    }
    assert "return 'planning'" in _function_body(_source(MAPPING), "mapStage")


def test_the_failed_phase_statuses_are_the_cards():
    states = _branches(_function_body(_source(MAPPING), "mapPhaseState"), "s")
    assert states["failed"] == set(rp.PHASE_FAILED_STATUSES)


def test_the_phase_types_are_the_new_phase_forms_and_the_orms():
    block = re.search(r"const PHASE_TYPES = \[(.*?)\]", _source(RELEASES_PAGE), re.S)
    assert block, "PHASE_TYPES not found in ReleasesPage.tsx"
    assert tuple(re.findall(r"value: '([a-z_]+)'", block.group(1))) == rp.PHASE_TYPES
    orm = (BACKEND / "app" / "models" / "postgres.py").read_text(encoding="utf-8")
    assert "# Phase type: " + "|".join(rp.PHASE_TYPES) in orm
    assert rp.PHASE_TYPE_SENTENCE == "Phase type must be one of: " + ", ".join(rp.PHASE_TYPES) + "."


@pytest.mark.parametrize(
    ("status", "stage"),
    [
        ("released", "released"), ("Shipped", "released"), ("COMPLETED", "released"),
        ("cancelled", "cancelled"), ("canceled", "cancelled"), ("Archived", "cancelled"),
        ("in_progress", "in_progress"), ("in-progress", "in_progress"), ("ACTIVE", "in_progress"),
        ("running", "in_progress"),
        ("planning", "planning"), ("draft", "planning"), ("", "planning"), (None, "planning"),
    ],
)
def test_release_stage_behaves_like_map_stage(status, stage):
    assert rp.release_stage(status) == stage


def test_the_module_imports_nothing_from_app():
    tree = ast.parse(Path(rp.__file__).read_text(encoding="utf-8"))
    imported = {
        (node.module or "") if isinstance(node, ast.ImportFrom) else alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert not any(name == "app" or name.startswith("app.") for name in imported), imported


def test_the_bounds_and_sentences():
    assert (rp.MAX_BULK_PHASES, rp.MAX_PRD_BYTES, rp.MAX_PRD_CHARS) == (30, 200_000, 200_000)
    assert (rp.MAX_DOCX_INFLATED_BYTES, rp.MAX_DOCX_MEMBERS) == (5_000_000, 300)
    assert (rp.PRD_LLM_TIMEOUT_SECONDS, rp.PRD_LLM_INPUT_CHARS) == (45, 24_000)
    assert (rp.PORTFOLIO_WEEKS_DEFAULT, rp.PORTFOLIO_WEEKS_MIN, rp.PORTFOLIO_WEEKS_MAX) == (12, 4, 26)
    assert rp.SCHEDULE_MAX_RELEASES == 500 and rp.SCHEDULE_FORMATS == ("ics", "csv")
    assert set(rp.FALLBACK_REASON_TEXT) == set(rp.DRAFT_FALLBACK_REASONS)
    assert rp.PHASE_DRAFT_SOURCES == ("rules", "llm", "manual")
    assert rp.PORTFOLIO_APPROXIMATED_SERIES == ("in_progress", "ready_to_ship", "blocked")
    assert rp.PHASE_NAME_TAKEN.format(name="Smoke") == "Phase 'Smoke' already exists in this release"
    assert rp.PHASE_NAME_REPEATED.format(name="Smoke") == "Phase 'Smoke' appears twice in this request"
    assert rp.RELEASE_NOT_FOUND == "Release not found"
    # The 409 text add_phase already sends is the shared template's.
    service = (BACKEND / "app" / "services" / "release_service.py").read_text(encoding="utf-8")
    assert "already exists in this release" in service


def test_the_a0_activity_specs_and_the_attribution_rule_link():
    """The two bulk-phase events (G-B emits them) and the rules page's link (G-D's page)."""
    from app.services.activity import events as E

    for name in ("release.phases_cloned", "release.phases_generated"):
        spec = E.ACTIVITY_EVENTS[name]
        assert (spec.category, spec.entity_type, spec.write_mode) == ("release", "release", "outcome")
    cloned = E.ACTIVITY_EVENTS["release.phases_cloned"].summary_template.format(
        entity_label="2026.12", count=4, source_release="2026.10",
    )
    assert cloned == "Release 2026.12: 4 phases copied from 2026.10"
    generated = E.ACTIVITY_EVENTS["release.phases_generated"].summary_template.format(
        entity_label="2026.13", count=3, source="rules",
    )
    assert generated == "Release 2026.13: 3 phases created (rules)"
    assert E.entity_href("attribution_rule", "a1b2") == "/releases/attribution-rules"


def test_the_bulk_bound_reaches_the_schema():
    from pydantic import ValidationError

    from app.models.schemas import ReleasePhaseBulkItem, ReleasePhasesBulkRequest

    item = {"name": "Smoke"}
    assert len(ReleasePhasesBulkRequest(phases=[item] * rp.MAX_BULK_PHASES).phases) == rp.MAX_BULK_PHASES
    with pytest.raises(ValidationError):
        ReleasePhasesBulkRequest(phases=[item] * (rp.MAX_BULK_PHASES + 1))
    with pytest.raises(ValidationError):
        ReleasePhasesBulkRequest(phases=[])
    with pytest.raises(ValidationError):
        ReleasePhaseBulkItem(name="Smoke", unknown=1)
    assert ReleasePhaseBulkItem(name="Smoke").phase_type == "qa_testing"
