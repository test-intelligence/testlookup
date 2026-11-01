"""W25d HYG-04 phase 2: the cutover module (``services/suite_membership_cutover.py``), no database.

A0 cases (G-A0): the fixed vocabulary and sentences (contract §1.1); the PURE
``wire_status`` mapping (D-W25d-5); ``legacy_model_active`` driven through the
REAL ``feature_flags.is_enabled`` with only the flag store faked (instance-wide:
allow-lists ignored; a missing row is OFF; ``fresh=True`` skips the shared
cache; always the caller's session); ``sync_legacy_if_active`` bound to the
REAL ``sync_suite_membership`` signature (ON -> one call, OFF -> none, no
``try``).

G-A cases (§6.3): each route mapper's keys equal the legacy branch's dict
literal IN ORDER (read from the handler source with ``ast``); the canonical
mappers (``wire_status`` per row, ``review_tag``, string ids); the ``/changes``
merge (order, ``limit``, the ``run_id`` filter on both statements, the uuid5 id
stable per row and distinct per event type); the SQL twin of ``wire_status``;
the runner's R-0 skip (a fake session factory: OFF → ``{"skipped":
"cut_over"}`` and no claim; ON → the claim); ``cutover_state`` from fake rows;
the canonical reconciler's ``suite.sync_deleted`` line (OFF → one per suite +
ONE suite-name read; ON → none and no extra read).
"""
from __future__ import annotations

import ast
import inspect
import re
import textwrap
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.routers import test_management_exports as exports_router
from app.services import feature_flags as ff
from app.services import suite_membership_cutover as cutover

PROJECT = uuid.UUID("11111111-0000-4000-8000-000000000001")
OTHER_PROJECT = uuid.UUID("22222222-0000-4000-8000-000000000002")
RUN = uuid.UUID("33333333-0000-4000-8000-000000000003")
CONFIRMED_AT = datetime(2026, 11, 1, 10, 12, tzinfo=timezone.utc)
ROUTES = Path(__file__).resolve().parents[1] / "app" / "routers" / "test_management_exports.py"


# ── the vocabulary ───────────────────────────────────────────────────────────


def test_the_flag_key_and_the_wire_vocabulary():
    assert cutover.FEATURE_FLAG == "suite_membership_legacy_writes"
    assert cutover.WIRE_STATUSES == ("active", "deleted", "needs_review")
    assert cutover.DERIVED_EVENT_TYPES == ("added", "deleted")
    assert cutover.EVENT_ID_NAMESPACE == uuid.UUID("6f1d3c2e-25d0-4c4b-9a77-25d0c0ffee25")


def test_the_wire_statuses_are_the_membership_routes_query_pattern():
    """``/membership?status=`` keeps validating exactly the values the canonical branch can answer."""
    match = re.search(r'status: Optional\[str\] = Query\(None, pattern="\^\(([a-z_|]+)\)\$"\)', ROUTES.read_text(encoding="utf-8"))
    assert match, "the /membership status Query pattern moved"
    assert set(match.group(1).split("|")) == set(cutover.WIRE_STATUSES)


def test_the_fixed_sentences():
    assert cutover.NOT_READY_SENTENCE.format(streak=3, required=7) == "Not ready: day 3 of 7 with no differences."
    assert cutover.ALREADY_CUT_OVER_SENTENCE == "Already cut over: the old suite-membership tables are not written."
    assert cutover.COMPARISON_RUNNING_SENTENCE == "A comparison is running; cut over when it has finished."
    assert cutover.COMPARISON_RETIRED_SENTENCE == (
        "The comparison is retired: the old suite-membership tables are no longer written."
    )
    assert cutover.CUTOVER_REASON.format(streak=7) == "HYG-04 cutover after 7 clean UTC days"
    assert cutover.RETIRED_DETAIL == "Not seen in the project's recent runs; retired from the catalog."


