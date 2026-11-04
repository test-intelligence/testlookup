"""Regression guard (W31 STRAT-12, E-10 / D-W31-12): no log line names a person.

The defect this guards
----------------------
Fifteen log lines rendered a username or an e-mail into the message
(``"Policy created: %s v%d by %s", …, current_user.username``,
``"Failed login attempt for: %s", form_data.username``, an ``email=`` kwarg …).
A log sink is the operator's, outside the product: an erasure can pseudonymise
every row TestLookup holds and still leave the person's name in every log line
written afterwards. W31 changed them to log the user ID; this guard keeps any
FUTURE line from naming a person again.

The rule (an AST scan of ``backend/app``): no logger call — ``<logger>.debug /
info / warning / warn / error / exception / critical / log / msg``, where the
receiver is a logger name (``logger``, ``_slog``, ``log``, ``_logger``,
``slog``, ``LOGGER``, ``self.logger`` …) — passes ``<x>.username``,
``<x>.email`` or ``<x>.full_name`` anywhere in a positional argument or a
keyword value (directly, inside an f-string, ``str(...)``, a ``%`` / ``.format``
expression). Log the id (``by=str(current_user.id)``) instead.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

APP_ROOT = pathlib.Path(__file__).resolve().parents[2] / "app"

PERSON_ATTRIBUTES = frozenset({"username", "email", "full_name"})
LOG_METHODS = frozenset({"debug", "info", "warning", "warn", "error", "exception", "critical", "log", "msg"})

#: Lines found by this scan in files OUTSIDE W31's file ownership (contract §6.2), keyed by (file, the call's
#: first string argument) so a moved line still matches. A RATCHET: it may only shrink — an entry whose line
#: is gone (fixed) turns ``test_every_pinned_exception_still_exists`` red until it is removed here.
#: Empty since the W31 orchestrator fixed the last three (routers/ingest.py ×2 → user_id, worker/tasks.py's digest
#: failure → the user id and the error type).
KNOWN_OUTSIDE_W31: dict[tuple[str, str], str] = {}


def _is_logger(receiver: ast.AST) -> bool:
    """``logger`` / ``_slog`` / ``log`` / ``self.logger`` / ``LOGGER`` … — a name that says it logs."""
    if isinstance(receiver, ast.Name):
        name = receiver.id.lower().lstrip("_")
    elif isinstance(receiver, ast.Attribute):
        name = receiver.attr.lower().lstrip("_")
    else:
        return False
    return name in {"log", "logger", "slog", "structlog_logger", "audit_logger"} or name.endswith("logger")


def _person_attributes(node: ast.AST) -> list[str]:
    return [
        child.attr for child in ast.walk(node)
        if isinstance(child, ast.Attribute) and child.attr in PERSON_ATTRIBUTES
    ]


def _message(call: ast.Call) -> str:
    first = call.args[0] if call.args else None
    return first.value if isinstance(first, ast.Constant) and isinstance(first.value, str) else ""


def _hits(source: str, rel: str) -> list[tuple[str, str, str]]:
    """(the report line, the pin key's file, the call's message) per offending argument."""
    found: list[tuple[str, str, str]] = []
    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr not in LOG_METHODS or not _is_logger(node.func.value):
            continue
        values = list(node.args) + [keyword.value for keyword in node.keywords]
        for value in values:
            for attribute in _person_attributes(value):
                found.append((f"{rel}:{node.lineno} passes .{attribute} to {ast.unparse(node.func)}()",
                              rel, _message(node)))
    return found


def _violations(source: str, rel: str) -> list[str]:
    return [line for line, _rel, _msg in _hits(source, rel)]


def _scan() -> tuple[list[tuple[str, str, str]], int]:
    hits: list[tuple[str, str, str]] = []
    calls = 0
    for path in sorted(APP_ROOT.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        rel = path.relative_to(APP_ROOT.parent).as_posix()
        hits.extend(_hits(source, rel))
        calls += sum(
            1 for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr in LOG_METHODS and _is_logger(node.func.value)
        )
    return hits, calls


@pytest.mark.regression
def test_every_pinned_exception_still_exists() -> None:
    """The ratchet only shrinks: a fixed line must leave KNOWN_OUTSIDE_W31 too."""
    hits, _calls = _scan()
    present = {(rel, message) for _line, rel, message in hits}
    stale = sorted(set(KNOWN_OUTSIDE_W31) - present)
    assert not stale, f"these pinned log lines no longer name a person — remove them from KNOWN_OUTSIDE_W31: {stale}"


@pytest.mark.regression
def test_no_log_line_names_a_person() -> None:
    hits, calls = _scan()
    violations = [line for line, rel, message in hits if (rel, message) not in KNOWN_OUTSIDE_W31]
    assert calls > 500, f"fail-open: the scan saw only {calls} logger calls under {APP_ROOT}"
    assert not violations, (
        "These log lines pass a person's username / e-mail / full name; log the user id instead "
        "(by=str(current_user.id)) — an erasure cannot reach the operator's log sink:\n  " + "\n  ".join(violations)
    )


@pytest.mark.regression
@pytest.mark.parametrize(
    "line",
    [
        'logger.info("Policy created: %s by %s", policy.id, current_user.username)',
        '_slog.info("admin_creating_user", email=payload.email, by=str(current_user.id))',
        'logger.warning(f"Failed login for {form_data.username}")',
        'self.logger.error("x %s", str(user.full_name))',
        'log.info("sent to %s" % recipient.email)',
    ],
)
def test_the_scan_sees_each_shape(line: str) -> None:
    assert _violations(line, "probe.py"), line


@pytest.mark.regression
@pytest.mark.parametrize(
    "line",
    [
        'logger.info("Policy created: %s by user_id=%s", policy.id, current_user.id)',
        '_slog.info("user_profile_updated", user_id=str(user_id), by=str(current_user.id))',
        'settings.email = payload.email',
        'cache.info(user.username)',
    ],
)
def test_the_scan_leaves_ids_and_non_log_calls_alone(line: str) -> None:
    assert not _violations(line, "probe.py"), line
