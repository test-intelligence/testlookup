"""W16 AGT-13 shadow pairs against a real PostgreSQL (contract §2.2 test 4).

What a scripted session cannot show:

1. **The list's SQL.** ``count(*) OVER ()`` as ``total`` under a page limit, the project's pending count + ``EXISTS``
   in one statement, and the page's sources resolved in ONE ``UNION ALL`` statement FILTERED BY the project: a key
   naming another project's test case resolves to no source (never a lookup by id alone). Three statements a page.
2. **The write and read sanitizers on JSONB.** ``store_shadow_pair`` stores a token-like string redacted.
3. **Golden match.** The one statement joins pair → test case → its run IN THE PAIR'S PROJECT → its analysis → the
   newest usable feedback: two pairs are labelled with the right verdicts, the rest (no feedback, an unusable one,
   a summary pair, another project's pair, a malformed key) stay pending, and a second batch finds nothing.
4. **The 30-day expiry.** A 31-day pending pair goes, a 31-day labelled one and a 29-day pending one stay, in
   bounded batches; ``run_purge`` previews and deletes the project's class only.

Every row lives under fresh projects and is removed at the end; every window is computed from a ``now`` built in
the test. The fixture awaits ``dispose_engine_for_loop()`` before building its engine and after disposing it, and
closes the app's Redis / Mongo clients (``run_purge`` binds Mongo). Requires ``TESTLOOKUP_POSTGRES_TEST_DSN``
migrated to head (0231) and a reachable Mongo. To be listed in the ``postgres-integration`` job of
``.github/workflows/ci.yml`` (after ``test_ai_eval_runner_postgres.py``).
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytest.importorskip("asyncpg")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

ROOT = "agent.root_cause_analysis.v1"
SUMMARY = "agent.summary.v1"


def _dsn() -> str:
    value = os.environ.get("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


class _NoStorage:
    async def list_objects(self, prefix, *, bucket=None):
        return []

    async def delete_prefix(self, prefix, *, bucket=None):
        return 0

    async def delete_object(self, key, *, bucket=None):
        return None


@pytest_asyncio.fixture(loop_scope="module")
async def env():
    from app.db import mongo as app_mongo
    from app.db import postgres as app_postgres
    from app.db.mongo import close_mongo, get_mongo_db
    from app.db.redis_client import close_redis

    dsn = _dsn()
    await app_postgres.dispose_engine_for_loop()
    app_mongo.reset_mongo_client()  # bind Motor to this module's loop
    engine = create_async_engine(dsn, pool_size=2, max_overflow=0)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield engine, sessions, get_mongo_db()
    finally:
        await engine.dispose()
        await app_postgres.dispose_engine_for_loop()
        await close_redis()
        await close_mongo()


class _Statements:
    """Counts the statements the engine sends while active (the per-page statement budget)."""

    def __init__(self, engine):
        self.engine = engine.sync_engine
        self.sql: list[str] = []

    def _on(self, _conn, _cursor, statement, *_args):
        self.sql.append(statement)

    def __enter__(self):
        event.listen(self.engine, "before_cursor_execute", self._on)
        return self

    def __exit__(self, *_exc):
        event.remove(self.engine, "before_cursor_execute", self._on)


async def _world(sessions, *, now):
    """Two projects, one labeller, a run + test cases each, analyses and feedback on P's cases."""
    from app.models.postgres import AIAnalysis, AIFeedback, Project, TestCase, TestRun, User, UserRole

    w = {"P": uuid.uuid4(), "Q": uuid.uuid4(), "E": uuid.uuid4(), "user": uuid.uuid4()}
    tag = w["P"].hex[:10]
    async with sessions() as db:
        for key in ("P", "Q", "E"):
            db.add(Project(id=w[key], name=f"gdsp-{key}-{tag}", slug=f"gdsp-{key.lower()}-{tag}", is_active=True))
        db.add(User(id=w["user"], email=f"gdsp-{tag}@example.com", username=f"gdsp_{tag}", full_name="Lena Ortiz",
                    hashed_password="!unusable", role=UserRole.QA_LEAD.value))
        await db.flush()
        for key in ("P", "Q"):
            run_id = uuid.uuid4()
            w[f"run_{key}"] = run_id
            db.add(TestRun(id=run_id, project_id=w[key], build_number=f"b-{key}-{tag}", jenkins_job="gdsp",
                           status="FAILED", ingestion_source="upload", branch="main", primary_suite_name="checkout"))
        await db.flush()
        for name in ("tc1", "tc2", "tc3", "tc4"):
            w[name] = uuid.uuid4()
            db.add(TestCase(id=w[name], test_run_id=w["run_P"], test_name=f"test_{name}_checkout", class_name="checkout",
                            suite_name="checkout", test_fingerprint=f"fp-{name}-{tag}", status="FAILED", duration_ms=10))
        w["tcQ"] = uuid.uuid4()
        db.add(TestCase(id=w["tcQ"], test_run_id=w["run_Q"], test_name="test_q_checkout", class_name="checkout",
                        suite_name="checkout", test_fingerprint=f"fp-q-{tag}", status="FAILED", duration_ms=10))
        await db.flush()
        analyses = {name: uuid.uuid4() for name in ("tc1", "tc2", "tc3", "tc4")}
        for name, category in (("tc1", "PRODUCT_BUG"), ("tc2", "FLAKY"), ("tc3", "TEST_DATA"), ("tc4", "FLAKY")):
            db.add(AIAnalysis(id=analyses[name], test_case_id=w[name], failure_category=category, confidence_score=70))
        await db.flush()
        feedback = (
            ("tc1", "correct", None, now - timedelta(days=2)),       # truth = the analysis' PRODUCT_BUG
            ("tc2", "incorrect", "TEST_DATA", now - timedelta(days=3)),  # truth = the correction
            ("tc2", "correct", None, now - timedelta(days=9)),        # older: the newest usable one wins
            ("tc4", "incorrect", None, now - timedelta(days=1)),      # unusable: no correction
        )
        w["feedback"] = {}
        for name, rating, corrected, at in feedback:
            fid = uuid.uuid4()
            w["feedback"].setdefault(name, fid)
            db.add(AIFeedback(id=fid, analysis_id=analyses[name], test_case_id=w[name], user_id=w["user"],
                              rating=rating, corrected_category=corrected, source="manual", created_at=at))
        await db.commit()
    return w