# ── wire_status (pure) ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("status", "confirmed_at", "expected"),
    [
        ("active", None, "active"),
        ("active", CONFIRMED_AT, "active"),            # a stale confirmation never hides an active test
        ("deleted", None, "needs_review"),             # retired by the catalog, nobody confirmed yet
        ("deleted", CONFIRMED_AT, "deleted"),          # retired AND confirmed: reviewed
        ("needs_review", None, "needs_review"),
        ("needs_review", CONFIRMED_AT, "deleted"),
    ],
)
def test_wire_status(status, confirmed_at, expected):
    assert cutover.wire_status(status, confirmed_at) == expected


# ── legacy_model_active (the REAL is_enabled; only the flag store is faked) ──


class _FlagStore:
    """``_load_from_redis`` / ``_load_from_db`` / ``_store_in_redis`` fakes; nothing reaches Redis or Postgres."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, *, redis_row: dict | None, db_row: dict | None) -> None:
        self.redis_reads: list[str] = []
        self.db_reads: list[tuple[Any, str]] = []
        self.redis_writes: list[str] = []
        store = self

        async def _load_redis(key: str):
            store.redis_reads.append(key)
            return None if redis_row is None else {"key": key, **redis_row}

        async def _load_db(db, key: str):
            store.db_reads.append((db, key))
            return None if db_row is None else {"key": key, **db_row}

        async def _store(key: str, _flag):
            store.redis_writes.append(key)

        monkeypatch.setattr(ff, "_load_from_redis", _load_redis)
        monkeypatch.setattr(ff, "_load_from_db", _load_db)
        monkeypatch.setattr(ff, "_store_in_redis", _store)


def _row(**overrides: Any) -> dict:
    return {"enabled_global": True, "enabled_projects": [], "enabled_roles": [], "rollout_percent": 100, **overrides}


async def test_on_is_on_for_the_whole_instance_even_with_allow_lists(monkeypatch):
    """A cutover is instance-wide: a project or role allow-list never narrows it (``any_scope=True``)."""
    _FlagStore(monkeypatch, redis_row=_row(enabled_projects=[str(OTHER_PROJECT)], enabled_roles=["ADMIN"]), db_row=None)
    assert await cutover.legacy_model_active(object()) is True


async def test_off_when_the_kill_switch_is_off(monkeypatch):
    _FlagStore(monkeypatch, redis_row=_row(enabled_global=False), db_row=None)
    assert await cutover.legacy_model_active(object()) is False


async def test_a_missing_row_reads_as_off_on_the_callers_session(monkeypatch):
    store = _FlagStore(monkeypatch, redis_row=None, db_row=None)
    db = object()
    assert await cutover.legacy_model_active(db) is False
    assert store.db_reads == [(db, cutover.FEATURE_FLAG)]   # the caller's session, never a session of its own


async def test_fresh_skips_the_shared_cache_and_reads_the_row(monkeypatch):
    store = _FlagStore(monkeypatch, redis_row=_row(enabled_global=False), db_row=_row())
    db = object()
    assert await cutover.legacy_model_active(db, fresh=True) is True
    assert store.redis_reads == [] and store.redis_writes == []
    assert store.db_reads == [(db, cutover.FEATURE_FLAG)]


async def test_it_asks_is_enabled_deployment_wide_with_the_callers_session(monkeypatch):
    calls: list[tuple[tuple, dict]] = []

    async def _recorder(*args, **kwargs):
        calls.append((args, kwargs))
        return True

    monkeypatch.setattr(ff, "is_enabled", _recorder)
    db = object()
    assert await cutover.legacy_model_active(db) is True
    assert await cutover.legacy_model_active(db, fresh=True) is True
    assert calls == [
        ((cutover.FEATURE_FLAG,), {"db": db, "fresh": False, "any_scope": True}),
        ((cutover.FEATURE_FLAG,), {"db": db, "fresh": True, "any_scope": True}),
    ]


# ── sync_legacy_if_active (bound to the REAL writer signature) ───────────────


@pytest.fixture
def writer(monkeypatch):
    """Replace the legacy writer with a recorder whose calls must bind to the REAL signature."""
    from app.services import suite_sync_service

    real = inspect.signature(suite_sync_service.sync_suite_membership)
    calls: list[inspect.BoundArguments] = []

    async def _fake(*args, **kwargs):
        calls.append(real.bind(*args, **kwargs))
        return []

    monkeypatch.setattr(suite_sync_service, "sync_suite_membership", _fake)
    return calls


def _flag(monkeypatch, value: bool | Exception) -> list[dict]:
    seen: list[dict] = []

    async def _active(db, *, fresh: bool = False):
        seen.append({"db": db, "fresh": fresh})
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(cutover, "legacy_model_active", _active)
    return seen


async def test_on_runs_the_legacy_writer_once(monkeypatch, writer):
    db = object()
    seen = _flag(monkeypatch, True)
    assert await cutover.sync_legacy_if_active(db, PROJECT, RUN) is True
    assert seen == [{"db": db, "fresh": False}]
    assert len(writer) == 1
    assert dict(writer[0].arguments) == {"db": db, "project_id": PROJECT, "run_id": RUN}


async def test_off_never_runs_the_legacy_writer(monkeypatch, writer):
    _flag(monkeypatch, False)
    assert await cutover.sync_legacy_if_active(object(), PROJECT, RUN) is False
    assert writer == []


async def test_a_failing_flag_read_raises_into_the_callers_handling(monkeypatch, writer):
    """No ``try`` in the gate: ingestion's existing ``except`` (and finalize's ``_run_isolated``) handle it."""
    _flag(monkeypatch, RuntimeError("flag store down"))
    with pytest.raises(RuntimeError):
        await cutover.sync_legacy_if_active(object(), PROJECT, RUN)
    assert writer == []


async def test_a_failing_writer_raises_into_the_callers_handling(monkeypatch):
    from app.services import suite_sync_service

    async def _boom(db, project_id, run_id):
        raise LookupError("writer failed")

    monkeypatch.setattr(suite_sync_service, "sync_suite_membership", _boom)
    _flag(monkeypatch, True)
    with pytest.raises(LookupError):
        await cutover.sync_legacy_if_active(object(), PROJECT, RUN)


# ── G-A: the route mappers answer the legacy keys, in the legacy order ────────

FIRST_RUN = uuid.UUID("44444444-0000-4000-8000-000000000004")
CANON = uuid.UUID("55555555-0000-4000-8000-000000000005")
SUITE = "zz-cutover-suite"
CREATED = datetime(2026, 10, 30, 9, 30, tzinfo=timezone.utc)

HANDLERS = [
    (exports_router.get_suite_membership, cutover.MEMBERSHIP_KEYS),
    (exports_router.get_suite_changes, cutover.CHANGE_KEYS),
    (exports_router.get_suite_deleted, cutover.DELETED_KEYS),
]


def _legacy_dict_keys(handler: Any) -> tuple[str, ...]:
    """The keys of the ONE ``[{...} for row in rows]`` literal in the handler: the legacy branch's answer."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(handler)))
    literals = [n.elt for n in ast.walk(tree) if isinstance(n, ast.ListComp) and isinstance(n.elt, ast.Dict)]
    assert len(literals) == 1, f"{handler.__name__}: expected one dict-building comprehension"
    return tuple(key.value for key in literals[0].keys)  # type: ignore[union-attr]


