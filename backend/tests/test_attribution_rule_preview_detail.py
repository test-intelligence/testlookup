"""W11 REL-09: the attribution preview says where a matched run would really land.

``POST /api/v1/projects/{project_id}/attribution-rules/preview`` evaluated the
candidate rule ALONE. At ingest an earlier enabled rule that matches the same
run wins (rung 3 is a total order: priority, created_at, id), and a run that
named its release (rung 1) or was matched to a milestone (rung 2) never reaches
the rules. ``detail=true`` answers that; without it the preview is exactly the
pre-W11 contract (one statement, four keys) — the direct calls in
``tests/regression/test_attribution_rules_router.py`` pass neither new
parameter and must keep working.

A statement-scripted session: each ``execute`` gets the next scripted answer
and is recorded, so the ORDER and SHAPE of the statements are asserted, not
just the counts. The ladder agreement against a real database (and the real
linker) is ``tests/integration/test_attribution_preview_postgres.py``.
"""
from __future__ import annotations

import inspect
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

pytest.importorskip("sqlalchemy")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.dialects import postgresql  # noqa: E402

from app.core.deps import get_current_active_user  # noqa: E402
from app.db.postgres import get_db  # noqa: E402
from app.models.postgres import AttributionMatchField  # noqa: E402
from app.routers import release_attribution_rules as router_mod  # noqa: E402

PROJECT = uuid.uuid4()
NOW = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)
OLD_KEYS = {"considered", "matched", "field_present", "sample"}
OLD_SAMPLE_KEYS = {"run_id", "build_number", "branch", "environment"}
NEW_KEYS = {"shadowed", "explicit", "target_exists", "unattributed_recent"}
NEW_SAMPLE_KEYS = {"tags", "created_at", "current_release_name", "would_land"}


def _body(**over):
    payload = {
        "name": "release line",
        "match_field": "branch",
        "match_pattern": "release/*",
        "target_release_name": "2.6",
        "priority": 100,
        "is_enabled": True,
    }
    payload.update(over)
    return router_mod.AttributionRuleIn(**payload)


def _run(branch=None, *, tags=None, environment=None, build_number=None, release=uuid.uuid4(), minutes=0):
    return SimpleNamespace(
        id=uuid.uuid4(),
        branch=branch,
        environment=environment,
        build_number=build_number or f"b-{uuid.uuid4().hex[:6]}",
        tags=tags or [],
        created_at=NOW - timedelta(minutes=minutes),
        primary_release_id=release,
    )


def _rule(field, pattern, *, priority=100, days_old=1, enabled=True, rule_id=None, target="x"):
    return SimpleNamespace(
        id=rule_id or uuid.uuid4(),
        project_id=PROJECT,
        name=f"{field} {pattern}",
        match_field=field,
        match_pattern=pattern,
        target_release_name=target,
        priority=priority,
        is_enabled=enabled,
        created_at=NOW - timedelta(days=days_old),
    )


class _Answer:
    """One scripted result: rows for ``.scalars().all()`` / ``.all()``, or a scalar."""

    def __init__(self, rows=None, scalar=None):
        self._rows = list(rows or [])
        self._scalar = scalar

    def scalars(self):
        return self

    def all(self):
        return self._rows

    def scalar_one_or_none(self):
        return self._scalar


class _ScriptedSession:
    def __init__(self, *answers):
        self._answers = list(answers)
        self.statements: list = []
        self.added: list = []
        self.commits = 0

    async def execute(self, stmt, *a, **kw):
        self.statements.append(stmt)
        assert self._answers, f"unscripted statement #{len(self.statements)}: {self.sql(-1)}"
        return self._answers.pop(0)

    def add(self, obj):  # pragma: no cover - the preview must never write
        self.added.append(obj)

    async def commit(self):  # pragma: no cover - the preview must never write
        self.commits += 1

    def sql(self, i: int) -> str:
        return str(self.statements[i].compile(dialect=postgresql.dialect())).replace("\n", " ")

    def params(self, i: int) -> dict:
        return self.statements[i].compile(dialect=postgresql.dialect()).params


