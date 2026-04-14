"""
SQL helpers — small, dependency-free utilities shared by services and routers.
"""
from __future__ import annotations


def escape_like(value: str) -> str:
    r"""
    Escape ``%`` and ``_`` wildcards (and the escape character itself) in a
    user-supplied substring so it can be safely embedded in a ``LIKE`` /
    ``ILIKE`` pattern.

    Without this, a user typing ``test_case`` would match ``testXcase`` as
    well (because ``_`` is the LIKE "any single char" wildcard), and a user
    typing ``50%`` would match everything that starts with ``50``.

    The returned string is still plain user input — the caller wraps it with
    ``%`` fences (``f"%{escape_like(q)}%"``) and must pass the ``escape``
    argument when invoking ``.ilike(..., escape="\\")``.
    """
    if not value:
        return ""
    # Escape the escape character FIRST so we don't double-escape % and _.
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def like_contains(value: str) -> str:
    """Return a ``%escaped%`` contains-pattern for use with ``.ilike(..., escape='\\\\')``."""
    return f"%{escape_like(value)}%"
