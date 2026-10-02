"""VIZ-207 -- the systemic cluster membership key against real PostgreSQL.

1. **SQL == Python.** Migration 0193's backfill expression
   (``KEY_BY_CLUSTER_SQL``) equals ``systemic_cluster_service.membership_key``
   on random fingerprint sets, including mixed case, punctuation and non-ASCII
   (where a locale collation would order differently from Python's ``sorted``).
2. **store_clusters writes it**, through the real ORM.
3. **The API returns it** additively on ``/analytics/systemic-clusters``: the
   stored key; for a row written before 0193's code (NULL), the key of its full
   member set; and under a suite filter -- which shows only the members that
   ran in scope -- still the key of the FULL set, never of the shown subset.

The seeded world is ``test_analytics_scope_postgres.py``'s; every cluster here
belongs to its throwaway project and goes with it.
"""
from __future__ import annotations

import importlib.util
import random
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text

from tests.integration.test_analytics_scope_postgres import S1
from tests.integration.test_analytics_scope_postgres import world as _seeded_world

#: pytest finds a fixture by the module attribute's name.
world = _seeded_world

pytest.importorskip("asyncpg")
pytest.importorskip("httpx")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

PATH = "/api/v1/analytics/systemic-clusters"
_MIGRATION = (
    Path(__file__).resolve().parents[2] / "migrations" / "versions"
    / "0193_systemic_cluster_membership_key.py"
)


