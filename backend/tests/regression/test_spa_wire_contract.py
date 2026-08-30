"""The SPA is the third API client, and nothing checked its wire either.

``test_cli_and_mcp_wire_contract.py`` guards the CLI and the MCP server, and
says why it stops there: *"The UI's contract with the backend is exercised
constantly."* That is an assumption, not a check. It is also the reason the SPA
was left out while the other two clients drifted into 404s and silently-dropped
request fields.

Measured before writing this: **313 distinct `/api/v1/...` paths** across
`frontend/src/services` and `frontend/src/hooks`, against **388 routes** on the
assembled app — and every one resolves. So this guard finds nothing today. It
is here because the frontend service layer sits at **18.7% statement / 7.7%
function coverage**, so "exercised constantly" is true of a handful of paths
and false of most: `apiKeyService`, `authService`, `chatService`,
`digestService`, `fixerService` and a dozen more are at literal 0%. A renamed
route would reach production as a 404 in a page nobody's test opens.

The CLI's own regression is the precedent for what that costs:
``testlookup tests list`` called ``/runs/{run_id}/test-cases`` — a path the
runs router never served — and *"the command had never worked"*.

This guard covers the whole client surface in one assertion rather than
chasing the same number with 45 mock-axios unit tests, which would raise the
coverage percentage while checking that the code calls the URL it was written
to call.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.regression

REPO_ROOT = Path(__file__).resolve().parents[3]
SERVICES_DIR = REPO_ROOT / "frontend" / "src" / "services"
HOOKS_DIR = REPO_ROOT / "frontend" / "src" / "hooks"

#: A ``/api/v1/...`` path inside a single-, double- or back-quoted string.
#: Backticks matter: most parameterised calls are template literals.
_PATH_RE = re.compile(r"""['"`](/api/v1/[^'"`\s]*)['"`]""")

#: Literals that are NOT request paths. Recorded as data with the reason, so a
#: future addition has to be justified rather than silently tolerated.
_NOT_REQUEST_PATHS: dict[str, str] = {
    "/api/v1/auth/mfa/": (
        "prefix constant in services/api.ts (NO_REFRESH_PREFIXES) — matched "
        "against outgoing URLs to skip the token refresh, never requested"
    ),
}


def _normalise(path: str) -> str:
    """``/runs/${runId}/tests?days=7`` -> ``/runs/{}/tests``.

    Template substitutions and path params both collapse to ``{}``: the param
    *name* is the client's business, the shape is the contract. The query
    string is dropped — FastAPI routes on the path alone.
    """
    path = re.sub(r"\$\{[^}]*\}", "{}", path)
    path = re.sub(r"\{[^}]*\}", "{}", path)
    path = path.split("?")[0].split("#")[0]
    return path.rstrip("/") or "/"


def _real_routes() -> set[str]:
    from app.main import app

    return {
        _normalise(p)
        for p in (getattr(route, "path", None) for route in app.routes)
        if p
    }


def _frontend_files() -> list[Path]:
    files: list[Path] = []
    for d in (SERVICES_DIR, HOOKS_DIR):
        if d.is_dir():
            files += sorted(d.rglob("*.ts")) + sorted(d.rglob("*.tsx"))
    # A test file's fixture URLs are not the app's contract.
    return [f for f in files if ".test." not in f.name and ".spec." not in f.name]


def _literal_paths() -> dict[Path, set[str]]:
    found: dict[Path, set[str]] = {}
    for f in _frontend_files():
        src = f.read_text(encoding="utf-8", errors="ignore")
        paths = {
            m.group(1) for m in _PATH_RE.finditer(src)
            if m.group(1) not in _NOT_REQUEST_PATHS
        }
        if paths:
            found[f] = paths
    return found


def test_the_frontend_tree_is_actually_on_disk():
    """A guard that silently scanned nothing would be worse than no guard.

    The counts are floors, not exact pins: they must not quietly fall to zero
    if the directory moves or the regex stops matching template literals.
    """
    assert SERVICES_DIR.is_dir(), f"frontend services not found at {SERVICES_DIR}"
    assert HOOKS_DIR.is_dir(), f"frontend hooks not found at {HOOKS_DIR}"

    scanned = _literal_paths()
    assert len(scanned) >= 30, f"expected to scan many modules, saw {len(scanned)}"
    total = len({p for paths in scanned.values() for p in paths})
    assert total >= 200, f"expected to find many API paths, found {total}"


def test_the_route_table_is_populated():
    """Guards the other half: an empty route set would pass every path."""
    routes = _real_routes()
    api = {r for r in routes if r.startswith("/api/v1/")}
    assert len(api) >= 300, f"only {len(api)} /api/v1 routes on the app"


def test_every_spa_api_path_resolves_to_a_real_route():
    """The class this pins, from the CLI's own history: a client calls a path
    the backend does not serve, every call 404s, and nothing fails loudly."""
    routes = _real_routes()
    offenders: list[str] = []
    for f, paths in _literal_paths().items():
        for p in sorted(paths):
            if _normalise(p) not in routes:
                offenders.append(f"{f.relative_to(REPO_ROOT).as_posix()} -> {p}")

    assert not offenders, (
        "The SPA calls a path the backend does not serve, so every request "
        "404s. Most of these modules have no unit test, so the page simply "
        "renders empty:\n  " + "\n  ".join(offenders)
    )


def test_the_excluded_literals_are_still_non_paths():
    """`_NOT_REQUEST_PATHS` is an exclusion list, and an exclusion list rots.

    If one of these ever becomes a real route, the entry is hiding a path the
    guard should be checking — so require that it does NOT resolve.
    """
    routes = _real_routes()
    for literal, reason in _NOT_REQUEST_PATHS.items():
        assert _normalise(literal) not in routes, (
            f"{literal} is excluded as a non-request literal ({reason}), but it "
            "now matches a real route — drop the exclusion so it is checked"
        )
