"""Regression guard: a snapshot payload-shape change must bump
``CURRENT_SCHEMA_VERSION``.

The defect this guards
----------------------
``get_cached_snapshot`` serves any stored row whose
``schema_version >= CURRENT_SCHEMA_VERSION``. So a change to the payload's
shape that does not bump the constant is **invisible on every run that has
already been analysed** — the corrected code runs only for snapshots that do
not exist yet.

That is not hypothetical. Adding ``broken_tests`` / ``unknown_tests`` to the
run block shipped, deployed, and was verified live as still missing:

    _snapshot: {"cached": true, "stale": false}
    run keys:  broken_tests present? False

while the corrected source was confirmed present in the running container.
36 stored snapshots sat at version 3 against a ``CURRENT_SCHEMA_VERSION`` of
3, so every one of them kept serving the old shape. Merging and deploying
were both green; only reading the live payload showed it.

How this guard works
--------------------
The run block's key set is pinned here alongside the schema version. Changing
the keys without bumping the version fails this test, and the message says
what to do. It cannot detect every possible shape change — nested payload
sections are not enumerated — so it pins the block that has actually drifted,
and the module docstring carries the rule for the rest.
"""
from __future__ import annotations

import ast
import inspect

import pytest

pytestmark = pytest.mark.regression

# The run block's keys as of CURRENT_SCHEMA_VERSION 5. If you change these,
# bump the version in the same commit and update this list.
EXPECTED_RUN_KEYS = {
    "id",
    "build_number",
    "branch",
    "status",
    "total_tests",
    "passed_tests",
    "failed_tests",
    "ingestion_attempted_tests",
    "ingestion_rejected_tests",
    "ingestion_complete",
    "ingestion_rejection_reasons",
    "broken_tests",
    "unknown_tests",
    "skipped_tests",
    "pass_rate",
    "duration_ms",
    "start_time",
    "end_time",
    "ocp_namespace",
}
PINNED_AT_SCHEMA_VERSION = 5


def _run_block_keys() -> set[str]:
    """Literal keys of the ``run_summary`` dict, parsed rather than matched.

    An earlier version of a sibling guard sliced this text to the first ``}``
    and was truncated by a ``{id}`` inside a comment. Parsing the AST cannot
    be confused by prose.
    """
    from app.services import run_intelligence_service

    tree = ast.parse(inspect.getsource(run_intelligence_service))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
        if "run_summary" not in targets or not isinstance(node.value, ast.Dict):
            continue
        return {
            k.value for k in node.value.keys
            if isinstance(k, ast.Constant) and isinstance(k.value, str)
        }
    raise AssertionError("run_summary dict literal not found — guard is blind")


def test_the_guard_can_find_the_run_block():
    """Fail-open check: an empty key set must not read as agreement."""
    keys = _run_block_keys()
    assert len(keys) >= 10, f"parsed too few run-block keys: {sorted(keys)}"


def test_the_run_block_shape_matches_what_this_version_pins():
    actual = _run_block_keys()
    added = actual - EXPECTED_RUN_KEYS
    removed = EXPECTED_RUN_KEYS - actual
    assert not (added or removed), (
        f"the snapshot run block changed shape (added={sorted(added)}, "
        f"removed={sorted(removed)}). Bump CURRENT_SCHEMA_VERSION in "
        "intelligence_snapshot_service.py and update EXPECTED_RUN_KEYS here — "
        "otherwise every stored snapshot keeps serving the OLD shape, because "
        "get_cached_snapshot admits any row with schema_version >= the current "
        "one."
    )


def test_the_schema_version_was_bumped_for_this_shape():
    from app.services.intelligence_snapshot_service import CURRENT_SCHEMA_VERSION

    assert CURRENT_SCHEMA_VERSION == PINNED_AT_SCHEMA_VERSION, (
        f"CURRENT_SCHEMA_VERSION is {CURRENT_SCHEMA_VERSION} but this file "
        f"pins the run-block shape at {PINNED_AT_SCHEMA_VERSION}. Keep the two "
        "in step so a shape change cannot ship without invalidating the cache."
    )


def test_the_cache_gate_is_still_version_sensitive():
    """The bump only helps while the read path actually compares versions."""
    from app.services import intelligence_snapshot_service as svc

    src = inspect.getsource(svc.get_cached_snapshot)
    assert "schema_version" in src and "CURRENT_SCHEMA_VERSION" in src, (
        "get_cached_snapshot no longer compares the stored schema_version "
        "against CURRENT_SCHEMA_VERSION, so bumping the constant would stop "
        "invalidating anything"
    )


def test_every_snapshot_read_path_is_version_gated():
    """The bump only works if BOTH read paths honour it.

    ``get_cached_snapshot`` compared versions from the start;
    ``get_stale_snapshot`` did not. So a superseded row was refused by the
    fresh path, the request fell through to the stale path, and the same
    obsolete payload came back with ``stale: true``. The run-block fix was
    invisible through two deploys because of it.

    Stale and obsolete are different: a stale snapshot has the right shape and
    old content, which is safe to serve while a refresh runs. A snapshot at a
    superseded schema version has the wrong shape, and is not.

    Written against every public reader rather than the one that was broken —
    a sibling read path is exactly how this escaped the first time.
    """
    from app.services import intelligence_snapshot_service as svc

    readers = [svc.get_cached_snapshot, svc.get_stale_snapshot]
    ungated = [
        fn.__name__ for fn in readers
        if "CURRENT_SCHEMA_VERSION" not in inspect.getsource(fn)
    ]
    assert not ungated, (
        f"these snapshot readers ignore the schema version: {ungated}. Any one "
        "of them will serve a payload in a superseded shape and silently "
        "defeat a version bump."
    )


def test_the_reader_list_still_finds_real_functions():
    """Fail-open check: an empty or wrong reader list would pass vacuously."""
    from app.services import intelligence_snapshot_service as svc

    for name in ("get_cached_snapshot", "get_stale_snapshot"):
        fn = getattr(svc, name, None)
        assert fn is not None and callable(fn), f"{name} is not a function any more"
        assert "RunIntelligenceSnapshot" in inspect.getsource(fn), (
            f"{name} no longer reads the snapshot table; the guard above is "
            "checking the wrong thing"
        )
