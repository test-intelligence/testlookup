"""VIZ-207 -- the systemic cluster membership key, and the sweep's epoch bump.

``cluster_key`` is a rank and the sweep re-inserts every row nightly; the
membership key is the identity of an exact member set. These tests pin what
it promises (same members -> same key, in any order, on any night) and what it
does NOT (one member more -> a different key). The SQL twin in migration 0193
is held to this function in ``tests/integration/test_systemic_membership_key_postgres.py``.

The sweep half: ``recompute_systemic_clusters`` must bump the analytics epoch
once per project whose clusters changed (written, or the last ones removed),
after that project's commit, and never for a project whose commit failed or
that had nothing before and nothing after.
"""
from __future__ import annotations

import asyncio
import hashlib
import uuid
from unittest.mock import MagicMock

import pytest

from app.services import systemic_cluster_service as svc
from app.services.systemic_cluster_service import build_clusters, membership_key


def test_the_key_is_sha256_of_the_sorted_members_32_hex():
    members = ["fp-b", "fp-a", "fp-c"]
    expected = hashlib.sha256("fp-a\nfp-b\nfp-c".encode()).hexdigest()[:32]
    assert membership_key(members) == expected
    assert len(expected) == 32 and int(expected, 16) >= 0


def test_order_does_not_matter_and_it_is_deterministic():
    """M-207b: a key over UNSORTED members changes with the order the sweep
    happened to produce them in."""
    members = ["c3", "a1", "B2", "_x", "é"]
    keys = {membership_key(list(order)) for order in (
        members, list(reversed(members)), sorted(members), members[2:] + members[:2]
    )}
    assert len(keys) == 1
    assert membership_key(members) == membership_key(tuple(members))


def test_code_point_order_not_locale_order():
    # "B" (U+0042) sorts before "a" (U+0061) by code point; a locale order
    # would put "a" first. The SQL backfill uses COLLATE "C" for the same order.
    assert membership_key(["a", "B"]) == hashlib.sha256(b"B\na").hexdigest()[:32]


def test_one_member_more_or_less_is_a_new_key():
    base = ["t1", "t2", "t3"]
    assert membership_key(base + ["t4"]) != membership_key(base)
    assert membership_key(base[:2]) != membership_key(base)


def test_the_dataclass_exposes_its_key():
    cluster = svc.SystemicCluster(cluster_key="sfc_001", members=("b", "a"), cohesion=1.0,
                                  co_failure_runs=3)
    assert cluster.membership_key == membership_key(["a", "b"])


def _co_failing(prefix: str, size: int, runs: range) -> dict[str, set]:
    return {f"{prefix}{i}": set(runs) for i in range(size)}


def test_a_new_larger_cluster_moves_the_rank_key_but_not_the_identity():
    """The sweep-swap property: ``sfc_001`` changes owner when a larger cluster
    appears; the membership key stays with the members."""
    night1 = {**_co_failing("x", 3, range(0, 4)), **_co_failing("y", 2, range(10, 14))}
    night2 = {**night1, **_co_failing("z", 4, range(20, 24))}
    one = {c.cluster_key: c for c in build_clusters(night1)}
    two = {c.cluster_key: c for c in build_clusters(night2)}
    x_key = membership_key(["x0", "x1", "x2"])
    assert one["sfc_001"].membership_key == x_key
    assert two["sfc_001"].membership_key == membership_key(["z0", "z1", "z2", "z3"])
    assert two["sfc_002"].membership_key == x_key  # same members, new rank
    assert one["sfc_002"].membership_key == two["sfc_003"].membership_key
    # Determinism: the same input twice gives the same keys.
    assert [c.membership_key for c in build_clusters(night2)] == [
        c.membership_key for c in build_clusters(night2)
    ]


# ── store_clusters writes the key ───────────────────────────────────────────


class _StoreSession:
    def __init__(self):
        self.added: list = []

    async def execute(self, *_a, **_k):
        result = MagicMock()
        result.scalars.return_value.all.return_value = []
        return result

    def add(self, row):
        self.added.append(row)

    async def flush(self):
        return None


