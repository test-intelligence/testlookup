"""W25d HYG-04 (the cutover) -- where the coverage map's ``needs_review_count`` comes from (contract §2.1 C-3).

The count follows the cutover flag ``suite_membership_legacy_writes``: the
legacy ``suite_memberships`` rows while it is ON (the seeded state; the
statement text is today's, byte for byte, which is why
``tests/test_coverage_map_paging.py`` and
``tests/integration/test_coverage_map_postgres.py`` stay green unedited), the
catalog's retired-and-unconfirmed rows after the cutover -- the SAME rows
Test Management's "Deleted from Suite" lists (``suite_membership_cutover.
canonical_deleted``), so the tile and the expander agree. ``build_coverage_map``
asks the flag ONCE, on the caller's session, and never for a denied scope. The
Postgres half (legacy 2 vs canonical 1 for one seeded project) is the golden
test in ``tests/integration/test_suite_membership_cutover_postgres.py``.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import pytest

from app.services import coverage_map_service as svc
from app.services import suite_membership_cutover as cutover
from app.services.analytics_scope import AnalyticsScope

FROZEN = datetime(2026, 11, 1, 12, 0, 0, tzinfo=timezone.utc)
PROJECT = uuid.UUID("11111111-1111-4111-8111-111111111111")

#: The legacy CTE exactly as W08 TRND-12 wrote it (the text the paging test pins piece by piece).
LEGACY_CTE = """, nr AS (
            SELECT DISTINCT sm.test_fingerprint AS fp
            FROM suite_memberships sm
            WHERE sm.project_id = :cm_project
              AND sm.status = 'needs_review'
        )"""
CANONICAL_CTE = """, nr AS (
            SELECT c.test_fingerprint AS fp
            FROM canonical_test_cases c
            WHERE c.project_id = :cm_project
              AND c.status <> 'active'
              AND c.retirement_confirmed_at IS NULL
        )"""


def _scope(**kwargs: Any) -> AnalyticsScope:
    base = dict(project_id=PROJECT, allowed_project_ids=None, release_ids=(), suite_names=(), days=30)
    base.update(kwargs)
    return AnalyticsScope(**base)  # type: ignore[arg-type]


def test_the_two_sources():
    assert (svc.REVIEW_SOURCE_LEGACY, svc.REVIEW_SOURCE_CANONICAL) == ("legacy", "canonical")
    assert svc.REVIEW_SOURCES == ("legacy", "canonical")


@pytest.mark.parametrize("depth_args", [(1,), (2, "s"), (3, "s", "c")])
def test_the_default_is_the_legacy_text_byte_for_byte(depth_args):
    level = svc.parse_level(*depth_args)
    default_sql, default_params = svc.build_statement(level, _scope(), now=FROZEN)
    legacy_sql, legacy_params = svc.build_statement(level, _scope(), now=FROZEN, review_source="legacy")
    assert default_sql == legacy_sql and default_params == legacy_params
    assert LEGACY_CTE in default_sql and CANONICAL_CTE not in default_sql
    assert "retirement_confirmed_at" not in default_sql


@pytest.mark.parametrize("depth_args", [(1,), (2, "s"), (3, "s", "c")])
def test_the_canonical_source_reads_the_unconfirmed_retirements(depth_args):
    level = svc.parse_level(*depth_args)
    legacy_sql, legacy_params = svc.build_statement(level, _scope(), now=FROZEN)
    sql, params = svc.build_statement(level, _scope(), now=FROZEN, review_source="canonical")
    assert CANONICAL_CTE in sql
    assert "suite_memberships" not in sql
    assert params == legacy_params and params["cm_project"] == PROJECT
    # Only the CTE differs: the flag, the count and the roll-up are the same text.
    assert sql.replace(CANONICAL_CTE, LEGACY_CTE) == legacy_sql
    assert "COUNT(*) FILTER (WHERE needs_review) AS needs_review_count" in sql
    assert "SUM(needs_review_count) AS needs_review_count" in sql


def test_the_canonical_predicate_is_the_deleted_routes_predicate():
    """One predicate, two readers: the tile's "{n} need review" and the expander's "Deleted from Suite"."""
    from sqlalchemy.dialects import postgresql

    route = str(cutover._wire_predicate(cutover.WIRE_NEEDS_REVIEW).compile(dialect=postgresql.dialect()))
    assert route == (
        "canonical_test_cases.status != %(status_1)s AND canonical_test_cases.retirement_confirmed_at IS NULL"
    )
    assert "c.status <> 'active'" in CANONICAL_CTE and "c.retirement_confirmed_at IS NULL" in CANONICAL_CTE
    assert cutover._CANONICAL_ACTIVE == "active"


def test_outside_one_project_neither_source_reads_anything():
    scope = _scope(project_id=None, allowed_project_ids=frozenset({PROJECT, uuid.uuid4()}))
    for source in svc.REVIEW_SOURCES:
        sql, params = svc.build_statement(svc.parse_level(1), scope, now=FROZEN, review_source=source)
        assert "suite_memberships" not in sql and "retirement_confirmed_at" not in sql
        assert "cm_project" not in params
        assert "CAST(NULL AS BIGINT) AS needs_review_count" in sql


def test_an_unknown_source_is_refused():
    with pytest.raises(ValueError):
        svc.build_statement(svc.parse_level(1), _scope(), now=FROZEN, review_source="suite_sync")


def test_the_definition_states_each_sources_meaning():
    legacy = svc.definitions(svc.parse_level(1), FROZEN)
    assert legacy == svc.definitions(svc.parse_level(1), FROZEN, review_source="legacy")
    assert "suite_memberships" in legacy["needs_review"] and "last suite sync" in legacy["needs_review"]
    canonical = svc.definitions(svc.parse_level(1), FROZEN, review_source="canonical")
    assert canonical["needs_review"] == (
        "needs_review_count: the node's tests the catalog retired (absent from the project's last N runs) "
        "and no one has confirmed yet, in this project. Test Management lists them under the suite's "
        "'Deleted from Suite'. Counted for one project only; null otherwise, never 0."
    )
    assert {k: v for k, v in canonical.items() if k != "needs_review"} == {
        k: v for k, v in legacy.items() if k != "needs_review"
    }


class _Db:
    """Answers the map's ONE statement with no rows and records it."""

    def __init__(self) -> None:
        self.statements: list[str] = []

    async def execute(self, statement: Any, params: Any = None) -> Any:
        self.statements.append(str(statement))

        class _Rows:
            def fetchall(self) -> list:
                return []

        return _Rows()