def _links(*rows):
    """``(test_run_id, link_source, release name)`` rows for statement (b)."""
    return _Answer(rows=list(rows))


async def _preview(db, body=None, **kw):
    return await router_mod.preview_attribution_rule(
        project_id=PROJECT, body=body or _body(), limit=200, db=db, _=None, __=None, **kw
    )


# ── 1. Without detail: the old contract, byte for byte ──────────────────────


@pytest.mark.asyncio
async def test_without_detail_the_preview_is_one_statement_and_the_four_old_keys():
    runs = [_run("release/2.5"), _run("main")]
    db = _ScriptedSession(_Answer(rows=runs))

    out = await _preview(db)

    assert len(db.statements) == 1, "the plain preview gained a statement"
    assert "FROM test_runs" in db.sql(0)
    assert set(out) == OLD_KEYS
    assert [set(s) for s in out["sample"]] == [OLD_SAMPLE_KEYS]
    assert out["considered"] == 2 and out["matched"] == 1
    assert db.added == [] and db.commits == 0


# ── 2. With detail: four statements, in order, each in the path project ─────


@pytest.mark.asyncio
async def test_with_detail_four_statements_run_in_the_contract_order():
    run = _run("release/2.5")
    db = _ScriptedSession(_Answer(rows=[run]), _Answer(rows=[]), _links(), _Answer(scalar=None))

    out = await _preview(db, detail=True)

    assert len(db.statements) == 4
    assert "FROM test_runs" in db.sql(0)
    rules_sql = db.sql(1)
    assert "FROM release_attribution_rules" in rules_sql
    assert "release_attribution_rules.project_id = " in rules_sql
    assert "release_attribution_rules.is_enabled IS true" in rules_sql
    links_sql = db.sql(2)
    assert "FROM release_test_run_links JOIN releases" in links_sql
    assert "release_test_run_links.is_primary IS true" in links_sql
    assert "releases.project_id = " in links_sql, "a foreign release's name must never be read"
    target_sql = db.sql(3)
    assert "FROM releases" in target_sql and "lower(releases.name)" in target_sql
    assert "LIMIT" in target_sql
    # Every statement is confined to the PATH project.
    for i in (1, 2, 3):
        assert PROJECT in db.params(i).values(), f"statement {i} is not bound to the path project"

    assert set(out) == OLD_KEYS | NEW_KEYS
    assert [set(s) for s in out["sample"]] == [OLD_SAMPLE_KEYS | NEW_SAMPLE_KEYS]
    assert db.added == [] and db.commits == 0, "a preview never writes"


@pytest.mark.asyncio
async def test_no_matched_run_skips_the_link_read():
    db = _ScriptedSession(_Answer(rows=[_run("main")]), _Answer(rows=[]), _Answer(scalar=1))

    out = await _preview(db, detail=True)

    assert len(db.statements) == 3
    assert out["matched"] == 0 and out["shadowed"] == 0 and out["explicit"] == 0
    assert out["target_exists"] is True


# ── 3. Shadowed: an earlier saved rule wins at ingest ───────────────────────


@pytest.mark.asyncio
async def test_a_run_an_earlier_rule_also_matches_is_shadowed():
    shadowed = _run("release/2.5", tags=["rc-1"], minutes=1)
    free = _run("release/2.6", tags=["nightly"], minutes=2)
    earlier = _rule("tag", "rc-*", priority=10)
    later = _rule("branch", "release/*", priority=500)   # matches both, but runs AFTER the candidate
    db = _ScriptedSession(
        _Answer(rows=[shadowed, free]), _Answer(rows=[earlier, later]), _links(), _Answer(scalar=None),
    )

    out = await _preview(db, detail=True)

    assert out["matched"] == 2
    assert out["shadowed"] == 1
    assert [s["would_land"] for s in out["sample"]] == ["earlier_rule", "rule"]
    # The saved rules are read in the ladder's own order (match_attribution_rule's ORDER BY).
    assert (
        "ORDER BY release_attribution_rules.priority ASC, release_attribution_rules.created_at ASC, "
        "release_attribution_rules.id ASC"
    ) in db.sql(1)