async def test_store_clusters_writes_the_membership_key():
    from app.models.postgres import SystemicFlakeCluster

    db = _StoreSession()
    cluster = svc.SystemicCluster(cluster_key="sfc_001", members=("m2", "m1"), cohesion=0.9,
                                  co_failure_runs=4)
    assert await svc.store_clusters(db, uuid.uuid4(), [cluster]) == 1
    rows = [r for r in db.added if isinstance(r, SystemicFlakeCluster)]
    assert [r.membership_key for r in rows] == [membership_key(["m1", "m2"])]


# ── the sweep bumps the epoch (VIZ-212 mutation path, F10 / M-207f) ─────────


class _SweepSession:
    def __init__(self, journal, ids, fail_commit_for=()):
        self.journal = journal
        self.ids = ids
        self.fail_commit_for = set(fail_commit_for)
        self.current = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False

    async def execute(self, *_a, **_k):
        result = MagicMock()
        result.scalars.return_value.all.return_value = list(self.ids)
        return result

    async def commit(self):
        if self.current in self.fail_commit_for:
            raise RuntimeError("commit failed")
        self.journal.append(("commit", str(self.current)))

    async def rollback(self):
        self.journal.append(("rollback", str(self.current)))


@pytest.fixture
def sweep(monkeypatch):
    from app.services import cache_service
    from app.worker import tasks

    journal: list[tuple[str, str]] = []

    async def _bumps(project_ids):
        seen: list[str] = []
        for pid in project_ids:
            if pid and str(pid) not in seen:
                seen.append(str(pid))
        journal.extend(("bump", pid) for pid in seen)

    async def _bump(pid):
        journal.append(("bump", str(pid)))

    monkeypatch.setattr(cache_service, "bump_analytics_epochs", _bumps)
    monkeypatch.setattr(cache_service, "bump_analytics_epoch", _bump)
    monkeypatch.setattr(tasks, "_run_async", asyncio.run)

    def run(ids, *, before, written, fail_commit_for=()):
        import app.db.postgres as pg

        session = _SweepSession(journal, ids, fail_commit_for)
        monkeypatch.setattr(pg, "AsyncSessionLocal", lambda: session)

        async def _cluster_project(_db, pid):
            session.current = pid
            return []

        async def _count(_db, pid):
            return before[pid]

        async def _store(_db, pid, _found):
            journal.append(("mutate", str(pid)))
            return written[pid]

        monkeypatch.setattr(svc, "cluster_project", _cluster_project)
        monkeypatch.setattr(svc, "existing_cluster_count", _count)
        monkeypatch.setattr(svc, "store_clusters", _store)
        return tasks.recompute_systemic_clusters.run()

    return journal, run


def test_the_sweep_bumps_each_changed_project_once_after_its_commit(sweep):
    journal, run = sweep
    wrote, dissolved, quiet, failed = (uuid.uuid4() for _ in range(4))
    ids = [wrote, dissolved, quiet, failed]
    out = run(
        ids,
        before={wrote: 1, dissolved: 2, quiet: 0, failed: 1},
        written={wrote: 3, dissolved: 0, quiet: 0, failed: 2},
        fail_commit_for={failed},
    )
    assert out["errors"] == 1 and out["clusters"] == 3
    bumps = [pid for kind, pid in journal if kind == "bump"]
    # written -> bump; all removed -> bump; nothing before or after -> none;
    # a rolled-back replace -> none.
    assert sorted(bumps) == sorted([str(wrote), str(dissolved)])
    last_commit = max(i for i, (kind, _) in enumerate(journal) if kind == "commit")
    assert all(i > last_commit for i, (kind, _) in enumerate(journal) if kind == "bump"), journal


def test_a_sweep_that_changed_nothing_bumps_nothing(sweep):
    journal, run = sweep
    pid = uuid.uuid4()
    run([pid], before={pid: 0}, written={pid: 0})
    assert [e for e in journal if e[0] == "bump"] == []