async def _pair(sessions, *, project, sample_key, now, age=timedelta(hours=1), agent=ROOT, status="pending",
                incumbent="PRODUCT_BUG", candidate="FLAKY", label=None, labelled_by=None):
    from app.models.postgres import AIEvalShadowPair

    pid = uuid.uuid4()
    async with sessions() as db:
        db.add(AIEvalShadowPair(
            id=pid, project_id=project, agent_id=agent, sample_key=sample_key, incumbent_tier="llm",
            candidate_tier="slm", incumbent_output={"failure_category": incumbent, "root_cause_summary": "llm says"},
            candidate_output={"failure_category": candidate, "root_cause_summary": "slm says"}, incumbent_tokens=40,
            candidate_tokens=12, total_tokens=52, label_status=status, label=label, labelled_by=labelled_by,
            labelled_at=now - age if status != "pending" else None, created_at=now - age,
        ))
        await db.commit()
    return pid


async def _cleanup(sessions, w):
    async with sessions() as db:
        await db.execute(text("DELETE FROM users WHERE id = :u"), {"u": w["user"]})
        for key in ("P", "Q", "E"):
            await db.execute(text("DELETE FROM projects WHERE id = :p"), {"p": w[key]})
        await db.commit()


async def _statuses(sessions, ids):
    async with sessions() as db:
        rows = (await db.execute(
            text("SELECT id, label_status, label FROM ai_eval_shadow_pairs WHERE id = ANY(:ids)"), {"ids": list(ids)},
        )).all()
    return {row[0]: (row[1], row[2]) for row in rows}