@pytest.mark.asyncio
async def test_a_new_rule_goes_after_every_saved_rule_of_its_priority():
    """Saved now, its created_at is the newest: a same-priority rule runs first."""
    run = _run("release/2.5", tags=["rc-1"])
    same_priority = _rule("tag", "rc-*", priority=100, days_old=30)
    db = _ScriptedSession(_Answer(rows=[run]), _Answer(rows=[same_priority]), _links(), _Answer(scalar=None))

    out = await _preview(db, detail=True)

    assert out["shadowed"] == 1
    assert out["sample"][0]["would_land"] == "earlier_rule"


@pytest.mark.asyncio
async def test_a_disabled_saved_rule_shadows_nothing():
    run = _run("release/2.5", tags=["rc-1"])
    disabled = _rule("tag", "rc-*", priority=1, enabled=False)
    db = _ScriptedSession(_Answer(rows=[run]), _Answer(rows=[disabled]), _links(), _Answer(scalar=None))

    out = await _preview(db, detail=True)

    assert out["shadowed"] == 0
    assert out["sample"][0]["would_land"] == "rule"


# ── 4. Explicit: rung 1 / 2 keeps the run where it is ───────────────────────


@pytest.mark.asyncio
async def test_a_run_that_named_its_release_stays_named():
    run = _run("release/2.5", tags=["rc-1"])
    earlier = _rule("tag", "rc-*", priority=10)
    db = _ScriptedSession(
        _Answer(rows=[run]), _Answer(rows=[earlier]),
        _links((run.id, "explicit_client", "2.5.0")), _Answer(scalar=None),
    )

    out = await _preview(db, detail=True)

    assert out["explicit"] == 1
    # One landing per run, in the ladder's precedence: rung 1 claims it before any rule.
    assert out["shadowed"] == 0
    sample = out["sample"][0]
    assert sample["would_land"] == "explicit"
    assert sample["current_release_name"] == "2.5.0"


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["explicit_client", "manual_ui", "external_match"])
async def test_every_asserted_link_source_counts_as_explicit(source):
    run = _run("release/2.5")
    db = _ScriptedSession(_Answer(rows=[run]), _Answer(rows=[]), _links((run.id, source, "R")), _Answer(scalar=None))
    out = await _preview(db, detail=True)
    assert out["explicit"] == 1 and out["sample"][0]["would_land"] == "explicit"


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["rule_match", "cutoff_window", "active_release", "unknown", None])
async def test_an_inferred_link_does_not_keep_the_run(source):
    run = _run("release/2.5")
    db = _ScriptedSession(_Answer(rows=[run]), _Answer(rows=[]), _links((run.id, source, "R")), _Answer(scalar=None))
    out = await _preview(db, detail=True)
    assert out["explicit"] == 0 and out["sample"][0]["would_land"] == "rule"
    assert out["sample"][0]["current_release_name"] == "R"


# ── 5. Editing: a rule does not shadow itself, and keeps its place ──────────


@pytest.mark.asyncio
async def test_editing_excludes_the_rule_itself_from_the_earlier_rules():
    me = _rule("branch", "release/*", priority=100, days_old=5)
    run = _run("release/2.5")
    db = _ScriptedSession(_Answer(rows=[run]), _Answer(rows=[me]), _links(), _Answer(scalar=None))

    out = await _preview(db, detail=True, rule_id=me.id)

    assert out["shadowed"] == 0, "the edited rule shadowed itself"
    assert out["sample"][0]["would_land"] == "rule"
    # The edited rule is read whatever its state, so its own position is known.
    assert "release_attribution_rules.id = " in db.sql(1)
    assert me.id in db.params(1).values()


@pytest.mark.asyncio
async def test_an_edit_that_raises_the_priority_is_not_shadowed_by_its_saved_self():
    """Its saved row (priority 100) sorts ahead of the edited one (200): still itself."""
    me = _rule("branch", "release/*", priority=100, days_old=5)
    run = _run("release/2.5")
    db = _ScriptedSession(_Answer(rows=[run]), _Answer(rows=[me]), _links(), _Answer(scalar=None))

    out = await _preview(db, body=_body(priority=200), detail=True, rule_id=me.id)

    assert out["shadowed"] == 0 and out["sample"][0]["would_land"] == "rule"


