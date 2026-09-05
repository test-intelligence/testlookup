"""Regression: the release vocabulary says what the code can actually do.

Four design-gate findings, all the same shape — a name or a sentence that
described something the code did not do.

* ``CONDITIONAL_GO`` is one of four declared verdicts and nothing emits it;
  ``conditions_for_go`` is a column with no writer.
* Three spellings appeared to be "unattributed" drift. They are not — they
  denote three DIFFERENT states, and the audit's suggestion to unify them would
  have destroyed a real distinction. Two were bare literals, which is the part
  that was genuinely wrong.
* ``release_sort_key``'s docstring said "five digits" with five-digit examples
  while ``SEGMENT_WIDTH`` was 6 — the module's documentation disagreed with the
  module about the one thing it exists to define.
* Migration 0151 said it added ``project_id`` "so it can be enforced by the
  database too". The column is bare: no foreign key, no CHECK. That sentence is
  the one a reader consults before deciding whether an application-level check
  is still needed.

The pattern is worth naming: none of these is a crash. Each is a statement that
a future reader would act on, and each was false.
"""
from __future__ import annotations

import inspect
import re

import pytest

pytest.importorskip("sqlalchemy")

pytestmark = pytest.mark.regression

from app.core import release_filter  # noqa: E402
from app.models.postgres import LinkSource  # noqa: E402
from app.services import release_gate_decision_service as decisions  # noqa: E402
from app.services import release_rollup_service as rollup_svc  # noqa: E402
from app.services import release_sort_key  # noqa: E402


# ── A conditional verdict must carry conditions ──────────────────────────────


@pytest.mark.asyncio
async def test_a_conditional_go_without_conditions_is_refused():
    """Otherwise it is a GO recorded under a word implying work remains.

    The reader of the scorecard cannot act on it and cannot tell it apart from
    a pass.
    """
    with pytest.raises(ValueError, match="requires conditions_for_go"):
        await decisions.record_decision(
            None, "r1", "CONDITIONAL_GO", conditions_for_go=None
        )

    with pytest.raises(ValueError, match="requires conditions_for_go"):
        await decisions.record_decision(
            None, "r1", "CONDITIONAL_GO", conditions_for_go=[]
        )


@pytest.mark.asyncio
async def test_an_unknown_verdict_is_still_refused():
    """The pre-existing guard, kept: the column is String(20), so the database
    accepts anything and a drifted value would simply never match a status
    query again."""
    with pytest.raises(ValueError, match="unknown verdict"):
        await decisions.record_decision(None, "r1", "SHIP_IT")


def test_the_gate_cannot_currently_produce_a_conditional_go():
    """Records the gap rather than pretending it is closed.

    ``decide`` returns GO / NO_GO / NOT_EVALUATED and nothing passes
    ``conditions_for_go``. The verdict stays in the vocabulary because the
    column and the AI council's recommendation both use it — narrowing it would
    reject a row a future caller is entitled to record.

    If a slice ever teaches the gate to emit it, this test fails and should be
    replaced by one pinning WHEN it is emitted.
    """
    src = inspect.getsource(rollup_svc.decide)
    assert "CONDITIONAL_GO" not in src, (
        "the gate now emits CONDITIONAL_GO — replace this test with one that "
        "pins the rule for when, and assert conditions_for_go travels with it"
    )
    assert "CONDITIONAL_GO" in decisions.VERDICTS, (
        "the verdict was removed from the vocabulary; if that is deliberate, "
        "the column comment and the AI council's mapping need updating too"
    )


# ── Three names, three states ────────────────────────────────────────────────


def test_the_three_unattributed_spellings_stay_distinct():
    """Collapsing them would report a backfilled link and a missing one as the
    same thing.

    The query sentinel, "a link exists but its provenance is lost", and "there
    is no primary link row" are three different facts about a run.
    """
    spellings = {
        release_filter.UNATTRIBUTED,
        LinkSource.UNKNOWN.value,
        rollup_svc.NO_PRIMARY_LINK,
    }
    assert len(spellings) == 3, (
        "two of the three states now share a spelling, so the attribution mix "
        "can no longer distinguish them"
    )


def test_the_rollup_labels_come_from_named_constants():
    """The part that WAS wrong: the distinction lived only in whoever last read
    the line.

    Checked on the AST, not the source text. The function now carries a comment
    explaining all three spellings — which contains the literal — so a
    substring check matches the PROSE and reports a bare literal that is not
    there. This file exists to catch documentation being mistaken for code, and
    the first version of this test made exactly that mistake.
    """
    import ast
    import textwrap

    tree = ast.parse(textwrap.dedent(inspect.getsource(rollup_svc.build_rollup)))
    literals = {
        n.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
    }
    assert rollup_svc.NO_PRIMARY_LINK not in literals, (
        "the bucket label is a bare literal again — name it, so the three "
        "spellings cannot be conflated by someone skimming for a string"
    )
    assert LinkSource.UNKNOWN.value not in literals, (
        "the fallback link source is a bare literal again"
    )

    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert "NO_PRIMARY_LINK" in names
    assert "UNKNOWN" in attrs


# ── Documentation that matches the code ──────────────────────────────────────


