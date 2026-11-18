"""GAP-B04: no route's role floor goes down without a reviewed snapshot change.

322 routes are gated by ``require_role`` / ``require_project_role``
(``app/core/deps.py``), and every new or refactored route types its floor by
hand. Nothing pinned a floor: lowering ``require_role(UserRole.ADMIN)`` on
``POST /api/v1/scim-tokens`` to QA_LEAD failed no test.

* Static: the floor of every role-gated route, read from the real dependency
  trees of ``app.routes``, must equal ``tests/security_role_floor_snapshot.json``.
  A lowered or removed floor fails as such; any other difference (a new
  gated route, a raised floor, a deleted route) fails until the snapshot is
  regenerated -- ``python -m tests.test_security_role_floor`` from ``backend/``
  -- and the diff is reviewed.
* Behaviour, for the routes where a lower role does the most damage (SCIM
  tokens, the RAG domain allow-list, secret providers, the GitHub App, API
  keys, the SMTP test): each role below the floor gets 403 before the handler
  runs; the floor role gets past every dependency.
"""

from __future__ import annotations

import inspect
import json
import uuid
from pathlib import Path

import pytest
from fastapi.routing import APIRoute

from app.models.postgres import UserRole

SNAPSHOT = Path(__file__).with_name("security_role_floor_snapshot.json")
ORDER = [
    UserRole.VIEWER,
    UserRole.TESTER,
    UserRole.QA_ENGINEER,
    UserRole.QA_LEAD,
    UserRole.ADMIN,
]
_GATES = {"require_role": "role", "require_project_role": "project_role"}


def _calls(dependant, seen=None):
    seen = set() if seen is None else seen
    for sub in dependant.dependencies:
        if sub.call is not None and id(sub.call) not in seen:
            seen.add(id(sub.call))
            yield sub.call
        yield from _calls(sub, seen)


def _gate(call) -> tuple[str, UserRole] | None:
    if getattr(call, "__module__", "") != "app.core.deps":
        return None
    factory = getattr(call, "__qualname__", "").split(".<locals>.", 1)[0]
    if factory not in _GATES:
        return None
    role = inspect.getclosurevars(call).nonlocals.get("min_role")
    assert isinstance(role, UserRole), f"{factory} closure without a min_role: {call}"
    return _GATES[factory], role


def current_floors() -> dict[str, dict[str, str]]:
    """``"METHOD /path" -> {"role": ..., "project_role": ...}`` for every role-gated route.

    Per gate kind the HIGHEST role in the tree is the floor (a router-level
    QA_ENGINEER under a route-level ADMIN is an ADMIN route).
    """
    from app.main import app

    floors: dict[str, dict[str, str]] = {}
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        best: dict[str, UserRole] = {}
        for call in _calls(route.dependant):
            gate = _gate(call)
            if gate is not None:
                kind, role = gate
                if kind not in best or ORDER.index(role) > ORDER.index(best[kind]):
                    best[kind] = role
        if best:
            for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
                floors[f"{method} {route.path}"] = {
                    k: v.value for k, v in sorted(best.items())
                }
    return dict(sorted(floors.items()))


def _render(floors: dict) -> str:
    """One route per line, sorted: a floor change is a one-line diff."""
    lines = [
        f" {json.dumps(k)}: {json.dumps(floors[k], sort_keys=True)}"
        for k in sorted(floors)
    ]
    return "{\n" + ",\n".join(lines) + "\n}\n"


def _lowered(snapshot: dict, current: dict) -> list[str]:
    out = []
    for key, was in snapshot.items():
        now = current.get(key)
        if now is None:
            continue  # gone or ungated: decided below
        for kind, role in was.items():
            if kind not in now:
                out.append(f"{key}: {kind} {role} -> no {kind} gate")
            elif ORDER.index(UserRole(now[kind])) < ORDER.index(UserRole(role)):
                out.append(f"{key}: {kind} {role} -> {now[kind]}")
    return out


def _route_keys() -> set[str]:
    from app.main import app

    return {
        f"{m} {r.path}"
        for r in app.routes
        if isinstance(r, APIRoute)
        for m in r.methods - {"HEAD", "OPTIONS"}
    }


