"""W11 REL-09 against a real PostgreSQL: the attribution preview cannot disagree
with the ladder.

``POST /projects/{id}/attribution-rules/preview?detail=true`` says, for each
matched run, where it would land if the previewed rule were saved: ``rule``
(the candidate claims it), ``earlier_rule`` (an enabled saved rule ahead of it
wins) or ``explicit`` (the run named its release: rung 1 keeps it). What only a
real database and the REAL linker can prove:

* for every sample, ``would_land == "rule"`` exactly when
  ``release_linker.link_run_or_default`` — run on a copy of that run after the
  candidate is saved (no external match; rung 1 only for a run that named its
  release) — links the copy by ``rule_match`` through the CANDIDATE
  (``match_attribution_rule``'s own choice), for a new rule AND for an edit;
* the counts (``shadowed``, ``explicit``, ``unattributed_recent``) and the
  target's case-insensitive existence check run on real rows;
* another project's rules never shadow, and another project's rule id passed
  as ``rule_id`` changes nothing (the id is read only inside the PATH project:
  no oracle);
* the plain preview is ONE statement and the detailed one FOUR.

Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` and a database migrated to head;
skips otherwise. Every test runs inside one outer transaction that is rolled
back, so nothing is left behind.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytest.importorskip("asyncpg")

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

NOW = datetime.now(timezone.utc)


def _dsn() -> str:
    value = os.getenv("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


@pytest.fixture
async def engine():
    eng = create_async_engine(_dsn(), pool_size=1, max_overflow=0)
    yield eng
    await eng.dispose()


@pytest.fixture
async def db(engine):
    async with engine.connect() as conn:
        outer = await conn.begin()
        try:
            factory = async_sessionmaker(
                bind=conn, expire_on_commit=False, join_transaction_mode="create_savepoint"
            )
            async with factory() as session:
                yield session
        finally:
            await outer.rollback()


def _body(**over):
    from app.routers.release_attribution_rules import AttributionRuleIn

    payload = {
        "name": "release line",
        "match_field": "branch",
        "match_pattern": "release/*",
        "target_release_name": "2.6",
        "priority": 100,
        "is_enabled": True,
    }
    payload.update(over)
    return AttributionRuleIn(**payload)


@pytest.fixture
async def world(db):
    """Project A: three saved rules, five releases, twenty runs with links of
    three sources (and three with none). Project B: a rule that would shadow
    every one of A's runs if it leaked across."""
    from app.models.postgres import (
        Project,
        Release,
        ReleaseAttributionRule,
        ReleaseTestRunLink,
        TestRun,
    )

    token = uuid.uuid4().hex[:10]
    a = Project(id=uuid.uuid4(), name=f"Attr A {token}", slug=f"attr-a-{token}")
    b = Project(id=uuid.uuid4(), name=f"Attr B {token}", slug=f"attr-b-{token}")
    db.add_all([a, b])
    await db.flush()

    def release(project, name, *, active=False):
        return Release(
            id=uuid.uuid4(), project_id=project.id, name=name, status="in_progress",
            is_active=active, activated_at=(NOW - timedelta(days=60)) if active else None,
        )

    rel = {
        "active": release(a, "Active line", active=True),
        "named": release(a, "2.5.0"),
        "rc": release(a, "2.5-RC"),
        "late": release(a, "2.7"),
        "window": release(a, "Window"),
        "b_active": release(b, "B active", active=True),
    }
    db.add_all(rel.values())
    await db.flush()

    def rule(project, name, field, pattern, target, priority, days_old, *, enabled=True):
        return ReleaseAttributionRule(
            id=uuid.uuid4(), project_id=project.id, name=name, match_field=field,
            match_pattern=pattern, target_release_name=target, priority=priority,
            is_enabled=enabled, created_at=NOW - timedelta(days=days_old),
        )

    rules = {
        # Ahead of the candidate (priority 10 < 100): shadows its rc-tagged runs.
        "rc": rule(a, "RC tags", "tag", "rc-*", "2.5-RC", 10, 3),
        # Behind it (500): matches the same branches, never wins over the candidate.
        "late": rule(a, "Late branches", "branch", "release/*", "2.7", 500, 2),
        # Ahead, but disabled: never counts.
        "off": rule(a, "Off", "branch", "release/*", "Off", 1, 1, enabled=False),
        # Another project's: must never count for A.
        "b": rule(b, "B everything", "branch", "*", "B active", 1, 5),
    }
    db.add_all(rules.values())
    await db.flush()

    # (key, branch, tags, link source, release key) — newest first.
    specs = [
        ("m_rc", "release/2.5", ["rc-1"], "active_release", "active"),        # earlier_rule
        ("m_named", "release/2.5", ["rc-2"], "explicit_client", "named"),     # explicit (before the rc rule)
        ("m_plain", "release/2.6", [], "active_release", "active"),           # rule
        ("m_late", "release/2.6", ["nightly"], "rule_match", "late"),         # rule (candidate before "late")
        ("m_case", "RELEASE/2.6", [], None, None),                            # rule; unattributed
        ("m_manual", "release/2.6-hotfix", [], "manual_ui", "named"),         # explicit
        ("m_rc_case", "release/x", ["RC-9"], "cutoff_window", "window"),      # earlier_rule (case-insensitive tag)
        ("m_client", "release/y", [], "explicit_client", "named"),            # explicit
        ("n_main", "main", ["rc-1"], "rule_match", "rc"),
        ("n_dev", "develop", [], "active_release", "active"),
        ("n_none", None, [], None, None),                                     # unattributed
        ("n_feat", "feature/release/2.6", [], "active_release", "active"),    # glob is anchored: no match
        ("n_hot", "hotfix/1", [], "active_release", "active"),
        ("n_tag", "main", ["release/2.6"], "active_release", "active"),       # a tag, not a branch
        ("n_env", "main", [], "active_release", "active"),
        ("n_x1", "trunk", [], None, None),                                    # unattributed
        ("n_x2", "trunk", [], "active_release", "active"),
        ("n_x3", "trunk", [], "explicit_client", "named"),
        ("n_x4", "trunk", [], "active_release", "active"),
        ("n_x5", "trunk", [], "active_release", "active"),
    ]
    runs = {}
    for i, (key, branch, tags, source, rkey) in enumerate(specs):
        run = TestRun(
            id=uuid.uuid4(), project_id=a.id, build_number=f"attr-{token}-{i:02d}", jenkins_job="attr",
            branch=branch, tags=tags, environment="staging", ingestion_source="unknown",
            primary_release_id=rel[rkey].id if rkey else None,
            created_at=NOW - timedelta(minutes=i + 1),
        )
        runs[key] = (run, source, rkey)
        db.add(run)
    await db.flush()
    for run, source, rkey in runs.values():
        if rkey:
            db.add(ReleaseTestRunLink(
                release_id=rel[rkey].id, test_run_id=run.id, is_primary=True,
                link_source=source, project_id=a.id,
            ))
    await db.flush()
    return {"a": a, "b": b, "rel": rel, "rules": rules, "runs": runs, "token": token}