def _key_sql() -> str:
    spec = importlib.util.spec_from_file_location("m0193", _MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module.KEY_BY_CLUSTER_SQL


_ALPHABET = "abcdefABCDEF0123456789_-.:/ éÉß日<>'\""


def _random_sets(rng: random.Random, count: int) -> list[list[str]]:
    out = []
    for _ in range(count):
        size = rng.randint(1, 12)
        members = {"".join(rng.choice(_ALPHABET) for _ in range(rng.randint(1, 64))) for _ in range(size)}
        out.append(sorted(members, key=lambda _m: rng.random()))  # arbitrary order
    return out


async def _clusters(world, sets: list[list[str]], *, keyed: bool) -> list[uuid.UUID]:
    from app.models.postgres import SystemicFlakeCluster, SystemicFlakeClusterMember
    from app.services.systemic_cluster_service import membership_key

    ids = []
    async with world.sessions.begin() as db:
        for members in sets:
            cid = uuid.uuid4()
            ids.append(cid)
            db.add(SystemicFlakeCluster(
                id=cid, project_id=world.p1, cluster_key=f"k-{cid.hex[:12]}", label="x",
                cause_family="unknown", size=len(members), cohesion=0.9, co_failure_runs=2,
                window_days=60, membership_key=membership_key(members) if keyed else None,
            ))
            await db.flush()
            for fp in members:
                db.add(SystemicFlakeClusterMember(cluster_id=cid, test_fingerprint=fp, failure_runs=2))
    return ids


async def _drop(world, ids) -> None:
    async with world.sessions.begin() as db:
        await db.execute(text("DELETE FROM systemic_flake_cluster WHERE id = ANY(:ids)"), {"ids": ids})


async def test_the_migration_sql_equals_the_python_key(world) -> None:
    from app.services.systemic_cluster_service import membership_key

    sets = _random_sets(random.Random(207), 60)
    sets.append(["a", "B", "_", "é", "Z"])  # code point order != en_US order
    ids = await _clusters(world, sets, keyed=False)
    try:
        async with world.sessions() as db:
            rows = (await db.execute(
                text(f"SELECT k.cluster_id, k.membership_key FROM ({_key_sql()}) k "
                     "WHERE k.cluster_id = ANY(:ids)"),
                {"ids": ids},
            )).all()
        got = {row.cluster_id: row.membership_key for row in rows}
        assert len(got) == len(sets)
        for cid, members in zip(ids, sets):
            assert got[cid] == membership_key(members), members
    finally:
        await _drop(world, ids)


async def test_store_clusters_persists_the_key(world) -> None:
    from app.models.postgres import SystemicFlakeCluster
    from app.services.systemic_cluster_service import (
        SystemicCluster,
        existing_cluster_count,
        membership_key,
        store_clusters,
    )

    project = uuid.uuid4()
    from app.models.postgres import Project

    async with world.sessions.begin() as db:
        db.add(Project(id=project, name=f"be3-store-{project.hex[:8]}",
                       slug=f"be3-store-{project.hex[:8]}", is_active=True))
    try:
        clusters = [
            SystemicCluster(cluster_key="sfc_001", members=("m3", "m1", "m2"), cohesion=0.8,
                            co_failure_runs=5),
            SystemicCluster(cluster_key="sfc_002", members=("n2", "n1"), cohesion=0.7,
                            co_failure_runs=3),
        ]
        async with world.sessions() as db:
            assert await existing_cluster_count(db, project) == 0
            assert await store_clusters(db, project, clusters) == 2
            await db.commit()
            assert await existing_cluster_count(db, project) == 2
            rows = (await db.execute(
                text("SELECT cluster_key, membership_key FROM systemic_flake_cluster "
                     "WHERE project_id = :p ORDER BY cluster_key"), {"p": project}
            )).all()
        assert [(r.cluster_key, r.membership_key) for r in rows] == [
            ("sfc_001", membership_key(["m1", "m2", "m3"])),
            ("sfc_002", membership_key(["n1", "n2"])),
        ]
        assert SystemicFlakeCluster.__table__.c.membership_key.nullable is True
    finally:
        async with world.sessions.begin() as db:
            await db.execute(text("DELETE FROM projects WHERE id = :p"), {"p": project})


async def _items(world, params) -> list[dict]:
    resp = await world.client.get(PATH, params=params, headers=world.member)
    assert resp.status_code == 200, resp.text
    return resp.json()["items"]


async def test_the_api_returns_the_full_set_key_stored_legacy_and_filtered(world) -> None:
    from app.services.systemic_cluster_service import membership_key

    from app.services.analytics_scope import effective_suite_sql

    async with world.sessions() as db:
        rows = (await db.execute(text(
            f"SELECT DISTINCT tc.test_fingerprint AS fp, lower(trim({effective_suite_sql()})) AS suite "
            "FROM test_cases tc JOIN test_runs tr ON tr.id = tc.test_run_id "
            "WHERE tr.project_id = :p ORDER BY 1"), {"p": world.p1})).all()
    in_s1 = sorted({r.fp for r in rows if r.suite == S1.lower()})
    elsewhere = sorted({r.fp for r in rows} - set(in_s1))
    assert len(in_s1) >= 2 and len(elsewhere) >= 2
    mixed = [in_s1[0], elsewhere[0], elsewhere[1]]  # only one member ran in S1
    keyed = await _clusters(world, [mixed], keyed=True)
    legacy = await _clusters(world, [[in_s1[1], elsewhere[1]]], keyed=False)
    try:
        items = {i["cluster_key"]: i for i in await _items(world, [("project_id", str(world.p1))])}
        k_keyed, k_legacy = f"k-{keyed[0].hex[:12]}", f"k-{legacy[0].hex[:12]}"
        assert items[k_keyed]["membership_key"] == membership_key(mixed)
        assert items[k_legacy]["membership_key"] == membership_key([in_s1[1], elsewhere[1]])
        assert items[k_keyed]["cluster_key"] == k_keyed  # the old key is still there

        filtered = {i["cluster_key"]: i for i in await _items(
            world, [("project_id", str(world.p1)), ("suite_name", S1)])}
        shown = [m["test_fingerprint"] for m in filtered[k_keyed]["members"]]
        assert shown == [in_s1[0]]  # the filter selects members...
        # ...but the identity is still the cluster's, not the subset's.
        assert filtered[k_keyed]["membership_key"] == membership_key(mixed)
        assert filtered[k_legacy]["membership_key"] == membership_key([in_s1[1], elsewhere[1]])
    finally:
        await _drop(world, keyed + legacy)


async def test_the_migration_sql_ignores_the_column_collation(world) -> None:
    """The backfill must order by code point whatever the collation. On Alpine
    (musl) ``en_US.utf8`` IS byte order, so the default database cannot tell;
    a temp table of the same name shadows the real one for this session with
    an ICU collation, where ``_`` < ``a`` < ``B``, so an order that follows the
    column's collation hashes differently."""
    from app.services.systemic_cluster_service import membership_key

    members = ["a", "B", "_", "Z", "é"]
    async with world.engine.connect() as conn:
        has_icu = await conn.scalar(text(
            "SELECT count(*) FROM pg_collation WHERE collname = 'und-x-icu'"))
        if not has_icu:
            pytest.skip("no ICU collation in this PostgreSQL build")
        try:
            await conn.execute(text(
                "CREATE TEMP TABLE systemic_flake_cluster_member "
                "(cluster_id uuid, test_fingerprint text COLLATE \"und-x-icu\")"))
            cid = uuid.uuid4()
            for fp in members:
                await conn.execute(text(
                    "INSERT INTO pg_temp.systemic_flake_cluster_member VALUES (:c, :f)"),
                    {"c": cid, "f": fp})
            icu_order = (await conn.execute(text(
                "SELECT string_agg(test_fingerprint, ',' ORDER BY test_fingerprint) "
                "FROM pg_temp.systemic_flake_cluster_member"))).scalar_one()
            assert icu_order.split(",") != sorted(members)  # the shadow really reorders
            got = (await conn.execute(text(f"SELECT k.membership_key FROM ({_key_sql()}) k"))).scalar_one()
            assert got == membership_key(members)
        finally:
            await conn.rollback()  # the temp table goes with the transaction
