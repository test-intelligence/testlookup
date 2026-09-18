from datetime import datetime, timezone
from contextlib import asynccontextmanager

import pytest
from pydantic import ValidationError
from pymongo.errors import DuplicateKeyError

from app.services.decision_report_service import (
    DecisionReportV1,
    list_decision_report_versions,
    load_decision_report,
    publish_decision_report,
    record_decision_report_attempt,
)
from app.services import decision_report_service as report_service

REAL_SUBJECT_LOCK = report_service._lock_report_subject


@pytest.fixture(autouse=True)
def _subject_lock_for_unit_tests(monkeypatch):
    """Unit reports use symbolic IDs; production and integration use PG locks."""
    @asynccontextmanager
    async def unlocked(_test_run_id):
        yield

    monkeypatch.setattr(report_service, "_lock_report_subject", unlocked)


@pytest.mark.asyncio
async def test_subject_lock_emits_postgres_for_share(monkeypatch, mocker):
    run_id = uuid.uuid4()
    result = mocker.MagicMock()
    result.scalar_one_or_none.return_value = run_id
    pg = mocker.AsyncMock()
    pg.execute.return_value = result
    session_cm = mocker.MagicMock()
    session_cm.__aenter__ = mocker.AsyncMock(return_value=pg)
    session_cm.__aexit__ = mocker.AsyncMock(return_value=False)
    monkeypatch.setattr(
        "app.db.postgres.AsyncSessionLocal", lambda: session_cm
    )

    async with REAL_SUBJECT_LOCK(str(run_id)):
        statement = pg.execute.await_args.args[0]
        assert statement._for_update_arg is not None
        assert statement._for_update_arg.read is True

    pg.commit.assert_awaited_once()


class _Cursor:
    def __init__(self, rows):
        self.rows = rows

    def sort(self, *_args):
        return self

    def limit(self, *_args):
        return self

    async def to_list(self, length=1):
        return self.rows[:length]


class _Collection:
    def __init__(self):
        self.docs = []

    def find(self, query, *_args):
        rows = [
            doc for doc in self.docs
            if doc.get("test_run_id") == query["test_run_id"]
            and doc.get("status") == query["status"]
            and (
                "pipeline_run_id" not in query
                or doc.get("pipeline_run_id") == query["pipeline_run_id"]
            )
            and (
                "report_version" not in query
                or doc.get("report_version") == query["report_version"]
            )
        ]
        rows.sort(key=lambda doc: doc.get("report_version", 0), reverse=True)
        return _Cursor(rows)

    async def insert_one(self, document):
        self.docs.append(document)

    async def update_one(self, *_args, **_kwargs):
        raise AssertionError("immutable reports must not be updated")


@pytest.mark.asyncio
async def test_publication_holds_the_subject_lock_through_mongo_insert(monkeypatch):
    locked = False

    @asynccontextmanager
    async def subject_lock(_test_run_id):
        nonlocal locked
        locked = True
        try:
            yield
        finally:
            locked = False

    class LockCheckingCollection(_Collection):
        async def insert_one(self, document):
            assert locked, "Mongo publication escaped the TestRun share lock"
            await super().insert_one(document)

    class LockCheckingMongo(_Mongo):
        def __getitem__(self, name):
            if name not in self.collections:
                self.collections[name] = LockCheckingCollection()
            return self.collections[name]

    monkeypatch.setattr(report_service, "_lock_report_subject", subject_lock)
    await publish_decision_report(
        LockCheckingMongo(),
        state=_state(),
        decision={"verification": {"status": "passed"}},
        markdown="verified",
    )
    assert locked is False


class _Mongo:
    def __init__(self):
        self.collections = {}

    def __getitem__(self, name):
        return self.collections.setdefault(name, _Collection())


class _RaceCollection(_Collection):
    def __init__(self):
        super().__init__()
        self.raced = False

    async def insert_one(self, document):
        if not self.raced:
            self.raced = True
            winner = dict(document)
            winner["report_id"] = "winner"
            winner["pipeline_run_id"] = "other-pipeline"
            self.docs.append(winner)
            raise DuplicateKeyError("simulated report-version race")
        return await super().insert_one(document)


class _RacingMongo(_Mongo):
    def __getitem__(self, name):
        if name not in self.collections:
            self.collections[name] = _RaceCollection()
        return self.collections[name]