async def _preview(db, project, body, **kw):
    from app.routers.release_attribution_rules import preview_attribution_rule

    return await preview_attribution_rule(
        project_id=project.id, body=body, limit=200, db=db, _=None, __=None, **kw
    )


class _Statements:
    """Counts the statements one call sends (the preview's own, nothing else)."""

    def __init__(self, db):
        self.db = db
        self.count = 0

    async def __aenter__(self):
        await self.db.flush()
        self.sync = (await self.db.connection()).sync_connection
        event.listen(self.sync, "before_cursor_execute", self._on)
        return self

    async def __aexit__(self, *exc):
        event.remove(self.sync, "before_cursor_execute", self._on)

    def _on(self, *args, **kwargs):
        self.count += 1


async def _ladder_picks(db, world, sample, candidate_id) -> bool:
    """Link a COPY of the sample's run through the real ladder; did the candidate claim it?

    Rung 1 applies only to a run that NAMED its release (its primary link is an
    assertion), with that name; every other copy names none. No copy carries a
    pull request, so rung 2 finds nothing (as for every seeded run).
    """
    from app.models.postgres import ASSERTED_LINK_SOURCES, ReleaseTestRunLink, TestRun
    from app.services import release_attribution, release_linker

    original, source, _ = next(v for v in world["runs"].values() if str(v[0].id) == sample["run_id"])
    copy = TestRun(
        id=uuid.uuid4(), project_id=world["a"].id,
        build_number=f"copy-{original.build_number}-{uuid.uuid4().hex[:4]}", jenkins_job="attr-copy",
        branch=original.branch, tags=list(original.tags or []), environment=original.environment,
        ingestion_source="unknown",
    )
    db.add(copy)
    await db.flush()
    named = sample["current_release_name"] if source in ASSERTED_LINK_SOURCES else None
    await release_linker.link_run_or_default(db, world["a"].id, named, copy.id)
    link_source = (
        await db.execute(
            select(ReleaseTestRunLink.link_source).where(
                ReleaseTestRunLink.test_run_id == copy.id, ReleaseTestRunLink.is_primary.is_(True)
            )
        )
    ).scalar_one()
    if link_source != "rule_match":
        return False
    winner = await release_attribution.match_attribution_rule(db, world["a"].id, copy)
    return winner is not None and winner.id == candidate_id


