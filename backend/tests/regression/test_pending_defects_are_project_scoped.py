"""Pending-defect review must not leak across projects.

Found by exploratory testing (2026-08-07) while covering defects / Jira
promotion. ``GET /api/v1/deep-investigate/defects/pending-review`` selected
**every** defect with ``approval_status == PENDING_REVIEW``, with no project
filter of any kind::

    result = await db.execute(
        select(Defect)
        .where(Defect.approval_status == ActionStatus.PENDING_REVIEW)
        .order_by(Defect.created_at.desc())
        ...
    )

The only gate was ``require_role(UserRole.QA_LEAD)``, which checks the caller's
ROLE, not their project membership. QA_LEAD is not a global role in this
codebase — ``get_accessible_project_ids()`` resolves non-admins to the projects
they belong to. So a QA lead of project A received the ``title``, ``severity``,
``component`` and ``owner_team`` of pending defects belonging to every other
project on the deployment.

Two things distinguish this from the earlier role-dependent bugs in this session
(the ``/metrics`` 500 and ``/stream/active``): there, the ``accessible is None``
branch *skipped a validation* that should have run for everyone. Here, ``None``
correctly means ADMIN — legitimately unscoped. The defect was the total absence
of a filter for everyone else.

Why the architecture ratchet missed it: ``test_architectural_authorization``
requires a ``require_*_access`` guard for routers with a ``{project_id}``-style
PATH param. This endpoint has no path param, so it was never in scope.

``defects.project_id`` is indexed (``ix_defects_project_id``) — the schema
already anticipated this filter.
"""
from __future__ import annotations

import inspect
import re

import pytest

pytest.importorskip("sqlalchemy")

from app.routers import deep_investigation  # noqa: E402


def _handler_src() -> str:
    """Executable source only — comments and the docstring stripped.

    The fix's own docstring names ``get_accessible_project_ids`` and
    ``project_id`` while explaining the bug. A substring search over the raw
    source would be satisfied by that prose, which has already fooled three
    structural assertions in this session.
    """
    src = inspect.getsource(deep_investigation.list_pending_defects)
    out, in_doc = [], False
    for line in src.split("\n"):
        stripped = line.strip()
        if stripped.startswith(('"""', "'''")):
            if not in_doc and stripped.count('"""') >= 2 and len(stripped) > 3:
                continue  # single-line docstring
            in_doc = not in_doc
            continue
        if in_doc or stripped.startswith("#"):
            continue
        out.append(line.split("  # ")[0])
    return "\n".join(out)


class TestTheQueryIsScoped:
    def test_handler_resolves_the_caller_s_accessible_projects(self):
        assert "get_accessible_project_ids" in _handler_src(), (
            "the pending-defect list never asks which projects the caller may "
            "see, so it returns every project's defects to any QA_LEAD"
        )

    def test_handler_filters_defects_by_project(self):
        src = _handler_src()
        assert re.search(r"Defect\.project_id\.in_\(", src), (
            "no project filter on the Defect query — titles, components and "
            "owner teams leak across projects"
        )

    def test_admins_remain_unscoped(self):
        """``accessible is None`` means ADMIN and must NOT be filtered.

        Unlike the /metrics and /stream/active bugs, the None branch here is
        correct behaviour, not a skipped check.
        """
        src = _handler_src()
        assert "accessible is not None" in src, (
            "the filter must be conditional on a non-None accessible set, or "
            "admins lose their legitimate cross-project view"
        )

    def test_the_filter_is_applied_before_pagination(self):
        """Filtering after LIMIT would page over the unscoped set and return
        the wrong rows — and sometimes an empty page for a valid user."""
        src = _handler_src()
        filter_at = src.find("Defect.project_id.in_")
        limit_at = src.find(".limit(")
        assert filter_at != -1 and limit_at != -1
        assert filter_at < limit_at, (
            "project filter is applied after pagination; the page would be "
            "drawn from every project's defects"
        )


class TestRoleGateIsStillThere:
    def test_qa_lead_role_requirement_is_preserved(self):
        """Scoping is added to the role gate, not swapped for it."""
        src = inspect.getsource(deep_investigation.list_pending_defects)
        assert "require_role" in src or "QA_LEAD" in src, (
            "the QA_LEAD role gate disappeared; scoping must be additive"
        )

    def test_pending_review_filter_is_preserved(self):
        assert "PENDING_REVIEW" in _handler_src()


def test_no_other_handler_in_this_router_selects_defects_unscoped():
    """Breadth check on the same file.

    Flags any other ``select(Defect)`` that never mentions ``project_id`` — the
    shape that caused this leak.
    """
    src = inspect.getsource(deep_investigation)
    collapsed = re.sub(r"\s+", " ", src)
    selects = [m.start() for m in re.finditer(r"select\(Defect\)", collapsed)]
    for pos in selects:
        window = collapsed[pos : pos + 400]
        assert "project_id" in window, (
            "a select(Defect) in this router has no project_id filter within "
            f"400 chars: ...{window[:160]}..."
        )
