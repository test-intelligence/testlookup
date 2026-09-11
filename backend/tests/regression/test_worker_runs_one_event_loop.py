"""A worker runs its tasks on the runner's one event loop, and on no other (QA of re-audit M1).

Loop-bound clients -- ``get_redis()``, the engine, the shared httpx client --
belong to the loop that first uses them, and the runner drains them when it
discards its loop. A task that started a loop of its own (``asyncio.run()``
inside a Celery task, say) would hand those clients to a second loop, and leave
behind clients that nothing drains. Nothing does that today; this keeps it so.
"""
from __future__ import annotations

import ast
import pathlib

APP = pathlib.Path(__file__).resolve().parents[2] / "app"

#: (path under app/, enclosing function; "*" for the whole file) -> why it may.
_ALLOWED = {
    ("worker/loop_runner.py", "*"): "the runner itself",
    ("services/prompt_registry.py", "_attest"): "a command-line entry point; it never runs in a worker",
    ("services/prompt_eval_recordings.py", "main"): "the --record command-line entry point (re-audit M16); never runs in a worker",
}

_ASYNCIO_LOOP_FUNCTIONS = {"run", "new_event_loop"}
_LOOP_METHODS = {"run_until_complete", "run_forever"}


def _loop_runs(source: str) -> list[tuple[int, str]]:
    """(line, enclosing function) for every call in ``source`` that runs a loop."""
    tree = ast.parse(source)
    modules: set[str] = set()
    functions: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules |= {alias.asname or alias.name for alias in node.names if alias.name == "asyncio"}
        elif isinstance(node, ast.ImportFrom) and node.module == "asyncio":
            functions |= {
                alias.asname or alias.name
                for alias in node.names
                if alias.name in _ASYNCIO_LOOP_FUNCTIONS
            }

    found: list[tuple[int, str]] = []

    def visit(node: ast.AST, function: str) -> None:
        for child in ast.iter_child_nodes(node):
            inner = child.name if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) else function
            if isinstance(child, ast.Call):
                target = child.func
                if isinstance(target, ast.Attribute) and (
                    target.attr in _LOOP_METHODS
                    or (
                        target.attr in _ASYNCIO_LOOP_FUNCTIONS
                        and isinstance(target.value, ast.Name)
                        and target.value.id in modules
                    )
                ):
                    found.append((child.lineno, function))
                elif isinstance(target, ast.Name) and target.id in functions:
                    found.append((child.lineno, function))
            visit(child, inner)

    visit(tree, "<module>")
    return found


def _scan() -> dict[tuple[str, str], list[int]]:
    hits: dict[tuple[str, str], list[int]] = {}
    for path in sorted(APP.rglob("*.py")):
        rel = path.relative_to(APP).as_posix()
        for line, function in _loop_runs(path.read_text(encoding="utf-8")):
            hits.setdefault((rel, function), []).append(line)
    return hits


def test_no_code_outside_the_runner_runs_an_event_loop():
    unexpected = {
        where: lines
        for where, lines in _scan().items()
        if (where[0], "*") not in _ALLOWED and where not in _ALLOWED
    }
    assert not unexpected, (
        "these run an event loop of their own; code a worker can reach must "
        f"go through loop_runner.run_async instead: {unexpected}"
    )


def test_the_scan_sees_the_runner_and_the_command_line_entry():
    hits = _scan()
    assert any(rel == "worker/loop_runner.py" for rel, _ in hits), "the scan cannot see the runner"
    assert ("services/prompt_registry.py", "_attest") in hits, "the scan cannot see asyncio.run"


def test_the_scan_catches_the_shapes_it_claims():
    source = (
        "import asyncio\n"
        "import asyncio as aio\n"
        "from asyncio import run as go, new_event_loop\n"
        "\n"
        "def task_a():\n"
        "    asyncio.run(work())\n"
        "\n"
        "def task_b():\n"
        "    aio.run(work())\n"
        "\n"
        "def task_c():\n"
        "    go(work())\n"
        "\n"
        "def task_d():\n"
        "    loop = new_event_loop()\n"
        "    loop.run_until_complete(work())\n"
        "\n"
        "def fine():\n"
        "    asyncio.sleep(0)\n"
        "    run(work())\n"
    )
    functions = [function for _, function in _loop_runs(source)]
    assert sorted(functions) == ["task_a", "task_b", "task_c", "task_d", "task_d"], functions
