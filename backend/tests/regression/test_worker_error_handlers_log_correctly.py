"""Worker error handlers must survive being run.

Four Celery error handlers in ``app/worker/tasks.py`` called the module's
**stdlib** logger (``logger = logging.getLogger(__name__)``, line 13) with
structlog-style keyword fields. ``logging.Logger._log()`` accepts no keyword
beyond exc_info/stack_info/stacklevel/extra, so the log call raised
``TypeError`` *inside the except block*. Three things died together:

1. the diagnostic was never written — the real failure went unrecorded;
2. ``raise RuntimeError(...) from None`` never ran, so the deliberate scrub
   was dead code and the original exception's full chained traceback escaped
   (on the live homelab that meant an asyncpg trace carrying an internal
   host:port, which is exactly what ``from None`` was there to suppress);
3. callers saw ``TypeError: Logger._log() got an unexpected keyword argument``
   instead of the failure that actually happened.

Two of the four were firing continuously on the live homelab. A previous fix
had already hit this trap in this same file and left a comment naming it, so
the guard here is the class, not the instance:

* behaviourally — every one of the four handlers is driven through its failure
  path and must surface its own ``RuntimeError``, never a logging ``TypeError``;
* statically — no stdlib logger anywhere in ``backend/app`` may take keyword
  fields (mirrors the ``backend.stdlib-logger-kwargs`` quality gate, so a
  bare ``pytest`` run catches it even without the gate).
"""
from __future__ import annotations

import ast
import pathlib
import uuid

import pytest

pytest.importorskip("celery")

from app.worker import tasks  # noqa: E402

# Keywords logging.Logger.<level> actually accepts.
_STDLIB_LOG_KWARGS = {"exc_info", "stack_info", "stacklevel", "extra"}
_LEVELS = {
    "debug", "info", "warning", "warn",
    "error", "exception", "critical", "fatal", "log",
}


class _Boom(ConnectionRefusedError):
    """Stands in for the real failure (Postgres refusing a connection)."""


# ── 1. Behaviour: each handler must survive its own error path ───────────────


def _patch_raiser(monkeypatch, module_path: str, attr: str) -> None:
    """Point ``module_path.attr`` at a coroutine that always raises."""
    import importlib

    module = importlib.import_module(module_path)

    async def _raise(*_a, **_kw):
        raise _Boom(111, "Connect call failed")

    monkeypatch.setattr(module, attr, _raise)


HANDLERS = [
    pytest.param(
        "process_decision_report_supersessions",
        "app.services.decision_report_supersession_service",
        "process_pending_decision_report_supersessions",
        "report supersession failed",
        (),
        id="decision_report_supersessions",
    ),
    pytest.param(
        "relay_agent_action_dispatch_outbox",
        "app.services.agent_action_ledger_service",
        "relay_action_dispatch_outbox",
        "action dispatch relay failed",
        (),
        id="action_dispatch_relay",
    ),
    pytest.param(
        "resume_agent_child_investigation",
        "app.agents.investigator.workflow",
        "resume_investigation",
        "child resume failed",
        (str(uuid.uuid4()),),
        id="cluster_child_resume",
    ),
    pytest.param(
        "execute_agent_action",
        "app.services.agent_action_ledger_service",
        "execute_agent_action",
        "action execution failed",
        (str(uuid.uuid4()), str(uuid.uuid4())),
        id="agent_action_execution",
    ),
]


@pytest.mark.parametrize("task_name,module_path,attr,message,args", HANDLERS)
def test_handler_raises_its_own_error_not_a_logging_typeerror(
    monkeypatch, task_name, module_path, attr, message, args
):
    _patch_raiser(monkeypatch, module_path, attr)
    task = getattr(tasks, task_name)

    result = task.apply(args=args)
    exc = result.result

    assert not isinstance(exc, TypeError), (
        f"{task_name} raised a logging TypeError instead of handling the "
        f"failure — the except block itself is broken: {exc}"
    )
    assert isinstance(exc, RuntimeError), (
        f"{task_name} should surface RuntimeError, got {type(exc).__name__}: {exc}"
    )
    assert message in str(exc)
    # The handler names the ORIGINAL failure, which is the whole point of
    # logging it before re-raising.
    assert _Boom.__name__ in str(exc) or "ConnectionRefusedError" in str(exc)


@pytest.mark.parametrize("task_name,module_path,attr,message,args", HANDLERS)
def test_handler_suppresses_the_original_traceback(
    monkeypatch, task_name, module_path, attr, message, args
):
    """``from None`` must actually take effect.

    While the log call raised, the re-raise never ran and the original
    exception escaped with its context intact — leaking a trace the scrub was
    written to withhold.

    Assert on ``__suppress_context__``, NOT ``__cause__``. Measured in both
    states: ``__cause__`` is ``None`` either way, so asserting on it passes
    against the bug and guards nothing. ``__suppress_context__`` is True only
    when ``raise ... from None`` actually executed — and it is the flag that
    decides whether Python prints "During handling of the above exception,
    another exception occurred" followed by the original trace, which is
    exactly what the live worker logs were emitting.
    """
    _patch_raiser(monkeypatch, module_path, attr)

    exc = getattr(tasks, task_name).apply(args=args).result

    assert exc.__suppress_context__ is True, (
        f"{task_name}: 'from None' did not take effect — the original "
        f"{type(exc.__context__).__name__} traceback still escapes with "
        f"{type(exc).__name__}"
    )


# ── 2. Static: the class, across the whole backend ───────────────────────────


def _stdlib_logger_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
            continue
        func = node.value.func
        dotted = ""
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            dotted = f"{func.value.id}.{func.attr}"
        elif isinstance(func, ast.Name):
            dotted = func.id
        if dotted in {"logging.getLogger", "getLogger"}:
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
    return names


def test_no_stdlib_logger_is_called_with_keyword_fields():
    """A module may bind BOTH loggers, so this judges per NAME, not per file.

    Judging per file would either miss ``worker/tasks.py`` (which binds both)
    or condemn ~140 correct stdlib calls beside it.
    """
    app_root = pathlib.Path(__file__).resolve().parents[2] / "app"
    assert app_root.is_dir(), f"cannot find backend/app at {app_root}"

    offenders: list[str] = []
    scanned = 0
    for path in sorted(app_root.rglob("*.py")):
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if "getLogger" not in text:
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        stdlib_names = _stdlib_logger_names(tree)
        if not stdlib_names:
            continue
        scanned += 1
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr in _LEVELS
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id in stdlib_names):
                continue
            bad = sorted(
                k.arg for k in node.keywords
                if k.arg is not None and k.arg not in _STDLIB_LOG_KWARGS
            )
            if bad:
                offenders.append(
                    f"{path.name}:{node.lineno} "
                    f"{node.func.value.id}.{node.func.attr}({', '.join(bad)}=)"
                )

    # Guard the guard: if the walk found no stdlib-logging modules at all it
    # proved nothing, and an empty result must not read as success.
    assert scanned > 0, "found no modules binding a stdlib logger — this test checked nothing"
    assert not offenders, (
        "stdlib logging.Logger called with structlog-style keyword fields — "
        "raises TypeError when the line runs:\n  " + "\n  ".join(offenders)
    )
