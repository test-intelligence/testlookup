"""W25a Day-0 follow-up — ``suite_membership_reconcile`` and its in-pod script, without a database.

An in-memory project answers the reconcile's SELECT shapes and applies its INSERT / UPDATE statements, so the
plan can be read row by row and the parity matrix (``suite_membership_parity.classify``, unedited) re-run on the
result. The real-Postgres proof (compare_project → 0 blocking, an import end to end) is
``tests/integration/test_suite_membership_reconcile_postgres.py``.
"""
from __future__ import annotations

import asyncio
import inspect
import uuid
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional

import pytest
from sqlalchemy.sql import Insert, Select, Update

from app.models.postgres import SuiteMembership
from app.services import suite_membership_parity as parity
from app.services import suite_membership_reconcile as rec
from app.services import suite_sync_service

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "reconcile_legacy_suite_memberships.py"
T0 = datetime(2026, 8, 7, 0, 24, 57, tzinfo=timezone.utc)
LONG = "L" * 495                                   # "<LONG>-deleted" is 503 characters: wider than the column


class _Result:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return list(self._rows)

    def scalars(self) -> "_Result":
        return _Result([row[0] for row in self._rows])


class Project:
    """One project's rows in memory; ``execute`` answers the reconcile's four SELECT shapes and applies its writes."""

    def __init__(self) -> None:
        self.pid = uuid.uuid4()
        self.legacy: list[dict[str, Any]] = []
        self.canonical: list[dict[str, Any]] = []
        self.runs: dict[uuid.UUID, datetime] = {}
        self.managed: list[tuple[uuid.UUID, str, str]] = []
        self.statements: list[str] = []
        self.writes: list[str] = []

    async def execute(self, statement: Any, params: Any = None) -> _Result:
        self.statements.append(type(statement).__name__)
        if isinstance(statement, Select):
            table = statement.selected_columns[0].table.name
            if table == "suite_memberships":
                return _Result([(r["id"], r["suite_name"], r["test_fingerprint"], r["status"], r["test_name"],
                                 r["class_name"], r["first_seen_run_id"], r["last_seen_run_id"]) for r in self.legacy])
            if table == "canonical_test_cases":
                return _Result([(c["id"], c["fp"], c["status"], c["source"], c["suite"], c["is_default"], c["name"],
                                 None, c["first"], c["last"], c["deleted_at"]) for c in self.canonical])
            if table == "test_runs":
                return _Result(list(self.runs.items()))
            if table == "managed_test_cases":
                return _Result(sorted((i, fp) for i, fp, status in self.managed if status != "deprecated"))
            raise AssertionError(f"unexpected SELECT from {table}")
        if isinstance(statement, Insert):
            assert statement.table.name == "suite_memberships"
            self.writes.append("INSERT suite_memberships")
            self.legacy.extend(dict(values) for values in params)
            return _Result([])
        if isinstance(statement, Update):
            bound = statement.compile().params
            self.writes.append(f"UPDATE {statement.table.name}")
            rows = self.legacy if statement.table.name == "suite_memberships" else self.canonical
            [row] = [r for r in rows if r["id"] == bound["id_1"]]
            row["first_seen_run_id" if rows is self.legacy else "first"] = bound["first_seen_run_id"]
            return _Result([])
        raise AssertionError(f"unexpected statement {statement}")

    # seeding
    def run(self, at: datetime) -> uuid.UUID:
        run_id = uuid.uuid4()
        self.runs[run_id] = at
        return run_id

    def canon(self, fp: str, suite: str, *, status: str = "active", source: str = "execution", default: bool = False,
              first: Optional[uuid.UUID] = None, last: Optional[uuid.UUID] = None,
              deleted_at: Optional[uuid.UUID] = None, name: Optional[str] = None) -> None:
        self.canonical.append(dict(id=uuid.uuid4(), fp=fp, status=status, source=source, suite=suite, is_default=default,
                                   name=name or f"test_{fp}", first=first, last=last, deleted_at=deleted_at))

    def legacy_row(self, fp: str, suite: str, *, first: Optional[uuid.UUID], last: Optional[uuid.UUID],
                   status: str = "active", name: Optional[str] = None) -> None:
        self.legacy.append(dict(id=uuid.uuid4(), suite_name=suite, test_fingerprint=fp, status=status,
                                test_name=name or f"test_{fp}", class_name=None, first_seen_run_id=first,
                                last_seen_run_id=last))

    def classify(self) -> Counter[str]:
        legacy = [parity.LegacyRow(r["suite_name"], r["test_fingerprint"], r["status"], r["test_name"], r["class_name"],
                                   r["first_seen_run_id"], r["last_seen_run_id"]) for r in self.legacy]
        canonical = [parity.CanonicalRow(c["fp"], c["status"], c["source"], c["suite"], c["is_default"], c["name"], None,
                                         c["first"], c["last"]) for c in self.canonical]
        defaults = {c["suite"] for c in self.canonical if c["is_default"]}
        _keys, divergences = parity.classify(legacy, canonical, set(), defaults)
        return Counter(d.reason for d in divergences)


