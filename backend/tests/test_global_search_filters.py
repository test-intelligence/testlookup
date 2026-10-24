"""W12 SRCH-04: the structured filters inside the global-search adapters.

Every property here is about the SQL Postgres receives (the approach of
``tests/regression/test_global_search_tags_use_the_index.py``): a filter is
an AND term inside EACH half of the test-case UNION and inside the single
statement of every other adapter -- never an OR across tables -- and with no
filter every statement is byte-identical to the one built before W12.

``BASELINE_SQL`` was captured from the adapters BEFORE the W12 edit (each
statement from its first ``FROM`` on: the column lists grow with the ORM, the
shape must not) for four calls of ``global_search(db, "checkout", ...)``.
The live-database proof is ``tests/integration/test_global_search_filters_postgres.py``.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy.dialects import postgresql

from app.services import global_search_service as gs
from app.services.global_search_service import AdapterFilters
from app.services.search_query_parser import parse_query

PROJECT = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
OTHER = uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
RELEASE = uuid.UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")
NOW = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)

#: global_search's adapters run in sorted entity-type order.
ADAPTER_ORDER = ["defect", "flaky_test", "release", "suite", "test_case", "test_run"]
CASES = {
    "plain": dict(),
    "pinned_days": dict(project_id=str(PROJECT), days=7),
    "allowed": dict(allowed_project_ids={PROJECT, OTHER}),
    "one_type": dict(entity_types={"test_case"}, page=2, size=20),
}

BASELINE_SQL: dict[tuple[str, str], str] = {
    ('plain', 'defect'): (
        '\n'
        'FROM defects JOIN projects ON projects.id = defects.project_id \n'
        "WHERE defects.jira_ticket_id ILIKE %(jira_ticket_id_1)s ESCAPE '\\\\' AND projects.is_active IS true ORDER BY defects.created_at DESC \n"
        ' LIMIT %(param_1)s'
    ),
    ('plain', 'flaky_test'): (
        '\n'
        'FROM test_case_history JOIN test_cases ON test_case_history.test_case_id = test_cases.id JOIN test_runs ON test_case_history.test_run_id = test_runs.id JOIN projects ON projects.id = test_runs.project_id \n'
        "WHERE test_cases.test_name ILIKE %(test_name_1)s ESCAPE '\\\\' AND projects.is_active IS true GROUP BY test_case_history.test_fingerprint, test_runs.project_id \n"
        'HAVING count(*) >= %(count_1)s AND (count(*) FILTER (WHERE test_case_history.status IN (__[POSTCOMPILE_status_1]))) * %(param_1)s >= count(*) * %(count_2)s AND (count(*) FILTER (WHERE test_case_history.status IN (__[POSTCOMPILE_status_1]))) * %(param_2)s <= count(*) * %(count_3)s \n'
        ' LIMIT %(param_3)s'
    ),
    ('plain', 'release'): (
        '\n'
        'FROM releases JOIN projects ON projects.id = releases.project_id \n'
        "WHERE (releases.name ILIKE %(name_1)s ESCAPE '\\\\' OR releases.version ILIKE %(version_1)s ESCAPE '\\\\') AND projects.is_active IS true ORDER BY releases.created_at DESC \n"
        ' LIMIT %(param_1)s'
    ),
    ('plain', 'suite'): (
        '\n'
        'FROM test_cases JOIN test_runs ON test_cases.test_run_id = test_runs.id JOIN projects ON projects.id = test_runs.project_id \n'
        "WHERE coalesce(CASE WHEN (test_runs.trigger_source = %(trigger_source_1)s) THEN nullif(trim(test_runs.primary_suite_name), %(nullif_1)s) END, nullif(trim(test_cases.suite_name), %(nullif_2)s)) ILIKE %(coalesce_1)s ESCAPE '\\\\' AND coalesce(CASE WHEN (test_runs.trigger_source = %(trigger_source_1)s) THEN nullif(trim(test_runs.primary_suite_name), %(nullif_1)s) END, nullif(trim(test_cases.suite_name), %(nullif_2)s)) IS NOT NULL AND projects.is_active IS true GROUP BY coalesce(CASE WHEN (test_runs.trigger_source = %(trigger_source_1)s) THEN nullif(trim(test_runs.primary_suite_name), %(nullif_1)s) END, nullif(trim(test_cases.suite_name), %(nullif_2)s)), test_runs.project_id ORDER BY count(*) DESC \n"
        ' LIMIT %(param_1)s'
    ),
    ('plain', 'test_case'): (
        '\n'
        'FROM test_cases JOIN test_runs ON test_cases.test_run_id = test_runs.id JOIN (SELECT own_text.id AS id, own_text.created_at AS created_at \n'
        'FROM (SELECT test_cases.id AS id, test_cases.created_at AS created_at \n'
        'FROM test_cases JOIN test_runs ON test_cases.test_run_id = test_runs.id JOIN projects ON projects.id = test_runs.project_id \n'
        "WHERE (test_cases.test_name ILIKE %(test_name_1)s ESCAPE '\\\\' OR test_cases.suite_name ILIKE %(suite_name_1)s ESCAPE '\\\\' OR test_cases.error_message ILIKE %(error_message_1)s ESCAPE '\\\\' OR CAST(test_cases.tags AS TEXT) ILIKE %(param_1)s ESCAPE '\\\\') AND projects.is_active IS true ORDER BY test_cases.created_at DESC \n"
        ' LIMIT %(param_2)s) AS own_text UNION SELECT run_suite.id AS id, run_suite.created_at AS created_at \n'
        'FROM (SELECT test_cases.id AS id, test_cases.created_at AS created_at \n'
        'FROM test_cases JOIN test_runs ON test_cases.test_run_id = test_runs.id JOIN projects ON projects.id = test_runs.project_id \n'
        "WHERE test_runs.primary_suite_name ILIKE %(primary_suite_name_1)s ESCAPE '\\\\' AND projects.is_active IS true ORDER BY test_cases.created_at DESC \n"
        ' LIMIT %(param_3)s) AS run_suite) AS hits ON hits.id = test_cases.id ORDER BY test_cases.created_at DESC \n'
        ' LIMIT %(param_4)s'
    ),
    ('plain', 'test_run'): (
        '\n'
        'FROM test_runs JOIN projects ON projects.id = test_runs.project_id \n'
        "WHERE (test_runs.build_number ILIKE %(build_number_1)s ESCAPE '\\\\' OR test_runs.branch ILIKE %(branch_1)s ESCAPE '\\\\' OR test_runs.jenkins_job ILIKE %(jenkins_job_1)s ESCAPE '\\\\' OR CAST(test_runs.tags AS VARCHAR) ILIKE %(param_1)s ESCAPE '\\\\') AND projects.is_active IS true ORDER BY test_runs.created_at DESC \n"
        ' LIMIT %(param_2)s'
    ),
    ('pinned_days', 'defect'): (
        '\n'
        'FROM defects JOIN projects ON projects.id = defects.project_id \n'
        "WHERE defects.jira_ticket_id ILIKE %(jira_ticket_id_1)s ESCAPE '\\\\' AND projects.is_active IS true AND defects.project_id = %(project_id_1)s::UUID AND defects.created_at >= %(created_at_1)s ORDER BY defects.created_at DESC \n"
        ' LIMIT %(param_1)s'
    ),
    ('pinned_days', 'flaky_test'): (
        '\n'
        'FROM test_case_history JOIN test_cases ON test_case_history.test_case_id = test_cases.id JOIN test_runs ON test_case_history.test_run_id = test_runs.id JOIN projects ON projects.id = test_runs.project_id \n'
        "WHERE test_cases.test_name ILIKE %(test_name_1)s ESCAPE '\\\\' AND projects.is_active IS true AND test_runs.project_id = %(project_id_1)s::UUID AND test_case_history.created_at >= %(created_at_1)s GROUP BY test_case_history.test_fingerprint, test_runs.project_id \n"
        'HAVING count(*) >= %(count_1)s AND (count(*) FILTER (WHERE test_case_history.status IN (__[POSTCOMPILE_status_1]))) * %(param_1)s >= count(*) * %(count_2)s AND (count(*) FILTER (WHERE test_case_history.status IN (__[POSTCOMPILE_status_1]))) * %(param_2)s <= count(*) * %(count_3)s \n'
        ' LIMIT %(param_3)s'
    ),
    ('pinned_days', 'release'): (
        '\n'
        'FROM releases JOIN projects ON projects.id = releases.project_id \n'
        "WHERE (releases.name ILIKE %(name_1)s ESCAPE '\\\\' OR releases.version ILIKE %(version_1)s ESCAPE '\\\\') AND projects.is_active IS true AND releases.project_id = %(project_id_1)s::UUID AND releases.created_at >= %(created_at_1)s ORDER BY releases.created_at DESC \n"
        ' LIMIT %(param_1)s'
    ),
    ('pinned_days', 'suite'): (
        '\n'
        'FROM test_cases JOIN test_runs ON test_cases.test_run_id = test_runs.id JOIN projects ON projects.id = test_runs.project_id \n'
        "WHERE coalesce(CASE WHEN (test_runs.trigger_source = %(trigger_source_1)s) THEN nullif(trim(test_runs.primary_suite_name), %(nullif_1)s) END, nullif(trim(test_cases.suite_name), %(nullif_2)s)) ILIKE %(coalesce_1)s ESCAPE '\\\\' AND coalesce(CASE WHEN (test_runs.trigger_source = %(trigger_source_1)s) THEN nullif(trim(test_runs.primary_suite_name), %(nullif_1)s) END, nullif(trim(test_cases.suite_name), %(nullif_2)s)) IS NOT NULL AND projects.is_active IS true AND test_runs.project_id = %(project_id_1)s::UUID AND test_cases.created_at >= %(created_at_1)s GROUP BY coalesce(CASE WHEN (test_runs.trigger_source = %(trigger_source_1)s) THEN nullif(trim(test_runs.primary_suite_name), %(nullif_1)s) END, nullif(trim(test_cases.suite_name), %(nullif_2)s)), test_runs.project_id ORDER BY count(*) DESC \n"
        ' LIMIT %(param_1)s'
    ),
    ('pinned_days', 'test_case'): (
        '\n'
        'FROM test_cases JOIN test_runs ON test_cases.test_run_id = test_runs.id JOIN (SELECT own_text.id AS id, own_text.created_at AS created_at \n'
        'FROM (SELECT test_cases.id AS id, test_cases.created_at AS created_at \n'
        'FROM test_cases JOIN test_runs ON test_cases.test_run_id = test_runs.id JOIN projects ON projects.id = test_runs.project_id \n'
        "WHERE (test_cases.test_name ILIKE %(test_name_1)s ESCAPE '\\\\' OR test_cases.suite_name ILIKE %(suite_name_1)s ESCAPE '\\\\' OR test_cases.error_message ILIKE %(error_message_1)s ESCAPE '\\\\' OR CAST(test_cases.tags AS TEXT) ILIKE %(param_1)s ESCAPE '\\\\') AND projects.is_active IS true AND test_runs.project_id = %(project_id_1)s::UUID AND test_cases.created_at >= %(created_at_1)s ORDER BY test_cases.created_at DESC \n"
        ' LIMIT %(param_2)s) AS own_text UNION SELECT run_suite.id AS id, run_suite.created_at AS created_at \n'
        'FROM (SELECT test_cases.id AS id, test_cases.created_at AS created_at \n'
        'FROM test_cases JOIN test_runs ON test_cases.test_run_id = test_runs.id JOIN projects ON projects.id = test_runs.project_id \n'
        "WHERE test_runs.primary_suite_name ILIKE %(primary_suite_name_1)s ESCAPE '\\\\' AND projects.is_active IS true AND test_runs.project_id = %(project_id_2)s::UUID AND test_cases.created_at >= %(created_at_2)s ORDER BY test_cases.created_at DESC \n"
        ' LIMIT %(param_3)s) AS run_suite) AS hits ON hits.id = test_cases.id ORDER BY test_cases.created_at DESC \n'
        ' LIMIT %(param_4)s'
    ),
    ('pinned_days', 'test_run'): (
        '\n'
        'FROM test_runs JOIN projects ON projects.id = test_runs.project_id \n'
        "WHERE (test_runs.build_number ILIKE %(build_number_1)s ESCAPE '\\\\' OR test_runs.branch ILIKE %(branch_1)s ESCAPE '\\\\' OR test_runs.jenkins_job ILIKE %(jenkins_job_1)s ESCAPE '\\\\' OR CAST(test_runs.tags AS VARCHAR) ILIKE %(param_1)s ESCAPE '\\\\') AND projects.is_active IS true AND test_runs.project_id = %(project_id_1)s::UUID AND test_runs.created_at >= %(created_at_1)s ORDER BY test_runs.created_at DESC \n"
        ' LIMIT %(param_2)s'
    ),
    ('allowed', 'defect'): (
        '\n'
        'FROM defects JOIN projects ON projects.id = defects.project_id \n'
        "WHERE defects.jira_ticket_id ILIKE %(jira_ticket_id_1)s ESCAPE '\\\\' AND projects.is_active IS true AND defects.project_id IN (__[POSTCOMPILE_project_id_1]) ORDER BY defects.created_at DESC \n"
        ' LIMIT %(param_1)s'
    ),
    ('allowed', 'flaky_test'): (
        '\n'
        'FROM test_case_history JOIN test_cases ON test_case_history.test_case_id = test_cases.id JOIN test_runs ON test_case_history.test_run_id = test_runs.id JOIN projects ON projects.id = test_runs.project_id \n'
        "WHERE test_cases.test_name ILIKE %(test_name_1)s ESCAPE '\\\\' AND projects.is_active IS true AND test_runs.project_id IN (__[POSTCOMPILE_project_id_1]) GROUP BY test_case_history.test_fingerprint, test_runs.project_id \n"
        'HAVING count(*) >= %(count_1)s AND (count(*) FILTER (WHERE test_case_history.status IN (__[POSTCOMPILE_status_1]))) * %(param_1)s >= count(*) * %(count_2)s AND (count(*) FILTER (WHERE test_case_history.status IN (__[POSTCOMPILE_status_1]))) * %(param_2)s <= count(*) * %(count_3)s \n'
        ' LIMIT %(param_3)s'
    ),
    ('allowed', 'release'): (
        '\n'
        'FROM releases JOIN projects ON projects.id = releases.project_id \n'
        "WHERE (releases.name ILIKE %(name_1)s ESCAPE '\\\\' OR releases.version ILIKE %(version_1)s ESCAPE '\\\\') AND projects.is_active IS true AND releases.project_id IN (__[POSTCOMPILE_project_id_1]) ORDER BY releases.created_at DESC \n"
        ' LIMIT %(param_1)s'
    ),
    ('allowed', 'suite'): (
        '\n'
        'FROM test_cases JOIN test_runs ON test_cases.test_run_id = test_runs.id JOIN projects ON projects.id = test_runs.project_id \n'
        "WHERE coalesce(CASE WHEN (test_runs.trigger_source = %(trigger_source_1)s) THEN nullif(trim(test_runs.primary_suite_name), %(nullif_1)s) END, nullif(trim(test_cases.suite_name), %(nullif_2)s)) ILIKE %(coalesce_1)s ESCAPE '\\\\' AND coalesce(CASE WHEN (test_runs.trigger_source = %(trigger_source_1)s) THEN nullif(trim(test_runs.primary_suite_name), %(nullif_1)s) END, nullif(trim(test_cases.suite_name), %(nullif_2)s)) IS NOT NULL AND projects.is_active IS true AND test_runs.project_id IN (__[POSTCOMPILE_project_id_1]) GROUP BY coalesce(CASE WHEN (test_runs.trigger_source = %(trigger_source_1)s) THEN nullif(trim(test_runs.primary_suite_name), %(nullif_1)s) END, nullif(trim(test_cases.suite_name), %(nullif_2)s)), test_runs.project_id ORDER BY count(*) DESC \n"
        ' LIMIT %(param_1)s'
    ),
    ('allowed', 'test_case'): (
        '\n'
        'FROM test_cases JOIN test_runs ON test_cases.test_run_id = test_runs.id JOIN (SELECT own_text.id AS id, own_text.created_at AS created_at \n'
        'FROM (SELECT test_cases.id AS id, test_cases.created_at AS created_at \n'
        'FROM test_cases JOIN test_runs ON test_cases.test_run_id = test_runs.id JOIN projects ON projects.id = test_runs.project_id \n'
        "WHERE (test_cases.test_name ILIKE %(test_name_1)s ESCAPE '\\\\' OR test_cases.suite_name ILIKE %(suite_name_1)s ESCAPE '\\\\' OR test_cases.error_message ILIKE %(error_message_1)s ESCAPE '\\\\' OR CAST(test_cases.tags AS TEXT) ILIKE %(param_1)s ESCAPE '\\\\') AND projects.is_active IS true AND test_runs.project_id IN (__[POSTCOMPILE_project_id_1]) ORDER BY test_cases.created_at DESC \n"
        ' LIMIT %(param_2)s) AS own_text UNION SELECT run_suite.id AS id, run_suite.created_at AS created_at \n'
        'FROM (SELECT test_cases.id AS id, test_cases.created_at AS created_at \n'
        'FROM test_cases JOIN test_runs ON test_cases.test_run_id = test_runs.id JOIN projects ON projects.id = test_runs.project_id \n'
        "WHERE test_runs.primary_suite_name ILIKE %(primary_suite_name_1)s ESCAPE '\\\\' AND projects.is_active IS true AND test_runs.project_id IN (__[POSTCOMPILE_project_id_2]) ORDER BY test_cases.created_at DESC \n"
        ' LIMIT %(param_3)s) AS run_suite) AS hits ON hits.id = test_cases.id ORDER BY test_cases.created_at DESC \n'
        ' LIMIT %(param_4)s'
    ),
    ('allowed', 'test_run'): (
        '\n'
        'FROM test_runs JOIN projects ON projects.id = test_runs.project_id \n'
        "WHERE (test_runs.build_number ILIKE %(build_number_1)s ESCAPE '\\\\' OR test_runs.branch ILIKE %(branch_1)s ESCAPE '\\\\' OR test_runs.jenkins_job ILIKE %(jenkins_job_1)s ESCAPE '\\\\' OR CAST(test_runs.tags AS VARCHAR) ILIKE %(param_1)s ESCAPE '\\\\') AND projects.is_active IS true AND test_runs.project_id IN (__[POSTCOMPILE_project_id_1]) ORDER BY test_runs.created_at DESC \n"
        ' LIMIT %(param_2)s'
    ),
    ('one_type', 'test_case'): (
        '\n'
        'FROM test_cases JOIN test_runs ON test_cases.test_run_id = test_runs.id JOIN (SELECT own_text.id AS id, own_text.created_at AS created_at \n'
        'FROM (SELECT test_cases.id AS id, test_cases.created_at AS created_at \n'
        'FROM test_cases JOIN test_runs ON test_cases.test_run_id = test_runs.id JOIN projects ON projects.id = test_runs.project_id \n'
        "WHERE (test_cases.test_name ILIKE %(test_name_1)s ESCAPE '\\\\' OR test_cases.suite_name ILIKE %(suite_name_1)s ESCAPE '\\\\' OR test_cases.error_message ILIKE %(error_message_1)s ESCAPE '\\\\' OR CAST(test_cases.tags AS TEXT) ILIKE %(param_1)s ESCAPE '\\\\') AND projects.is_active IS true ORDER BY test_cases.created_at DESC \n"
        ' LIMIT %(param_2)s) AS own_text UNION SELECT run_suite.id AS id, run_suite.created_at AS created_at \n'
        'FROM (SELECT test_cases.id AS id, test_cases.created_at AS created_at \n'
        'FROM test_cases JOIN test_runs ON test_cases.test_run_id = test_runs.id JOIN projects ON projects.id = test_runs.project_id \n'
        "WHERE test_runs.primary_suite_name ILIKE %(primary_suite_name_1)s ESCAPE '\\\\' AND projects.is_active IS true ORDER BY test_cases.created_at DESC \n"
        ' LIMIT %(param_3)s) AS run_suite) AS hits ON hits.id = test_cases.id ORDER BY test_cases.created_at DESC \n'
        ' LIMIT %(param_4)s'
    ),
}


class _Result:
    def __init__(self, rows=()):
        self._rows = list(rows)

    def all(self):
        return self._rows

    def scalars(self):
        return self


class _RecordingDB:
    def __init__(self, rows=()):
        self.statements = []
        self._rows = rows

    async def execute(self, stmt, *args, **kwargs):
        self.statements.append(stmt)
        return _Result(self._rows)


def _sql(stmt, *, literal: bool = False) -> str:
    kwargs = {"compile_kwargs": {"literal_binds": True}} if literal else {}
    return str(stmt.compile(dialect=postgresql.dialect(), **kwargs))


def _tail(sql: str) -> str:
    return sql[sql.index("\nFROM "):]


def _parse(q: str):
    return parse_query(q, now=NOW)


# ── No filter: byte-identical to before W12 ──────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("case", sorted(CASES))
@pytest.mark.parametrize("with_parsed", [False, True], ids=["parsed_none", "parsed_checkout"])
async def test_no_filter_sql_is_byte_identical_to_before(case, with_parsed):
    db = _RecordingDB()
    extra = {"parsed": _parse("checkout")} if with_parsed else {}
    await gs.global_search(db, "checkout", **CASES[case], **extra)
    names = ["test_case"] if case == "one_type" else ADAPTER_ORDER
    assert len(db.statements) == len(names)
    for name, stmt in zip(names, db.statements):
        assert _tail(_sql(stmt)) == BASELINE_SQL[(case, name)], f"{case}/{name} changed shape"


@pytest.mark.asyncio
async def test_parsed_none_answers_without_a_parsed_block_and_the_router_shape_with_one():
    plain = await gs.global_search(_RecordingDB(), "checkout")
    parsed = await gs.global_search(_RecordingDB(), "checkout", parsed=_parse("checkout"))
    assert "parsed" not in plain
    assert parsed.pop("parsed")["searched_entity_types"] == list(ADAPTER_ORDER_BY_VOCAB)
    assert parsed == plain


ADAPTER_ORDER_BY_VOCAB = ("test_case", "test_run", "suite", "defect", "flaky_test", "release")


# ── The test-case UNION: AND terms in BOTH halves ────────────────────────────


def _where_clauses(sql: str) -> list[str]:
    return [m.group(1) for m in re.finditer(r"where (.+?) order by", sql, re.S)]


def _paren_group(clause: str, opener: str) -> str:
    """The parenthesised group that starts at ``opener`` (``(`` included)."""
    start = clause.index(opener)
    depth = 0
    for index in range(start, len(clause)):
        if clause[index] == "(":
            depth += 1
        elif clause[index] == ")":
            depth -= 1
            if depth == 0:
                return clause[start:index + 1]
    raise AssertionError("unbalanced group")


async def _case_sql(filters: AdapterFilters | None, q: str = "checkout") -> str:
    db = _RecordingDB()
    await gs._search_test_cases(db, q, None, None, filters=filters)
    [stmt] = db.statements
    return _sql(stmt, literal=True).lower()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("filters", "term"),
    [
        (AdapterFilters(statuses=frozenset({"failed", "error"})), "test_cases.status in ('broken', 'failed')"),
        (AdapterFilters(branches=("Main",)), "lower(test_runs.branch) in ('main')"),
        (AdapterFilters(envs=("QA",)), "lower(test_runs.environment) in ('qa')"),
        (AdapterFilters(release_ids=(RELEASE,)), f"test_runs.primary_release_id in ('{RELEASE}')"),
        (AdapterFilters(owners=("team a",)), "test_cases.owner ilike '%%team a%%'"),
        (
            AdapterFilters(suites=("API Regression",)),
            "lower(coalesce(case when (test_runs.trigger_source = 'live_stream') then "
            "nullif(trim(test_runs.primary_suite_name), '') end, nullif(trim(test_cases.suite_name), ''))) "
            "= 'api regression'",
        ),
    ],
    ids=["status", "branch", "env", "release", "owner", "suite"],
)
async def test_each_filter_is_an_and_term_of_both_union_halves(filters, term):
    sql = await _case_sql(filters)
    halves = _where_clauses(sql)
    assert len(halves) == 2
    for clause in halves:
        assert term in clause, f"a half of the UNION ignores the filter:\n{clause}"
        assert f" and {term}" in clause, f"the filter is not an AND term:\n{clause}"


@pytest.mark.asyncio
async def test_with_suite_the_own_text_or_is_unchanged_and_holds_no_run_column():
    plain = _where_clauses(await _case_sql(None))
    filtered = _where_clauses(await _case_sql(AdapterFilters(suites=("API Regression",))))
    own_text_or = _paren_group(filtered[0], "(test_cases.test_name ilike")
    assert own_text_or == _paren_group(plain[0], "(test_cases.test_name ilike")
    assert "primary_suite_name" not in own_text_or, (
        "the run-level suite is inside the test-case OR again: a bitmap OR cannot "
        "combine indexes across tables"
    )
    # The run-level half still matches the label on its own.
    assert filtered[1].startswith("test_runs.primary_suite_name ilike")


@pytest.mark.asyncio
async def test_owner_is_an_or_on_one_column_only():
    sql = await _case_sql(AdapterFilters(owners=("a", "b")))
    for clause in _where_clauses(sql):
        group = _paren_group(clause, "(test_cases.owner ilike")
        assert group.count("test_cases.owner ilike") == 2
        assert "test_runs." not in group


@pytest.mark.asyncio
async def test_each_half_stays_bounded_with_filters():
    db = _RecordingDB()
    await gs._search_test_cases(
        db, "x", None, None, override_limit=25, filters=AdapterFilters(statuses=frozenset({"failed"})),
    )
    assert _sql(db.statements[0], literal=True).lower().count("limit 25") == 3


# ── The other adapters ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_filters_are_and_terms_of_the_one_statement():
    db = _RecordingDB()
    await gs._search_test_runs(
        db, "x", None, None,
        filters=AdapterFilters(
            statuses=frozenset({"failed"}), suites=("API",), branches=("main",), envs=("qa",),
            release_ids=(RELEASE,),
        ),
    )
    sql = _sql(db.statements[0], literal=True).lower()
    where = sql.split("\nwhere ", 1)[1]
    assert "and test_runs.status in ('failed')" in where
    assert "and lower(test_runs.branch) in ('main')" in where
    assert "and lower(test_runs.environment) in ('qa')" in where
    assert f"and test_runs.primary_release_id in ('{RELEASE}')" in where
    # run_in_suite_scope_clause: a run with a row in the suite (or, with no rows, its label).
    assert "exists (select test_cases.id" in where
    assert "test_cases.test_run_id = test_runs.id" in where


@pytest.mark.asyncio
async def test_suite_and_flaky_filters_use_the_effective_suite():
    for adapter in (gs._search_suites, gs._search_flaky_tests):
        db = _RecordingDB()
        await adapter(db, "x", None, None, filters=AdapterFilters(suites=("API",), branches=("main",)))
        sql = _sql(db.statements[0], literal=True).lower()
        assert (
            "and lower(coalesce(case when (test_runs.trigger_source = 'live_stream') then "
            "nullif(trim(test_runs.primary_suite_name), '') end, nullif(trim(test_cases.suite_name), ''))) = 'api'"
        ) in sql, adapter.__name__
        assert "and lower(test_runs.branch) in ('main')" in sql, adapter.__name__


@pytest.mark.asyncio
async def test_defect_owner_and_release_ids():
    db = _RecordingDB()
    await gs._search_defects(db, "x", None, None, filters=AdapterFilters(owners=("payments",)))
    assert "and defects.owner_team ilike '%%payments%%'" in _sql(db.statements[0], literal=True).lower()

    db = _RecordingDB()
    await gs._search_releases(db, "x", None, None, filters=AdapterFilters(release_ids=(RELEASE,)))
    assert f"and releases.id in ('{RELEASE}')" in _sql(db.statements[0], literal=True).lower()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "adapter",
    [gs._search_test_cases, gs._search_test_runs, gs._search_suites, gs._search_flaky_tests, gs._search_releases],
)
async def test_a_release_that_resolved_to_nothing_issues_no_statement(adapter):
    db = _RecordingDB()
    assert await adapter(db, "x", None, None, filters=AdapterFilters(release_ids=())) == []
    assert db.statements == []


# ── global_search: unsupported kinds, type:, release: ───────────────────────


@pytest.mark.asyncio
async def test_an_entity_that_cannot_apply_a_filter_is_not_searched():
    db = _RecordingDB()
    result = await gs.global_search(db, "x", entity_types={"defect"}, parsed=_parse("branch:main x"))
    assert db.statements == []
    assert result["parsed"]["unsupported_by"] == {"defect": ["branch"]}
    assert result["parsed"]["searched_entity_types"] == []
    assert result["parsed"]["notes"] == ["branch: is not supported for Defects, so no defects were searched."]


@pytest.mark.asyncio
async def test_status_error_cannot_apply_to_runs():
    db = _RecordingDB()
    result = await gs.global_search(
        db, "x", entity_types={"test_case", "test_run"}, parsed=_parse("status:error x"),
    )
    assert result["parsed"]["unsupported_by"] == {"test_run": ["status"]}
    assert result["parsed"]["searched_entity_types"] == ["test_case"]
    assert len(db.statements) == 1


@pytest.mark.asyncio
async def test_status_failed_over_all_searches_tests_and_runs_only():
    db = _RecordingDB()
    result = await gs.global_search(db, "", parsed=_parse("status:failed"))
    block = result["parsed"]
    assert block["searched_entity_types"] == ["test_case", "test_run"]
    assert block["unsupported_by"] == {
        "suite": ["status"], "defect": ["status"], "flaky_test": ["status"], "release": ["status"],
    }, "unsupported_by stays per kind"
    assert block["notes"] == [
        "status: applies only to Tests and Runs, so suites, defects, flaky tests and releases were not searched."
    ], "one condensed sentence, not four"
    assert len(db.statements) == 2
    # One test-case filter, so the adapter's per-type cap is the fan-out one (two kinds searched).
    assert result["counts_are_exact"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("q", "project_id", "unsupported_by", "notes"),
    [
        ("status:failed", str(PROJECT),
         {"suite": ["status"], "defect": ["status"], "flaky_test": ["status"], "release": ["status"]},
         ["status: applies only to Tests and Runs, so suites, defects, flaky tests and releases were not searched."]),
        ("branch:main", None, {"defect": ["branch"], "release": ["branch"]},
         ["branch: applies only to Tests, Runs, Suites and Flaky, so defects and releases were not searched."]),
        ("owner:x", None,
         {"test_run": ["owner"], "suite": ["owner"], "flaky_test": ["owner"], "release": ["owner"]},
         ["owner: applies only to Tests and Defects, so runs, suites, flaky tests and releases were not searched."]),
        ("status:error", None,
         {"test_run": ["status"], "suite": ["status"], "defect": ["status"], "flaky_test": ["status"],
          "release": ["status"]},
         ["status: applies only to Tests, so runs, suites, defects, flaky tests and releases were not searched."]),
    ],
    ids=["status_failed_in_a_project", "branch", "owner", "status_error"],
)
async def test_a_key_several_kinds_cannot_apply_is_one_note(q, project_id, unsupported_by, notes):
    result = await gs.global_search(_RecordingDB(), q, project_id=project_id, parsed=_parse(q))
    assert result["parsed"]["unsupported_by"] == unsupported_by
    assert result["parsed"]["notes"] == notes


@pytest.mark.asyncio
async def test_a_single_kind_not_searched_keeps_its_own_sentence():
    result = await gs.global_search(_RecordingDB(), "x", entity_types={"test_case", "defect"},
                                    parsed=_parse("branch:main x"))
    assert result["parsed"]["notes"] == ["branch: is not supported for Defects, so no defects were searched."]


@pytest.mark.asyncio
async def test_type_narrows_the_entity_types():
    db = _RecordingDB()
    result = await gs.global_search(db, "x", parsed=_parse("type:run x"))
    assert result["parsed"]["searched_entity_types"] == ["test_run"]
    assert len(db.statements) == 1
    assert "from test_runs" in _sql(db.statements[0]).lower()


@pytest.mark.asyncio
async def test_type_outside_the_requested_scope_searches_nothing_and_says_so():
    db = _RecordingDB()
    result = await gs.global_search(db, "x", entity_types={"defect"}, parsed=_parse("type:run x"))
    assert db.statements == []
    assert result["total"] == 0
    assert result["parsed"]["notes"] == ["type:run is outside the Defects scope: nothing was searched."]


@pytest.mark.asyncio
async def test_since_overrides_days():
    db = _RecordingDB()
    await gs.global_search(db, "x", entity_types={"defect"}, days=300, parsed=_parse("since:2026-10-01 x"))
    sql = _sql(db.statements[0], literal=True).lower()
    assert "defects.created_at >= '2026-10-01 00:00:00+00:00'" in sql


@pytest.mark.asyncio
async def test_an_unresolved_release_searches_nothing_that_needs_it():
    db = _RecordingDB()
    result = await gs.global_search(
        db, "x", parsed=_parse('release:"9.9" x'), release_ids=(), release_unresolved="9.9",
    )
    # defects cannot apply release: (not searched); the five that can find nothing without a statement.
    assert db.statements == []
    assert result["parsed"]["release_unresolved"] == "9.9"
    assert result["parsed"]["notes"][-1] == 'No release named "9.9" in your projects.'


@pytest.mark.asyncio
async def test_a_typed_release_the_caller_did_not_resolve_fails_closed():
    db = _RecordingDB()
    await gs.global_search(db, "x", entity_types={"test_case"}, parsed=_parse("release:2026.11 x"))
    assert db.statements == []


@pytest.mark.asyncio
async def test_the_free_text_not_the_raw_query_is_matched():
    db = _RecordingDB()
    result = await gs.global_search(db, "status:failed checkout", entity_types={"test_case"},
                                    parsed=_parse("status:failed checkout"))
    sql = _sql(db.statements[0], literal=True).lower()
    assert "ilike '%%checkout%%'" in sql
    assert "status:failed" not in sql
    assert result["query"] == "status:failed checkout"


@pytest.mark.asyncio
async def test_an_empty_membership_set_answers_the_parsed_block_without_a_statement():
    db = _RecordingDB()
    result = await gs.global_search(db, "x", allowed_project_ids=set(), parsed=_parse("x"))
    assert db.statements == []
    assert result["parsed"]["searched_entity_types"] == []


# ── resolve_release_values: one statement, inside the scope ─────────────────


class _Row:
    def __init__(self, id, name, version):
        self.id, self.name, self.version = id, name, version


@pytest.mark.asyncio
async def test_release_resolution_is_one_statement_scoped_to_the_pinned_project():
    db = _RecordingDB(rows=[_Row(RELEASE, "2026.11", "v11")])
    ids, unresolved = await gs.resolve_release_values(
        db, ["2026.11", "V11", str(RELEASE), "nope"], project_id=PROJECT, allowed_project_ids=None,
    )
    assert ids == (RELEASE,)
    assert unresolved == "nope"
    [stmt] = db.statements
    sql = _sql(stmt).lower()
    where = sql.split("\nwhere ", 1)[1]
    assert "projects.is_active is true" in where
    assert "releases.project_id = %(project_id_1)s::uuid" in where
    assert "releases.id = any (%(release_ids)s::uuid[])" in where
    assert "lower(releases.name) = any (%(release_names)s::varchar[])" in where
    assert "lower(releases.version) = any (%(release_names)s::varchar[])" in where
    assert "limit" in sql
    params = stmt.compile(dialect=postgresql.dialect()).params
    assert params["project_id_1"] == PROJECT
    assert params["release_names"] == ["2026.11", "v11", str(RELEASE), "nope"]


@pytest.mark.asyncio
async def test_release_resolution_for_a_member_is_scoped_to_their_projects():
    db = _RecordingDB(rows=[])
    ids, unresolved = await gs.resolve_release_values(
        db, [str(RELEASE)], project_id=None, allowed_project_ids={PROJECT},
    )
    # The foreign release is outside the scope, so the statement returns nothing:
    # the same answer as an unknown or a malformed value.
    assert (ids, unresolved) == ((), str(RELEASE))
    [stmt] = db.statements
    where = _sql(stmt).lower().split("\nwhere ", 1)[1]
    assert "releases.project_id = any (%(release_scope_ids)s::uuid[])" in where
    assert stmt.compile(dialect=postgresql.dialect()).params["release_scope_ids"] == [PROJECT]


@pytest.mark.asyncio
async def test_release_resolution_without_values_or_memberships_issues_no_statement():
    db = _RecordingDB()
    assert await gs.resolve_release_values(db, [], project_id=None, allowed_project_ids=None) == ((), None)
    assert await gs.resolve_release_values(db, ["x"], project_id=None, allowed_project_ids=set()) == ((), "x")
    assert db.statements == []


@pytest.mark.asyncio
async def test_a_malformed_release_value_reads_like_an_unknown_one():
    db = _RecordingDB(rows=[])
    assert await gs.resolve_release_values(
        db, ["not-a-uuid'--"], project_id=PROJECT, allowed_project_ids=None,
    ) == ((), "not-a-uuid'--")
    # No uuid in the values: no id term at all (the malformed value is only a name candidate).
    assert "release_ids" not in _sql(db.statements[0])
