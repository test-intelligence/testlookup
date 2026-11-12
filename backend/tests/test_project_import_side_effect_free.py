"""W30 ADM-05 phase 2 (contract §2.5 G-1): the project import's code path imports nothing that sends or runs.

An import writes rows, documents and objects — and calls NO ingest, finalize,
notification, digest, webhook, Git / Jira, defect, flaky, release-gate, agent,
LLM or search code (the reference K3s install's SMTP is a live mailbox, its LLM a live model).
This guard walks the REAL source (pure AST, nothing imported, nothing run) from
the import path outward and fails naming any forbidden module it can reach.

The walk is function-granular: a module that is LOADED contributes its
module-level imports (what loading it executes); a function / class the path
USES contributes the imports inside its body (lazy imports run when it runs)
and every same-module function it references. The seeds are every function of
the import modules (``project_import_service``, ``project_import_history``,
``project_import_rows``, ``project_export_documents``, ``project_export_objects``)
and the runner's import job (``run_import_job`` and what it calls). The ONE
allowed exception is the runner's lazy ``app.worker.tasks`` import inside
``_enqueue_reindex`` (the project-scoped full reindex after commit, §0.8) —
allowed BY NAME, nowhere else.

A forbidden module reached only through code the import already called before
W30 is listed in ``KNOWN_TRANSITIVE`` with its chain and why no side effect is
reached; the list may only shrink. The runtime spies (G-2, the round trip) prove
the calls; this guard proves the reach.
"""
from __future__ import annotations

import ast
import re
from collections import deque
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Optional

BACKEND = Path(__file__).resolve().parents[1]
APP = BACKEND / "app"
RUNNER = "app.worker.project_data_runner"
SEED_MODULES = (
    "app.services.project_import_service",
    "app.services.project_import_history",
    "app.services.project_import_rows",
    "app.services.project_export_documents",
    "app.services.project_export_objects",
)
RUNNER_ENTRY = "run_import_job"
#: (module, function) whose lazy import of the named module is allowed — and nothing else is.
ALLOWED_LAZY = {(RUNNER, "_enqueue_reindex"): "app.worker.tasks"}

FORBIDDEN = (
    r"app\.worker\.tasks", r"app\.services\.ingestion.*", r"app\.services\.run_downstream_outbox",
    r"app\.services\.notification(\..+)?", r"app\.services\.notification_.+", r"app\.services\.webhook_service",
    r"aiosmtplib(\..+)?", r"app\.services\.digest.*", r"app\.services\.github_.+", r"app\.services\.gitlab_.+",
    r"app\.services\..*jira.*", r"app\.services\.defect.*", r"app\.services\.flaky_.+",
    r"app\.services\.release_linker", r"app\.services\.release_gate.*", r"app\.agents(\..+)?",
    r"app\.services\.llm_factory", r"app\.services\.model_router", r"app\.services\.semantic_search",
)

#: module → "<import chain> — <why no side effect is reached>". Computed on 2026-11-03's tree; may only shrink.
KNOWN_TRANSITIVE: dict[str, str] = {
    "app.services.ingestion_sanitization": (
        "project_import_service -> app.models.schemas (module level: `from app.services.ingestion_sanitization "
        "import strip_nul_bytes`) — every route and phase 1's import load schemas; the module is pure text / JSON "
        "sanitisers (no session, no I/O, no task); nothing of the ingest PIPELINE is reached"
    ),
    "app.services.flaky_signals": (
        "project_import_service.get_effective_policy -> app.services.retention_service (module level) -> "
        "app.services.ai_history_core (module level: `from app.services.flaky_signals import error_signature`) — "
        "the §2.7 retention pause reads the effective policy; flaky_signals is a pure error-signature hash "
        "(no DB, no I/O, no outbound call — its own docstring); no flaky detection, quarantine or mail is reached"
    ),
}


# ── the source model ─────────────────────────────────────────────────────────

@lru_cache(maxsize=None)
def _path(module: str) -> Optional[Path]:
    base = BACKEND.joinpath(*module.split("."))
    if base.with_suffix(".py").is_file():
        return base.with_suffix(".py")
    if (base / "__init__.py").is_file():
        return base / "__init__.py"
    return None


def _is_module(name: str) -> bool:
    return name.startswith("app.") and _path(name) is not None or name == "app"


@lru_cache(maxsize=None)
def _tree(module: str) -> Optional[ast.Module]:
    path = _path(module)
    return ast.parse(path.read_text(encoding="utf-8")) if path is not None else None


@lru_cache(maxsize=None)
def _defs(module: str) -> dict[str, ast.AST]:
    tree = _tree(module)
    if tree is None:
        return {}
    return {
        node.name: node for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }


def _parents(module: str) -> list[str]:
    parts = module.split(".")
    return [".".join(parts[:i]) for i in range(1, len(parts))]


Item = tuple[str, Optional[str]]          # (module, None = loaded | a def name | "*" = every def)


def _absolute(module: str, node: ast.ImportFrom) -> Optional[str]:
    if node.level == 0:
        return node.module
    package = module.split(".") if _path(module) and _path(module).name == "__init__.py" else module.split(".")[:-1]
    base = package[: len(package) - (node.level - 1)]
    return ".".join(base + ([node.module] if node.module else []))


def _is_type_checking(node: ast.AST) -> bool:
    test = getattr(node, "test", None)
    return isinstance(node, ast.If) and (
        (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING")
        or (isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING")
    )


def _imports(module: str, nodes: Iterable[ast.AST], skip: Optional[str] = None):
    """(targets, aliases): what the import statements among ``nodes`` execute, and name → module aliases."""
    targets: list[Item] = []
    aliases: dict[str, str] = {}
    for node in nodes:
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == skip:
                    continue
                targets.append((alias.name, None))
                aliases[alias.asname or alias.name.split(".")[0]] = alias.name if alias.asname else alias.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom):
            source = _absolute(module, node)
            if not source or source == skip:
                continue
            for alias in node.names:
                name = alias.asname or alias.name
                if _is_module(f"{source}.{alias.name}"):
                    targets.append((f"{source}.{alias.name}", None))
                    aliases[name] = f"{source}.{alias.name}"
                else:
                    targets.append((source, alias.name))
    return targets, aliases


def _uses(module: str, nodes: Iterable[ast.AST], aliases: dict[str, str]) -> list[Item]:
    """Same-module defs a body references by name, and ``alias.attr`` uses of imported modules."""
    defs = _defs(module)
    out: list[Item] = []
    for node in nodes:
        if isinstance(node, ast.Name) and node.id in defs:
            out.append((module, node.id))
        elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in aliases:
            out.append((aliases[node.value.id], node.attr))
    return out


@lru_cache(maxsize=None)
def _module_level(module: str) -> list[ast.AST]:
    """The statements loading ``module`` executes (defs' bodies and TYPE_CHECKING blocks excluded)."""
    tree = _tree(module)
    out: list[ast.AST] = []
    for stmt in tree.body if tree is not None else []:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)) or _is_type_checking(stmt):
            out.extend(stmt.decorator_list if hasattr(stmt, "decorator_list") else [])
            continue
        out.extend(ast.walk(stmt))
    return out


def _expand(item: Item) -> list[Item]:
    module, symbol = item
    if _tree(module) is None:
        return []
    found: list[Item] = [(p, None) for p in _parents(module)]
    level = _module_level(module)
    module_targets, module_aliases = _imports(module, level)
    if symbol is None:
        return found + module_targets + _uses(module, level, module_aliases)
    found.append((module, None))
    names = list(_defs(module)) if symbol == "*" else [symbol]
    for name in names:
        node = _defs(module).get(name)
        if node is None:
            continue                                   # a constant / re-export: loading the module covers it
        body = list(ast.walk(node))
        targets, aliases = _imports(module, body, skip=ALLOWED_LAZY.get((module, name)))
        found += targets + _uses(module, body, {**module_aliases, **aliases})
    return found


@lru_cache(maxsize=1)
def _closure_of_the_real_tree() -> dict[Item, Optional[Item]]:
    return import_path_closure()


def import_path_closure() -> dict[Item, Optional[Item]]:
    """Every (module, symbol) the import path reaches → the item that reached it (None for a seed)."""
    seeds: list[Item] = [(m, "*") for m in SEED_MODULES] + [(RUNNER, None), (RUNNER, RUNNER_ENTRY)]
    parent: dict[Item, Optional[Item]] = {seed: None for seed in seeds}
    queue = deque(seeds)
    while queue:
        item = queue.popleft()
        for target in _expand(item):
            if target not in parent:
                parent[target] = item
                queue.append(target)
    return parent


def _chain(parent: dict[Item, Optional[Item]], item: Item) -> str:
    links: list[str] = []
    current: Optional[Item] = item
    while current is not None:
        module, symbol = current
        links.append(module if symbol in (None, "*") else f"{module}.{symbol}")
        current = parent[current]
    return " <- ".join(links)


def forbidden_reached(parent: dict[Item, Optional[Item]]) -> dict[str, str]:
    """forbidden module → the shortest chain that reaches it (the first found, breadth-first)."""
    out: dict[str, str] = {}
    for item in parent:
        module = item[0]
        if module not in out and any(re.fullmatch(pattern, module) for pattern in FORBIDDEN):
            out[module] = _chain(parent, item)
    return out