def _world() -> SimpleNamespace:
    p = Project()
    r1, r2 = p.run(T0), p.run(T0 + timedelta(seconds=1))
    ra, rb = sorted((p.run(T0 + timedelta(seconds=5)), p.run(T0 + timedelta(seconds=5))), key=str)   # created together
    linked_case = uuid.uuid4()
    p.managed += [(linked_case, "linked", "approved"), (uuid.uuid4(), "deprecatedcase", "deprecated")]
    for fp in ("plain", "linked", "deprecatedcase"):                         # (a) active, no legacy row anywhere
        p.canon(fp, "Smoke", first=r1, last=r2)
    p.canon("retired", "Regression", status="deleted", first=r1, last=r1, deleted_at=r2)       # (a) → the bucket
    p.canon("review", "Regression", status="needs_review", first=r1, last=r1, deleted_at=r2)   # (a) → the bucket
    p.canon("default", "Default Suite (P)", default=True, first=r1, last=r2)                   # EXPECTED default_suite
    p.canon("managed", "Smoke", source="managed")                                               # EXPECTED catalog_only
    p.canon("toolong", LONG, status="deleted", first=r1, last=r1)                               # unfit: bucket too wide
    p.canon("odd", "Smoke", source="unheard_of", first=r1, last=r2)                             # unfit: unknown source
    p.canon("moved", "Regression", first=r1, last=r2)                                           # EXPECTED moved_suite
    p.legacy_row("moved", "Smoke", first=r1, last=r2)
    p.canon("canonlater", "Smoke", first=r2, last=r2)                                           # (b) canonical is later
    p.legacy_row("canonlater", "Smoke", first=r1, last=r2)
    p.canon("legacylater", "Smoke", first=r1, last=r2)                                          # (b) legacy is later
    p.legacy_row("legacylater", "Smoke", first=r2, last=r2)
    p.canon("tie", "Smoke", first=ra, last=rb)                                                  # (b) same instant
    p.legacy_row("tie", "Smoke", first=rb, last=rb)
    p.canon("drift", "Smoke", first=r2, last=r2, name="test_renamed")                          # name_drift wins: untouched
    p.legacy_row("drift", "Smoke", first=r1, last=r2)
    return SimpleNamespace(p=p, r1=r1, r2=r2, ra=ra, rb=rb, linked_case=linked_case)


def _legacy(p: Project, fp: str) -> list[dict[str, Any]]:
    return [r for r in p.legacy if r["test_fingerprint"] == fp]


def _canonical(p: Project, fp: str) -> dict[str, Any]:
    [row] = [c for c in p.canonical if c["fp"] == fp]
    return row


EXPECTED_PLAN = {
    "legacy_inserted": 5, "legacy_inserted_removed": 2, "first_seen_aligned_legacy": 2,
    "first_seen_aligned_canonical": 1, "left_default_suite": 1, "left_catalog_only": 1, "left_unfit": 2,
}


async def test_a_dry_run_reads_and_counts_but_writes_nothing() -> None:
    w = _world()
    before = w.p.classify()
    assert before["missing_in_legacy"] == 7 and before["first_seen_differs"] == 3 and before["name_drift"] == 1
    legacy_before = [dict(r) for r in w.p.legacy]

    result = await rec.reconcile_project(w.p, w.p.pid, apply=False)

    assert (result.project_id, result.applied) == (w.p.pid, False)
    assert result.counts() == EXPECTED_PLAN and result.changes == 8
    assert w.p.writes == [] and set(w.p.statements) == {"Select"}
    assert w.p.legacy == legacy_before and w.p.classify() == before