@pytest.mark.asyncio
async def test_an_edited_rule_keeps_its_created_at_among_same_priority_rules():
    me = _rule("branch", "release/*", priority=100, days_old=5)
    older = _rule("tag", "rc-*", priority=100, days_old=10)     # ahead of me at ingest
    newer = _rule("tag", "hot-*", priority=100, days_old=1)     # behind me at ingest
    by_older = _run("release/2.5", tags=["rc-1"])
    by_newer = _run("release/2.6", tags=["hot-1"])
    db = _ScriptedSession(
        _Answer(rows=[by_older, by_newer]), _Answer(rows=[older, me, newer]), _links(), _Answer(scalar=None),
    )

    out = await _preview(db, detail=True, rule_id=me.id)

    assert [s["would_land"] for s in out["sample"]] == ["earlier_rule", "rule"]
    assert out["shadowed"] == 1


@pytest.mark.asyncio
async def test_an_edit_that_lowers_the_priority_moves_the_rule_ahead():
    me = _rule("branch", "release/*", priority=100, days_old=5)
    other = _rule("tag", "rc-*", priority=50, days_old=10)
    run = _run("release/2.5", tags=["rc-1"])
    db = _ScriptedSession(_Answer(rows=[run]), _Answer(rows=[other, me]), _links(), _Answer(scalar=None))

    out = await _preview(db, body=_body(priority=10), detail=True, rule_id=me.id)

    assert out["shadowed"] == 0 and out["sample"][0]["would_land"] == "rule"


@pytest.mark.asyncio
async def test_a_rule_id_the_project_does_not_hold_reads_as_a_new_rule():
    """Another project's rule id is never found: (a) is confined to the path project
    (its id is AND-ed with ``project_id``), so the answer is the one without it — no oracle."""
    run = _run("release/2.5", tags=["rc-1"])
    earlier = _rule("tag", "rc-*", priority=10)
    foreign = uuid.uuid4()
    with_id = _ScriptedSession(_Answer(rows=[run]), _Answer(rows=[earlier]), _links(), _Answer(scalar=None))
    without = _ScriptedSession(_Answer(rows=[run]), _Answer(rows=[earlier]), _links(), _Answer(scalar=None))

    a = await _preview(with_id, detail=True, rule_id=foreign)
    b = await _preview(without, detail=True)

    assert a == b
    sql = with_id.sql(1)
    where = sql.split(" WHERE ", 1)[1]
    assert where.startswith("release_attribution_rules.project_id = "), (
        "the rule id must be AND-ed with the PATH project, never read on its own"
    )
    assert with_id.params(1)["project_id_1"] == PROJECT


# ── 6. The target release ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_target_is_matched_like_the_linker_stripped_and_case_insensitive():
    db = _ScriptedSession(_Answer(rows=[_run("release/2.5")]), _Answer(rows=[]), _links(), _Answer(scalar=1))

    out = await _preview(db, body=_body(target_release_name="  RC-2.6 "), detail=True)

    assert out["target_exists"] is True
    assert "rc-2.6" in db.params(3).values(), "the target must be stripped and lower-cased (release_linker._lookup_release)"


@pytest.mark.asyncio
async def test_a_target_no_release_carries_reads_false():
    db = _ScriptedSession(_Answer(rows=[_run("release/2.5")]), _Answer(rows=[]), _links(), _Answer(scalar=None))
    out = await _preview(db, detail=True)
    assert out["target_exists"] is False


# ── 7. Unattributed and the richer sample ───────────────────────────────────


@pytest.mark.asyncio
async def test_unattributed_recent_counts_considered_runs_with_no_primary_release():
    runs = [_run("main", release=None), _run("release/2.5", release=None), _run("develop")]
    db = _ScriptedSession(_Answer(rows=runs), _Answer(rows=[]), _links(), _Answer(scalar=None))

    out = await _preview(db, detail=True)

    assert out["unattributed_recent"] == 2
    assert out["sample"][0]["current_release_name"] is None


