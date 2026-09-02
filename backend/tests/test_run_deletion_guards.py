"""S2c — what must refuse a per-run delete, and what must never be deleted.

The nightly purge gets these for free. It only ever touches projects that
opted in, and it only ever selects rows already past a cutoff, so a prefix
pointing somewhere unexpected is still a prefix inside a project that asked to
be purged. A DELETE endpoint has neither bound: an ADMIN names one run and the
executor does what the row says.
"""
from __future__ import annotations

import uuid

import pytest

pytest.importorskip("sqlalchemy")

from app.services import run_deletion_service as svc  # noqa: E402


# ── the prefix guard (RET-D9) ────────────────────────────────────────────────


def test_a_prefix_outside_the_project_is_refused_not_deleted():
    """``TestRun.minio_prefix`` is uploader-derived.

    ``webhooks.py`` builds it as ``key.split("/")[:-1]`` from the object key
    the uploader chose, and nothing validates it beyond rejecting empty. A
    sentinel written to ``uploads/shared/upload_complete.json`` yields the
    prefix ``uploads/shared/`` — and a delete honouring that wipes a shared
    area for every project.
    """
    project_id, run_id = uuid.uuid4(), uuid.uuid4()

    safe, refused = svc.safe_artifact_prefixes(
        project_id, run_id, "uploads/shared/"
    )

    assert "uploads/shared/" in refused
    assert "uploads/shared/" not in safe
    assert all(str(project_id) in p for p in safe), (
        "every prefix handed to delete_prefix must name the project"
    )


@pytest.mark.parametrize(
    "prefix",
    [
        "uploads/",
        "/",
        "",
        "../",
        "uploads/{other}/",
        "{other}/runs/b12/",
        "uploads/{project}x/",
        "notuploads/{project}/",
        # Starts INSIDE the scope and climbs out — a root check alone
        # accepts this one.
        "uploads/{project}/../shared/",
        "{project}/../{other}/runs/b1/",
    ],
)
def test_prefixes_that_escape_the_project_are_all_refused(prefix):
    """Including the near-misses.

    ``uploads/{project}x/`` shares a string prefix with the project scope and
    a naive ``startswith`` on the bare id would accept it.
    """
    project_id, run_id, other = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    candidate = prefix.format(project=project_id, other=other)

    safe, refused = svc.safe_artifact_prefixes(project_id, run_id, candidate)

    assert candidate not in safe, f"{candidate!r} escaped the project scope"


@pytest.mark.parametrize(
    "template",
    [
        "uploads/{project}/{run}/",
        "{project}/runs/build-42/",
        "{project}/builds/build-42/",
    ],
)
def test_the_shapes_the_product_actually_writes_are_accepted(template):
    """The guard must not be so tight that it deletes nothing.

    These three are what ``ingestion``, ``webhooks.py`` and ``debug.py``
    produce. A guard that refused them would leave every run's objects behind
    while reporting a successful delete.
    """
    project_id, run_id = uuid.uuid4(), uuid.uuid4()
    candidate = template.format(project=project_id, run=run_id)

    safe, refused = svc.safe_artifact_prefixes(project_id, run_id, candidate)

    assert candidate in safe, f"{candidate!r} is a real prefix and was refused"
    assert candidate not in refused


def test_the_canonical_prefix_is_always_included():
    """``uploads/{project}/{run}/`` is derived, not stored.

    A run whose ``minio_prefix`` is NULL still has objects there, and the
    nightly purge already deletes it unconditionally.
    """
    project_id, run_id = uuid.uuid4(), uuid.uuid4()

    safe, _refused = svc.safe_artifact_prefixes(project_id, run_id, None)

    assert f"uploads/{project_id}/{run_id}/" in safe


def test_a_refused_prefix_is_reported_rather_than_dropped():
    """Silence here reads as "there was nothing to delete".

    The operator needs to know an object prefix survived the delete, or the
    storage will not drop and nothing will say why.
    """
    project_id, run_id = uuid.uuid4(), uuid.uuid4()

    _safe, refused = svc.safe_artifact_prefixes(
        project_id, run_id, "uploads/shared/"
    )

    assert refused, "the refusal must be returned, not swallowed"


# ── the citation guard ───────────────────────────────────────────────────────


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return type("S", (), {"all": staticmethod(lambda: list(self._rows))})()

    def all(self):
        return list(self._rows)

    def scalar(self):
        return self._rows[0] if self._rows else None

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None


class _DB:
    """Answers each SELECT from a queue, in the order the service issues them."""

    def __init__(self, *results):
        self._queue = list(results)
        self.executed = 0

    async def execute(self, *_a, **_kw):
        self.executed += 1
        return _Result(self._queue.pop(0) if self._queue else [])


class _Mongo:
    def __init__(self, count):
        self._count = count

    def __getitem__(self, _name):
        outer = self

        class _C:
            async def count_documents(self, _query):
                return outer._count

        return _C()


