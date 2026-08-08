"""Throughput budgets must be reachable and actually evaluated.

``performance_budgets.py`` calls itself the single source of truth for
performance expectations and carries four ``THROUGHPUT_BUDGETS``. For a long
time they were only ever serialized into :func:`get_all_budgets` — a reporting
dict. There was **no lookup accessor at all**, so no caller could check one, and
the load harness compared p95 latency only.

That was not harmless. Measured against the live deployment, ``keyword_search``
throughput was:

    postgres limited to 1 core .....  17.4 rps   vs the 20.0 budget  -> BREACH
    postgres raised to 4 cores .....  28.7 rps   vs the 20.0 budget  -> pass

So a real regression breached a written-down budget and the harness printed
"All budgets met", because nothing on any code path ever read ``min_rps``.

This is the same failure as the inert latency gate: an expectation recorded in
one place with no mechanism behind it. These tests pin the mechanism.
"""
from __future__ import annotations

import ast
import importlib.util
import re
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.regression

BACKEND_ROOT = Path(__file__).resolve().parents[2]
HARNESS = BACKEND_ROOT / "scripts" / "load_test_concurrent.py"


def _load_harness():
    """Import the harness by path — it lives in scripts/, not in the package."""
    spec = importlib.util.spec_from_file_location("_load_harness", HARNESS)
    module = importlib.util.module_from_spec(spec)
    # Register before exec: @dataclass resolves its own module out of
    # sys.modules while processing annotations, and raises AttributeError on a
    # module that is not there yet.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _harness_source_without_comments() -> str:
    """Source with comments and docstrings removed.

    Structural assertions on this file have been fooled before by prose: a
    comment explaining a defect contains the very words that describe it, so a
    naive substring search passes against code that still has the bug.
    """
    src = HARNESS.read_text(encoding="utf-8")
    src = re.sub(r"^\s*#.*$", "", src, flags=re.MULTILINE)
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)):
            body = getattr(node, "body", None)
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                if isinstance(body[0].value.value, str):
                    body.pop(0)
    return ast.unparse(tree)


class TestTheBudgetsAreReachable:
    def test_a_lookup_accessor_exists(self):
        """Without this, nothing can check a throughput budget by name."""
        from app.services.performance_budgets import get_throughput_budget

        b = get_throughput_budget("search_concurrent")
        assert b is not None, "the search_concurrent throughput budget vanished"
        assert b.min_rps > 0

    def test_unknown_operations_return_none_not_a_default(self):
        """A silent default would make a typo'd operation look like a pass."""
        from app.services.performance_budgets import get_throughput_budget

        assert get_throughput_budget("no_such_operation") is None


class TestTheHarnessBindsThem:
    def test_at_least_one_scenario_carries_a_throughput_budget(self):
        harness = _load_harness()
        bound = [sc for sc in harness.SCENARIOS if getattr(sc, "throughput_op", "")]
        assert bound, (
            "no scenario declares throughput_op, so --check-budgets evaluates "
            "latency only and every throughput budget goes unchecked"
        )

    def test_every_declared_op_resolves_to_a_real_budget(self):
        """The failure mode that made the latency gate inert: a name that does
        not resolve returns 0, and a 0 budget is skipped rather than reported.
        A typo here would silently switch the check back off."""
        harness = _load_harness()
        for sc in harness.SCENARIOS:
            op = getattr(sc, "throughput_op", "")
            if not op:
                continue
            assert harness._throughput_budget(op) > 0, (
                f"scenario {sc.operation!r} declares throughput_op={op!r} but "
                f"that name resolves to no budget — the check is silently off"
            )

    def test_search_throughput_is_among_them(self):
        """The one with a measured breach behind it."""
        harness = _load_harness()
        ops = {getattr(sc, "throughput_op", "") for sc in harness.SCENARIOS}
        assert "search_concurrent" in ops


class TestTheCheckActuallyCompares:
    def test_the_budget_check_reads_measured_rps(self):
        src = _harness_source_without_comments()
        assert "_throughput_budget" in src, (
            "the harness never resolves a throughput budget"
        )
        assert re.search(r"result\.rps\s*(>=|<)", src), (
            "measured rps is never compared against a budget, so throughput "
            "budgets are still decorative"
        )

    def test_uncovered_budgets_are_enumerated(self):
        """A budget no scenario drives must be *named*, not silently omitted —
        otherwise the summary line implies coverage the run does not have."""
        src = _harness_source_without_comments()
        assert "_all_throughput_budgets" in src, (
            "nothing enumerates the full budget list, so budgets this harness "
            "does not exercise (run_ingestion, live_events_batch) disappear "
            "from the report instead of being flagged as unchecked"
        )