@pytest.mark.parametrize(("handler", "keys"), HANDLERS, ids=[h.__name__ for h, _ in HANDLERS])
def test_the_canonical_keys_are_the_legacy_branchs_keys_in_order(handler, keys):
    assert _legacy_dict_keys(handler) == keys


@pytest.mark.parametrize(("handler", "keys"), HANDLERS, ids=[h.__name__ for h, _ in HANDLERS])
def test_scope_first_then_one_flag_question_then_the_legacy_query(handler, keys):
    """C-2: the scope guard runs first and unchanged; ONE predicate decides; the legacy query stays inline."""
    src = inspect.getsource(handler)
    assert src.count("legacy_model_active(db)") == 1
    guard = src.index("resolve_project_scope(")
    question = src.index("legacy_model_active(db)")
    legacy = src.index("select(SuiteMembership")
    assert guard < src.index("allowed is not None") < question < legacy


def _catalog(**overrides: Any) -> SimpleNamespace:
    row = dict(
        id=CANON, suite_name=SUITE, test_fingerprint="fp-gamma", test_name="test_gamma", class_name="Gamma",
        source="managed", status="active", retirement_confirmed_at=None, last_seen_run_id=RUN,
        first_seen_run_id=FIRST_RUN, managed_test_case_id=None, created_at=CREATED, deleted_at_run_id=None,
    )
    row.update(overrides)
    return SimpleNamespace(**row)