def _flag(monkeypatch: pytest.MonkeyPatch, value: bool) -> list[dict]:
    seen: list[dict] = []

    async def _active(db: Any, *, fresh: bool = False) -> bool:
        seen.append({"db": db, "fresh": fresh})
        return value

    monkeypatch.setattr(cutover, "legacy_model_active", _active)
    return seen


@pytest.mark.parametrize(("legacy", "source"), [(True, "legacy"), (False, "canonical")])
async def test_build_coverage_map_asks_the_flag_once_on_the_callers_session(monkeypatch, legacy, source):
    db = _Db()
    seen = _flag(monkeypatch, legacy)
    payload = await svc.build_coverage_map(db, _scope(), svc.CoverageLevel(1), now=FROZEN)
    assert seen == [{"db": db, "fresh": False}]                       # once, the caller's session, the cache allowed
    [statement] = db.statements
    assert (LEGACY_CTE if legacy else CANONICAL_CTE) in statement
    assert payload["definitions"]["needs_review"] == svc.NEEDS_REVIEW_DEFINITIONS[source]


async def test_a_denied_scope_asks_nothing_and_states_the_default(monkeypatch):
    db = _Db()
    seen = _flag(monkeypatch, False)
    payload = await svc.build_coverage_map(db, _scope(denied=True), svc.CoverageLevel(1), now=FROZEN)
    assert seen == [] and db.statements == []
    assert payload["definitions"]["needs_review"] == svc.NEEDS_REVIEW_DEFINITIONS["legacy"]