async def test_apply_writes_the_legacy_writers_rows_and_aligns_first_seen_on_the_earlier_run() -> None:
    w = _world()
    expected_before = {r: n for r, n in w.p.classify().items() if r in ("default_suite", "catalog_only", "moved_suite")}

    result = await rec.reconcile_project(w.p, w.p.pid, apply=True)

    assert result.counts() == EXPECTED_PLAN and result.applied
    assert Counter(w.p.writes) == {"INSERT suite_memberships": 1, "UPDATE suite_memberships": 2,
                                   "UPDATE canonical_test_cases": 1}
    [plain] = _legacy(w.p, "plain")
    assert {k: plain[k] for k in ("project_id", "suite_name", "status", "review_tag", "source", "managed_test_case_id",
                                  "test_name", "class_name", "first_seen_run_id", "last_seen_run_id",
                                  "deleted_at_run_id")} == {
        "project_id": w.p.pid, "suite_name": "Smoke", "status": "active", "review_tag": None, "source": "execution",
        "managed_test_case_id": None, "test_name": "test_plain", "class_name": None, "first_seen_run_id": w.r1,
        "last_seen_run_id": w.r2, "deleted_at_run_id": None,
    }
    assert "tags" not in plain                                      # the legacy writer never sets custom tags
    [linked] = _legacy(w.p, "linked")
    assert (linked["source"], linked["managed_test_case_id"]) == ("linked", w.linked_case)
    [deprecated] = _legacy(w.p, "deprecatedcase")
    assert (deprecated["source"], deprecated["managed_test_case_id"]) == ("execution", None)
    for fp in ("retired", "review"):
        [row] = _legacy(w.p, fp)
        assert (row["suite_name"], row["status"], row["review_tag"], row["deleted_at_run_id"]) == (
            "Regression-deleted", "needs_review", "needs_review", w.r2)
    for fp in ("default", "managed", "toolong", "odd"):
        assert _legacy(w.p, fp) == [], fp                            # left alone

    assert _canonical(w.p, "canonlater")["first"] == w.r1           # the earlier sighting wins
    assert [r["first_seen_run_id"] for r in _legacy(w.p, "canonlater")] == [w.r1]
    assert [r["first_seen_run_id"] for r in _legacy(w.p, "legacylater")] == [w.r1]
    assert _canonical(w.p, "legacylater")["first"] == w.r1
    assert _canonical(w.p, "tie")["first"] == w.ra                  # same instant → the smaller id string
    assert [r["first_seen_run_id"] for r in _legacy(w.p, "tie")] == [w.ra]
    assert _canonical(w.p, "drift")["first"] == w.r2                # another reason's key is never touched
    assert [r["first_seen_run_id"] for r in _legacy(w.p, "drift")] == [w.r1]

    after = w.p.classify()
    blocking = {r: n for r, n in after.items() if r in parity.vocab.BLOCKING_REASONS}
    assert blocking == {"missing_in_legacy": 2, "name_drift": 1}    # exactly the two unfit keys + the drift
    assert {r: n for r, n in after.items() if r in ("default_suite", "catalog_only", "moved_suite")} == expected_before

    again = await rec.reconcile_project(w.p, w.p.pid, apply=True)   # idempotent
    assert again.changes == 0 and Counter(w.p.writes) == Counter(
        {"INSERT suite_memberships": 1, "UPDATE suite_memberships": 2, "UPDATE canonical_test_cases": 1})
    assert (again.left_default_suite, again.left_catalog_only, again.left_unfit) == (1, 1, 2)


async def test_a_run_without_a_time_leaves_the_pair_alone() -> None:
    p = Project()
    r1 = p.run(T0)
    gone = uuid.uuid4()                                            # not in test_runs
    p.canon("x", "Smoke", first=gone, last=r1)
    p.legacy_row("x", "Smoke", first=r1, last=r1)
    result = await rec.reconcile_project(p, p.pid, apply=True)
    assert (result.left_unfit, result.changes, p.writes) == (1, 0, [])


async def test_a_clean_project_issues_two_reads_and_nothing_else() -> None:
    p = Project()
    r1 = p.run(T0)
    p.canon("ok", "Smoke", first=r1, last=r1)
    p.legacy_row("ok", "Smoke", first=r1, last=r1)
    result = await rec.reconcile_project(p, p.pid, apply=True)
    assert result.changes == 0 and p.statements == ["Select", "Select"] and p.writes == []


def test_the_mirrored_values_are_the_legacy_writers_own() -> None:
    """If suite_sync_service's new-member or removal shape changes, the mirror must change with it."""
    source = inspect.getsource(suite_sync_service)
    assert 'source="linked" if managed_id else "execution"' in source
    assert 'ManagedTestCase.status != "deprecated"' in source
    assert 'member.status = "needs_review"' in source and 'member.review_tag = "needs_review"' in source
    assert 'deleted_bucket = f"{suite_name}-deleted"' in source
    assert (rec.LEGACY_LINKED, rec.MIRRORED_SOURCE, rec.MANAGED_RETIRED, rec.LEGACY_REMOVED, rec.LEGACY_ACTIVE) == (
        "linked", "execution", "deprecated", "needs_review", "active")
    assert rec.DELETED_BUCKET_SUFFIX == "-deleted"
    assert rec.SUITE_NAME_MAX == SuiteMembership.__table__.c.suite_name.type.length


