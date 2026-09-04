"""Regression: the P0 hard cap answers for the release it was asked about (W3).

The live defect
---------------
``get_dashboard_summary`` takes a ``release_id`` and scopes its pass rate and
every trend to it — then counted P0 defects PROJECT-WIDE for the
``max_p0_defects`` hard cap. A cap breach forces NO_GO regardless of the
pass-rate band, so asking the dashboard about 2.4.0 returned red because of an
open CRITICAL found in 2.3.0 that nobody ever said affects 2.4.0. The release
filter reached every number on that summary except the one able to veto all of
them.

``/metrics/summary`` is release-scopeable and has live frontend callers, so this
was reachable in the product, not a latent gap.

Why the count DELEGATES rather than filtering
----------------------------------------------
"Affects this release" is SQL narrowing PLUS a Python containment test, because
``affects_releases`` is a portable JSON column whose search operators differ
between Postgres and SQLite. A second copy of that rule in ``metrics_service``
would drift, and the two halves would stop agreeing about what blocks a
release — the same two-modules-one-rule failure that has bitten this codebase
before.

And why ``affects_releases`` had to become writable
----------------------------------------------------
Nothing in the product wrote that column, so it was permanently NULL:
``blocking_defects`` only ever took its found-in fallback, and the
asserted-impact branch it exists for could not execute. A rule with an
unreachable branch is a rule nobody has tested.
"""
from __future__ import annotations

import ast
import inspect
import uuid

import pytest

pytest.importorskip("sqlalchemy")

pytestmark = pytest.mark.regression

from app.services import metrics_service  # noqa: E402
from app.services import release_defect_service as defect_svc  # noqa: E402

PROJECT = uuid.uuid4()
RELEASE_A = uuid.uuid4()
RELEASE_B = uuid.uuid4()


class _Defect:
    def __init__(self, severity, release_id=None, affects=None, title="t"):
        self.id = uuid.uuid4()
        self.severity = severity
        self.release_id = release_id
        self.affects_releases = affects
        self.title = title


# ── The count narrows to one release ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_cap_counts_only_defects_that_affect_the_release(monkeypatch):
    """The defect itself: a CRITICAL found in another release must not veto
    this one."""
    async def _blocking(db, release_id, project_id):
        assert str(release_id) == str(RELEASE_A)
        return [_Defect("CRITICAL", release_id=RELEASE_A)]

    monkeypatch.setattr(defect_svc, "blocking_defects", _blocking)
    n = await metrics_service.count_open_critical_defects(
        object(), PROJECT, RELEASE_A
    )
    assert n == 1


@pytest.mark.asyncio
async def test_a_critical_from_another_release_does_not_veto_this_one(monkeypatch):
    async def _blocking(db, release_id, project_id):
        # blocking_defects already applied the affects/found-in rule; a defect
        # belonging to RELEASE_B simply is not in the result.
        return []

    monkeypatch.setattr(defect_svc, "blocking_defects", _blocking)
    assert await metrics_service.count_open_critical_defects(
        object(), PROJECT, RELEASE_A
    ) == 0


@pytest.mark.asyncio
async def test_only_criticals_count_toward_the_p0_cap(monkeypatch):
    """``blocking_defects`` blocks on CRITICAL *and* HIGH; the P0 cap is
    CRITICAL alone. Counting its whole result would silently tighten the cap."""
    async def _blocking(db, release_id, project_id):
        return [
            _Defect("CRITICAL"),
            _Defect("HIGH"),
            _Defect(None),
            _Defect("critical"),  # case must not matter
        ]

    monkeypatch.setattr(defect_svc, "blocking_defects", _blocking)
    assert await metrics_service.count_open_critical_defects(
        object(), PROJECT, RELEASE_A
    ) == 2


