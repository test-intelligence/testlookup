"""S4 — report lifecycle: reports outlive their runs, but not forever.

"Storage of the reports" was the first thing the brief asked for, and reports
were managed by nothing: ``decision_reports`` and ``decision_report_attempts``
were the only two ``Collections`` members absent from the purge's plan table,
so nothing had ever deleted one.

The clock matters as much as the coverage. A report is evidence ABOUT a run —
purging it on the runs clock would destroy the evidence at the exact moment its
subject went, which is when it is most likely to be wanted.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("sqlalchemy")

from app.db.mongo import Collections  # noqa: E402
from app.services import retention_service as rs  # noqa: E402


def _plan_for(collection: str):
    matches = [p for p in rs._MONGO_PLANS if p[0] == collection]
    assert len(matches) == 1, f"{collection} appears {len(matches)} times in _MONGO_PLANS"
    return matches[0]


# ── coverage: the two collections nothing purged ─────────────────────────────


def test_every_mongo_collection_is_covered_by_the_purge():
    """Derived, not listed.

    ``decision_reports`` and ``decision_report_attempts`` went unpurged
    because nothing compared ``Collections`` against the plan table. A test
    naming today's gaps would have to be edited for tomorrow's; this one fails
    when a NEW collection is added without a retention decision.
    """
    declared = {
        value
        for name, value in vars(Collections).items()
        if not name.startswith("_") and isinstance(value, str)
    }
    planned = {plan[0] for plan in rs._MONGO_PLANS}

    # Collections deliberately outside the run-scoped purge, each with a reason.
    EXEMPT: set[str] = set()

    missing = declared - planned - EXEMPT
    assert not missing, (
        f"{sorted(missing)} are Mongo collections that no retention clock "
        "touches — they grow forever. Add a plan entry or an EXEMPT reason."
    )


@pytest.mark.parametrize(
    "collection",
    [Collections.DECISION_REPORTS, Collections.DECISION_REPORT_ATTEMPTS],
)
def test_reports_are_purged_on_the_audit_clock_not_the_runs_clock(collection):
    """The load-bearing decision of this slice.

    ``audit_days >= runs_days`` is an enforced invariant, so the audit window
    is strictly wider. Keying reports on ``purge_run_strs`` would delete them
    in the same sweep that deleted their runs.
    """
    _name, field, attr = _plan_for(collection)

    assert field == "test_run_id"
    assert attr == "audit_run_strs", (
        f"{collection} keys on {attr!r} — on the runs clock a report dies with "
        "its run, which is the opposite of what a report is for"
    )


def test_the_audit_run_set_is_wider_than_the_runs_set():
    """Not just a different attribute — a genuinely later cutoff.

    If both clocks resolved to the same runs, keying on either would look
    correct and the distinction would be decorative.
    """
    now = datetime(2026, 6, 1, tzinfo=timezone.utc)
    # Deliberately NOT skippable: an earlier version guessed the helper's name
    # and skipped when it was wrong, reporting success for an invariant it had
    # never evaluated.
    assert hasattr(rs, "compute_cutoffs"), "the cutoff helper was renamed"
    from types import SimpleNamespace

    # compute_cutoffs reads ATTRIBUTES, not dict keys.
    cutoffs = rs.compute_cutoffs(
        SimpleNamespace(
            raw_events_days=7, runs_days=30, artifacts_days=7, audit_days=365
        ),
        now,
    )

    assert cutoffs["audit"] < cutoffs["runs"], (
        "the audit cutoff must be EARLIER than the runs cutoff, so the set of "
        "runs past it is wider"
    )


def test_a_single_run_delete_takes_its_report_with_it():
    """The audit clock protects reports from the POLICY purge.

    It is not a reason to strand a report whose subject an operator
    deliberately deleted — that leaves a report about a run nobody can look up.
    """
    import inspect

    source = inspect.getsource(rs.resolve_run_candidates)
    assert "audit_run_strs=[str(run_id)]" in source, (
        "the run-scoped resolver leaves audit_run_strs empty, so an explicit "
        "single-run delete would leave its decision report behind forever"
    )


# ── the candidate set carries it ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_run_scoped_candidates_carry_the_report_scope():
    run_id = uuid.uuid4()

    class _R:
        @staticmethod
        def scalars():
            return type("S", (), {"all": staticmethod(lambda: [])})()

    class _DB:
        async def execute(self, *_a, **_kw):
            return _R()

    cand, _plan = await rs.resolve_run_candidates(
        _DB(), project_id=uuid.uuid4(), run_id=run_id, minio_prefix=None
    )

    assert cand.audit_run_strs == [str(run_id)]


def test_the_candidate_dataclass_declares_the_audit_run_scope():
    assert "audit_run_strs" in rs._Candidates.__dataclass_fields__


# ── the module header must say what it covers ────────────────────────────────


def test_the_header_table_names_the_report_collections():
    """The header is the first thing anyone reads about this service.

    It listed the covered stores while two collections went untouched; a
    reader auditing retention coverage from it would have concluded reports
    were handled.
    """
    doc = rs.__doc__ or ""
    assert "decision_report" in doc, (
        "decision reports are purged but the service header does not say so"
    )


# ── RET-D16: the table that no project-scoped purge can reach ────────────────


def test_the_eval_cycle_table_records_why_it_has_no_project_scope():
    """The epic says: give it a scope column, or document it as unreachable.
    Do not leave it undecided.

    The decision is that a scope column would be WRONG. A cycle evaluates the
    report-generation corpus across many projects; stamping one project_id on
    it would be false, and purging it with that project would destroy an
    attestation still describing live behaviour elsewhere.
    """
    from app.models.postgres import DecisionReportEvalCycle

    doc = DecisionReportEvalCycle.__doc__ or ""
    assert "RET-D16" in doc, "the decision is not recorded where a reader will find it"
    assert "project_id" in doc


def test_the_eval_cycle_table_really_has_no_project_scope():
    """If a scope column is ever added, the docstring above becomes false —
    fail here rather than let the two drift."""
    from app.models.postgres import DecisionReportEvalCycle

    columns = {c.name for c in DecisionReportEvalCycle.__table__.c}
    assert "project_id" not in columns and "test_run_id" not in columns, (
        "a scope column was added; RET-D16's recorded decision says that is "
        "wrong, so either revisit the decision or drop the column"
    )


def test_the_eval_cycle_growth_argument_rests_on_a_unique_cycle_key():
    """The bound is cadence, not a clock. That only holds while one row is
    written per cycle — a unique constraint is what enforces it."""
    from app.models.postgres import DecisionReportEvalCycle

    uniques = [
        c for c in DecisionReportEvalCycle.__table__.constraints
        if getattr(c, "name", "") == "uq_drec_cycle_key"
    ]
    assert uniques, (
        "cycle_key lost its unique constraint, so a cycle can now write many "
        "rows and the bounded-growth argument in the docstring no longer holds"
    )


# ── compliance packs: retire early, then delete ─────────────────────────────


class _PackResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _PackDB:
    def __init__(self, pack):
        self._pack = pack
        self.committed = 0
        self.added: list = []

    async def execute(self, *_a, **_kw):
        return _PackResult(self._pack)

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        pass

    async def commit(self):
        self.committed += 1


def _pack(expires_in_days=365, project_id=None):
    from types import SimpleNamespace

    return SimpleNamespace(
        id=uuid.uuid4(),
        project_id=project_id or uuid.uuid4(),
        release_id=uuid.uuid4(),
        minio_key="compliance/pack.zip",
        retention_expires_at=datetime.now(timezone.utc)
        + timedelta(days=expires_in_days),
    )


@pytest.mark.asyncio
async def test_a_pack_inside_its_retention_window_is_refused():
    """The window is the guard.

    Holds are S9 and do not exist; shipping a hold check now would be a branch
    nothing can trigger. `retention_expires_at` already exists, is already
    enforced by the nightly purge, and until now was decorative on the way in —
    nothing stopped a delete ignoring it.
    """
    from app.services import compliance_pack_lifecycle as lifecycle

    blockers = lifecycle.deletion_blockers(_pack(expires_in_days=365))

    assert blockers
    assert any("retention" in b.lower() for b in blockers)


@pytest.mark.asyncio
async def test_a_pack_past_its_retention_window_is_deletable():
    """The other half — a guard that refuses everything deletes nothing, and
    the seven-year default would make that indistinguishable from working."""
    from app.services import compliance_pack_lifecycle as lifecycle

    assert lifecycle.deletion_blockers(_pack(expires_in_days=-1)) == []


def test_retiring_early_sets_the_expiry_to_now_rather_than_deleting():
    """Retire-early hands the work to the purge that already does it.

    A parallel delete path would be a second implementation of the
    object-then-row ordering the nightly sweep already gets right (object
    first, so a failed object delete leaves the row for the next sweep to
    retry).
    """
    import inspect

    from app.services import compliance_pack_lifecycle as lifecycle

    source = inspect.getsource(lifecycle.retire_early)
    assert "retention_expires_at" in source
    assert "delete(" not in source, (
        "retire_early deletes directly instead of letting the purge do it"
    )


def test_the_lifecycle_service_stages_and_does_not_commit():
    """Transaction-boundary rule: the router owns the commit."""
    import inspect

    from app.services import compliance_pack_lifecycle as lifecycle

    for fn in (lifecycle.retire_early,):
        body = inspect.getsource(fn).replace(fn.__doc__ or "", "")
        assert "commit" not in body, f"{fn.__name__} owns a commit"


# ── the two routes ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_deleting_a_pack_inside_its_window_is_409(mocker):
    """The refusal names the expiry, so the operator knows what to do next."""
    from fastapi import HTTPException

    from app.routers.compliance_packs import delete_compliance_pack

    pack = _pack(expires_in_days=365)
    mocker.patch(
        "app.services.compliance_pack_lifecycle.get_pack_for_write",
        mocker.AsyncMock(return_value=pack),
    )
    mocker.patch(
        "app.routers.compliance_packs.resolve_project_scope", mocker.AsyncMock()
    )
    storage = mocker.patch("app.db.storage.get_storage_provider")

    with pytest.raises(HTTPException) as exc:
        await delete_compliance_pack(
            pack_id=pack.id,
            current_user=mocker.Mock(id=uuid.uuid4()),
            db=_PackDB(pack),
        )

    assert exc.value.status_code == 409
    assert "retention" in str(exc.value.detail).lower()
    storage.assert_not_called()


@pytest.mark.asyncio
async def test_a_retired_pack_can_then_be_deleted(mocker):
    """The two steps compose — otherwise retire-early is a dead end."""
    from app.routers.compliance_packs import delete_compliance_pack
    from app.services import compliance_pack_lifecycle as lifecycle

    pack = _pack(expires_in_days=365)
    lifecycle.retire_early(pack, reason="superseded")

    deleted: list = []

    class _DB(_PackDB):
        async def delete(self, obj):
            deleted.append(obj)

    mocker.patch(
        "app.services.compliance_pack_lifecycle.get_pack_for_write",
        mocker.AsyncMock(return_value=pack),
    )
    mocker.patch(
        "app.routers.compliance_packs.resolve_project_scope", mocker.AsyncMock()
    )
    provider = mocker.Mock()
    provider.delete_object = mocker.AsyncMock()
    mocker.patch("app.db.storage.get_storage_provider", return_value=provider)

    await delete_compliance_pack(
        pack_id=pack.id,
        current_user=mocker.Mock(id=uuid.uuid4()),
        db=_DB(pack),
    )

    assert deleted == [pack]
    # Object BEFORE row: a failed object delete must leave the row for the
    # next sweep rather than orphaning the ZIP.
    provider.delete_object.assert_awaited_once()


@pytest.mark.asyncio
async def test_retiring_requires_the_pack_id_typed_back(mocker):
    from fastapi import HTTPException

    from app.models.schemas import RetireCompliancePackRequest
    from app.routers.compliance_packs import retire_compliance_pack

    pack = _pack()
    mocker.patch(
        "app.services.compliance_pack_lifecycle.get_pack_for_write",
        mocker.AsyncMock(return_value=pack),
    )
    mocker.patch(
        "app.routers.compliance_packs.resolve_project_scope", mocker.AsyncMock()
    )

    with pytest.raises(HTTPException) as exc:
        await retire_compliance_pack(
            pack_id=pack.id,
            body=RetireCompliancePackRequest(
                confirmation_id=str(uuid.uuid4()), reason="wrong id"
            ),
            current_user=mocker.Mock(id=uuid.uuid4()),
            db=_PackDB(pack),
        )

    assert exc.value.status_code == 422


def test_no_hold_check_is_stubbed_before_holds_exist():
    """S9 adds it. A check against a table that does not exist would read as
    a working guard while never firing once — the defect this epic
    catalogues, shipped in the code that catalogues it."""
    import inspect

    from app.services import compliance_pack_lifecycle as lifecycle

    body = inspect.getsource(lifecycle.deletion_blockers).replace(
        lifecycle.deletion_blockers.__doc__ or "", ""
    )
    assert "hold" not in body.lower(), (
        "a hold check exists in code but no hold table does; it can never fire"
    )


# ── reports as their own storage line ───────────────────────────────────────


@pytest.mark.asyncio
async def test_report_bytes_are_their_own_line_not_folded_into_mongo(mocker):
    """"How much are my reports costing me" was the brief's first question.

    A single Mongo figure that also contains raw Allure payloads and pod
    events cannot answer it. Reports also ride a different clock — audit, not
    runs — so a reader comparing this line to the runs figure is comparing two
    retention windows, which is the comparison worth being able to make.
    """
    from app.services import storage_accounting_service as sas

    assert hasattr(sas, "_reports_footprint")
    assert set(sas._REPORT_COLLECTIONS) == {
        Collections.DECISION_REPORTS,
        Collections.DECISION_REPORT_ATTEMPTS,
    }
    # And they must NOT also be counted in the run-scoped bucket, or the
    # project total would double-count every report.
    assert not set(sas._REPORT_COLLECTIONS) & set(sas._RUN_SCOPED_COLLECTIONS), (
        "report collections appear in both buckets — the total double-counts"
    )


@pytest.mark.asyncio
async def test_an_unreachable_report_store_reports_unmeasured_not_zero():
    """0 and "could not look" are opposite findings, and this line exists to
    justify a deletion — a fabricated 0 argues for deleting nothing."""
    from app.services import storage_accounting_service as sas

    class _Broken:
        def __getitem__(self, _name):
            class _C:
                async def count_documents(self, _q):
                    raise ConnectionError("mongo down")

            return _C()

        async def command(self, *_a, **_kw):
            raise ConnectionError("mongo down")

    out = await sas._reports_footprint(uuid.uuid4(), [uuid.uuid4()], _Broken())

    assert out.measured is False
    assert out.bytes_ is None or out.bytes_ == 0
    assert out.unreachable_reason


@pytest.mark.asyncio
async def test_a_project_with_no_runs_reports_zero_reports_measured():
    """The other half: genuinely empty is a measurement and must say so."""
    from app.services import storage_accounting_service as sas

    out = await sas._reports_footprint(uuid.uuid4(), [], object())

    assert out.measured is True
    assert out.items == 0