def test_the_module_never_commits_or_writes_events_or_audit_rows() -> None:
    source = inspect.getsource(rec)
    code = source.split('"""', 2)[2]                                # past the module docstring
    for forbidden in (".commit(", ".rollback(", ".flush(", "SuiteMembershipEvent", "AuditLog", "record_activity"):
        assert forbidden not in code, forbidden


# ── the in-pod script ────────────────────────────────────────────────────────


def _load_script() -> dict[str, Any]:
    """The script as ``python - < script`` runs it: compiled from text, no file path, not as __main__."""
    text = SCRIPT.read_text(encoding="utf-8")
    assert "__file__" not in text
    namespace: dict[str, Any] = {"__name__": "reconcile_from_stdin"}
    exec(compile(text, "<stdin>", "exec"), namespace)              # noqa: S102 - our own script, read from the repo
    return namespace


class _Sessions:
    """A session factory that proves one session at a time and records commits / rollbacks per session."""

    def __init__(self, project_ids: list[uuid.UUID]) -> None:
        self.project_ids = project_ids
        self.open = self.peak = 0
        self.log: list[str] = []

    def __call__(self) -> "_Sessions._Session":
        return _Sessions._Session(self)

    class _Session:
        def __init__(self, owner: "_Sessions") -> None:
            self.owner = owner

        async def __aenter__(self) -> "_Sessions._Session":
            self.owner.open += 1
            self.owner.peak = max(self.owner.peak, self.owner.open)
            self.owner.log.append("open")
            return self

        async def __aexit__(self, *_: Any) -> None:
            self.owner.open -= 1
            self.owner.log.append("close")

        async def execute(self, statement: Any, params: Any = None) -> _Result:
            return _Result([(pid,) for pid in self.owner.project_ids])

        async def commit(self) -> None:
            self.owner.log.append("commit")

        async def rollback(self) -> None:
            self.owner.log.append("rollback")


@pytest.mark.parametrize("apply", [False, True])
def test_the_script_visits_each_project_in_its_own_session_and_prints_only_prefixes_and_counts(
    apply: bool, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    from app.db import postgres as app_postgres

    projects = [uuid.uuid4(), uuid.uuid4()]
    sessions = _Sessions(projects)
    seen: list[tuple[uuid.UUID, bool]] = []

    async def reconcile(db: Any, project_id: uuid.UUID, *, apply: bool) -> rec.ReconcileResult:
        seen.append((project_id, apply))
        return rec.ReconcileResult(project_id=project_id, applied=apply, legacy_inserted=3, left_default_suite=1)

    async def compare(db: Any, project_id: uuid.UUID, *, window_runs: int) -> Any:
        return SimpleNamespace(divergences=[SimpleNamespace(blocking=True, test_name="test_secret_name"),
                                            SimpleNamespace(blocking=False, test_name="test_other")])

    async def no_dispose() -> None:
        sessions.log.append("dispose")

    monkeypatch.setattr(app_postgres, "get_session_factory", lambda: sessions)
    monkeypatch.setattr(app_postgres, "dispose_engine_for_loop", no_dispose)
    monkeypatch.setattr(rec, "reconcile_project", reconcile)
    monkeypatch.setattr(parity, "compare_project", compare)
    script = _load_script()

    code = asyncio.run(script["main"](["--apply"] if apply else []))

    assert code == 0 and seen == [(pid, apply) for pid in projects]
    assert sessions.peak == 1                                      # one session at a time
    per_project = ["open", "commit" if apply else "rollback", "rollback", "close"]
    assert sessions.log == ["open", "close", *per_project, *per_project, "dispose"]
    out = capsys.readouterr().out.splitlines()
    mode = "apply" if apply else "dry-run"
    assert out[0] == f"mode={mode} projects=2"
    for line, pid in zip(out[1:3], projects):
        assert line.startswith(f"{str(pid)[:8]} legacy_inserted=3 ") and line.endswith(" blocking=1")
        assert str(pid) not in line
    assert out[3].startswith(f"TOTAL mode={mode} legacy_inserted=6 ") and out[3].endswith(" blocking=2")
    assert "secret" not in "\n".join(out)


def test_the_script_refuses_a_bad_project_id_and_documents_apply(capsys: pytest.CaptureFixture[str]) -> None:
    script = _load_script()
    with pytest.raises(SystemExit) as refused:
        asyncio.run(script["main"](["--project-id", "not-a-uuid"]))
    assert refused.value.code == 2
    with pytest.raises(SystemExit) as helped:
        asyncio.run(script["main"](["--help"]))
    assert helped.value.code == 0 and "--apply" in capsys.readouterr().out
