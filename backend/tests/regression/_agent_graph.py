"""One definition of "which agent classes owe what", shared by the guards.

Two regression suites assert properties over the same population — that every
agent implementing ``run()`` drives its stage lifecycle, and that it records a
decision. They used to compute that population separately, and both computed it
as "classes whose base is literally ``BaseAgent``". Probing the deployment with
``issubclass`` showed that misses seven classes reached through an intermediate
parent.

A rule with two implementations drifts; this module is the one implementation.
``test_the_guard_and_the_gate_agree`` in the decision suite additionally pins it
against ``scripts/quality_gate.py``, so the CI gate and these tests cannot come
to different conclusions about who is covered.
"""
from __future__ import annotations

import ast
import pathlib

AGENTS_ROOT = pathlib.Path(__file__).resolve().parents[2] / "app" / "agents"

# Support modules under app/agents/ that hold no agent implementation. Mirrors
# _SUPPORT_AGENT_FILES in scripts/quality_gate.py.
SUPPORT_FILES = {
    "__init__.py", "state.py", "workflow.py", "conversation.py", "base.py",
    "consistency.py", "evidence.py", "runners.py", "pipeline.py",
    "persistence.py", "run_compare_agent.py",
}


def agent_classes() -> list[tuple[str, ast.ClassDef]]:
    """Every class under ``app/agents/`` reaching ``BaseAgent`` by any path.

    Resolved by class name across files, because the inheritance that matters
    spans modules (``InfraHypothesisAgent`` -> ``HypothesisAgent`` ->
    ``BaseAgent``). Source-level rather than by import, so a module that fails
    to import cannot silently drop out of the population.
    """
    bases: dict[str, list[str]] = {}
    located: list[tuple[str, ast.ClassDef]] = []
    for path in sorted(AGENTS_ROOT.rglob("*.py")):
        if path.name in SUPPORT_FILES:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover — a broken file fails elsewhere
            continue
        for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
            bases[cls.name] = [
                getattr(b, "id", getattr(b, "attr", "")) for b in cls.bases
            ]
            located.append((path.name, cls))

    def derives(name: str, seen: frozenset = frozenset()) -> bool:
        if name in seen:  # pragma: no cover — cycles are impossible in Python
            return False
        return any(
            base == "BaseAgent" or derives(base, seen | {name})
            for base in bases.get(name, [])
        )

    return [(filename, cls) for filename, cls in located if derives(cls.name)]


def defines_run(cls: ast.ClassDef) -> bool:
    """The class implements the stage itself, rather than inheriting it."""
    return any(
        isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)) and m.name == "run"
        for m in cls.body
    )


def delegates_to_super_run(cls: ast.ClassDef) -> bool:
    """``super().run(...)`` — whatever the parent does still happens."""
    return any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "run"
        and isinstance(node.func.value, ast.Call)
        and getattr(node.func.value.func, "id", "") == "super"
        for node in ast.walk(cls)
    )


def class_calls(cls: ast.ClassDef, attr: str) -> bool:
    return any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == attr
        for node in ast.walk(cls)
    )


def implementers() -> list[tuple[str, ast.ClassDef]]:
    """Agent classes that implement ``run`` and do not simply delegate upward.

    These are the classes that owe both properties. One that inherits ``run``
    is covered wherever that ``run`` is defined.
    """
    return [
        (filename, cls) for filename, cls in agent_classes()
        if defines_run(cls) and not delegates_to_super_run(cls)
    ]
