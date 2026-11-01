"""W25d HYG-04 phase 2 — the cutover against a real PostgreSQL (contract §2.1 "The golden test", §6.3 G-A).

ONE project of this module's own (non-hex sentinel names) holds both models in
the state D26's matrix calls equal — asserted FIRST with the W25a harness
(``compare_project`` → no blocking divergence): suite S + the project's default
suite; runs R1, R2, R3; canonical A1, A2 active (first R1, last R3), X1 retired
and unconfirmed (``deleted_at_run_id = R3``), X2 retired AND confirmed; legacy S
rows A1, A2 active with the same pointers, ``S-deleted`` rows X1, X2
``needs_review`` (``deleted_at_run_id = R3``); legacy events ``added`` × 4 at
R1, ``deleted`` × 2 at R3 and one ``modified``.

1. **The golden test.** The three REAL Test Management handlers are called
   twice — ``legacy_model_active`` monkeypatched True, then False (no flag row
   is written for this part) — and every documented difference is asserted BY
   NAME: the same keys and value types in both states; ``/membership`` equal;
   ``/deleted`` canonical = legacy MINUS X2 (confirmed is reviewed); ``/changes``
   canonical = legacy restricted to ``added`` / ``deleted`` (``modified`` is not
   derivable), ``run_id=R3`` filters both alike, ``limit=1`` returns one; the
   coverage map's ``needs_review_count`` is 2 legacy (X1, X2) and 1 canonical (X1).
2. **The REAL cutover on the shared database.** The real POST handler, with
   this module's own instance admin and its own clean parity rows: 3 clean
   days → 409 "Not ready: day 3 of 7 …"; 7 → 200; the flag row reads OFF
   (``fresh``); the real GET answers ``cutover.legacy_writes == false`` with
   ``cut_over_at``, the admin's name and ``streak_days_at_cutover == 7``; a
   second POST → 409 already; "Compare now" → 409 retired.

CI runs every ``*_postgres.py`` module in ONE database, and the flag is
instance-wide. TEARDOWN (always, in ``finally``) restores the flag row to the
values it had before (ON, its ``updated_at`` and ``updated_by_user_id``), deletes
the ``settings_audit_log`` rows this module's admin wrote, drops the flag's
Redis cache entry and proves the flag reads ON again on a fresh read; then the
module's parity rows, catalog rows, project (suites, runs, cases, memberships
and events cascade) and user go — counted afterwards, a leak fails THIS module.
``dispose_engine_for_loop()`` and ``close_redis()`` run before the engine is
built and after it is disposed. Run through ``pg_pytest_w25d.sh a …``
(``df_w25d_a`` at head = 0251).
"""
from __future__ import annotations

import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytest.importorskip("asyncpg")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

_NON_HEX = "ghijkmnpqrstuvwxyz"
_CLASS = "zz.golden.Case"


