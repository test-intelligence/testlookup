"""Regression test suite for 2026-05-18/19 bug fixes + enhancements.

Every file in this package pins a specific bug or enhancement so the
fix can't silently regress. See ``docs/REGRESSION_TEST_SUITE.md`` for
the full manifest and ``memory/project_regression_test_initiative.md``
for the multi-session plan.

Convention: each test module starts with a docstring linking back to
the problem statement and the fix commit, then registers under the
``regression`` pytest marker via the autouse fixture in this
package's conftest.
"""