@pytest.mark.asyncio
async def test_the_unattributed_bucket_falls_through_to_project_wide(monkeypatch):
    """Runs belonging to no release make the project's open P0s the relevant
    set, so the sentinel must NOT be handed to a release lookup that would
    match nothing and report a clean zero."""
    called = False

    async def _blocking(db, release_id, project_id):
        nonlocal called
        called = True
        return []

    monkeypatch.setattr(defect_svc, "blocking_defects", _blocking)

    class _Session:
        async def execute(self, stmt):
            class _R:
                def scalar(self_inner):
                    return 7
            return _R()

    n = await metrics_service.count_open_critical_defects(
        _Session(), PROJECT, "unattributed"
    )
    assert not called, "the sentinel was treated as a release id"
    assert n == 7, "the project-wide query did not run for the Unattributed bucket"


@pytest.mark.asyncio
async def test_no_release_leaves_the_original_query_untouched(monkeypatch):
    """Every existing caller passes no release; that path must not change."""
    async def _blocking(db, release_id, project_id):
        raise AssertionError("release path taken with no release requested")

    monkeypatch.setattr(defect_svc, "blocking_defects", _blocking)

    class _Session:
        async def execute(self, stmt):
            class _R:
                def scalar(self_inner):
                    return 3
            return _R()

    assert await metrics_service.count_open_critical_defects(_Session(), PROJECT) == 3


# ── The call site actually passes it ─────────────────────────────────────────


def test_the_dashboard_passes_its_release_to_the_cap():
    """Checked by ARGUMENT COUNT at the call site, not by a substring.

    ``count_open_critical_defects`` appeared in this function throughout the
    defect's life — with two arguments. "The name is present" is exactly the
    check that could not see the bug.
    """
    tree = ast.parse(inspect.getsource(metrics_service.get_dashboard_summary))
    calls = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and getattr(n.func, "id", getattr(n.func, "attr", None))
        == "count_open_critical_defects"
    ]
    assert calls, "the readiness branch no longer counts P0 defects at all"
    for call in calls:
        assert len(call.args) >= 3, (
            "the release-scoped dashboard still counts P0 defects project-wide "
            "— a CRITICAL from another release will force NO_GO here"
        )


# ── assert_affects has a writer ──────────────────────────────────────────────


def test_asserted_impact_is_deduplicated_and_sorted():
    """Stable order: a column that reorders itself makes every audit diff look
    like a change."""
    out = defect_svc.assert_affects(
        _Defect("CRITICAL"), [str(RELEASE_B), str(RELEASE_A), str(RELEASE_B)]
    )
    assert out == sorted({str(RELEASE_A), str(RELEASE_B)})


def test_an_empty_assertion_stores_null_not_an_empty_list():
    """NULL means "not triaged" and is what the found-in fallback keys on. An
    empty list would read as "affects nothing", which silently stops the defect
    blocking anything at all."""
    assert defect_svc.assert_affects(_Defect("CRITICAL"), []) is None


def test_intake_accepts_asserted_impact():
    from app.models.schemas import DefectIntakeRequest

    payload = DefectIntakeRequest(
        project_id=PROJECT, title="a defect", severity="P0",
        affects_releases=[str(RELEASE_A)],
    )
    assert payload.affects_releases == [str(RELEASE_A)]


def test_the_intake_writer_goes_through_the_service():
    """Called by ARGUMENT, not by name-in-file: ``assert_affects`` is imported
    and mentioned in prose in several modules that never invoke it."""
    from app.services import analytics_service

    tree = ast.parse(inspect.getsource(analytics_service.create_manual_defect))
    calls = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and getattr(n.func, "id", getattr(n.func, "attr", None)) == "assert_affects"
    ]
    assert calls, (
        "manual defect intake does not record asserted impact, so "
        "affects_releases stays NULL and blocking_defects can only ever take "
        "its found-in fallback"
    )


# ── The gate consults defects, and the verdict combines ──────────────────────