# ── the guard ────────────────────────────────────────────────────────────────

def test_the_import_path_reaches_no_forbidden_module():
    reached = forbidden_reached(_closure_of_the_real_tree())
    unexpected = {m: chain for m, chain in reached.items() if m not in KNOWN_TRANSITIVE}
    assert not unexpected, (
        "the project import's code path reaches modules that send / run things (an import must call no ingest, "
        "notification, webhook, Git / Jira, defect, flaky, gate, agent, LLM or search code):\n"
        + "\n".join(f"  {m}  via  {chain}" for m, chain in sorted(unexpected.items()))
        + "\nremove the import, or — only for a pure helper reached through code the import already calls — add it "
        "to KNOWN_TRANSITIVE with its chain and why no side effect is reached"
    )


def test_known_transitive_only_shrinks():
    # an entry nothing reaches any more is stale: delete it (the list may only shrink)
    reached = forbidden_reached(_closure_of_the_real_tree())
    stale = sorted(set(KNOWN_TRANSITIVE) - set(reached))
    assert not stale, f"{stale} are no longer reached by the import path: remove them from KNOWN_TRANSITIVE"
    for module, why in KNOWN_TRANSITIVE.items():
        assert any(re.fullmatch(p, module) for p in FORBIDDEN), f"{module} is not a forbidden module"
        assert " — " in why and "->" in why, f"{module}: give the chain AND why no side effect is reached"


def test_the_walk_finds_what_it_must():
    # fail-open guard: the walk really reaches the import's own machinery
    modules = {m for m, _ in _closure_of_the_real_tree()}
    for expected in (*SEED_MODULES, RUNNER, "app.services.project_export_entities", "app.models.postgres",
                     "app.services.project_data_job_service", "app.services.safe_archive"):
        assert expected in modules, f"the walk did not reach {expected}: the walker is broken"
    assert len(modules) >= 60
    assert all(_path(m) is not None for m in SEED_MODULES), "a seed module moved: update SEED_MODULES"
    assert RUNNER_ENTRY in _defs(RUNNER), f"{RUNNER}.{RUNNER_ENTRY} is gone: update RUNNER_ENTRY"


def test_the_one_lazy_worker_tasks_import_is_allowed_by_name_only():
    # today the runner's reindex enqueue is the only path to app.worker.tasks; its allowance is per FUNCTION
    for (module, function), allowed in ALLOWED_LAZY.items():
        assert module == RUNNER and allowed == "app.worker.tasks"
        node = _defs(module).get(function)
        if node is not None:
            body_imports = [n for n in ast.walk(node) if isinstance(n, (ast.Import, ast.ImportFrom))]
            others = [n for n in body_imports if (getattr(n, "module", None) or "") not in ("", allowed)
                      and (getattr(n, "module", None) or "").startswith("app.")]
            assert not others, f"{function} may import {allowed} and nothing else from app.*"


# ── mutation self-checks (the guard must fail when the path grows a forbidden import) ──

def _with_injected(module: str, source_line: str, *, into: Optional[str] = None) -> dict[str, str]:
    """``forbidden_reached`` with ``source_line`` added to a parsed COPY of ``module`` (at module level, or at the
    top of def ``into``) — no file is touched."""
    real_tree = _tree
    path = _path(module)
    assert path is not None, module
    mutated = ast.parse(path.read_text(encoding="utf-8"))
    injected = ast.parse(source_line).body
    if into is None:
        mutated.body[1:1] = injected
    else:
        target = next(n for n in mutated.body if getattr(n, "name", None) == into)
        target.body[0:0] = injected

    def fake_tree(name: str) -> Optional[ast.Module]:
        return mutated if name == module else real_tree(name)

    _defs.cache_clear()
    _module_level.cache_clear()
    globals()["_tree"] = fake_tree
    try:
        return forbidden_reached(import_path_closure())
    finally:
        globals()["_tree"] = real_tree
        _defs.cache_clear()
        _module_level.cache_clear()


def test_mutation_a_notification_import_in_the_history_import_is_named():
    reached = _with_injected("app.services.project_import_history", "from app.services import notification_service")
    assert "app.services.notification_service" in reached
    assert reached["app.services.notification_service"].startswith("app.services.notification_service")


def test_mutation_worker_tasks_outside_the_allowed_function_is_named():
    reached = _with_injected(RUNNER, "from app.worker.tasks import reindex_search", into=RUNNER_ENTRY)
    assert "app.worker.tasks" in reached