def test_the_membership_mapper_keys_and_values():
    row = cutover.membership_row(_catalog())
    assert tuple(row) == cutover.MEMBERSHIP_KEYS
    assert row == {
        "id": str(CANON), "suite_name": SUITE, "test_fingerprint": "fp-gamma", "test_name": "test_gamma",
        "class_name": "Gamma", "source": "managed", "status": "active", "review_tag": None,
        "last_seen_run_id": str(RUN), "first_seen_run_id": str(FIRST_RUN), "managed_test_case_id": None,
        "created_at": CREATED.isoformat(),
    }


@pytest.mark.parametrize(
    ("status", "confirmed", "wire", "tag"),
    [
        ("active", None, "active", None),
        ("deleted", None, "needs_review", "needs_review"),   # retired, nobody confirmed: the expander's list
        ("deleted", CONFIRMED_AT, "deleted", None),          # reviewed
    ],
)
def test_the_membership_mapper_answers_the_wire_status(status, confirmed, wire, tag):
    row = cutover.membership_row(_catalog(status=status, retirement_confirmed_at=confirmed))
    assert (row["status"], row["review_tag"]) == (wire, tag)


def test_the_deleted_mapper_keys_and_values():
    row = cutover.deleted_row(_catalog(status="deleted", deleted_at_run_id=RUN, first_seen_run_id=None), SUITE)
    assert tuple(row) == cutover.DELETED_KEYS
    assert row == {
        "id": str(CANON), "original_suite": SUITE, "test_fingerprint": "fp-gamma", "test_name": "test_gamma",
        "class_name": "Gamma", "status": "needs_review", "review_tag": "needs_review",
        "deleted_at_run_id": str(RUN), "last_seen_run_id": str(RUN), "created_at": CREATED.isoformat(),
    }


# ── G-A: /changes — derived events, their ids and the merge ─────────────────


def _event_row(canonical_id: uuid.UUID, at: datetime | None, *, run: uuid.UUID = RUN) -> SimpleNamespace:
    return SimpleNamespace(id=canonical_id, suite_name=SUITE, test_fingerprint=f"fp-{canonical_id.hex[:4]}z",
                           test_name="test_x", run_id=run, at=at)


def test_the_event_id_is_stable_per_row_and_distinct_per_event_type():
    assert cutover.event_id(CANON, "added") == cutover.event_id(str(CANON), "added")
    assert cutover.event_id(CANON, "added") != cutover.event_id(CANON, "deleted")
    assert cutover.event_id(CANON, "deleted") == str(uuid.uuid5(cutover.EVENT_ID_NAMESPACE, f"{CANON}:deleted"))


def test_a_derived_event_has_the_legacy_keys_and_only_derivable_types():
    added = cutover.change_event(_event_row(CANON, CREATED), "added")
    deleted = cutover.change_event(_event_row(CANON, CREATED), "deleted")
    assert tuple(added) == cutover.CHANGE_KEYS
    assert (added["event_type"], added["details"], added["old_values"], added["new_values"]) == ("added", None, None, None)
    assert (deleted["event_type"], deleted["details"]) == ("deleted", cutover.RETIRED_DETAIL)
    assert added["run_id"] == str(RUN) and added["created_at"] == CREATED.isoformat()
    with pytest.raises(ValueError):
        cutover.change_event(_event_row(CANON, CREATED), "modified")