@pytest.mark.parametrize(
    "rollup_verdict,defect_verdict,expected",
    [
        ("GO", "GO", "GO"),
        ("GO", "NO_GO", "NO_GO"),
        ("GO", "NOT_EVALUATED", "NOT_EVALUATED"),
        ("NOT_EVALUATED", "NO_GO", "NO_GO"),
        ("NO_GO", "GO", "NO_GO"),
        ("NOT_EVALUATED", "GO", "NOT_EVALUATED"),
    ],
)
def test_the_worse_verdict_wins(rollup_verdict, defect_verdict, expected):
    """A known blocker is decisive, so NO_GO outranks "cannot tell"; and an
    unassessed criterion must never read as a pass, so NOT_EVALUATED outranks
    GO. Both sides speak the same three-valued vocabulary, which is what makes
    combining them meaningful rather than a cast."""
    from app.services.release_gate_service import _worse_of

    assert _worse_of(rollup_verdict, defect_verdict) == expected


@pytest.mark.asyncio
async def test_the_gate_actually_consults_the_defect_service(monkeypatch):
    """The wiring itself, end to end.

    Everything else here tests the pieces: ``_worse_of`` in isolation,
    ``verdict_contribution`` in isolation. Mutation testing caught that gap —
    deleting the call from ``evaluate_release`` entirely left every one of
    those tests green, which is precisely the "well-tested module nothing
    reaches" shape this whole batch of work exists to fix.
    """
    import types

    from app.services import release_gate_service as gate
    from app.services import release_rollup_service as rollup_svc

    rollup = rollup_svc.ReleaseRollup(
        latest_by_test={f"t{i}": "PASSED" for i in range(9)}
    )
    rollup.status_counts = {s: 0 for s in rollup_svc.STATUSES}
    rollup.status_counts["PASSED"] = 9

    async def _rollup(db, rid, **kw):
        return rollup

    async def _defects(db, release_id, project_id):
        return {
            "blocking_count": 1,
            "blocking": [{"id": "d1", "title": "data loss", "severity": "CRITICAL"}],
            "unrated_count": 0, "unrated": [], "open_total": 1,
            "fully_triaged": True,
            "verdict": "NO_GO",
            "reasons": ["CRITICAL defect open: data loss"],
        }

    class _Session:
        async def execute(self, *a, **kw):
            release = types.SimpleNamespace(
                id="r1", baseline_release_id=None, project_id="p1"
            )

            class _R:
                def scalar_one_or_none(self_inner):
                    return release

            return _R()

    monkeypatch.setattr(rollup_svc, "build_rollup", _rollup)
    monkeypatch.setattr(gate.defect_svc, "defects_for_release", _defects)

    result = await gate.evaluate_release(_Session(), "r1", record=False)

    # Every test passed, so the rollup alone says GO. A release that ships over
    # a known open CRITICAL because its suite is green is the failure the gate
    # consults defects to prevent.
    assert result["verdict"] == "NO_GO", (
        "a green suite outvoted a known open CRITICAL — the gate is not "
        "consulting the defect service"
    )
    assert any("data loss" in r for r in result["blocking_reasons"]), (
        "the defect's reason never reached the verdict, so the release manager "
        "is told NO_GO without being told why"
    )
    assert result["defects"]["blocking_count"] == 1, (
        "the whole defect picture must travel with the verdict — a reader "
        "deciding whether to override needs to see WHICH defects"
    )


def test_an_open_critical_can_block_a_release_whose_tests_all_passed():
    """The reason the gate consults defects at all: the rollup answers from
    test results alone, so a release whose whole suite passes over a known open
    CRITICAL rolled up to GO."""
    summary = defect_svc.summarise_blocking([_Defect("CRITICAL", title="crash")])
    verdict, reasons = defect_svc.verdict_contribution(summary)
    assert verdict == "NO_GO"
    assert any("crash" in r for r in reasons)


def test_untriaged_defects_make_the_criterion_unevaluable_not_passing():
    """Severity is set by whoever filed the defect and is often absent on
    machine-created rows. A NULL severity is not a low one, and a gate that
    silently ignored unrated defects could be passed by leaving a field blank.
    """
    summary = defect_svc.summarise_blocking([_Defect(None, title="unrated")])
    verdict, reasons = defect_svc.verdict_contribution(summary)
    assert verdict == "NOT_EVALUATED"
    assert reasons