@pytest.mark.asyncio
async def test_a_run_cited_by_a_compliance_pack_is_refused():
    """A compliance pack is evidence assembled for an auditor.

    Deleting the run underneath it leaves a pack whose subject no longer
    exists — the pack still renders, and its numbers can no longer be checked
    against anything.
    """
    db = _DB(["pack-a"], [], [])

    blockers = await svc.citation_blockers(
        db, run_id=uuid.uuid4(), mongo=_Mongo(0)
    )

    assert blockers
    assert any("compliance" in b.lower() for b in blockers)


@pytest.mark.asyncio
async def test_a_run_linked_to_a_release_is_refused():
    db = _DB([], ["release-1"], [])

    blockers = await svc.citation_blockers(
        db, run_id=uuid.uuid4(), mongo=_Mongo(0)
    )

    assert any("release" in b.lower() for b in blockers)


@pytest.mark.asyncio
async def test_a_run_with_a_published_decision_report_is_refused():
    db = _DB([], [], [])

    blockers = await svc.citation_blockers(
        db, run_id=uuid.uuid4(), mongo=_Mongo(2)
    )

    assert any("decision report" in b.lower() for b in blockers)


@pytest.mark.asyncio
async def test_an_uncited_run_has_no_blockers():
    """The other half: a guard that refuses everything deletes nothing."""
    db = _DB([], [], [])

    assert await svc.citation_blockers(
        db, run_id=uuid.uuid4(), mongo=_Mongo(0)
    ) == []


@pytest.mark.asyncio
async def test_every_citation_is_reported_not_just_the_first():
    """An operator who clears one blocker should not discover a second.

    Returning early on the first hit turns one refusal into a sequence of
    them, each requiring another round trip.
    """
    db = _DB(["pack-a"], ["release-1"], [])

    blockers = await svc.citation_blockers(
        db, run_id=uuid.uuid4(), mongo=_Mongo(3)
    )

    assert len(blockers) == 3, f"expected all three citations, got {blockers}"


@pytest.mark.asyncio
async def test_an_unreachable_mongo_blocks_rather_than_allows():
    """The asymmetry runs the other way from the tombstone lookup.

    There, failing open risks a resurrected row an operator can delete again.
    Here, failing open deletes evidence that a published report cites, which is
    unrecoverable — so an unreachable store must refuse the delete.
    """
    class _Broken:
        def __getitem__(self, _name):
            class _C:
                async def count_documents(self, _q):
                    raise ConnectionError("mongo down")

            return _C()

    db = _DB([], [], [])

    blockers = await svc.citation_blockers(
        db, run_id=uuid.uuid4(), mongo=_Broken()
    )

    assert blockers, (
        "an unreachable citation store must block the delete; allowing it "
        "would destroy evidence nobody could prove was uncited"
    )
    assert any("could not" in b.lower() or "unreachable" in b.lower()
               for b in blockers)


@pytest.mark.asyncio
async def test_an_in_progress_run_is_refused():
    """Refusing IN_PROGRESS narrows the resurrection window at the door.

    It does not close it — a Celery task already holding the id does not
    re-read the status, which is why the tombstone exists as well.
    """
    from app.models.postgres import LaunchStatus

    assert svc.status_blocks_deletion(LaunchStatus.IN_PROGRESS)
    assert not svc.status_blocks_deletion(LaunchStatus.PASSED)
    assert not svc.status_blocks_deletion(LaunchStatus.FAILED)
    assert not svc.status_blocks_deletion(LaunchStatus.STOPPED)


def test_the_status_check_uses_the_columns_vocabulary():
    """A literal that is not in the column's enum matches nothing, forever.

    ``TestRun.status`` is a ``String(20)`` typed as ``LaunchStatus``, so
    callers hand this either the enum member or the stored string depending on
    whether the row came from the ORM or a raw select. Both must agree.

    Asserting only ``isinstance(..., bool)`` was the first version of this and
    it survived dropping the ``getattr(status, "value", ...)`` unwrap —
    ``str(LaunchStatus.IN_PROGRESS)`` is ``"LaunchStatus.IN_PROGRESS"``, which
    matches no vocabulary entry, so every run became deletable and the test
    still passed.
    """
    from app.models.postgres import LaunchStatus

    for member in LaunchStatus:
        assert svc.status_blocks_deletion(member) == svc.status_blocks_deletion(
            member.value
        ), f"{member!r} and {member.value!r} disagree"

    assert svc.status_blocks_deletion(LaunchStatus.IN_PROGRESS) is True
    assert svc.status_blocks_deletion("IN_PROGRESS") is True
    assert svc.status_blocks_deletion(LaunchStatus.PASSED) is False
    assert svc.status_blocks_deletion("PASSED") is False
