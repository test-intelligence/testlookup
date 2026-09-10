"""Regression: rung 3 of the attribution ladder can finally fire.

S3a shipped the model (migration 0154), the four-value match vocabulary, the
evaluator with its deterministic ordering, and the linker's call site at
``release_linker`` — and **no way to create a rule**. With no router the table
was always empty, so rung 3 could never fire on any deployment: such projects
fell straight past it to the active release, which cannot tell a hotfix branch
from a release candidate from trunk CI.

This file covers the CRUD that closes that, and the preview that keeps it safe.
The matching logic itself is untouched and is covered by
``test_release_attribution_rules.py``.

Why the preview exists
----------------------
The failure mode of an attribution rule is not that it misses — it is that it
matches EVERYTHING, and every run still gets attributed, so nothing looks wrong.
``release_attribution`` says so in its own comments. A rule is also retroactive
in effect: it decides where future runs land and nobody re-reads it afterwards.
``matched`` against ``considered`` is the one number that tells a useful rule
from one that captures the whole project.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

pytest.importorskip("sqlalchemy")

pytestmark = pytest.mark.regression

from fastapi import HTTPException  # noqa: E402

from app.models.postgres import AttributionMatchField  # noqa: E402
from app.routers import release_attribution_rules as router_mod  # noqa: E402

PROJECT = uuid.uuid4()


def _body(**over):
    payload = {
        "name": "hotfix branches",
        "match_field": "branch",
        "match_pattern": "hotfix/*",
        "target_release_name": "2.4.1",
        "priority": 10,
        "is_enabled": True,
    }
    payload.update(over)
    return router_mod.AttributionRuleIn(**payload)


def _run(branch=None, environment=None, build_number=None, tags=None):
    return SimpleNamespace(
        id=uuid.uuid4(),
        branch=branch,
        environment=environment,
        build_number=build_number,
        tags=tags or [],
    )


class _Session:
    def __init__(self, rows=None):
        self._rows = rows or []
        self.added: list = []
        self.deleted: list = []
        self.commits = 0

    async def execute(self, stmt=None, *a, **kw):
        rows = self._rows

        class _R:
            def scalars(self_inner):
                return self_inner

            def all(self_inner):
                return rows

            def scalar_one_or_none(self_inner):
                return rows[0] if rows else None

        return _R()

    def add(self, obj):
        self.added.append(obj)

    async def delete(self, obj):
        self.deleted.append(obj)

    async def commit(self):
        self.commits += 1

    async def refresh(self, obj):
        obj.created_at = obj.created_at or None


def _actor():
    return SimpleNamespace(id=uuid.uuid4(), username="qa.lead")


# ── The vocabulary is closed ─────────────────────────────────────────────────


@pytest.mark.parametrize("field", [f.value for f in AttributionMatchField])
def test_every_declared_match_field_is_accepted(field):
    """Derived from the enum, so a value added there is covered here without
    anyone remembering to."""
    router_mod._validate_match_field(field)


@pytest.mark.parametrize("bad", ["Branch", "commit_sha", "", "release_name"])
def test_a_field_the_evaluator_cannot_read_is_refused(bad):
    """The column is ``String(30)``, so the database accepts anything.

    A typo would save cleanly and then never match — the rule would sit in the
    list looking configured while the ladder fell straight past it. That is
    indistinguishable from "attribution just doesn't work".
    """
    with pytest.raises(HTTPException) as exc:
        router_mod._validate_match_field(bad)
    assert exc.value.status_code == 422
    assert "match_field" in str(exc.value.detail)


# ── Create ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_creating_a_rule_persists_every_field_the_evaluator_reads():
    db = _Session()
    actor = _actor()

    await router_mod.create_attribution_rule(
        project_id=PROJECT, body=_body(), db=db, current_user=actor, __=None
    )

    # Two rows now, in ONE transaction: the rule, and the activity-ledger row
    # recording who created it. An enabled catch-all rule was once left on a
    # real project by an e2e run and nobody could see who had created it —
    # asserting on BOTH rows is what keeps that record from quietly going away.
    from app.models.postgres import ProjectActivityEvent, ReleaseAttributionRule

    rules = [r for r in db.added if isinstance(r, ReleaseAttributionRule)]
    events = [r for r in db.added if isinstance(r, ProjectActivityEvent)]
    assert len(rules) == 1
    assert len(events) == 1
    assert events[0].event_type == "attribution_rule.created"
    assert events[0].project_id == PROJECT
    assert events[0].actor_id == actor.id

    rule = rules[0]
    assert rule.project_id == PROJECT
    assert rule.match_field == "branch"
    assert rule.match_pattern == "hotfix/*"
    assert rule.target_release_name == "2.4.1", (
        "the target is stored by NAME — a rule usually predates the release it "
        "names, and the name resolves through the auto-create path"
    )
    assert rule.priority == 10
    assert rule.created_by_id == actor.id
    assert db.commits == 1


@pytest.mark.asyncio
async def test_a_bad_match_field_is_refused_before_anything_is_written():
    db = _Session()
    with pytest.raises(HTTPException):
        await router_mod.create_attribution_rule(
            project_id=PROJECT, body=_body(match_field="Branch"),
            db=db, current_user=_actor(), __=None,
        )
    assert db.added == [], "a refused rule must not leave an activity row either"
    assert db.commits == 0


# ── List order is evaluation order ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_rules_are_listed_in_the_order_the_ladder_evaluates_them():
    """Which rule WINS is the whole question when two could match.

    Sorting by creation date instead would show a list that does not explain
    the outcome.
    """
    import inspect

    src = inspect.getsource(router_mod.list_attribution_rules)
    assert "priority.asc()" in src
    assert "created_at.asc()" in src, (
        "ties on priority must break deterministically, or two rules sharing a "
        "priority attribute the same run differently between runs"
    )
    assert "id.asc()" in src


# ── Scoping ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_rule_is_fetched_within_the_project_in_the_path():
    """Not merely by id.

    Without the project predicate a rule id from another project would be
    editable by anyone holding access to this one — the IDOR shape the
    authorization ratchet exists for.
    """
    import inspect

    src = inspect.getsource(router_mod._get_rule_or_404)
    assert "ReleaseAttributionRule.project_id == project_id" in src


@pytest.mark.asyncio
async def test_a_missing_rule_is_a_404_not_a_500():
    db = _Session(rows=[])
    with pytest.raises(HTTPException) as exc:
        await router_mod._get_rule_or_404(db, PROJECT, str(uuid.uuid4()))
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_a_malformed_rule_id_is_a_404_not_a_500():
    db = _Session(rows=[])
    with pytest.raises(HTTPException) as exc:
        await router_mod._get_rule_or_404(db, PROJECT, "not-a-uuid")
    assert exc.value.status_code == 404


# ── Delete does not rewrite history ──────────────────────────────────────────


def test_deleting_a_rule_leaves_past_attributions_alone():
    """A link carries ``link_source=rule_match`` and the release it produced.

    Re-attributing because a rule was retired would change what a past gate
    decision was based on, silently and after the fact.
    """
    import inspect

    src = inspect.getsource(router_mod.delete_attribution_rule)
    assert "ReleaseTestRunLink" not in src
    assert "re-attribute" in src.lower() or "does NOT re-attribute" in src


# ── The preview ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_preview_counts_matches_without_saving_anything():
    runs = [
        _run(branch="hotfix/login"),
        _run(branch="hotfix/cart"),
        _run(branch="main"),
        _run(branch=None),
    ]
    db = _Session(rows=runs)

    out = await router_mod.preview_attribution_rule(
        project_id=PROJECT, body=_body(), limit=200, db=db, _=_actor(), __=None
    )

    assert out["considered"] == 4
    assert out["matched"] == 2
    assert db.added == [], "the preview saved the rule it was asked to try"
    assert db.commits == 0


@pytest.mark.asyncio
async def test_the_preview_reports_how_many_runs_even_carry_the_field():
    """A field most runs do not carry produces a rule that fires rarely and
    unpredictably, which reads as "the rule is broken" long after it was
    written."""
    runs = [_run(branch="main"), _run(branch=None), _run(branch=None)]
    db = _Session(rows=runs)

    out = await router_mod.preview_attribution_rule(
        project_id=PROJECT, body=_body(match_pattern="*"), limit=200,
        db=db, _=_actor(), __=None,
    )

    assert out["field_present"] == 1, (
        "two of three runs carry no branch at all — without this number a rule "
        "matching '*' looks like it captures everything when it captures one"
    )


@pytest.mark.asyncio
async def test_the_preview_uses_the_same_predicate_as_the_ladder():
    """A preview that disagreed with the evaluator would be worse than none:
    it would give confidence in a rule that behaves differently in production.
    """
    import inspect

    src = inspect.getsource(router_mod.preview_attribution_rule)
    assert "release_attribution.rule_matches" in src, (
        "the preview reimplements matching instead of calling the evaluator"
    )


@pytest.mark.asyncio
async def test_a_catch_all_rule_is_visible_as_such():
    """The documented failure mode: a rule that matches EVERYTHING, where every
    run still gets attributed so nothing looks wrong."""
    runs = [_run(branch="main"), _run(branch="develop"), _run(branch="hotfix/x")]
    db = _Session(rows=runs)

    out = await router_mod.preview_attribution_rule(
        project_id=PROJECT, body=_body(match_pattern="*"), limit=200,
        db=db, _=_actor(), __=None,
    )

    assert out["matched"] == out["considered"] == 3, (
        "a catch-all rule must be plainly visible before it starts deciding "
        "where real runs land"
    )