def test_the_merge_orders_by_time_then_id_and_cuts_at_the_limit():
    ids = [uuid.UUID(f"{n}6666666-0000-4000-8000-00000000000{n}") for n in range(1, 6)]
    t1, t2, t3 = (CREATED + timedelta(hours=h) for h in (1, 2, 3))
    added = [_event_row(ids[0], t1), _event_row(ids[1], t2)]
    deleted = [_event_row(ids[2], t3), _event_row(ids[3], t2.replace(tzinfo=None)),   # naive reads as UTC
               _event_row(ids[4], None)]                                              # no time: last
    merged = cutover.merge_changes(added, deleted, 10)
    tie = sorted([cutover.event_id(ids[1], "added"), cutover.event_id(ids[3], "deleted")])
    assert [e["id"] for e in merged] == [
        cutover.event_id(ids[2], "deleted"), *tie, cutover.event_id(ids[0], "added"),
        cutover.event_id(ids[4], "deleted"),
    ]
    assert [e["id"] for e in cutover.merge_changes(added, deleted, 1)] == [cutover.event_id(ids[2], "deleted")]
    assert cutover.merge_changes(added, deleted, 0) == []


class _Result:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return list(self._rows)

    def scalars(self) -> SimpleNamespace:
        return SimpleNamespace(all=lambda: list(self._rows))


class _Session:
    """Answers each ``execute`` with the next scripted rows; records every statement."""

    def __init__(self, *results: list[Any]) -> None:
        self.statements: list[Any] = []
        self._results = list(results)
        self.info: dict = {}

    async def execute(self, stmt: Any, *args: Any, **kwargs: Any) -> _Result:
        self.statements.append(stmt)
        if not self._results:
            raise AssertionError(f"unexpected statement #{len(self.statements)}: {stmt}")
        return _Result(self._results.pop(0))


def _compiled(stmt: Any) -> tuple[str, dict]:
    from sqlalchemy.dialects import postgresql

    compiled = stmt.compile(dialect=postgresql.dialect())
    return str(compiled), dict(compiled.params)


async def test_changes_reads_two_statements_filters_both_by_run_and_merges():
    newer, older = uuid.UUID("77777777-0000-4000-8000-000000000007"), CANON
    db = _Session([_event_row(older, CREATED)], [_event_row(newer, CREATED + timedelta(minutes=5))])
    events = await cutover.canonical_changes(db, suite_name=SUITE, project_id=PROJECT, run_id=RUN, limit=1)
    assert [e["id"] for e in events] == [cutover.event_id(newer, "deleted")]
    (added_sql, added_params), (deleted_sql, deleted_params) = (_compiled(s) for s in db.statements)
    for sql, params in ((added_sql, added_params), (deleted_sql, deleted_params)):
        assert SUITE in params.values() and PROJECT in params.values() and RUN in params.values()
        assert "LIMIT" in sql and "JOIN test_suites" in sql and "LEFT OUTER JOIN test_runs" in sql
    assert "canonical_test_cases.first_seen_run_id = " in added_sql
    assert "canonical_test_cases.deleted_at_run_id = " in deleted_sql
    assert "canonical_test_cases.status != " in deleted_sql


async def test_changes_without_a_run_or_project_does_not_filter_on_them():
    db = _Session([], [])
    assert await cutover.canonical_changes(db, suite_name=SUITE, project_id=None, run_id=None, limit=5) == []
    for stmt in db.statements:
        sql, params = _compiled(stmt)
        assert RUN not in params.values() and "canonical_test_cases.project_id = " not in sql


# ── G-A: the SQL twin of wire_status, on the two list reads ─────────────────