def _state(pipeline="pipeline-1"):
    return {
        "project_id": "project-1",
        "test_run_id": "run-1",
        "pipeline_run_id": pipeline,
    }


@pytest.mark.asyncio
async def test_published_versions_are_immutable_and_supersede_previous():
    db = _Mongo()
    first = await publish_decision_report(
        db,
        state=_state(),
        decision={"verification": {"status": "passed"}, "decision_evidence_snapshot": {"snapshot_id": "snap-1"}},
        markdown="first",
    )
    second = await publish_decision_report(
        db,
        state=_state("pipeline-2"),
        decision={"verification": {"status": "passed"}},
        markdown="second",
    )

    assert first["report_version"] == 1
    assert second["report_version"] == 2
    assert second["supersedes_report_id"] == first["report_id"]
    assert len(db["decision_reports"].docs) == 2


@pytest.mark.asyncio
async def test_same_pipeline_publication_is_idempotent():
    db = _Mongo()
    state = _state("pipeline-retry")
    first = await publish_decision_report(
        db,
        state=state,
        decision={"verification": {"status": "passed"}},
        markdown="verified",
    )
    second = await publish_decision_report(
        db,
        state=state,
        decision={"verification": {"status": "passed"}, "changed": True},
        markdown="changed retry",
    )

    assert second == first
    assert len(db["decision_reports"].docs) == 1


@pytest.mark.asyncio
async def test_report_version_selection_is_run_scoped_and_bounded():
    db = _Mongo()
    await publish_decision_report(
        db, state=_state("pipeline-1"),
        decision={"verification": {"status": "passed"}, "evidence_bundle_sha256": "a" * 64}, markdown="v1",
    )
    await publish_decision_report(
        db, state=_state("pipeline-2"),
        decision={"verification": {"status": "passed"}, "evidence_bundle_sha256": "b" * 64}, markdown="v2",
    )

    selected = await load_decision_report(db, "run-1", report_version=1)
    assert selected is not None
    assert selected["report_version"] == 1
    assert await load_decision_report(db, "run-1", report_version=99) is None

    versions = await list_decision_report_versions(db, "run-1", limit=1)
    assert len(versions) == 1
    assert versions[0]["report_version"] == 2
    assert versions[0]["pipeline_run_id"] == "pipeline-2"
    assert versions[0]["evidence_bundle_sha256"] == "b" * 64
    assert "decision_intelligence" not in versions[0]


@pytest.mark.asyncio
async def test_version_race_retries_from_new_latest_report():
    db = _RacingMongo()
    report = await publish_decision_report(
        db,
        state=_state("pipeline-racer"),
        decision={"verification": {"status": "passed"}},
        markdown="verified",
    )

    assert report["report_version"] == 2
    assert report["supersedes_report_id"] == "winner"
    assert len(db["decision_reports"].docs) == 2



@pytest.mark.asyncio
@pytest.mark.asyncio
async def test_publish_deep_copies_nested_decision_payload():
    db = _Mongo()
    decision = {"verification": {"status": "passed"}, "metrics": {"failed": 1}}
    report = await publish_decision_report(
        db, state=_state(), decision=decision, markdown="verified",
    )
    decision["metrics"]["failed"] = 99

    assert report["decision_intelligence"]["metrics"]["failed"] == 1
    assert db["decision_reports"].docs[0]["decision_intelligence"]["metrics"]["failed"] == 1

async def test_rejected_attempt_does_not_replace_published_report():
    db = _Mongo()
    published = await publish_decision_report(
        db,
        state=_state(),
        decision={"verification": {"status": "passed"}},
        markdown="verified",
    )
    attempt = await record_decision_report_attempt(
        db,
        state=_state("pipeline-rejected"),
        status="rejected",
        reason="critic failed closed",
        verification={"status": "failed"},
    )

    assert attempt["status"] == "rejected"
    assert attempt["supersedes_report_id"] == published["report_id"]
    assert len(db["decision_reports"].docs) == 1
    assert len(db["decision_report_attempts"].docs) == 1


def test_decision_report_contract_rejects_mutation_and_invalid_version():
    with pytest.raises(ValidationError):
        DecisionReportV1(
            report_id="r",
            project_id="p",
            test_run_id="run",
            pipeline_run_id="pipe",
            report_version=0,
            generated_at=datetime.now(timezone.utc),
            decision_intelligence={},
            verification={},
            markdown_report="ok",
            unexpected="nope",
        )