@pytest.mark.asyncio
async def test_the_sample_carries_tags_and_created_at():
    tags = [f"t{i}" for i in range(12)]
    tags[1] = "x" * 150
    tags.insert(0, 7)                                   # a non-string tag is not shown
    run = _run("release/2.5", tags=tags)
    db = _ScriptedSession(_Answer(rows=[run]), _Answer(rows=[]), _links(), _Answer(scalar=None))

    out = await _preview(db, detail=True)

    sample = out["sample"][0]
    assert len(sample["tags"]) == 10
    assert sample["tags"][0] == "t0" and sample["tags"][1] == "x" * 100
    assert sample["created_at"] == run.created_at
    assert {s["would_land"] for s in out["sample"]} <= set(router_mod.PREVIEW_LANDINGS)


# ── 8. GET /fields ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_fields_route_lists_the_vocabulary_with_how_each_matches():
    out = await router_mod.list_attribution_rule_fields(project_id=PROJECT, _=None, __=None)

    assert [f.value for f in out.fields] == [f.value for f in AttributionMatchField]
    assert [f.glob for f in out.fields] == [True, True, False, True]
    assert {f.value: f.help for f in out.fields} == router_mod.ATTRIBUTION_FIELD_HELP
    assert {f.value: f.label for f in out.fields} == router_mod.ATTRIBUTION_FIELD_LABELS
    assert "db" not in inspect.signature(router_mod.list_attribution_rule_fields).parameters, (
        "the fields route is static: it has no statement to run"
    )


def test_every_match_field_has_a_label_and_help():
    values = {f.value for f in AttributionMatchField}
    assert set(router_mod.ATTRIBUTION_FIELD_LABELS) == values
    assert set(router_mod.ATTRIBUTION_FIELD_HELP) == values


# ── 9. Over HTTP: the response model, the plain defaults ────────────────────


def _client(session):
    app = FastAPI()
    app.include_router(router_mod.router)
    admin = SimpleNamespace(id=uuid.uuid4(), role="ADMIN", is_active=True, username="admin")

    async def _db():
        yield session

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_active_user] = lambda: admin
    return TestClient(app)


def _payload():
    return _body().model_dump()


def test_over_http_the_plain_preview_answers_exactly_the_old_keys():
    session = _ScriptedSession(_Answer(rows=[_run("release/2.5")]))
    response = _client(session).post(f"/api/v1/projects/{PROJECT}/attribution-rules/preview", json=_payload())
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == OLD_KEYS, "the new fields' defaults leaked into the plain preview"
    assert [set(s) for s in body["sample"]] == [OLD_SAMPLE_KEYS]


def test_over_http_detail_answers_every_field():
    run = _run("release/2.5", tags=["rc-1"])
    session = _ScriptedSession(_Answer(rows=[run]), _Answer(rows=[]), _links(), _Answer(scalar=None))
    response = _client(session).post(
        f"/api/v1/projects/{PROJECT}/attribution-rules/preview?detail=true", json=_payload()
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == OLD_KEYS | NEW_KEYS
    assert body["target_exists"] is False
    assert body["sample"][0]["tags"] == ["rc-1"]
    assert body["sample"][0]["would_land"] == "rule"
    assert body["sample"][0]["current_release_name"] is None


def test_over_http_a_malformed_rule_id_is_a_422_before_any_statement():
    session = _ScriptedSession()
    response = _client(session).post(
        f"/api/v1/projects/{PROJECT}/attribution-rules/preview?detail=true&rule_id=not-a-uuid", json=_payload()
    )
    assert response.status_code == 422
    assert session.statements == []


def test_over_http_the_fields_route_answers_the_four_fields():
    response = _client(_ScriptedSession()).get(f"/api/v1/projects/{PROJECT}/attribution-rules/fields")
    assert response.status_code == 200, response.text
    assert [f["value"] for f in response.json()["fields"]] == ["branch", "build_number", "environment", "tag"]


def test_the_new_parameters_are_plain_defaults():
    """Direct callers pass neither (tests/regression/test_attribution_rules_router.py)."""
    params = inspect.signature(router_mod.preview_attribution_rule).parameters
    assert params["detail"].default is False
    assert params["rule_id"].default is None