@pytest.mark.parametrize(
    ("wanted", "present", "absent"),
    [
        (None, ["canonical_test_cases.status = "], ["retirement_confirmed_at"]),          # default: active
        ("active", ["canonical_test_cases.status = "], ["retirement_confirmed_at"]),
        ("needs_review", ["canonical_test_cases.status != ", "retirement_confirmed_at IS NULL"], []),
        ("deleted", ["canonical_test_cases.status != ", "retirement_confirmed_at IS NOT NULL"], []),
    ],
)
async def test_membership_filters_on_the_mapped_status(wanted, present, absent):
    db = _Session([_catalog()])
    rows = await cutover.canonical_membership(db, suite_name=SUITE, project_id=PROJECT, status=wanted)
    assert [r["id"] for r in rows] == [str(CANON)]
    sql, params = _compiled(db.statements[0])
    where = sql[sql.index("WHERE "):]
    for fragment in present:
        assert fragment in where
    for fragment in absent:
        assert fragment not in where
    assert "ORDER BY canonical_test_cases.test_name" in sql and SUITE in params.values()


async def test_deleted_lists_the_unconfirmed_retirements_newest_first():
    db = _Session([_catalog(status="deleted", deleted_at_run_id=RUN)])
    rows = await cutover.canonical_deleted(db, suite_name=SUITE, project_id=None)
    assert [r["original_suite"] for r in rows] == [SUITE]
    sql, _ = _compiled(db.statements[0])
    assert "retirement_confirmed_at IS NULL" in sql and "canonical_test_cases.status != " in sql
    assert "deleted_observed_at DESC NULLS LAST" in sql and "canonical_test_cases.project_id = " not in sql


def test_an_unknown_wire_status_is_refused():
    with pytest.raises(ValueError):
        cutover._wire_predicate("modified")


# ── G-A: the runner's R-0 skip ──────────────────────────────────────────────


class _RunnerSession:
    def __init__(self) -> None:
        self.added: list[Any] = []
        self.statements: list[Any] = []

    async def __aenter__(self) -> "_RunnerSession":
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        return False

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def execute(self, stmt: Any, *args: Any, **kwargs: Any) -> Any:
        self.statements.append(stmt)
        raise AssertionError("the runner issued a statement")

    async def commit(self) -> None:
        raise AssertionError("the runner committed")

    async def rollback(self) -> None:
        return None


class _Log:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def __getattr__(self, level: str) -> Any:
        return lambda event_name, **kw: self.calls.append((level, event_name, kw))


def _metrics() -> dict[str, float]:
    from app.core.metrics import suite_parity_runs_total

    return {s: float(suite_parity_runs_total.labels(status=s)._value.get()) for s in ("clean", "diffs", "failed")}


@pytest.fixture
def runner(monkeypatch):
    from app.worker import suite_parity_runner

    log = _Log()
    monkeypatch.setattr(suite_parity_runner, "_slog", log)
    return SimpleNamespace(module=suite_parity_runner, log=log)


def _runner_flag(monkeypatch, runner, value: bool | Exception) -> list[dict]:
    seen: list[dict] = []

    async def _active(db, *, fresh: bool = False):
        seen.append({"db": db, "fresh": fresh})
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(runner.module, "legacy_model_active", _active)
    return seen


async def test_once_cut_over_the_runner_skips_with_no_row_and_no_metric(monkeypatch, runner):
    session = _RunnerSession()
    seen = _runner_flag(monkeypatch, runner, False)
    before = _metrics()
    result = await runner.module.run_parity("beat", session_factory=lambda: session)
    assert result == {"skipped": "cut_over"}
    assert seen == [{"db": session, "fresh": True}]                    # the row itself, on the runner's ONE session
    assert session.added == [] and session.statements == []           # no claim, no INSERT attempted
    assert _metrics() == before
    assert runner.log.calls == [("info", "suite_membership_parity_skipped", {"reason": "cut_over", "trigger": "beat"})]


async def test_while_legacy_writes_are_on_the_runner_proceeds_to_the_claim(monkeypatch, runner):
    session = _RunnerSession()
    _runner_flag(monkeypatch, runner, True)
    claims: list[tuple] = []

    async def _claim(db, trigger, requested_by_id, window_runs):
        claims.append((db, trigger, requested_by_id))
        return None                                                     # another run holds the claim

    monkeypatch.setattr(runner.module, "_claim", _claim)
    result = await runner.module.run_parity("admin", RUN, session_factory=lambda: session)
    assert result == {"skipped": "already_running"}
    assert claims == [(session, "admin", RUN)]