def _dsn() -> str:
    value = os.environ.get("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


def _tag() -> str:
    return "".join(secrets.choice(_NON_HEX) for _ in range(8))


async def _reset_app_clients() -> None:
    from app.db import postgres as app_postgres
    from app.db.redis_client import close_redis

    try:
        await close_redis()
    finally:
        await app_postgres.dispose_engine_for_loop()


@pytest_asyncio.fixture(loop_scope="module", scope="module")
async def env() -> Any:
    await _reset_app_clients()
    engine = create_async_engine(_dsn(), pool_size=2, max_overflow=1, pool_timeout=10)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield sessions
    finally:
        await engine.dispose()
        await _reset_app_clients()


async def _remove(env: Any, w: SimpleNamespace) -> None:
    """Everything this module wrote, in FK order; raises on a leak (a leak must fail THIS module)."""
    from app.models import postgres as m

    async with env() as db:
        await db.execute(delete(m.SuiteMembershipParityRun).where(
            m.SuiteMembershipParityRun.id.in_(w.parity_ids or {uuid.uuid4()})))
        await db.execute(delete(m.SettingsAuditLog).where(m.SettingsAuditLog.actor_id == w.admin.id))
        await db.execute(delete(m.CanonicalTestCase).where(m.CanonicalTestCase.project_id == w.project))
        await db.execute(delete(m.Project).where(m.Project.id == w.project))   # suites, runs, memberships cascade
        await db.execute(delete(m.User).where(m.User.id == w.admin.id))
        await db.commit()
    async with env() as db:
        left = 0
        for model in (m.SuiteMembership, m.SuiteMembershipEvent, m.TestSuite, m.TestRun, m.CanonicalTestCase):
            left += int((await db.execute(
                select(func.count()).select_from(model).where(model.project_id == w.project))).scalar_one())
        left += int((await db.execute(select(func.count()).select_from(m.SuiteMembershipParityRun).where(
            m.SuiteMembershipParityRun.id.in_(w.parity_ids or {uuid.uuid4()})))).scalar_one())
        left += int((await db.execute(select(func.count()).select_from(m.User).where(
            m.User.id == w.admin.id))).scalar_one())
    assert left == 0, f"{left} rows of this module survived its teardown"


@pytest_asyncio.fixture(loop_scope="module", scope="module")
async def world(env: Any) -> Any:
    from app.models import postgres as m

    tag = _tag()
    now = datetime.now(timezone.utc)
    w = SimpleNamespace(tag=tag, project=uuid.uuid4(), parity_ids=set(), now=now)
    w.suite = f"zz-golden-suite-{tag}"
    w.fp = {key: f"zzgold{key}{tag}" for key in ("a1", "a2", "x1", "x2")}
    w.name = {key: f"test_golden_{key}" for key in ("a1", "a2", "x1", "x2")}
    w.admin = m.User(id=uuid.uuid4(), username=f"zz-cut-admin-{tag}", email=f"cut-{tag}@cutover.example.test",
                     full_name="Cutover Admin", hashed_password="!" + secrets.token_urlsafe(16),
                     role=m.UserRole.ADMIN, is_active=True)
    try:
        async with env() as db:
            db.add(w.admin)
            db.add(m.Project(id=w.project, name=f"Cutover {tag}", slug=f"cutover-{tag}", is_active=True))
            await db.flush()
            suite = m.TestSuite(project_id=w.project, name=w.suite)
            default = m.TestSuite(project_id=w.project, name=f"Default Suite (Cutover {tag})", is_default=True)
            db.add_all([suite, default])
            runs = [m.TestRun(project_id=w.project, build_number=f"cutover-{tag}-{n}", status="PASSED",
                              created_at=now - timedelta(hours=4 - n)) for n in (1, 2, 3)]
            db.add_all(runs)
            await db.flush()
            r1, r2, r3 = runs
            w.r1, w.r2, w.r3 = r1.id, r2.id, r3.id
            for run, keys in ((r1, ("a1", "a2", "x1", "x2")), (r2, ("a1", "a2", "x1", "x2")), (r3, ("a1", "a2"))):
                for key in keys:
                    db.add(m.TestCase(test_run_id=run.id, test_name=w.name[key], class_name=_CLASS,
                                      status="PASSED", test_fingerprint=w.fp[key]))
            # The catalog: A1, A2 active; X1 retired, unconfirmed; X2 retired AND confirmed.
            for key in ("a1", "a2"):
                db.add(m.CanonicalTestCase(project_id=w.project, test_suite_id=suite.id, test_fingerprint=w.fp[key],
                                           test_name=w.name[key], class_name=_CLASS, status="active",
                                           source="execution", first_seen_run_id=r1.id, last_seen_run_id=r3.id))
            for key, confirmed in (("x1", None), ("x2", now - timedelta(minutes=10))):
                db.add(m.CanonicalTestCase(
                    project_id=w.project, test_suite_id=suite.id, test_fingerprint=w.fp[key], test_name=w.name[key],
                    class_name=_CLASS, status="deleted", source="execution", first_seen_run_id=r1.id,
                    last_seen_run_id=r2.id, deleted_at_run_id=r3.id, deleted_observed_at=now - timedelta(minutes=30),
                    retirement_confirmed_at=confirmed,
                    retirement_confirmed_by_id=w.admin.id if confirmed else None,
                ))
            # The legacy model: S rows A1, A2 active; the S-deleted bucket rows X1, X2 needs_review.
            for key in ("a1", "a2"):
                db.add(m.SuiteMembership(project_id=w.project, suite_name=w.suite, test_fingerprint=w.fp[key],
                                         test_name=w.name[key], class_name=_CLASS, source="execution",
                                         status="active", first_seen_run_id=r1.id, last_seen_run_id=r3.id))
            for key in ("x1", "x2"):
                db.add(m.SuiteMembership(project_id=w.project, suite_name=f"{w.suite}-deleted",
                                         test_fingerprint=w.fp[key], test_name=w.name[key], class_name=_CLASS,
                                         source="execution", status="needs_review", review_tag="needs_review",
                                         first_seen_run_id=r1.id, last_seen_run_id=r2.id, deleted_at_run_id=r3.id))
            for key in ("a1", "a2", "x1", "x2"):
                db.add(m.SuiteMembershipEvent(project_id=w.project, suite_name=w.suite, test_fingerprint=w.fp[key],
                                              test_name=w.name[key], event_type="added", run_id=r1.id))
            for key in ("x1", "x2"):
                db.add(m.SuiteMembershipEvent(
                    project_id=w.project, suite_name=w.suite, test_fingerprint=w.fp[key], test_name=w.name[key],
                    event_type="deleted", run_id=r3.id,
                    details=f"Test absent from latest run; moved to {w.suite}-deleted",
                ))
            db.add(m.SuiteMembershipEvent(project_id=w.project, suite_name=w.suite, test_fingerprint=w.fp["a1"],
                                          test_name=w.name["a1"], event_type="modified", run_id=r2.id,
                                          old_values={"class_name": "zz.golden.Old"},
                                          new_values={"class_name": _CLASS}))
            await db.commit()
        yield w
    finally:
        await _remove(env, w)


async def _routes(env: Any, w: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, legacy: bool) -> dict[str, Any]:
    """The three REAL handlers (+ the coverage map) with the switch pinned; every Query argument passed."""
    from app.routers import test_management_exports as exports
    from app.services import coverage_map_service as coverage
    from app.services import suite_membership_cutover as cutover
    from app.services.analytics_scope import AnalyticsScope

    async def _pinned(db: Any, *, fresh: bool = False) -> bool:
        return legacy

    monkeypatch.setattr(cutover, "legacy_model_active", _pinned)
    scope = AnalyticsScope(project_id=w.project, allowed_project_ids=None, release_ids=(), suite_names=(), days=30)
    async with env() as db:
        out = {
            "membership": await exports.get_suite_membership(
                suite_name=w.suite, project_id=w.project, status=None, db=db, current_user=w.admin),
            "needs_review": await exports.get_suite_membership(
                suite_name=w.suite, project_id=w.project, status="needs_review", db=db, current_user=w.admin),
            "confirmed": await exports.get_suite_membership(
                suite_name=w.suite, project_id=w.project, status="deleted", db=db, current_user=w.admin),
            "changes": await exports.get_suite_changes(
                suite_name=w.suite, run_id=None, project_id=w.project, limit=50, db=db, current_user=w.admin),
            "changes_r3": await exports.get_suite_changes(
                suite_name=w.suite, run_id=w.r3, project_id=w.project, limit=50, db=db, current_user=w.admin),
            "changes_one": await exports.get_suite_changes(
                suite_name=w.suite, run_id=None, project_id=w.project, limit=1, db=db, current_user=w.admin),
            "deleted": await exports.get_suite_deleted(
                suite_name=w.suite, project_id=w.project, db=db, current_user=w.admin),
        }
        tree = await coverage.build_coverage_map(db, scope, coverage.CoverageLevel(1), now=w.now)
        await db.rollback()
    out["coverage_root"] = next(n for n in tree["nodes"] if n["id"] == coverage.ROOT_ID)["stats"]
    out["coverage_definition"] = tree["definitions"]["needs_review"]
    return out


def _types(row: dict[str, Any]) -> dict[str, str]:
    return {key: type(value).__name__ for key, value in row.items()}


def _by_fp(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {row["test_fingerprint"]: row for row in rows}


async def test_the_seeded_models_agree_as_d26s_matrix_defines(env: Any, world: Any) -> None:
    from app.core.config import settings
    from app.services import suite_membership_parity as parity

    async with env() as db:
        result = await parity.compare_project(db, world.project, window_runs=settings.CANONICAL_DELETION_WINDOW_RUNS)
        await db.rollback()
    assert result.keys == 4
    assert [d for d in result.divergences if d.blocking] == []


async def test_the_golden_test_both_states_through_the_real_handlers(
    env: Any, world: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import coverage_map_service as coverage
    from app.services import suite_membership_cutover as cutover

    legacy = await _routes(env, world, monkeypatch, legacy=True)
    canonical = await _routes(env, world, monkeypatch, legacy=False)
    fp = world.fp

    # Every row of every route: the SAME keys, in the same order, in both states.
    for route, keys in (("membership", cutover.MEMBERSHIP_KEYS), ("changes", cutover.CHANGE_KEYS),
                        ("deleted", cutover.DELETED_KEYS)):
        for state in (legacy, canonical):
            assert state[route], route
            assert {tuple(row) for row in state[route]} == {keys}, route

    # /membership (status=active): the projections are equal; so are the value types.
    projected = ("suite_name", "test_fingerprint", "test_name", "class_name", "source", "status", "review_tag",
                 "first_seen_run_id", "last_seen_run_id", "managed_test_case_id")

    def _membership(rows: list[dict[str, Any]]) -> list[tuple]:
        return sorted(tuple(row[k] for k in projected) for row in rows)

    assert _membership(canonical["membership"]) == _membership(legacy["membership"])
    # ORDER BY test_name in both states (test_golden_a1 < test_golden_a2).
    assert [r["test_fingerprint"] for r in canonical["membership"]] == [fp["a1"], fp["a2"]]
    assert [r["test_fingerprint"] for r in legacy["membership"]] == [fp["a1"], fp["a2"]]
    assert _types(_by_fp(canonical["membership"])[fp["a1"]]) == _types(_by_fp(legacy["membership"])[fp["a1"]])

    # /deleted: canonical = legacy MINUS X2 — a confirmed retirement is reviewed (documented difference).
    deleted_projection = ("original_suite", "test_fingerprint", "test_name", "class_name", "status", "review_tag",
                          "deleted_at_run_id", "last_seen_run_id")

    def _deleted(rows: list[dict[str, Any]]) -> set[tuple]:
        return {tuple(row[k] for k in deleted_projection) for row in rows}

    legacy_minus_x2 = {row for row in _deleted(legacy["deleted"]) if row[1] != fp["x2"]}
    assert {r["test_fingerprint"] for r in legacy["deleted"]} == {fp["x1"], fp["x2"]}
    assert _deleted(canonical["deleted"]) == legacy_minus_x2
    assert _types(canonical["deleted"][0]) == _types(_by_fp(legacy["deleted"])[fp["x1"]])
    # ... and X2 answers status=deleted on /membership instead; X1 is the needs_review one (documented).
    assert [r["test_fingerprint"] for r in canonical["confirmed"]] == [fp["x2"]]
    assert [r["test_fingerprint"] for r in canonical["needs_review"]] == [fp["x1"]]
    assert canonical["needs_review"][0]["review_tag"] == "needs_review"

    # /changes: canonical = legacy restricted to added / deleted; "modified" is not derivable (documented).
    def _events(rows: list[dict[str, Any]], *, only_derivable: bool = False) -> list[tuple]:
        kept = [r for r in rows if not only_derivable or r["event_type"] in cutover.DERIVED_EVENT_TYPES]
        return sorted((r["event_type"], r["test_fingerprint"], r["test_name"], r["run_id"]) for r in kept)

    assert "modified" in {r["event_type"] for r in legacy["changes"]}
    assert "modified" not in {r["event_type"] for r in canonical["changes"]}
    assert _events(canonical["changes"]) == _events(legacy["changes"], only_derivable=True)
    assert len(canonical["changes"]) == 6
    for kind in cutover.DERIVED_EVENT_TYPES:
        one_canonical = next(r for r in canonical["changes"] if r["event_type"] == kind)
        one_legacy = next(r for r in legacy["changes"] if r["event_type"] == kind)
        assert _types(one_canonical) == _types(one_legacy), kind
    retired = [r for r in canonical["changes"] if r["event_type"] == "deleted"]
    assert {r["details"] for r in retired} == {cutover.RETIRED_DETAIL}
    assert len({r["id"] for r in canonical["changes"]}) == 6                       # one stable id per event
    # run_id=R3 filters both alike: the two retirements; limit=1 returns one in both states.
    assert _events(canonical["changes_r3"]) == _events(legacy["changes_r3"]) == sorted(
        ("deleted", fp[k], world.name[k], str(world.r3)) for k in ("x1", "x2"))
    assert len(canonical["changes_one"]) == len(legacy["changes_one"]) == 1

    # The coverage map: 2 legacy (X1, X2) vs 1 canonical (X1) — the same documented difference.
    assert legacy["coverage_root"]["needs_review_count"] == 2
    assert canonical["coverage_root"]["needs_review_count"] == 1
    assert legacy["coverage_definition"] == coverage.NEEDS_REVIEW_DEFINITIONS["legacy"]
    assert canonical["coverage_definition"] == coverage.NEEDS_REVIEW_DEFINITIONS["canonical"]


# ── the REAL cutover on the shared database ─────────────────────────────────


async def _seed_clean_days(env: Any, w: SimpleNamespace, days: range) -> None:
    """One clean comparison per UTC date, started 5 minutes before ``now`` minus n days (always in the past)."""
    from app.models.postgres import SuiteMembershipParityRun

    now = datetime.now(timezone.utc)
    async with env() as db:
        for n in days:
            run_id = uuid.uuid4()
            w.parity_ids.add(run_id)
            started = now - timedelta(days=n, minutes=5)
            db.add(SuiteMembershipParityRun(
                id=run_id, trigger="beat", status="clean", started_at=started, finished_at=started + timedelta(minutes=2),
                window_runs=5, projects_checked=1, keys_compared=4, blocking_diffs=0, expected_divergences=0,
                counts={}, per_project=[], sample=[],
            ))
        await db.commit()


async def _post(env: Any, w: SimpleNamespace) -> Any:
    from app.routers import admin_maintenance as routes

    async with env() as db:
        try:
            return await routes.cut_over_suite_membership(db=db, current_user=w.admin)
        except HTTPException as refusal:
            await db.rollback()
            return refusal


async def test_the_real_cutover_then_the_flag_is_restored(env: Any, world: Any) -> None:
    from app.models.postgres import FeatureFlag, SettingsAuditLog
    from app.routers import admin_maintenance as routes
    from app.services import feature_flags
    from app.services import suite_membership_cutover as cutover

    async with env() as db:
        before = (await db.execute(
            select(FeatureFlag.enabled_global, FeatureFlag.updated_at, FeatureFlag.updated_by_user_id)
            .where(FeatureFlag.key == cutover.FEATURE_FLAG)
        )).first()
    assert before is not None, "migration 0251's flag row is missing: the database is not at head"
    assert before.enabled_global is True, "the shared flag must start ON (the seeded state)"
    try:
        await _seed_clean_days(env, world, range(0, 3))
        refusal = await _post(env, world)
        assert isinstance(refusal, HTTPException) and refusal.status_code == 409
        assert refusal.detail == "Not ready: day 3 of 7 with no differences."

        await _seed_clean_days(env, world, range(3, 7))
        result = await _post(env, world)
        assert not isinstance(result, HTTPException), getattr(result, "detail", result)
        assert result.streak_days == 7 and result.cut_over_at is not None

        async with env() as db:
            assert await cutover.legacy_model_active(db, fresh=True) is False
            history = await routes.read_suite_membership_parity(db=db, current_user=world.admin)
            audit = (await db.execute(
                select(SettingsAuditLog.setting_key, SettingsAuditLog.action, SettingsAuditLog.changed_fields,
                       SettingsAuditLog.reason)
                .where(SettingsAuditLog.actor_id == world.admin.id)
            )).all()
            await db.rollback()
        assert history.cutover is not None
        assert history.cutover.legacy_writes is False and history.cutover.cut_over_at is not None
        assert history.cutover.cut_over_by_name == world.admin.username
        assert history.cutover.streak_days_at_cutover == 7
        assert history.streak_days == 7 and history.cutover_ready is True
        assert [(a.setting_key, a.action, a.changed_fields, a.reason) for a in audit] == [
            ("feature_flag:suite_membership_legacy_writes", "update", ["enabled_global"],
             "HYG-04 cutover after 7 clean UTC days")]

        again = await _post(env, world)
        assert isinstance(again, HTTPException) and again.status_code == 409
        assert again.detail == "Already cut over: the old suite-membership tables are not written."
        async with env() as db:
            with pytest.raises(HTTPException) as compare:
                await routes.trigger_suite_membership_parity(db=db, current_user=world.admin)
            await db.rollback()
        assert compare.value.status_code == 409
        assert compare.value.detail == "The comparison is retired: the old suite-membership tables are no longer written."
    finally:
        async with env() as db:
            await db.execute(
                update(FeatureFlag).where(FeatureFlag.key == cutover.FEATURE_FLAG).values(
                    enabled_global=before.enabled_global, updated_at=before.updated_at,
                    updated_by_user_id=before.updated_by_user_id,
                )
            )
            await db.execute(delete(SettingsAuditLog).where(SettingsAuditLog.actor_id == world.admin.id))
            await db.commit()
        await feature_flags.invalidate_flag_cache(cutover.FEATURE_FLAG)
        async with env() as db:
            restored = await cutover.legacy_model_active(db, fresh=True)
            cached = await cutover.legacy_model_active(db)
            await db.rollback()
        assert restored is True and cached is True, "the next module would see the cut-over state"
