"""DEF-05 unit: ``release_defect_service.affects_release_sql`` and the case-insensitive ``_affects``.

The fragment is the SQL twin of the gate's ``_affects`` (asserted impact, a
NON-EMPTY array, else where the defect was found); its PostgreSQL behaviour and
its parity with ``blocking_defects`` are pinned in
``tests/integration/test_defects_affects_release_postgres.py``. Here: the text,
the binds, the sentinel, the safety of the CASE, and ``_affects``'s case rule.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.services import analytics_service
from app.services import release_defect_service as svc

R = str(uuid.uuid4())
R2 = str(uuid.uuid4())


def test_no_ids_is_no_fragment():
    params: dict = {}
    assert svc.affects_release_sql(params, []) == ""
    assert svc.affects_release_sql(params, None) == ""
    assert params == {}


def test_one_id_is_bound_lower_cased_and_used_on_both_branches():
    params: dict = {}
    sql = svc.affects_release_sql(params, [R.upper()])
    assert params == {"affects_rid_0": R.lower()}
    assert sql.startswith("AND (CASE WHEN ")
    assert "json_array_elements_text(d.affects_releases)" in sql
    assert "lower(a.rid) IN (:affects_rid_0)" in sql
    assert "ELSE lower(CAST(d.release_id AS text)) IN (:affects_rid_0)" in sql


def test_a_single_string_is_one_id_not_its_characters():
    params: dict = {}
    svc.affects_release_sql(params, R)
    assert params == {"affects_rid_0": R}


def test_several_ids_are_one_in_list_and_duplicates_collapse():
    params: dict = {}
    sql = svc.affects_release_sql(params, [R, R2, R.upper()])
    assert params == {"affects_rid_0": R, "affects_rid_1": R2}
    assert "IN (:affects_rid_0, :affects_rid_1)" in sql


def test_asserted_means_a_non_empty_array_and_is_safe_on_a_scalar():
    # json_array_length raises on the JSON null scalar: the CASE guards it, an AND would not.
    asserted = svc.asserted_impact_sql("d")
    assert asserted == (
        "(CASE WHEN json_typeof(d.affects_releases) = 'array' "
        "THEN json_array_length(d.affects_releases) > 0 ELSE false END)"
    )


def test_the_unattributed_sentinel():
    params: dict = {}
    sql = svc.affects_release_sql(params, ["Unattributed"])
    assert params == {}
    assert sql == f"AND ((NOT {svc.asserted_impact_sql('d')} AND d.release_id IS NULL))"


def test_ids_plus_the_sentinel_are_the_union():
    params: dict = {}
    sql = svc.affects_release_sql(params, [R, "unattributed"])
    assert params == {"affects_rid_0": R}
    assert " OR (NOT " in sql and sql.startswith("AND (CASE WHEN")


def test_the_alias_is_checked():
    with pytest.raises(ValueError):
        svc.affects_release_sql({}, [R], alias="d; DROP TABLE defects")
    assert "x.affects_releases" in svc.affects_release_sql({}, [R], alias="x")


def _defect(affects, found_in=None):
    return SimpleNamespace(affects_releases=affects, release_id=found_in)


@pytest.mark.parametrize("defect,expected", [
    (_defect([R.upper()]), True),                 # a legacy upper-case asserted id
    (_defect(None, R.upper()), True),             # an upper-case found-in id
    (_defect([], R), True),                       # [] is not asserted: the fallback
    (_defect({"x": 1}, R), True),                 # not an array: not asserted
    (_defect([R2], R), False),                    # asserted elsewhere wins
    (_defect(None, None), False),                 # found nowhere, asserted nothing
])
def test_affects_is_case_insensitive_and_reads_only_a_non_empty_list(defect, expected):
    assert svc._affects(defect, R.lower()) is expected
    assert svc._affects(defect, R.upper()) is expected


# ── The scope fragment of the defects queries ───────────────────────────────


def test_found_in_keeps_the_viz202_text():
    params: dict = {}
    sql, needs = analytics_service._defect_scope_sql(params, release_id=R, suite_name=None)
    assert (sql, needs) == ("AND tr.id IS NOT NULL AND tr.primary_release_id = :release_id", True)
    assert params == {"release_id": R}
    assert analytics_service._defect_scope_sql({}, release_id=None, suite_name=None) == ("", False)


def test_affects_needs_no_execution_but_the_suite_still_does():
    params: dict = {}
    sql, needs = analytics_service._defect_scope_sql(params, release_id=R, suite_name=None, release_match="affects")
    assert needs is False and "tr." not in sql and ":affects_rid_0" in sql
    params = {}
    sql, needs = analytics_service._defect_scope_sql(
        params, release_id=[R, R2], suite_name="checkout", release_match="affects",
    )
    assert needs is True
    assert sql.endswith("AND tr.id IS NOT NULL " + analytics_service._add_suite_param({}, "checkout"))
    assert params["affects_rid_1"] == R2


def test_an_unknown_release_match_is_refused():
    with pytest.raises(ValueError):
        analytics_service._defect_scope_sql({}, release_id=R, suite_name=None, release_match="bogus")


@pytest.mark.parametrize("value,fragment", [
    (None, ""),
    ("active", "AND d.resolution_status IN ('OPEN', 'IN_PROGRESS')"),
    ("ACTIVE", "AND d.resolution_status IN ('OPEN', 'IN_PROGRESS')"),
    ("open", "AND d.resolution_status = :resolution_status"),
])
def test_the_status_fragment(value, fragment):
    params: dict = {}
    assert analytics_service._defect_status_sql(params, value) == fragment
    if value and value.upper() != "ACTIVE":
        assert params == {"resolution_status": value.upper()}
    else:
        assert params == {}
