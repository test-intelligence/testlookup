"""Regression guard: a skipped provider must not keep advertising its last
health verdict.

The defect
----------
``persist_probe_results`` began with::

    if r.status == "skipped":
        continue

which left that provider's ``IntegrationHealthCheck`` row completely
untouched — ``status`` and ``last_checked_at`` included. A provider that
stopped being probed therefore kept its last verdict forever.

Measured on the live deployment. ``AI_OFFLINE_MODE`` flipped to false on
2026-08-16, from which point ollama returned
``skipped ("AI_OFFLINE_MODE=false — using cloud LLM")`` every cycle. Five days
later::

    GET /api/v1/integration-health/status
    {"provider":"ollama","status":"healthy",
     "last_checked_at":"2026-08-16T03:30:00Z","message":"Models: "}

`ollama list` on that pod returned an empty table — zero models. So the health
page showed a green "healthy" badge for a service that could not answer a
single request and had not been checked in five days.

A health page that reports OK because it stopped looking is the worst version
of a health page (§6.6: fail-open — the check "passed" because it could not
run).

Two related facts this also fixes
---------------------------------
* The seven never-configured providers (jira, splunk, github, ocp, slack,
  teams, smtp) had **no row at all**, so they were invisible rather than
  visibly-not-monitored. Only 2 of 9 providers appeared on the page —
  verified after the fix: the endpoint now lists 9 (1 healthy + 8 skipped,
  the eighth skip being ollama).
* ``IntegrationHealthPage`` already defines a ``skipped`` badge style. That
  state was unreachable — nothing ever wrote it. Confirmed against the live
  database: ``skipped`` appeared in neither the current-status table nor the
  probe history (2291 healthy, 685 down, 0 skipped).

What must NOT change
--------------------
A skip is not a probe outcome and not a failure:

* no ``IntegrationProbeResult`` history row (it would corrupt ``uptime_pct``
  on the trends tab),
* ``consecutive_failures`` untouched (a skip must never trip the alert
  threshold),
* ``last_success_at`` untouched (the last real success is still the last real
  success).
"""
from __future__ import annotations

import ast
import inspect
import re
import textwrap

import pytest

pytestmark = pytest.mark.regression


def _persist_source() -> str:
    from app.services import integration_probe_service

    return inspect.getsource(integration_probe_service.persist_probe_results)


def _skipped_branch() -> str:
    """The body of the ``if r.status == "skipped":`` branch, via the AST.

    Text slicing does not work here and the failure is instructive: the first
    version cut from the ``if`` to the next literal ``"continue"``, and the
    branch's own comment contains the word *continue* ("A skip used to
    `continue` outright"). The slice came back 60 characters long. Three
    guards in this session have been broken by prose sitting next to the code
    they read — parse the tree instead.
    """
    src = textwrap.dedent(_persist_source())
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        if (
            isinstance(test, ast.Compare)
            and isinstance(test.left, ast.Attribute)
            and test.left.attr == "status"
            and any(
                isinstance(c, ast.Constant) and c.value == "skipped"
                for c in test.comparators
            )
        ):
            return "\n".join(
                ast.get_source_segment(src, stmt) or "" for stmt in node.body
            )
    raise AssertionError(
        'no `if r.status == "skipped":` branch found in persist_probe_results'
    )


def test_the_guard_can_see_the_skipped_branch():
    """Fail-open check: if the branch is gone or renamed, later assertions
    would be reading unrelated text and could pass on nothing."""
    branch = _skipped_branch()
    assert len(branch) > 100, (
        "the skipped branch is suspiciously short; the slice is probably "
        f"wrong: {branch!r}"
    )


def test_a_skipped_provider_gets_its_status_and_timestamp_rewritten():
    branch = _skipped_branch()
    assert 'hc.status = "skipped"' in branch, (
        "a skipped provider keeps its previous status — ollama advertised "
        "'healthy' for five days after probing stopped"
    )
    assert "hc.last_checked_at = now" in branch, (
        "the timestamp is not refreshed, so the page cannot show how stale "
        "the verdict is"
    )


def test_a_skip_is_not_recorded_as_a_probe_outcome():
    """No history row — it would corrupt uptime_pct on the trends tab."""
    branch = _skipped_branch()
    assert "IntegrationProbeResult(" not in branch, (
        "a skip is being written to probe history; uptime_pct would count "
        "'not monitored' cycles as probe results"
    )


def test_a_skip_is_not_treated_as_a_failure():
    branch = _skipped_branch()
    assert "consecutive_failures" not in branch, (
        "a skip touches consecutive_failures — repeated skips would trip the "
        "alert threshold for a provider nobody asked to monitor"
    )
    assert "last_success_at" not in branch, (
        "a skip rewrites last_success_at; the last real success is still the "
        "last real success"
    )


def test_the_stale_prometheus_series_is_scheduled_for_drop():
    """The gauge has no value meaning 'not monitored'.

    Its documented scale is 1=healthy / 0.5=degraded / 0=down, so a skipped
    provider previously kept its last reading indefinitely — ollama pinned at
    1.0 for five days. 0.0 would read as "down"; an absent series is the
    honest answer.
    """
    branch = _skipped_branch()
    assert "metric_updates.append((r.provider, None))" in branch, (
        "the Prometheus series for a skipped provider is left at its last "
        "value, which is the same stale-verdict bug one layer over"
    )


def test_the_skipped_status_the_ui_styles_is_actually_reachable():
    """``IntegrationHealthPage`` defines a ``skipped`` badge. Before this fix
    nothing ever wrote that status, so the style was dead code."""
    src = _persist_source()
    assert re.search(r'hc\.status\s*=\s*"skipped"', src), (
        "no code path writes status='skipped', so the UI's skipped badge can "
        "never render"
    )


def test_the_normal_path_still_records_history_and_failures():
    """The skip branch must not have swallowed the real probe handling."""
    src = _persist_source()
    assert "IntegrationProbeResult(" in src
    assert "hc.consecutive_failures = 0" in src
    assert "hc.last_success_at = now" in src