async def test_a_failing_flag_read_is_no_claim_and_never_raises_out(monkeypatch, runner):
    session = _RunnerSession()
    _runner_flag(monkeypatch, runner, ConnectionError("pg at db-Xq refused"))
    before = _metrics()
    result = await runner.module.run_parity("beat", session_factory=lambda: session)
    assert result == {"status": "failed", "error_code": "ConnectionError"}
    assert session.added == [] and _metrics()["failed"] == before["failed"] + 1
    assert "db-Xq" not in repr(runner.log.calls)


# ── G-A: cutover_state from fake rows ───────────────────────────────────────

CUT = datetime(2026, 11, 6, 10, 12, tzinfo=timezone.utc)


def _audit(action: str, fields: list[str] | None, at: datetime, name: str = "ada") -> SimpleNamespace:
    return SimpleNamespace(action=action, actor_name=name, changed_fields=fields, created_at=at)


def _clean_week(end: datetime) -> list[SimpleNamespace]:
    days = [end.replace(hour=6, minute=15) - timedelta(days=n) for n in range(7)]
    return [SimpleNamespace(status="clean", started_at=d, requested_by_id=None) for d in days]


def _legacy(monkeypatch, value: bool) -> list[Any]:
    seen: list[Any] = []

    async def _active(db, *, fresh: bool = False):
        seen.append(db)
        return value

    monkeypatch.setattr(cutover, "legacy_model_active", _active)
    return seen


async def test_cutover_state_while_on_is_all_nulls_and_reads_nothing_more(monkeypatch):
    db = _Session()
    seen = _legacy(monkeypatch, True)
    state = await cutover.cutover_state(db, now=CUT)
    assert state == {"legacy_writes": True, "cut_over_at": None, "cut_over_by_name": None,
                     "streak_days_at_cutover": None}
    assert seen == [db] and db.statements == []


async def test_cutover_state_off_takes_the_newest_enabled_global_update(monkeypatch):
    rows = [
        _audit("update", ["description"], CUT + timedelta(days=1), name="lee"),    # newer, not the switch
        _audit("update", ["enabled_global"], CUT),                                  # the change TO off
        _audit("update", ["enabled_global"], CUT - timedelta(days=30), name="old"),
    ]
    db = _Session(rows, _clean_week(CUT))
    _legacy(monkeypatch, False)
    state = await cutover.cutover_state(db, now=CUT + timedelta(days=2))
    assert state == {"legacy_writes": False, "cut_over_at": CUT, "cut_over_by_name": "ada",
                     "streak_days_at_cutover": 7}
    audit_sql, audit_params = _compiled(db.statements[0])
    assert "feature_flag:suite_membership_legacy_writes" in audit_params.values()
    assert "ORDER BY settings_audit_log.created_at DESC" in audit_sql and 20 in audit_params.values()
    runs_sql, runs_params = _compiled(db.statements[1])
    assert "suite_membership_parity_runs.started_at < " in runs_sql and CUT in runs_params.values()


@pytest.mark.parametrize(
    "rows",
    [
        [],                                                                          # flipped outside the audit trail
        [_audit("update", ["description"], CUT)],                                    # only a description edit
        [_audit("delete", ["enabled_global", "key"], CUT), _audit("update", ["enabled_global"], CUT)],  # deleted row
    ],
    ids=["no-row", "description-only", "newest-is-a-delete"],
)
async def test_cutover_state_off_without_a_cutover_row_has_no_date(monkeypatch, rows):
    db = _Session(rows)
    _legacy(monkeypatch, False)
    state = await cutover.cutover_state(db, now=CUT)
    assert state == {"legacy_writes": False, "cut_over_at": None, "cut_over_by_name": None,
                     "streak_days_at_cutover": None}
    assert len(db.statements) == 1                                     # no runs read without a cut-over date


