"""Regression: every classify_test caller must supply the failure text.

Bug (homelab, 2026-08-16): `POST /api/v1/analyze` — the user-facing "Analyze"
action — returned `UNKNOWN`, confidence 30, *"Could not determine failure cause
from available data. Manual review required."* for every test, always.

The failure text was stored correctly. Calling the engine directly on the very
same string classified it fine::

    RulesEngine.classify_test('AssertionError: expected total 100 but was 97')
        -> PRODUCT_BUG, confidence 55
    RulesEngine.classify_test('java.net.ConnectException: Connection refused')
        -> INFRASTRUCTURE, confidence 70

`routers/analyze.py` built its `test_case` payload out of ids, names and pod
metadata and never included `error_message` or `stack_trace` —
the two keys `analysis_router.classify_test` actually reads (its own docstring
says "Dict with error_message, duration_ms, severity, test_name, etc."). The
same omission was in `worker.tasks.run_live_test_analysis`, so live-stream
analysis had the same permanent UNKNOWN.

Why it stayed invisible: `classify_test` degrades to UNKNOWN instead of
failing. "Could not determine failure cause from available data" is true in a
useless way — there was no data, because the caller never sent any. Nothing
errored, no log fired, and the pipeline path (`analysis_agent`) *did* pass the
text, so the feature demonstrably worked somewhere.

The guard is the CLASS, enforced structurally: any call site that builds a
literal ``test_case`` dict must include the failure text. A behavioural test
alone would only cover the caller it happens to exercise, and this bug was
three callers disagreeing about a contract.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

APP = pathlib.Path(__file__).resolve().parents[2] / "app"

# Keys the router reads off `test_case` to do its job.
REQUIRED = "error_message"


def _classify_test_calls():
    """(file, lineno, keywords) for every classify_test(...) call in app/."""
    out = []
    for path in APP.rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = (
                fn.attr if isinstance(fn, ast.Attribute)
                else fn.id if isinstance(fn, ast.Name)
                else None
            )
            if name != "classify_test":
                continue
            kw = {k.arg: k.value for k in node.keywords if k.arg}
            out.append((path, node.lineno, kw))
    return out


def test_the_scanner_finds_the_call_sites():
    """Every assertion below iterates these. An empty scan passes vacuously."""
    calls = _classify_test_calls()
    assert len(calls) >= 3, f"expected several classify_test call sites, found {calls}"


def _enforced_sites() -> set[str]:
    """Call sites whose payload the parametrised test actually checks."""
    enforced = set()
    for path, _lineno, kwargs in _classify_test_calls():
        tc = kwargs.get("test_case")
        if not isinstance(tc, ast.Dict):
            continue
        keys = {k.value for k in tc.keys if isinstance(k, ast.Constant)}
        if any(k is None for k in tc.keys) and REQUIRED not in keys:
            continue  # splats another mapping — skipped by the check below
        enforced.add(path.name)
    return enforced


def test_the_guard_is_not_silently_skipping_the_callers_that_broke():
    """Anti-vacuity floor.

    The parametrised check SKIPS payloads that splat another mapping in. That
    is deliberate, but it means a caller can disable its own guard just by
    switching from explicit keys to ``**something`` — no test fails, and the
    call quietly stops being covered. That is exactly how this bug survived in
    the first place, so pin the callers that must stay enforced.
    """
    enforced = _enforced_sites()
    for required_file in ("analyze.py", "tasks.py", "analysis_agent.py"):
        assert required_file in enforced, (
            f"{required_file} no longer has an enforced classify_test payload — "
            f"it was probably changed to splat a mapping in, which turns the "
            f"contract check into a skip. Currently enforced: {sorted(enforced)}"
        )


@pytest.mark.parametrize(
    "path,lineno,kwargs",
    [pytest.param(p, ln, kw, id=f"{p.name}:{ln}") for p, ln, kw in _classify_test_calls()],
)
def test_every_literal_test_case_carries_the_failure_text(path, lineno, kwargs):
    """A call that builds its payload inline must include the failure text.

    Calls that forward a variable or ``**kwargs`` are skipped — they pass
    through whatever their caller supplied, so the contract is enforced one
    level up rather than here.
    """
    test_case = kwargs.get("test_case")
    if test_case is None or not isinstance(test_case, ast.Dict):
        pytest.skip("payload is not a dict literal at this call site")

    keys = {k.value for k in test_case.keys if isinstance(k, ast.Constant)}
    has_splat = any(k is None for k in test_case.keys)  # {**something}
    if has_splat and REQUIRED not in keys:
        pytest.skip("payload splats another mapping in; contract enforced upstream")

    assert REQUIRED in keys, (
        f"{path.relative_to(APP.parent)}:{lineno} calls classify_test with a "
        f"test_case that has no {REQUIRED!r}. Classification reads that key — "
        f"without it every tier returns UNKNOWN and the caller silently gets "
        f"'Could not determine failure cause from available data'. Keys "
        f"present: {sorted(keys)}"
    )


# ── The engine really does classify these, so UNKNOWN means missing input ──


@pytest.mark.parametrize(
    "text,expected",
    [
        ("AssertionError: expected total 100 but was 97", "PRODUCT_BUG"),
        ("java.net.ConnectException: Connection refused: db.internal:5432",
         "INFRASTRUCTURE"),
    ],
)
def test_the_engine_classifies_the_shapes_the_endpoint_returned_unknown_for(text, expected):
    """Pins the premise of the bug report: these are not hard cases. If the
    engine ever stops classifying them, this fails here rather than looking
    like a caller problem."""
    from app.services.rules_engine import RulesEngine

    result = RulesEngine.classify_test(text, test_name="t")
    assert result["failure_category"] == expected, (
        f"{text!r} classified as {result['failure_category']}, expected {expected}"
    )
    assert (result.get("confidence_score") or 0) > 30


def test_analyze_router_passes_the_stored_failure_text():
    """The specific regression, pinned at its source line rather than only by
    the generic scan above — this is the call the "Analyze" button makes."""
    src = (APP / "routers" / "analyze.py").read_text(encoding="utf-8")
    call = src.split("classify_test(", 1)[1][:900]
    for key in ("error_message", "stack_trace"):
        assert f'"{key}": tc.{key}' in call, (
            f"routers/analyze.py no longer forwards {key} from the stored test "
            f"case; the endpoint returns UNKNOWN for every input when it does not"
        )