async def test_the_list_pages_counts_and_resolves_sources_inside_the_project(env):
    from app.services import shadow_pair_service as svc
    from app.services.tier_comparison_service import store_shadow_pair

    engine, sessions, _mongo = env
    now = datetime.now(timezone.utc)
    w = await _world(sessions, now=now)
    try:
        own = await _pair(sessions, project=w["P"], sample_key=f"root_cause:{w['tc1']}", now=now, age=timedelta(hours=4))
        foreign = await _pair(sessions, project=w["P"], sample_key=f"root_cause:{w['tcQ']}", now=now, age=timedelta(hours=3))
        run_pair = await _pair(sessions, project=w["P"], agent=SUMMARY, sample_key=f"summary:{w['run_P']}", now=now,
                               age=timedelta(hours=2))
        labelled = await _pair(sessions, project=w["P"], sample_key=f"root_cause:{w['tc2']}", now=now,
                               age=timedelta(hours=5), status="human_labelled",
                               label={"verdict": "both", "source": "human"}, labelled_by=w["user"])
        await _pair(sessions, project=w["Q"], sample_key=f"root_cause:{w['tcQ']}", now=now)
        # The write sanitizer, on a real JSONB row (the producer's task path).
        async with sessions() as db:
            stored = await store_shadow_pair(
                db, project_id=w["P"], agent_id=ROOT, sample_key=f"root_cause:{w['tc3']}", incumbent_tier="llm",
                candidate_tier="slm", incumbent_output={"root_cause_summary": "Use token=Gh7Jk8Lm9Np0 to log in"},
                candidate_output={"failure_category": "TEST_DATA"}, incumbent_tokens=5, candidate_tokens=5,
                daily_token_budget=10_000,
            )
            await db.commit()
        assert stored is not None
        async with sessions() as db:
            raw = (await db.execute(
                text("SELECT incumbent_output FROM ai_eval_shadow_pairs WHERE id = :i"), {"i": stored.id},
            )).scalar_one()
        assert raw == {"root_cause_summary": "Use token=[REDACTED] to log in"}

        async with sessions() as db:
            with _Statements(engine) as seen:
                page = await svc.list_pairs(db, project_id=w["P"], label_status="pending", limit=3, offset=0)
        assert len(seen.sql) == 3, seen.sql
        assert sum("test_cases" in sql for sql in seen.sql) == 1, "the sources resolve in ONE statement"
        assert (page.total, page.pending_total, page.live_tier_pairs_seen) == (4, 4, True)
        assert [item.id for item in page.items] == [str(stored.id), str(run_pair), str(foreign)]
        by_id = {item.id: item for item in page.items}
        assert by_id[str(foreign)].source is None, "another project's test case is never resolved"
        assert by_id[str(run_pair)].source.label == f"Run #b-P-{w['P'].hex[:10]}"
        assert by_id[str(run_pair)].source.href == f"/runs/{w['run_P']}"
        assert by_id[str(stored.id)].source.href == f"/runs/{w['run_P']}?tab=tests"
        assert by_id[str(stored.id)].source.label == "test_tc3_checkout"

        async with sessions() as db:
            rest = await svc.list_pairs(db, project_id=w["P"], label_status="pending", limit=3, offset=3)
            everything = await svc.list_pairs(db, project_id=w["P"], label_status="all", limit=10)
            past_end = await svc.list_pairs(db, project_id=w["P"], label_status="pending", limit=3, offset=30)
            roots = await svc.list_pairs(db, project_id=w["P"], agent_id=ROOT, label_status="pending")
            other = await svc.list_pairs(db, project_id=w["Q"], label_status="all")
            empty = await svc.list_pairs(db, project_id=w["E"], label_status="all")
        assert [item.id for item in rest.items] == [str(own)] and rest.total == 4
        assert everything.total == 5
        assert {i.id: i.labelled_by_name for i in everything.items}[str(labelled)] == "Lena Ortiz"
        assert (past_end.items, past_end.total) == ([], 4)
        assert roots.total == 3 and str(run_pair) not in {i.id for i in roots.items}
        assert other.total == 1 and other.pending_total == 1
        assert (empty.total, empty.pending_total, empty.live_tier_pairs_seen) == (0, 0, False)
    finally:
        await _cleanup(sessions, w)


