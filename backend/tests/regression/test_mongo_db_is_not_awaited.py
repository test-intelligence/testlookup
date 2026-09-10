"""``get_mongo_db()`` is synchronous — awaiting it is a runtime TypeError.

Motor builds its client and database handles eagerly; only the QUERIES are
awaitable. So::

    mongo = await get_mongo_db()          # TypeError at runtime
    doc = await get_mongo_db()[c].find_one(...)   # correct - await binds to find_one

The first form raises

    TypeError: object AsyncIOMotorDatabase can't be used in 'await' expression

and nothing catches it. It surfaced as a bare 500 from
``DELETE /api/v1/runs/{run_id}`` with NO traceback in the structured logs,
which is why it survived: the endpoint looked broken for no discoverable
reason, and the only way to see the cause was to walk the handler's steps
in-process against the live database.

Four call sites had it — two routers and two worker tasks — so this was never
one careless line. A static check is the right guard because the failure is
invisible until that exact branch executes, and three of the four were on
paths (retention purge, worker deletion) that a normal test run never reaches.
"""
from __future__ import annotations

import ast
import pathlib

APP = pathlib.Path(__file__).resolve().parent.parent.parent / "app"


def _bare_awaits_of_get_mongo_db() -> list[str]:
    """Find `await get_mongo_db()` where the await binds to the CALL itself.

    Uses the AST rather than a regex so that
    ``await get_mongo_db()[coll].find_one()`` — where the await binds to
    ``find_one`` and is correct — is not flagged. A regex cannot tell those
    apart, and flagging the correct form would train readers to ignore this.
    """
    offenders: list[str] = []
    for path in sorted(APP.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Await):
                continue
            inner = node.value
            # `await get_mongo_db()` — the awaited expression IS the call.
            if (
                isinstance(inner, ast.Call)
                and isinstance(inner.func, ast.Name)
                and inner.func.id == "get_mongo_db"
            ):
                offenders.append(
                    f"{path.relative_to(APP).as_posix()}:{node.lineno}"
                )
    return offenders


def test_the_scan_can_see_the_pattern_at_all() -> None:
    """Guards the guard.

    If the AST walk stopped matching, the assertion below would pass
    vacuously forever. Parse a known-bad snippet and require a hit.
    """
    tree = ast.parse("async def f():\n    x = await get_mongo_db()\n")
    hits = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Await)
        and isinstance(n.value, ast.Call)
        and getattr(n.value.func, "id", None) == "get_mongo_db"
    ]
    assert len(hits) == 1


def test_the_scan_does_not_flag_an_awaited_query() -> None:
    """The correct form must not be reported, or the guard is noise."""
    tree = ast.parse(
        "async def f():\n"
        "    d = await get_mongo_db()['c'].find_one({})\n"
    )
    hits = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Await)
        and isinstance(n.value, ast.Call)
        and getattr(n.value.func, "id", None) == "get_mongo_db"
    ]
    assert hits == []


def test_no_call_site_awaits_get_mongo_db() -> None:
    offenders = _bare_awaits_of_get_mongo_db()
    assert not offenders, (
        "get_mongo_db() is synchronous - awaiting it raises "
        "TypeError: object AsyncIOMotorDatabase can't be used in 'await' "
        "expression, at runtime, with no traceback in the logs. Drop the "
        "await:\n  " + "\n  ".join(offenders)
    )
