"""UI-003: run order follows natural build numbers, not persistence time.

Sharded ingestion can commit runs out of order.  Lexical ordering is also
wrong for free-form identifiers (``ui-10`` sorts before ``ui-2``).  These
guards pin the executable PostgreSQL ORDER BY constructs used by Run #N and
both run-listing paths.
"""
from __future__ import annotations

import ast
import inspect

from sqlalchemy.dialects import postgresql

from app.routers import runs as runs_router
from app.services import runs_service


def _postgres_sql(expression) -> str:
    return " ".join(
        str(expression.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )).lower().split()
    )


def _assert_canonical_order(sql: str, direction: str, nulls: str) -> None:
    assert "regexp_replace(test_runs.build_number" in sql
    assert "string_to_array" in sql
    assert "cast(" in sql and "as bigint[]" in sql
    assert f"{direction} nulls {nulls}" in sql
    assert f"test_runs.build_number {direction}" in sql
    assert f"test_runs.created_at {direction}" in sql
    assert f"test_runs.id {direction}" in sql


def test_run_seq_window_uses_natural_ascending_order():
    """Run #N must assign ui-2 before ui-10 despite commit timestamps."""
    source = inspect.getsource(runs_service.fetch_run_seq_map)
    tree = ast.parse(source)
    calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "over"
    ]
    assert len(calls) == 1
    order_kw = next(kw.value for kw in calls[0].keywords if kw.arg == "order_by")
    order_source = ast.unparse(order_kw)
    assert order_source == (
        "(natural_build_number_key().asc().nulls_last(), "
        "TestRun.build_number.asc(), TestRun.created_at.asc(), TestRun.id.asc())"
    )


def test_natural_key_compiles_to_verified_postgres_array_key():
    sql = _postgres_sql(runs_service.natural_build_number_key())
    assert "test_runs.build_number ~ '[0-9]'" in sql
    assert "regexp_replace(test_runs.build_number" in sql
    assert "string_to_array" in sql
    assert "as bigint[]" in sql


def test_main_run_listing_uses_reverse_canonical_order():
    source = inspect.getsource(runs_service.list_project_runs)
    tree = ast.parse(source)
    order_calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "order_by"
        and len(node.args) == 4
    ]
    assert len(order_calls) == 1
    assert ast.unparse(order_calls[0]) == (
        "select(TestRun, Project.name.label('project_name')).outerjoin(Project, "
        "Project.id == TestRun.project_id).where(*filters).order_by("
        "natural_build_number_key().desc().nulls_first(), "
        "TestRun.build_number.desc(), TestRun.created_at.desc(), TestRun.id.desc())"
    )


def test_failed_run_listing_uses_reverse_canonical_order():
    source = inspect.getsource(runs_router.list_failed_run_ids)
    tree = ast.parse(source)
    order_calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "order_by"
        and len(node.args) == 4
    ]
    assert len(order_calls) == 1
    args = tuple(ast.unparse(arg) for arg in order_calls[0].args)
    assert args == (
        "natural_build_number_key().desc().nulls_first()",
        "TestRun.build_number.desc()",
        "TestRun.created_at.desc()",
        "TestRun.id.desc()",
    )