@pytest.fixture(scope="module")
def snapshot() -> dict:
    assert SNAPSHOT.is_file(), (
        f"missing {SNAPSHOT.name}: run python -m tests.test_security_role_floor"
    )
    return json.loads(SNAPSHOT.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def current() -> dict:
    return current_floors()


def test_the_walk_sees_the_role_gates(current):
    assert len(current) >= 300, len(current)
    assert current["POST /api/v1/scim-tokens"] == {"role": "ADMIN"}
    assert current["PUT /api/v1/projects/{project_id}/fixer/config"] == {
        "project_role": "QA_LEAD"
    }


def test_no_role_floor_is_lowered(snapshot, current):
    lowered = _lowered(snapshot, current)
    ungated = sorted(k for k in snapshot if k not in current and k in _route_keys())

    assert not lowered and not ungated, (
        "a route's role floor went DOWN. If that is intended, regenerate the snapshot "
        "(python -m tests.test_security_role_floor) and have the diff reviewed:\n  "
        + "\n  ".join(lowered + [f"{k}: no role gate any more" for k in ungated])
    )


def test_the_snapshot_is_current(snapshot, current):
    new = sorted(set(current) - set(snapshot))
    gone = sorted(set(snapshot) - set(current))
    changed = sorted(
        k for k in set(snapshot) & set(current) if snapshot[k] != current[k]
    )

    assert not (new or gone or changed), (
        "role floors differ from tests/security_role_floor_snapshot.json; regenerate it "
        "(python -m tests.test_security_role_floor) and review the diff.\n"
        f"  new gated routes: {new}\n  no longer gated or removed: {gone}\n"
        f"  changed: {[(k, snapshot[k], current[k]) for k in changed]}"
    )


def test_the_snapshot_file_is_canonical(snapshot):
    """Sorted keys, one stable rendering, LF line ends: a regeneration diff shows only real changes."""
    assert SNAPSHOT.read_bytes() == _render(snapshot).encode("utf-8")


# ── behaviour: the high-impact subset ───────────────────────────────────────

#: (method, path, floor, sends a body). A floor-role request with no body is a
#: 422 once every dependency passed; without a body route, the handler's first
#: database call proves the dependencies passed.
HIGH_IMPACT = [
    ("POST", "/api/v1/scim-tokens", UserRole.ADMIN, True),
    (
        "PUT",
        "/api/v1/knowledge-sources/governance/domain-allowlist",
        UserRole.ADMIN,
        True,
    ),
    ("GET", "/api/v1/settings/secret-providers", UserRole.ADMIN, False),
    ("PUT", "/api/v1/settings/github-app", UserRole.ADMIN, True),
    ("POST", "/api/v1/keys", UserRole.QA_ENGINEER, True),
    ("POST", "/api/v1/settings/smtp/test", UserRole.QA_LEAD, False),
]


class _HandlerReached(Exception):
    """Raised by the fake session: the request got past every dependency."""


class _Session:
    def __init__(self):
        self.touched = 0

    def _reached(self, *_args, **_kwargs):
        self.touched += 1
        raise _HandlerReached

    execute = scalar = scalars = get = commit = flush = _reached

    def add(self, *_args, **_kwargs):
        self._reached()


@pytest.fixture
def as_role():
    """A client whose caller has the given global role (no API key, no project binding)."""
    from httpx import ASGITransport, AsyncClient

    from app.core.deps import get_current_user_or_api_key
    from app.db.postgres import get_db
    from app.main import app
    from app.models.postgres import User

    session = _Session()

    def make(role: UserRole):
        uid = uuid.uuid4()
        user = User(
            id=uid,
            username=f"floor-{role.value.lower()}",
            email=f"floor-{uid.hex[:8]}@example.test",
            role=role.value,
            is_active=True,
            must_change_password=False,
            hashed_password="!unusable",
        )

        async def _user():
            return user

        async def _db():
            yield session

        app.dependency_overrides[get_current_user_or_api_key] = _user
        app.dependency_overrides[get_db] = _db
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        return AsyncClient(transport=transport, base_url="http://floor.test"), session

    yield make
    app.dependency_overrides.pop(get_current_user_or_api_key, None)
    app.dependency_overrides.pop(get_db, None)


def _below(floor: UserRole) -> list[UserRole]:
    return ORDER[: ORDER.index(floor)]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method, path, floor, role",
    [(m, p, f, r) for m, p, f, _b in HIGH_IMPACT for r in _below(f)],
    ids=lambda v: getattr(v, "value", v),
)
async def test_a_role_below_the_floor_is_refused_before_the_handler(
    as_role, method, path, floor, role
):
    client, session = as_role(role)
    async with client:
        resp = await client.request(method, path)

    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"] == f"Requires at least {floor.value} role"
    assert session.touched == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method, path, floor, has_body", HIGH_IMPACT, ids=lambda v: getattr(v, "value", v)
)
async def test_the_floor_role_gets_past_every_dependency(
    as_role, method, path, floor, has_body
):
    client, session = as_role(floor)
    async with client:
        resp = await client.request(method, path)

    if has_body:
        assert resp.status_code == 422, resp.text
        assert any(
            err.get("loc", [None])[0] == "body" for err in resp.json()["detail"]
        ), resp.text
    else:
        assert resp.status_code != 403, resp.text
        assert session.touched >= 1, "the handler never ran"


if __name__ == "__main__":
    SNAPSHOT.write_bytes(_render(current_floors()).encode("utf-8"))
    print(f"wrote {SNAPSHOT}")