async def test_golden_match_labels_only_pairs_with_their_projects_human_truth(env):
    from app.services import shadow_pair_service as svc

    _engine, sessions, _mongo = env
    now = datetime.now(timezone.utc)
    w = await _world(sessions, now=now)
    try:
        first = await _pair(sessions, project=w["P"], sample_key=f"root_cause:{w['tc1']}", now=now,
                            age=timedelta(days=2), incumbent="PRODUCT_BUG", candidate="FLAKY")
        corrected = await _pair(sessions, project=w["P"], sample_key=f"root_cause:{w['tc2']}", now=now,
                                age=timedelta(days=1), incumbent="FLAKY", candidate="test_data")
        untouched = [
            await _pair(sessions, project=w["P"], sample_key=f"root_cause:{w['tc3']}", now=now),   # no feedback
            await _pair(sessions, project=w["P"], sample_key=f"root_cause:{w['tc4']}", now=now),   # unusable one
            await _pair(sessions, project=w["P"], agent=SUMMARY, sample_key=f"summary:{w['run_P']}", now=now),
            await _pair(sessions, project=w["Q"], sample_key=f"root_cause:{w['tc1']}", now=now),   # P's case, Q's pair
            await _pair(sessions, project=w["P"], sample_key="root_cause:not-a-uuid", now=now),
        ]
        async with sessions() as db:
            labelled = await svc.golden_match_batch(db, now=now, limit=500)
            await db.commit()
        assert labelled == 2
        states = await _statuses(sessions, [first, corrected, *untouched])
        assert states[first] == ("golden_match", {
            "verdict": "incumbent", "failure_category": "PRODUCT_BUG", "source": "feedback",
            "feedback_id": str(w["feedback"]["tc1"]),
        })
        assert states[corrected][0] == "golden_match"
        assert states[corrected][1]["verdict"] == "candidate" and states[corrected][1]["failure_category"] == "TEST_DATA"
        assert states[corrected][1]["feedback_id"] == str(w["feedback"]["tc2"]), "the newest usable feedback"
        assert all(states[pid] == ("pending", None) for pid in untouched)
        async with sessions() as db:
            assert await svc.golden_match_batch(db, now=now) == 0, "an empty batch: nothing left to label"
    finally:
        await _cleanup(sessions, w)


async def test_expiry_takes_pending_pairs_past_thirty_days_in_batches(env):
    from app.services import shadow_pair_service as svc

    _engine, sessions, _mongo = env
    now = datetime.now(timezone.utc)
    w = await _world(sessions, now=now)
    try:
        old = [await _pair(sessions, project=w[k], sample_key=f"root_cause:{uuid.uuid4()}", now=now,
                           age=timedelta(days=31)) for k in ("P", "Q")]
        kept = [
            await _pair(sessions, project=w["P"], sample_key=f"root_cause:{uuid.uuid4()}", now=now,
                        age=timedelta(days=31), status="human_labelled", label={"verdict": "both", "source": "human"}),
            await _pair(sessions, project=w["P"], sample_key=f"root_cause:{uuid.uuid4()}", now=now,
                        age=timedelta(days=29)),
        ]
        total, batches = 0, 0
        while batches < 10:
            async with sessions() as db:
                deleted = await svc.expire_unlabelled_batch(db, now=now, limit=1)
                await db.commit()
            batches += 1
            total += deleted
            assert deleted <= 1
            if deleted == 0:
                break
        assert total == 2 and batches == 3
        assert set(await _statuses(sessions, [*old, *kept])) == set(kept)
    finally:
        await _cleanup(sessions, w)


async def test_the_retention_purge_previews_and_deletes_the_projects_unlabelled_pairs(env):
    from app.services import retention_service

    _engine, sessions, mongo = env
    now = datetime.now(timezone.utc)
    w = await _world(sessions, now=now)
    try:
        doomed = [await _pair(sessions, project=w["P"], sample_key=f"root_cause:{uuid.uuid4()}", now=now, age=age)
                  for age in (timedelta(days=31), timedelta(days=40))]
        kept = [
            await _pair(sessions, project=w["P"], sample_key=f"root_cause:{uuid.uuid4()}", now=now,
                        age=timedelta(days=40), status="golden_match", label={"verdict": "neither", "source": "feedback"}),
            await _pair(sessions, project=w["P"], sample_key=f"root_cause:{uuid.uuid4()}", now=now,
                        age=timedelta(days=5)),
            await _pair(sessions, project=w["Q"], sample_key=f"root_cause:{uuid.uuid4()}", now=now,
                        age=timedelta(days=40)),
        ]
        async with sessions() as db:
            preview = await retention_service.run_purge(
                db, project_id=w["P"], mode="preview", now=now, mongo=mongo, storage=_NoStorage(),
            )
            await db.commit()
        assert preview["candidates"]["shadow_pairs_unlabelled"] == 2
        assert "shadow_pairs_unlabelled" not in preview["unmeasured"]
        assert set(await _statuses(sessions, [*doomed, *kept])) == {*doomed, *kept}, "a preview deletes nothing"

        async with sessions() as db:
            out = await retention_service.run_purge(
                db, project_id=w["P"], mode="execute", now=now, mongo=mongo, storage=_NoStorage(),
            )
            await db.commit()
        assert out["counts"]["postgres"]["shadow_pairs_unlabelled"] == 2
        assert set(await _statuses(sessions, [*doomed, *kept])) == set(kept), "that project's pending pairs only"
    finally:
        await _cleanup(sessions, w)
