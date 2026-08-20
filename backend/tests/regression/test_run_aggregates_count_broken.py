"""Regression guard: every aggregate over ``test_runs`` that sums
``failed_tests`` must also sum ``broken_tests``.

The class
---------
"A failure is FAILED *or* BROKEN" is the canonical rule — declared in
``flaky_signals._FAILED_STATUSES``, applied in ``metrics_service._evaluated``,
``analysis_report_service``, ``ingestion._update_run_aggregates`` and
``run_status``. Hand-rolled aggregates keep dropping BROKEN anyway. This is
the **fifth and sixth** instances:

* the trend query (fixed; its comment describes the symptom)
* the ``/coverage`` denominator (fixed)
* the suite pass rate (fixed)
* ``new_failures_24h`` (fixed — ``test_new_failures_kpi_counts_broken.py``,
  whose docstring calls it "the last holdout")
* **``release_service.get_release_details``** — proven live, below
* **``test_management_exports.list_test_suites``** — latent, found by this
  guard rather than by observation

The existing guard for this class only ever reads ``metrics_service``. It
guarded the instance, which is why two more survived in other modules. This
one walks the whole tree.

The live evidence for the release instance
------------------------------------------
A ground-truth release of three runs, each 10 tests / 9 executed / 1 skipped,
with hand-computed outcomes (6+5+4 passed, 9 FAILED, 3 BROKEN)::

    GET /api/v1/releases/{id}
    "metrics": {"total_runs": 3, "total_tests": 30,
                "total_passed": 15, "total_failed": 9, "avg_pass_rate": 55.6}

``total_failed`` read **9** against a truth of **12**, and the response could
not add up: 30 − 15 − 9 leaves 6 unaccounted where only 3 tests were skipped.
Worse, ``avg_pass_rate`` averages per-run ``pass_rate``, which *does* treat
BROKEN as a non-pass — so one payload carried two definitions of failure, the
exact symptom the ``new_failures_24h`` fix recorded. ``ReleasesPage.tsx``
renders the number under the label "Failed".

Why the scan strips comments and docstrings
-------------------------------------------
A comment mentioning ``broken_tests`` must not satisfy the check — that is
the "guards matching their own comment" trap. Raw-SQL string literals are
deliberately kept, because that is where these aggregates actually live.
"""
from __future__ import annotations

import ast
import io
import pathlib
import re
import tokenize

import pytest

pytestmark = pytest.mark.regression

APP = pathlib.Path(__file__).resolve().parents[2] / "app"

_SUM_FAILED = re.compile(
    r"(SUM\s*\(\s*(?:tr\.)?failed_tests\s*\)|func\.sum\(\s*TestRun\.failed_tests\s*\))",
    re.I,
)
_SUM_BROKEN = re.compile(
    r"(SUM\s*\(\s*(?:tr\.)?broken_tests\s*\)|func\.sum\(\s*TestRun\.broken_tests\s*\))",
    re.I,
)


def _strip_comments(src: str) -> str:
    """Remove ``#`` comments, keeping every string literal."""
    out: list[str] = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type == tokenize.COMMENT:
                continue
            out.append(tok.string)
    except (tokenize.TokenError, IndentationError):
        return re.sub(r"#[^\n]*", "", src)
    return "\n".join(out)


def _executable_source(fn: ast.AST, module_src: str) -> str:
    seg = ast.get_source_segment(module_src, fn) or ""
    doc = ast.get_docstring(fn, clean=False)
    if doc:
        seg = seg.replace(doc, "", 1)
    return _strip_comments(seg)


def _functions_summing_failed_without_broken() -> list[str]:
    offenders: list[str] = []
    for path in sorted(APP.rglob("*.py")):
        try:
            module_src = path.read_text(encoding="utf-8")
            tree = ast.parse(module_src)
        except (SyntaxError, OSError):
            continue
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            body = _executable_source(fn, module_src)
            if _SUM_FAILED.search(body) and not _SUM_BROKEN.search(body):
                offenders.append(f"{path.name}::{fn.name}")
    return offenders


def _total_failed_sum_occurrences() -> int:
    total = 0
    for path in sorted(APP.rglob("*.py")):
        try:
            total += len(_SUM_FAILED.findall(path.read_text(encoding="utf-8")))
        except OSError:
            continue
    return total


def test_the_scan_can_actually_see_these_aggregates():
    """Fail-open check: zero offenders must mean "none", not "found nothing".

    If the pattern stops matching — a rename, a new query style — the guard
    below would pass by scanning nothing at all. That is the same fail-open
    shape as a check that reports OK because it could not look.
    """
    assert _total_failed_sum_occurrences() >= 10, (
        "the failed_tests-sum pattern matched almost nothing; the guard below "
        "would pass vacuously"
    )


def test_no_run_aggregate_sums_failed_without_broken():
    offenders = _functions_summing_failed_without_broken()
    assert not offenders, (
        "These aggregate FAILED but not BROKEN, so their failure count "
        f"silently excludes every infrastructure error: {offenders}. "
        "A failure is FAILED *or* BROKEN (flaky_signals._FAILED_STATUSES)."
    )


def test_a_comment_naming_broken_tests_does_not_satisfy_the_guard():
    """The scan must key on the aggregate, not on the word appearing nearby.

    Guards that matched their own comment have survived mutation seven times
    in this repo. This pins the stripping behaviour directly.
    """
    src = (
        "def f():\n"
        "    '''Sums SUM(tr.broken_tests) as well.'''\n"
        "    # also SUM(tr.broken_tests)\n"
        "    return text('SELECT SUM(tr.failed_tests) AS total_failed')\n"
    )
    fn = ast.parse(src).body[0]
    body = _executable_source(fn, src)
    assert _SUM_FAILED.search(body)
    assert not _SUM_BROKEN.search(body), (
        "a docstring or comment naming broken_tests satisfied the guard"
    )


def test_release_metrics_specifically_count_broken():
    """The instance that was proven live, pinned by name."""
    import inspect

    from app.services import release_service

    src = inspect.getsource(release_service.get_release_details)
    src = _strip_comments(src)
    assert "SUM(tr.failed_tests) + SUM(tr.broken_tests) AS total_failed" in src, (
        "the release headline dropped BROKEN while avg_pass_rate beside it "
        "counted them"
    )
