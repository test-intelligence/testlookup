"""Enterprise roles E00-S02: what each of the ten roles may do on every role-gated route.

``tests/security_role_floor_snapshot.json`` pins each route's FLOOR. With roles
that are not rungs on the ladder (RELEASE_MANAGER, PROJECT_MANAGER,
PRODUCT_MANAGER, DEVELOPER, AGENT) a floor no longer says who gets in: a route
guarded by ``require_permission(Permission.RELEASE_WRITE)`` has the floor
QA_LEAD and also admits a RELEASE_MANAGER. So this file pins the answer per
ROLE: ``tests/security_role_access_matrix.json`` lists, for every route a
``require_role`` / ``require_project_role`` guard covers, the roles it admits.

The answer is for a caller who holds the role both on the instance and in the
route's project, read from the guards in the route's real dependency tree:

* ``"QA_LEAD"`` -- admitted;
* ``"DEVELOPER (conditional)"`` -- admitted by a conditional grant on a route
  whose handler declares it checks the condition;
* absent -- refused with 403 before the handler runs.

Handler-level checks (``holds_role_for_project_write``, ``ensure_ingest_role``,
service-level role sets) are not visible here; the per-route tests own them.

Owner decision 2026-11-26: tests may change when a role's expected behaviour
changes. Any difference fails until the matrix is regenerated --
``python -m tests.test_security_role_access_matrix`` from ``backend/`` -- and the
diff, which is the reviewed list of behaviour changes per role, goes in the PR.
"""
from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest
from fastapi.routing import APIRoute

from app.core.permissions import Permission, has_permission, rank_index
from app.models.postgres import UserRole

MATRIX = Path(__file__).with_name("security_role_access_matrix.json")
ROLES = list(UserRole)
_GATES = ("require_role", "require_project_role")
_NEW_ROLES = [r for r in UserRole if r.value not in {"VIEWER", "TESTER", "QA_ENGINEER", "QA_LEAD", "ADMIN"}]


def _calls(dependant, seen=None):
    seen = set() if seen is None else seen
    for sub in dependant.dependencies:
        if sub.call is not None and id(sub.call) not in seen:
            seen.add(id(sub.call))
            yield sub.call
        yield from _calls(sub, seen)


def _gate(call):
    """``(min_role, permission, conditions_checked_in_handler)`` of a role guard, else ``None``."""
    if getattr(call, "__module__", "") != "app.core.deps":
        return None
    factory = getattr(call, "__qualname__", "").split(".<locals>.", 1)[0]
    if factory not in _GATES:
        return None
    nonlocals = inspect.getclosurevars(call).nonlocals
    role = nonlocals.get("min_role")
    assert isinstance(role, UserRole), f"{factory} closure without a min_role: {call}"
    permission = nonlocals.get("permission")
    assert permission is None or isinstance(permission, Permission), call
    return role, permission, nonlocals.get("conditions_checked_in_handler", False) is True


def answer(role: UserRole, gates) -> str:
    """``allow`` / ``conditional`` / ``deny`` for a caller holding ``role`` everywhere."""
    result = "allow"
    for min_role, permission, conditional_ok in gates:
        if rank_index(role) >= rank_index(min_role):
            continue
        if permission is not None and has_permission([role], permission):
            continue
        if permission is not None and conditional_ok and has_permission([role], permission, allow_conditional=True):
            result = "conditional"
            continue
        return "deny"
    return result


def current_matrix() -> dict[str, list[str]]:
    from app.main import app

    matrix: dict[str, list[str]] = {}
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        gates = [gate for call in _calls(route.dependant) if (gate := _gate(call)) is not None]
        if not gates:
            continue
        cells: list[str] = []
        for role in ROLES:
            verdict = answer(role, gates)
            if verdict == "allow":
                cells.append(role.value)
            elif verdict == "conditional":
                cells.append(f"{role.value} (conditional)")
        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            matrix[f"{method} {route.path}"] = cells
    return dict(sorted(matrix.items()))


def _render(matrix: dict) -> str:
    """One route per line, sorted: a change for one role is a one-line diff."""
    lines = [f" {json.dumps(k)}: {json.dumps(matrix[k])}" for k in sorted(matrix)]
    return "{\n" + ",\n".join(lines) + "\n}\n"


@pytest.fixture(scope="module")
def snapshot() -> dict:
    assert MATRIX.is_file(), f"missing {MATRIX.name}: run python -m tests.test_security_role_access_matrix"
    return json.loads(MATRIX.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def current() -> dict:
    return current_matrix()


def test_the_walk_sees_the_role_gates(current):
    assert len(current) >= 300, len(current)
    assert current["POST /api/v1/scim-tokens"] == ["ADMIN"]


def test_every_role_answer_matches_the_reviewed_matrix(snapshot, current):
    new = sorted(set(current) - set(snapshot))
    gone = sorted(set(snapshot) - set(current))
    changed = sorted(k for k in set(snapshot) & set(current) if snapshot[k] != current[k])
    details = []
    for key in changed:
        was, now = set(snapshot[key]), set(current[key])
        details.append(f"{key}: gained {sorted(now - was)} lost {sorted(was - now)}")
    assert not (new or gone or changed), (
        "per-role access differs from tests/security_role_access_matrix.json. If the change is intended and "
        "listed in the epic, regenerate it (python -m tests.test_security_role_access_matrix) and put the diff "
        f"in the PR.\n  new routes: {new}\n  removed or ungated: {gone}\n  changed:\n    " + "\n    ".join(details)
    )


def test_the_matrix_file_is_canonical(snapshot):
    assert MATRIX.read_bytes() == _render(snapshot).encode("utf-8")


def test_admin_is_admitted_everywhere(current):
    assert all("ADMIN" in roles for roles in current.values())


@pytest.mark.parametrize("role", _NEW_ROLES)
def test_a_new_role_gets_exactly_viewer_wherever_no_permission_is_named(role, current):
    """Fail closed (E00): until a route names a permission, a job role or AGENT is a VIEWER there."""
    from app.main import app

    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        gates = [gate for call in _calls(route.dependant) if (gate := _gate(call)) is not None]
        if not gates or any(permission is not None for _, permission, _ in gates):
            continue
        assert answer(role, gates) == answer(UserRole.VIEWER, gates), (route.path, role)


if __name__ == "__main__":  # python -m tests.test_security_role_access_matrix
    MATRIX.write_bytes(_render(current_matrix()).encode("utf-8"))
    print(f"wrote {MATRIX}")
