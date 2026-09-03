"""Regression: attribution rungs 3 and 4, and phase attribution (S3a-2).

Why determinism is the property under test
------------------------------------------
Both rungs can match more than one thing. A project with overlapping release
windows — a hotfix validated while the next minor is in QA, which is normal —
has several containing windows for the same run, and two rules can share a
priority.

"Whichever the database returned first" is not an answer here, and the reason
is specific: the output is an *attribution*, and a wrong one is invisible. The
run still shows a release. Nothing errors, nothing looks odd, and the same run
re-ingested could land somewhere else. So every ordering in this module is
total, and every tie-break ends in ``id`` — a column that cannot tie.

The other property is that inference never overwrites a person.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.models.postgres import AttributionMatchField
from app.services import release_attribution as attribution

NOW = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)


def _rule(**kw):
    base = dict(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        name="r",
        priority=100,
        is_enabled=True,
        match_field=AttributionMatchField.BRANCH.value,
        match_pattern="release/*",
        target_release_name="2.5.0",
        created_at=NOW,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _run(**kw):
    base = dict(
        id=uuid.uuid4(),
        branch=None,
        build_number=None,
        environment=None,
        tags=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _release(**kw):
    base = dict(
        id=uuid.uuid4(),
        name="r",
        sort_key="1|00002.000005.000000.000000|~",
        created_at=NOW,
        cutoff_start_at=None,
        cutoff_end_at=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


# ── Matching ─────────────────────────────────────────────────────────────────


def test_branch_glob_matches_case_insensitively():
    """CI providers disagree about branch-name casing.

    A rule that silently never fires because someone wrote ``Release/*`` looks
    exactly like "attribution doesn't work", which is a much harder thing to
    debug than a rule that is simply wrong.
    """
    rule = _rule(match_pattern="release/*")
    assert attribution.rule_matches(rule, _run(branch="release/2.5"))
    assert attribution.rule_matches(rule, _run(branch="Release/2.5"))
    assert attribution.rule_matches(_rule(match_pattern="Release/*"), _run(branch="release/2.5"))
    assert not attribution.rule_matches(rule, _run(branch="main"))


def test_an_absent_field_never_matches():
    """An unknown branch is not an empty branch.

    If a missing value matched, a single ``*`` rule — or any rule on a field
    the project does not populate — would silently capture every run in the
    project and attribute them all to one release.
    """
    for pattern in ("*", "release/*", ""):
        assert not attribution.rule_matches(
            _rule(match_pattern=pattern), _run(branch=None)
        )


def test_environment_matches_exactly_not_by_glob():
    """Environments are identifiers, not patterns.

    Globbing them would make ``prod`` match ``prod-canary``, quietly folding a
    canary's results into the production release's verdict.
    """
    rule = _rule(match_field=AttributionMatchField.ENVIRONMENT.value, match_pattern="prod")
    assert attribution.rule_matches(rule, _run(environment="prod"))
    assert attribution.rule_matches(rule, _run(environment=" PROD "))
    assert not attribution.rule_matches(rule, _run(environment="prod-canary"))


def test_tag_matches_when_any_tag_matches():
    rule = _rule(match_field=AttributionMatchField.TAG.value, match_pattern="rc-*")
    assert attribution.rule_matches(rule, _run(tags=["nightly", "rc-3"]))
    assert not attribution.rule_matches(rule, _run(tags=["nightly"]))
    assert not attribution.rule_matches(rule, _run(tags=None))
    # A non-string in the JSON list must not blow up the ingest path.
    assert not attribution.rule_matches(rule, _run(tags=[None, 7, {"a": 1}]))


# ── Rung 3 ordering ──────────────────────────────────────────────────────────


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return SimpleNamespace(all=lambda: list(self._rows))


class _Session:
    def __init__(self, rows):
        self._rows = rows
        self.statements = []

    async def execute(self, stmt):
        self.statements.append(stmt)
        return _Result(self._rows)


@pytest.mark.asyncio
async def test_rule_order_is_total_and_ends_in_a_column_that_cannot_tie():
    """priority, then created_at, then id.

    ``priority`` is intentionally not unique — reordering should be one UPDATE,
    not a dance around a constraint — so two rules can share it. Without the
    two further keys, which of them attributes a run would depend on row order,
    and the same run could land in a different release on a different day.
    """
    db = _Session([])
    await attribution.match_attribution_rule(db, uuid.uuid4(), _run(branch="x"))

    sql = str(db.statements[0])
    order = sql[sql.index("ORDER BY"):]
    for key in ("priority", "created_at", "id"):
        assert key in order, f"{key} missing from the rule ordering"
    assert order.index("priority") < order.index("created_at") < order.index("id")


@pytest.mark.asyncio
async def test_only_enabled_rules_are_considered():
    """Disabling a rule must actually stop it attributing.

    A disabled rule that still fires is worse than no toggle at all — the
    operator believes they have turned it off.
    """
    db = _Session([])
    await attribution.match_attribution_rule(db, uuid.uuid4(), _run(branch="x"))
    where = str(db.statements[0]).split("WHERE", 1)[1]
    assert "is_enabled" in where


@pytest.mark.asyncio
async def test_first_matching_rule_wins_and_later_ones_do_not_run():
    high = _rule(priority=1, match_pattern="release/*", target_release_name="2.5.0")
    low = _rule(priority=2, match_pattern="release/*", target_release_name="WRONG")
    db = _Session([high, low])

    got = await attribution.match_attribution_rule(
        db, uuid.uuid4(), _run(branch="release/2.5")
    )
    assert got is high
    assert got.target_release_name == "2.5.0"


# ── Rung 4 tie-break ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_narrowest_containing_window_wins():
    """Overlapping windows are normal, not exceptional.

    A two-day hotfix window sitting inside a six-week release window means the
    hotfix: the narrower window is the more specific claim. Picking the wider
    one would fold every hotfix run into the release it was branched from.
    """
    wide = _release(
        name="2.5.0",
        cutoff_start_at=NOW - timedelta(days=42),
        cutoff_end_at=NOW + timedelta(days=1),
    )
    narrow = _release(
        name="2.4.1-hotfix",
        cutoff_start_at=NOW - timedelta(days=1),
        cutoff_end_at=NOW + timedelta(days=1),
    )
    db = _Session([wide, narrow])

    got = await attribution.match_cutoff_window(db, uuid.uuid4(), NOW)
    assert got is narrow


@pytest.mark.asyncio
async def test_equal_windows_break_on_version_then_id():
    """Two windows of identical width still need one answer, every time."""
    span = (NOW - timedelta(days=5), NOW + timedelta(days=5))
    older = _release(name="2.4.0", sort_key="1|00002.000004.000000.000000|~",
                     cutoff_start_at=span[0], cutoff_end_at=span[1])
    newer = _release(name="2.5.0", sort_key="1|00002.000005.000000.000000|~",
                     cutoff_start_at=span[0], cutoff_end_at=span[1])

    # Same inputs, both orderings — the answer must not depend on row order.
    a = await attribution.match_cutoff_window(_Session([older, newer]), uuid.uuid4(), NOW)
    b = await attribution.match_cutoff_window(_Session([newer, older]), uuid.uuid4(), NOW)
    assert a.name == b.name == "2.4.0", "lower version wins a tie, deterministically"


@pytest.mark.asyncio
async def test_fully_tied_windows_still_resolve_identically():
    """When width, version AND created_at all tie, ``id`` is the only key left.

    Python's sort is stable, so an incomplete key silently defers to input
    order — and input order here is whatever Postgres returned, which is not
    guaranteed. The same run re-ingested would land in a different release,
    with nothing to show for it. This is the case the trailing ``id`` exists
    for, and the earlier tie-break test cannot reach it because its releases
    differ by version.
    """
    span = (NOW - timedelta(days=5), NOW + timedelta(days=5))
    shared = dict(
        sort_key="1|00002.000004.000000.000000|~",
        created_at=NOW,
        cutoff_start_at=span[0],
        cutoff_end_at=span[1],
    )
    a = _release(name="A", **shared)
    b = _release(name="B", **shared)

    forward = await attribution.match_cutoff_window(_Session([a, b]), uuid.uuid4(), NOW)
    reverse = await attribution.match_cutoff_window(_Session([b, a]), uuid.uuid4(), NOW)
    assert forward.id == reverse.id, (
        "identical windows must resolve to the same release regardless of the "
        "order the database returned them"
    )


@pytest.mark.asyncio
async def test_no_execution_time_means_no_window_match():
    """Guessing from ingest time here would defeat the execution timestamp.

    Rung 4's whole predicate is "the window containing when the run RAN". With
    no execution time there is nothing to test containment against, so the
    honest answer is to fall through to rung 5 rather than substitute a
    different moment and present the result as a window match.
    """
    r = _release(cutoff_start_at=NOW - timedelta(days=1), cutoff_end_at=NOW + timedelta(days=1))
    assert await attribution.match_cutoff_window(_Session([r]), uuid.uuid4(), None) is None


# ── Phase attribution ────────────────────────────────────────────────────────


def _phase(**kw):
    base = dict(id=uuid.uuid4(), order_index=0, planned_start=None, planned_end=None)
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.mark.asyncio
async def test_phase_match_returns_none_freely():
    """A run in a release but in no phase is a NORMAL state.

    Phases are optional and their planned windows need not cover the whole
    release. NULL phase_id means "in the release, not claimed by a phase" —
    which is different from unattributed, and must not be filled with a guess,
    because a phase gate evaluates exactly this membership.
    """
    assert await attribution.match_phase(_Session([]), uuid.uuid4(), NOW) is None
    assert await attribution.match_phase(_Session([_phase()]), uuid.uuid4(), None) is None


@pytest.mark.asyncio
async def test_narrowest_phase_window_wins():
    wide = _phase(planned_start=NOW - timedelta(days=20), planned_end=NOW + timedelta(days=20))
    narrow = _phase(planned_start=NOW - timedelta(days=1), planned_end=NOW + timedelta(days=1))
    got = await attribution.match_phase(_Session([wide, narrow]), uuid.uuid4(), NOW)
    assert got == narrow.id


def test_inference_never_overwrites_a_supplied_phase():
    """The only pre-S3a writer of phase_id was a human via the Link Run modal.

    A non-NULL value reaching the linker therefore means a person chose it.
    Overwriting it with an inferred phase would silently move a hand-placed run
    — and phase membership is what a phase gate reads, so the consequence is a
    gate evaluating a different body of evidence than the operator arranged.
    """
    import inspect

    from app.services import release_linker

    src = inspect.getsource(release_linker._link_with_phase)
    assert "if resolved_phase is None:" in src, (
        "phase inference must be guarded on the caller supplying nothing"
    )
    guard_at = src.index("if resolved_phase is None:")
    assert "match_phase" not in src[:guard_at], (
        "inference must not run before the caller's value is honoured"
    )


def test_every_ladder_rung_attributes_a_phase():
    """Every rung goes through the one phase-attributing helper.

    A rung that linked directly would attribute a release but never a phase, so
    phase gating would see an evidence gap that depends on which rung fired —
    invisible, and impossible to reason about from the data.

    The expected count is DERIVED from the rungs actually present rather than
    hardcoded. A hardcoded number breaks every time the ladder grows a rung,
    which trains people to bump it without looking — the opposite of what this
    test is for. Derived, it stays silent when a rung is added correctly and
    fails only when one skips the helper.
    """
    import inspect
    import re

    from app.services import release_linker

    src = inspect.getsource(release_linker.link_run_or_default)
    rungs = set(re.findall(r"LinkSource\.([A-Z_]+)\.value", src))
    assert len(rungs) >= 4, f"expected the ladder to have rungs, found {rungs}"
    assert src.count("_link_with_phase") == len(rungs), (
        f"{len(rungs)} rungs ({sorted(rungs)}) but "
        f"{src.count('_link_with_phase')} calls to the phase helper — a rung "
        f"is linking directly and will never attribute a phase"
    )


def test_rule_is_evaluated_before_the_cutoff_window():
    """A rule is a statement; a window is an inference.

    Someone who owns the project writing "release/* means the 2.5 line" should
    not lose to a date range that happens to overlap.
    """
    import inspect

    from app.services import release_linker

    src = inspect.getsource(release_linker.link_run_or_default)
    assert src.index("RULE_MATCH") < src.index("CUTOFF_WINDOW") < src.index("ACTIVE_RELEASE")
