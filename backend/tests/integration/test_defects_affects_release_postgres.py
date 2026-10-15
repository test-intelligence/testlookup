"""DEF-05 on real PostgreSQL: the defect list scoped to the defects that AFFECT a release.

``release_match=affects`` asks the release gate's own question
(``release_defect_service.affects_release_sql``): asserted impact (a NON-EMPTY
``affects_releases`` array), else where the defect was found (``release_id``).
The fixtures pin the three ways a defect asserts nothing -- SQL NULL, the JSON
``null`` scalar SQLAlchemy stores for Python ``None``, and ``[]`` -- an
upper-case legacy id, and another project's defect naming this release. The
parity test runs the gate's portable Python rule (``blocking_defects``) over
the same rows: the page and the gate must list the same set.

Requires ``TESTLOOKUP_POSTGRES_TEST_DSN`` (migrated to head) and ``REDIS_URL``.
"""
from __future__ import annotations

import pytest
from sqlalchemy import null, text

from tests.integration.defect_w06_world import SUITE, defect_world

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.fixture
async def world():
    async with defect_world() as w:
        async with w.sessions.begin() as db:
            in_r = [await w.case(db) for _ in range(4)]
            w.ids = {
                "A": await w.defect(db, affects_releases=[str(w.R)]),
                "B": await w.defect(db, release_id=w.R, affects_releases=null(), test_case_id=in_r[0],
                                    resolution_status="IN_PROGRESS", severity="CRITICAL"),
                "C": await w.defect(db, release_id=w.R, affects_releases=[str(w.R2)], test_case_id=in_r[1]),
                "D": await w.defect(db, release_id=w.R, affects_releases=None, test_case_id=in_r[2]),
                "E": await w.defect(db, release_id=w.R, affects_releases=[], test_case_id=in_r[3]),
                "F": await w.defect(db, affects_releases=[str(w.R).upper()]),
                "G": await w.defect(db, project_id=w.Q, affects_releases=[str(w.R)]),
                "H": await w.defect(db, affects_releases=null()),
            }
        w.name_of = {str(v): k for k, v in w.ids.items()}
        yield w


async def _list(w, params, headers=None):
    resp = await w.client.get("/api/v1/analytics/defects", params=params, headers=headers or w.admin)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    names = sorted(w.name_of[str(item["id"])] for item in body["items"] if str(item["id"]) in w.name_of)
    return names, body


async def test_the_fixtures_really_store_three_kinds_of_no_assertion(world):
    async with world.sessions() as db:
        rows = dict((await db.execute(text(
            "SELECT id, CASE WHEN affects_releases IS NULL THEN 'sql-null' "
            "ELSE json_typeof(affects_releases) || ':' || CAST(affects_releases AS text) END "
            "FROM defects WHERE id = ANY(:ids)"
        ), {"ids": [world.ids[k] for k in "BDE"]})).all())
    assert rows[world.ids["B"]] == "sql-null"
    assert rows[world.ids["D"]] == "null:null"
    assert rows[world.ids["E"]] == "array:[]"


async def test_affects_lists_asserted_impact_else_where_it_was_found(world):
    params = {"project_id": str(world.P), "release_id": str(world.R), "release_match": "affects", "size": 100}
    names, body = await _list(world, params)
    assert names == ["A", "B", "D", "E", "F"]
    assert body["total"] == 5
    # Another project's defect naming this release never appears (its project is not in scope).
    assert "G" not in names


async def test_found_in_is_unchanged_the_execution_s_release(world):
    params = {"project_id": str(world.P), "release_id": str(world.R), "size": 100}
    names, body = await _list(world, params)
    assert names == ["B", "C", "D", "E"]
    assert body["total"] == 4
    names_explicit, _ = await _list(world, {**params, "release_match": "found_in"})
    assert names_explicit == names


async def test_parity_with_the_release_gate(world):
    """The page (SQL) and the gate (``blocking_defects``, Python) read the same set."""
    from app.services import analytics_service, release_defect_service

    async with world.sessions() as db:
        listed = await analytics_service.list_defects(
            db, str(world.P), "ACTIVE", 1, 100, release_id=str(world.R), release_match="affects",
        )
        gate = await release_defect_service.blocking_defects(db, world.R, world.P)
    assert sorted(str(item["id"]) for item in listed["items"]) == sorted(str(d.id) for d in gate)
    assert {world.name_of[str(d.id)] for d in gate} == {"A", "B", "D", "E", "F"}


async def test_the_unattributed_sentinel_and_two_releases(world):
    base = {"project_id": str(world.P), "release_match": "affects", "size": 100}
    names, _ = await _list(world, {**base, "release_id": "unattributed"})
    assert names == ["H"], "asserted nothing AND found in no release"
    names, body = await _list(world, {**base, "release_id": [str(world.R), str(world.R2)]})
    assert names == ["A", "B", "C", "D", "E", "F"], "the union, each defect once"
    assert body["total"] == 6
    names, _ = await _list(world, {**base, "release_id": [str(world.R2), "unattributed"]})
    assert names == ["C", "H"]


async def test_the_suite_filter_still_needs_the_execution_in_affects_mode(world):
    params = {"project_id": str(world.P), "release_id": str(world.R), "release_match": "affects",
              "suite_name": SUITE, "size": 100}
    names, body = await _list(world, params)
    # A and F affect R but have no execution, so no suite can place them.
    assert names == ["B", "D", "E"]
    assert body["total"] == 3


async def test_an_unknown_release_match_is_a_422(world):
    resp = await world.client.get(
        "/api/v1/analytics/defects", params={"project_id": str(world.P), "release_match": "bogus"},
        headers=world.admin,
    )
    assert resp.status_code == 422, resp.text


async def test_active_lists_open_and_in_progress(world):
    names, body = await _list(world, {"project_id": str(world.P), "resolution_status": "active", "size": 100})
    assert names == ["A", "B", "C", "D", "E", "F", "H"]
    in_progress, _ = await _list(world, {"project_id": str(world.P), "resolution_status": "IN_PROGRESS"})
    assert in_progress == ["B"]