async def test_a_naive_audit_time_reads_as_utc(monkeypatch):
    db = _Session([_audit("update", ["enabled_global"], CUT.replace(tzinfo=None))], [])
    _legacy(monkeypatch, False)
    state = await cutover.cutover_state(db, now=CUT)
    assert state["cut_over_at"] == CUT and state["streak_days_at_cutover"] == 0


# ── G-A: the canonical reconciler's suite.sync_deleted line (C-5) ───────────


SUITE_A = uuid.UUID("88888888-0000-4000-8000-00000000000a")
SUITE_B = uuid.UUID("99999999-0000-4000-8000-00000000000b")


def _active_case(fp: str, suite_id: uuid.UUID) -> SimpleNamespace:
    return SimpleNamespace(test_fingerprint=fp, status="active", test_suite_id=suite_id, deleted_at_run_id=None,
                           deleted_observed_at=None, retirement_confirmed_at=None,
                           retirement_confirmed_by_id=None, retirement_reason=None)


@pytest.fixture
def recorded(monkeypatch) -> list[dict]:
    """``activity.service.record`` replaced by a recorder bound to its REAL signature."""
    from app.services.activity import service as activity

    real = inspect.signature(activity.record)
    calls: list[dict] = []

    async def _record(*args: Any, **kwargs: Any) -> None:
        calls.append(dict(real.bind(*args, **kwargs).arguments))

    monkeypatch.setattr(activity, "record", _record)
    return calls


def _sweep_session(*, with_names: bool) -> _Session:
    cases = [_active_case("fp-kept", SUITE_A), _active_case("fp-gone-one", SUITE_A),
             _active_case("fp-gone-two", SUITE_A), _active_case("fp-gone-three", SUITE_B)]
    results: list[list[Any]] = [[(RUN,)], [("fp-kept",)], cases]
    if with_names:
        results.append([(SUITE_B, "zz-beta"), (SUITE_A, "zz-alpha")])
    return _Session(*results)


async def test_off_the_reconciler_records_one_line_per_suite_with_one_name_read(monkeypatch, recorded):
    from app.services import test_suite_service

    db = _sweep_session(with_names=True)
    seen = _legacy(monkeypatch, False)
    counts = await test_suite_service.reconcile_canonical_deletions(db, PROJECT, window_runs=5)
    assert counts == {"deleted": 3, "unchanged": 1, "window_size": 1}
    assert seen == [db]                                                 # the caller's session
    assert len(db.statements) == 4                                      # the sweep's three + ONE suite-name read
    names_sql, _ = _compiled(db.statements[3])
    assert "FROM test_suites" in names_sql and "test_suites.id IN" in names_sql
    assert [(c["entity_label"], c["context"]) for c in recorded] == [
        ("zz-alpha", {"count": 2, "run_id": str(RUN)}),
        ("zz-beta", {"count": 1, "run_id": str(RUN)}),
    ]
    for call in recorded:
        assert call["db"] is db and call["project_id"] == PROJECT and call["event_type"] == "suite.sync_deleted"
        assert (call["actor"].actor_type, call["actor"].actor_ref) == ("system", "canonical_reconcile")
    assert [c["entity_id"] for c in recorded] == [SUITE_A, SUITE_B]


async def test_on_the_reconciler_records_nothing_and_reads_nothing_more(monkeypatch, recorded):
    """While legacy writes are ON the legacy writer records the line: no double line in the dual-write window."""
    from app.services import test_suite_service

    db = _sweep_session(with_names=False)
    _legacy(monkeypatch, True)
    counts = await test_suite_service.reconcile_canonical_deletions(db, PROJECT, window_runs=5)
    assert counts["deleted"] == 3
    assert recorded == [] and len(db.statements) == 3


async def test_a_sweep_that_retires_nothing_never_asks_the_flag(monkeypatch, recorded):
    from app.services import test_suite_service

    db = _Session([(RUN,)], [("fp-kept",)], [_active_case("fp-kept", SUITE_A)])
    seen = _legacy(monkeypatch, False)
    counts = await test_suite_service.reconcile_canonical_deletions(db, PROJECT, window_runs=5)
    assert counts["deleted"] == 0 and seen == [] and recorded == []