def test_the_sort_key_docstring_agrees_with_the_width_it_uses():
    """It said "five digits", with five-digit examples, while SEGMENT_WIDTH was
    6 — so the module's own documentation disagreed with it about the one thing
    the module defines."""
    doc = release_sort_key.__doc__ or ""
    examples = re.findall(r"``(?:\d+\|)?(\d{4,})\.", doc)
    for example in examples:
        assert len(example) == release_sort_key.SEGMENT_WIDTH, (
            f"the docstring shows a {len(example)}-digit segment while "
            f"SEGMENT_WIDTH is {release_sort_key.SEGMENT_WIDTH}"
        )
    assert examples, "no worked example survives in the docstring to check"


def test_the_worked_example_is_what_the_function_returns():
    """A docstring example nobody executes is a comment. This one is run."""
    doc = release_sort_key.__doc__ or ""
    assert release_sort_key.compute_sort_key("2.9.0", "2.9.0") in doc, (
        "the docstring's worked example is not what compute_sort_key returns"
    )


def test_migration_0151_does_not_claim_enforcement_it_lacks():
    """The column is bare — no foreign key, no CHECK.

    That sentence is what a reader consults before deciding whether they still
    need an application-level check, so claiming database enforcement was worse
    than saying nothing.
    """
    from pathlib import Path

    path = (
        Path(release_sort_key.__file__).resolve().parents[2]
        / "migrations" / "versions" / "0151_release_identity_and_primary_link.py"
    )
    text = path.read_text(encoding="utf-8")
    doc = text.split('"""')[1] if '"""' in text else text

    assert "so it can be enforced by the database too" not in doc, (
        "0151 claims database enforcement for link project_id; the column has "
        "no FK and no CHECK, and the guarantee is the service layer's alone"
    )
    # And the column really is bare, so the corrected wording stays true.
    assert 'sa.Column("project_id", sa.UUID(as_uuid=True), nullable=True)' in text
    assert "ForeignKey" not in text.split('sa.Column("project_id"')[1][:200]

# ── The external system owns the name, and only the name ─────────────────────


class _SyncedRel:
    def __init__(self, source_system="github", name="2.4.0"):
        import uuid as _uuid

        self.id = _uuid.uuid4()
        self.project_id = _uuid.uuid4()
        self.source_system = source_system
        self.name = name
        self.version = "2.4.0"
        self.status = "planning"
        self.sort_key = "000002.000004.000000.000000"
        self.is_auto_named = False
        # A terminal status triggers the active-flag rotation, which reads
        # this — a fake standing in for a real row has to carry it.
        self.is_active = False


class _RelSession:
    def __init__(self, rel):
        self._rel = rel

    async def execute(self, stmt=None, *a, **kw):
        rel = self._rel

        class _R:
            def scalar_one_or_none(self_inner):
                return rel

            def scalar(self_inner):
                return 0

            def scalars(self_inner):
                return self_inner

            def all(self_inner):
                return []

        return _R()

    async def flush(self):
        return None


@pytest.mark.asyncio
async def test_renaming_a_synced_release_is_refused():
    """Both syncs write ``existing.name`` from the external title on every run.

    Accepting the rename stored an edit that vanished at the next sync with no
    error and no trace — the failure migration 0155's own docstring predicted
    and nothing prevented.
    """
    from fastapi import HTTPException

    from app.routers.releases import ReleaseUpdate
    from app.services import release_service

    rel = _SyncedRel()
    with pytest.raises(HTTPException) as exc:
        await release_service.update_release(
            _RelSession(rel), str(rel.id), ReleaseUpdate(name="renamed locally")
        )
    assert exc.value.status_code == 409
    assert "github" in str(exc.value.detail), "the refusal must name the owner"
    assert "sync" in str(exc.value.detail)


@pytest.mark.asyncio
async def test_a_local_release_can_still_be_renamed():
    """The refusal is about EXTERNAL ownership. ``source_system`` NULL means
    "ours", which is the common case and not a gap to backfill."""
    from app.routers.releases import ReleaseUpdate
    from app.services import release_service

    rel = _SyncedRel(source_system=None)
    await release_service.update_release(
        _RelSession(rel), str(rel.id), ReleaseUpdate(name="renamed locally")
    )
    assert rel.name == "renamed locally"


@pytest.mark.asyncio
async def test_a_synced_release_still_accepts_the_fields_testlookup_owns():
    """Only the name is externally owned.

    Neither sync writes version, dates or status, and status is TestLookup's on
    purpose — jira_release_sync declines to map Jira's `released` flag so an
    external tool cannot mark a release shipped the gate never approved.
    Refusing these too would block edits nothing would ever overwrite.
    """
    from app.routers.releases import ReleaseUpdate
    from app.services import release_service

    rel = _SyncedRel()
    await release_service.update_release(
        _RelSession(rel), str(rel.id), ReleaseUpdate(status="in_progress")
    )
    assert rel.status == "in_progress"


@pytest.mark.asyncio
async def test_resubmitting_the_same_name_is_not_a_rename():
    """A UI that PUTs the whole form back must not 409 for having included the
    unchanged name — that would make a synced release uneditable in practice.
    """
    from app.routers.releases import ReleaseUpdate
    from app.services import release_service

    rel = _SyncedRel(name="2.4.0")
    await release_service.update_release(
        _RelSession(rel), str(rel.id), ReleaseUpdate(name="2.4.0", status="released")
    )
    assert rel.status == "released"