async def test_a_new_rule_preview_agrees_with_the_ladder(db, world):
    from app.models.postgres import ReleaseAttributionRule

    body = _body()
    async with _Statements(db) as plain:
        old = await _preview(db, world["a"], body)
    async with _Statements(db) as detailed:
        out = await _preview(db, world["a"], body, detail=True)

    assert plain.count == 1 and set(old) == {"considered", "matched", "field_present", "sample"}
    assert detailed.count == 4
    assert out["considered"] == 20
    assert out["matched"] == 8
    assert out["field_present"] == 19
    assert out["shadowed"] == 2, "the rc-tag rule (priority 10) claims two of the matched runs first"
    assert out["explicit"] == 3, "three matched runs named their release (explicit_client, manual_ui)"
    assert out["target_exists"] is False
    assert out["unattributed_recent"] == 3
    by_run = {s["run_id"]: s["would_land"] for s in out["sample"]}
    expected = {
        "m_rc": "earlier_rule", "m_named": "explicit", "m_plain": "rule", "m_late": "rule",
        "m_case": "rule", "m_manual": "explicit", "m_rc_case": "earlier_rule", "m_client": "explicit",
    }
    assert by_run == {str(world["runs"][k][0].id): v for k, v in expected.items()}
    names = {s["run_id"]: s["current_release_name"] for s in out["sample"]}
    assert names[str(world["runs"]["m_case"][0].id)] is None
    assert names[str(world["runs"]["m_late"][0].id)] == "2.7"

    # Save the candidate exactly as the create route would (created now: after
    # every saved rule of its priority), then run the REAL ladder on copies.
    saved = ReleaseAttributionRule(
        id=uuid.uuid4(), project_id=world["a"].id, name=body.name, match_field=body.match_field,
        match_pattern=body.match_pattern, target_release_name=body.target_release_name,
        priority=body.priority, is_enabled=True, created_at=datetime.now(timezone.utc),
    )
    db.add(saved)
    await db.flush()
    for sample in out["sample"]:
        picked = await _ladder_picks(db, world, sample, saved.id)
        assert (sample["would_land"] == "rule") is picked, (
            f"the preview said {sample['would_land']!r} for branch {sample['branch']!r}, the ladder disagreed"
        )


async def test_an_edit_preview_agrees_with_the_ladder(db, world):
    """Editing the rc-tag rule: it does not shadow itself, and keeps its place."""
    rc = world["rules"]["rc"]
    body = _body(name=rc.name, match_field="tag", match_pattern="rc-*",
                 target_release_name=" 2.5-rc ", priority=rc.priority)

    out = await _preview(db, world["a"], body, detail=True, rule_id=rc.id)

    assert out["matched"] == 4                       # m_rc, m_named, m_rc_case, n_main
    assert out["shadowed"] == 0, "the edited rule was counted as an earlier rule shadowing itself"
    assert out["explicit"] == 1                      # m_named
    assert out["target_exists"] is True, "2.5-rc matches the release 2.5-RC whatever the case"
    for sample in out["sample"]:
        picked = await _ladder_picks(db, world, sample, rc.id)
        assert (sample["would_land"] == "rule") is picked


async def test_another_projects_rules_and_rule_ids_change_nothing(db, world):
    body = _body()
    baseline = await _preview(db, world["a"], body, detail=True)
    foreign = await _preview(db, world["a"], body, detail=True, rule_id=world["rules"]["b"].id)

    assert foreign == baseline, "another project's rule id changed the answer (an oracle)"
    # B's catch-all at priority 1 would have shadowed every matched run had it leaked.
    assert baseline["shadowed"] == 2


async def test_a_preview_writes_nothing(db, world):
    from app.models.postgres import Release, ReleaseAttributionRule

    async def counts():
        rules = (await db.execute(select(ReleaseAttributionRule.id))).all()
        releases = (await db.execute(select(Release.id))).all()
        return len(rules), len(releases)

    before = await counts()
    await _preview(db, world["a"], _body(target_release_name="Brand new"), detail=True)
    assert await counts() == before, "a preview created a rule or a release"
    assert not db.new and not db.dirty
