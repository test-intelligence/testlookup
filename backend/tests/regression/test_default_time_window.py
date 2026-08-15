"""The default look-back window is 30 days, and the API agrees with the UI.

Two user reports, one cause:

* *"the entire AI intelligence page is broken — no records for all or most
  projects"*
* *"the pipeline runs drop down and test suite & build drop down have no
  options"*

Four projects' most recent runs were 8–10 days old. The runs endpoints
defaulted to **six** days and the UI store to seven, so every caller that
omitted ``days`` — the agents page's suite+build picker among them — got an
empty list. Nothing had failed; the window was simply shorter than the gap
since the last run, and a product that empties out after a quiet week reads as
an outage.

These tests pin the number in the API. The frontend counterpart lives in
``frontend/src/store/timeWindowStore.test.ts``; both exist because the value is
duplicated across the stack by necessity — the browser needs it before it can
call anything — and a duplicated constant that drifts is worse than either
value alone.
"""
from __future__ import annotations

import inspect

import pytest

DEFAULT_WINDOW_DAYS = 30


def _query_default(func, param: str):
    """The declared default for a FastAPI ``Query`` parameter."""
    sig = inspect.signature(func)
    default = sig.parameters[param].default
    # FastAPI wraps it in a Query object; unwrap when present.
    return getattr(default, "default", default)


def _query_bound(func, param: str, kind: str):
    """``ge``/``le`` live in the Query's annotated-types metadata, not as
    attributes — asserting the attribute silently reads ``None`` and passes
    against any value, which is the fail-open shape these tests exist to
    avoid."""
    query = inspect.signature(func).parameters[param].default
    for constraint in getattr(query, "metadata", []) or []:
        value = getattr(constraint, kind, None)
        if value is not None:
            return value
    return None


@pytest.mark.parametrize("endpoint", ["list_runs", "list_failed_run_ids"])
def test_runs_endpoints_default_to_thirty_days(endpoint):
    """A caller that omits ``days`` must still see a quiet project's history."""
    import app.routers.runs as runs

    assert _query_default(getattr(runs, endpoint), "days") == DEFAULT_WINDOW_DAYS


def test_the_two_runs_endpoints_do_not_diverge():
    """``/runs`` and ``/runs/failed-ids`` answer the same question about the
    same corpus. A page reading one and acting on the other would silently
    disagree with itself."""
    import app.routers.runs as runs

    assert _query_default(runs.list_runs, "days") == _query_default(
        runs.list_failed_run_ids, "days"
    )


def test_all_time_is_still_reachable():
    """``0`` means no date filter. Raising the default must not remove the
    escape hatch for someone auditing an old release."""
    import app.routers.runs as runs

    assert _query_bound(runs.list_runs, "days", "ge") == 0
    source = inspect.getsource(runs.list_runs)
    assert "0 = all time" in source


def test_the_ceiling_still_bounds_the_read():
    """A default is not a licence to scan forever."""
    import app.routers.runs as runs

    assert _query_bound(runs.list_runs, "days", "le") == 365


def test_the_api_default_matches_the_frontend_store():
    """The value is duplicated across the stack by necessity — the browser
    needs a window before it can call anything. Duplicated constants drift;
    this is the tripwire.
    """
    from pathlib import Path

    store = (
        Path(__file__).resolve().parents[3]
        / "frontend"
        / "src"
        / "store"
        / "timeWindowStore.ts"
    )
    if not store.exists():  # pragma: no cover - backend-only checkouts
        pytest.skip("frontend not present in this checkout")
    text = store.read_text(encoding="utf-8")
    assert f"export const DEFAULT_TIME_WINDOW_DAYS = {DEFAULT_WINDOW_DAYS}" in text


def test_the_intelligence_page_default_matches_too():
    """That page keeps its own URL-param default so deep-links stay shareable,
    which is exactly why it can drift from the store unnoticed."""
    from pathlib import Path

    page = (
        Path(__file__).resolve().parents[3]
        / "frontend"
        / "src"
        / "pages"
        / "IntelligenceHubPage.tsx"
    )
    if not page.exists():  # pragma: no cover
        pytest.skip("frontend not present in this checkout")
    text = page.read_text(encoding="utf-8")
    assert "params.get('range') ?? '30d'" in text


def test_the_store_migration_reseeds_superseded_defaults():
    """A stored 7 is indistinguishable from "never chose", so raising the
    default is pointless unless existing sessions move with it."""
    from pathlib import Path

    store = (
        Path(__file__).resolve().parents[3]
        / "frontend"
        / "src"
        / "store"
        / "timeWindowStore.ts"
    )
    if not store.exists():  # pragma: no cover
        pytest.skip("frontend not present in this checkout")
    text = store.read_text(encoding="utf-8")
    assert "SUPERSEDED_DEFAULTS" in text
    # Both previous defaults must be listed, or users sit on a stale window.
    assert "[7, 1]" in text
    assert "version: 3" in text
