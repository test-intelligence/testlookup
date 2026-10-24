"""W12 SRCH-02: THE guard -- every ChromaDB collection goes through the ONE
registry (``chroma_collections.COLLECTION_FAMILIES``, contract §2.4, §0.16).

(a) The registry lists exactly the SEVEN families; each family's helper, driven
    with a recording fake client, opens ``collection_name(...)`` with the
    generation's ``embedding_function=`` -- and on the legacy generation today's
    name with NO ``embedding_function`` kwarg (the dupdetect helper: only the
    embedding function, on an in-memory random name).
(b) A source scan over ``backend/app/**/*.py``: every
    ``get_or_create_collection`` / ``create_collection`` / ``get_collection`` /
    ``delete_collection`` sits in a family's owner module or
    ``chroma_collections.py``, and none passes a string literal or an f-string
    (except the owner's one ``f"dupdetect_{…}"``) -- so an eighth site fails.
(c) Every owner imports ``chroma_collections`` and resolves through it.

No network: the client is a fake; ``current_generations`` and
``embedding_function_for`` are patched on the registry module.
"""
from __future__ import annotations

import ast
import importlib
import json
import re
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services import chroma_collections as cc
from app.services.embedding_function import DEFAULT_EMBEDDER, EmbedderSpec

APP_DIR = Path(__file__).resolve().parents[1] / "app"
REGISTRY_MODULE = "app.services.chroma_collections"
PROJECT = "0f3e9c1b-2a4d-4c6e-8f10-123456789abc"
NON_LEGACY = cc.Generation("demo", EmbedderSpec("ollama", "nomic-embed-text:v1.5"))
SENTINEL_EF = object()

# Contract §1.2, verbatim: (base, code, per_project, rebuild, owner, hnsw_space).
EXPECTED_FAMILIES = [
    ("test_case_search",     "tcs", False, "postgres",  "app.services.semantic_search",             None),
    ("knowledge_chunks",     "kc",  False, "copy",      "app.services.knowledge_chunking_service",  "cosine"),
    ("agent_memory_vectors", "amv", False, "copy",      "app.services.agent_memory_service",        "cosine"),
    ("ai_analysis_cache",    "aac", True,  "drop",      "app.services.semantic_cache",              "cosine"),
    ("open_defects",         "od",  True,  "drop",      "app.services.defect_promotion_service",    None),
    ("failure_clusters",     "fc",  False, "drop",      "app.tools.embed_and_cluster",              None),
    ("dupdetect",            "dd",  True,  "ephemeral", "app.services.duplicate_detection_service", None),
]
FAMILY_BASES = [row[0] for row in EXPECTED_FAMILIES]


def test_the_registry_is_exactly_the_seven_families_in_order():
    rows = [(f.base, f.code, f.per_project, f.rebuild, f.owner, f.hnsw_space) for f in cc.COLLECTION_FAMILIES]
    assert rows == EXPECTED_FAMILIES
    assert len({f.code for f in cc.COLLECTION_FAMILIES}) == 7
    assert {f.rebuild for f in cc.COLLECTION_FAMILIES} == set(cc.REBUILD_KINDS)


@pytest.mark.parametrize("row", EXPECTED_FAMILIES, ids=FAMILY_BASES)
def test_every_owner_imports_the_registry_and_resolves_its_family_through_it(row):
    base, _code, _per_project, _rebuild, owner, _space = row
    assert cc.family(base).owner == owner
    module = importlib.import_module(owner)
    source = Path(module.__file__).read_text(encoding="utf-8")
    assert re.search(r"from app\.services import chroma_collections\b", source), owner
    assert "chroma_collections.resolve(" in source, owner


# ── (a) each family's helper, driven ─────────────────────────────────────────

class _FakeCollection:
    def upsert(self, *args, **kwargs):
        return None

    def add(self, *args, **kwargs):
        return None

    def count(self):
        return 0

    def query(self, *args, **kwargs):
        return {"ids": [[]], "distances": [[]], "documents": [[]], "metadatas": [[]]}


class _RecordingClient:
    def __init__(self):
        self.calls: list[tuple[str, str, dict]] = []

    def get_or_create_collection(self, name, **kwargs):
        self.calls.append(("get_or_create_collection", name, kwargs))
        return _FakeCollection()

    def create_collection(self, name, **kwargs):
        self.calls.append(("create_collection", name, kwargs))
        return _FakeCollection()


@pytest.fixture
def generation(monkeypatch):
    """Pin the ACTIVE generation; a non-default embedder resolves to SENTINEL_EF."""
    holder = {"active": cc.LEGACY_GENERATION}

    async def _current():
        return cc.Generations(active=holder["active"], target=None, retiring=())

    async def _ef_for(gen):
        return None if gen.embedder.key == "default" else SENTINEL_EF

    monkeypatch.setattr(cc, "current_generations", _current)
    monkeypatch.setattr(cc, "embedding_function_for", _ef_for)
    return holder


async def _drive_test_case_search(monkeypatch, client):
    from app.services import semantic_search

    monkeypatch.setattr(semantic_search, "_get_chroma_client", AsyncMock(return_value=client))
    await semantic_search._get_or_create_collection()


async def _drive_knowledge_chunks(monkeypatch, client):
    from app.services import knowledge_chunking_service

    monkeypatch.setattr(knowledge_chunking_service, "_get_chroma_client", AsyncMock(return_value=client))
    await knowledge_chunking_service._get_or_create_knowledge_collection()


async def _drive_agent_memory_vectors(monkeypatch, client):
    from app.services import agent_memory_service

    monkeypatch.setattr(agent_memory_service, "_get_chroma_client", AsyncMock(return_value=client))
    await agent_memory_service._get_or_create_collection()


async def _drive_ai_analysis_cache(monkeypatch, client):
    from app.services import semantic_cache

    monkeypatch.setattr(semantic_cache, "_get_chroma_client", AsyncMock(return_value=client))
    await semantic_cache._get_or_create_collection(PROJECT)


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return SimpleNamespace(all=lambda: list(self._rows))


async def _drive_open_defects(monkeypatch, client):
    pytest.importorskip("chromadb")
    from app.db import chroma as chroma_db
    from app.services import agent_memory_service, defect_promotion_service, storage_config_service

    monkeypatch.setattr(agent_memory_service, "find_duplicate_defect_memory", AsyncMock(return_value={}))
    monkeypatch.setattr(
        storage_config_service, "get_effective_storage_config",
        AsyncMock(return_value={"chroma_host": "127.0.0.1", "chroma_port": 8000}),
    )
    monkeypatch.setattr(chroma_db, "get_chroma_client", lambda **_kwargs: client)
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_Rows([SimpleNamespace(id=uuid.uuid4(), title="Checkout 500", cluster_id="c1")]))
    await defect_promotion_service._find_duplicate_semantic(project_id=PROJECT, duplicate_hint="Checkout 500", db=db)


async def _drive_failure_clusters(monkeypatch, client):
    from app.tools import embed_and_cluster as module

    monkeypatch.setattr(module, "_get_chroma_client", AsyncMock(return_value=client))
    payload = json.dumps({"test_ids": ["t1", "t2"], "error_messages": ["boom one", "boom two"]})
    await module.embed_and_cluster.coroutine(payload)


async def _drive_dupdetect(monkeypatch, client):
    chromadb = pytest.importorskip("chromadb")
    from app.services import duplicate_detection_service

    monkeypatch.setattr(chromadb, "EphemeralClient", lambda *args, **kwargs: client)
    view = SimpleNamespace(id=uuid.uuid4(), norm_text="login fails", norm_title="login")
    await duplicate_detection_service._semantic_neighbours([view])


DRIVERS = {
    "test_case_search": _drive_test_case_search,
    "knowledge_chunks": _drive_knowledge_chunks,
    "agent_memory_vectors": _drive_agent_memory_vectors,
    "ai_analysis_cache": _drive_ai_analysis_cache,
    "open_defects": _drive_open_defects,
    "failure_clusters": _drive_failure_clusters,
    "dupdetect": _drive_dupdetect,
}


def test_every_family_has_a_driver():
    assert sorted(DRIVERS) == sorted(FAMILY_BASES)


def _expected_name(base: str, gen: cc.Generation) -> str:
    fam = cc.family(base)
    return cc.collection_name(base, project_id=PROJECT if fam.per_project else None, generation=gen)


@pytest.mark.parametrize("base", FAMILY_BASES)
async def test_on_the_legacy_generation_the_helper_makes_todays_call(base, monkeypatch, generation):
    client = _RecordingClient()
    generation["active"] = cc.LEGACY_GENERATION
    await DRIVERS[base](monkeypatch, client)
    assert len(client.calls) == 1, client.calls
    method, name, kwargs = client.calls[0]
    assert "embedding_function" not in kwargs
    if base == "dupdetect":
        assert method == "create_collection"
        assert re.fullmatch(r"dupdetect_[0-9a-f]{32}", name)
        assert kwargs == {}
    else:
        assert method == "get_or_create_collection"
        legacy = f"{base}_{PROJECT}" if cc.family(base).per_project else base
        assert name == legacy == _expected_name(base, cc.LEGACY_GENERATION)


@pytest.mark.parametrize("base", FAMILY_BASES)
async def test_on_another_generation_the_helper_passes_its_name_and_embedding_function(base, monkeypatch, generation):
    client = _RecordingClient()
    generation["active"] = NON_LEGACY
    await DRIVERS[base](monkeypatch, client)
    assert len(client.calls) == 1, client.calls
    _method, name, kwargs = client.calls[0]
    assert kwargs.get("embedding_function") is SENTINEL_EF
    if base == "dupdetect":
        assert re.fullmatch(r"dupdetect_[0-9a-f]{32}", name)   # in-memory: only the function changes
    else:
        assert name == _expected_name(base, NON_LEGACY)
        assert name.startswith("demo_") and name.endswith("__" + NON_LEGACY.embedder_tag)
    space = cc.family(base).hnsw_space
    if space is not None:
        assert kwargs.get("metadata") == {"hnsw:space": space}


async def test_a_prefix_only_generation_renames_without_an_embedding_function(monkeypatch, generation):
    client = _RecordingClient()
    generation["active"] = cc.Generation("demo", DEFAULT_EMBEDDER)
    await _drive_test_case_search(monkeypatch, client)
    assert client.calls == [("get_or_create_collection", "demo_test_case_search", {})]


# ── (b) the source scan: an eighth collection site fails ─────────────────────

COLLECTION_METHODS = frozenset({"get_or_create_collection", "create_collection", "get_collection", "delete_collection"})
ALLOWED_MODULES = frozenset({f.owner for f in cc.COLLECTION_FAMILIES} | {REGISTRY_MODULE})
DUPDETECT_OWNER = cc.family("dupdetect").owner


def _module_name(path: Path) -> str:
    parts = list(path.relative_to(APP_DIR).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(["app", *parts])


def _name_argument(node: ast.Attribute, parent: ast.AST | None) -> ast.AST | None:
    """The collection-name argument of ``x.<method>(name, …)`` or ``to_thread(x.<method>, name, …)``."""
    if not isinstance(parent, ast.Call):
        return None
    if parent.func is node:
        args = parent.args
    elif node in parent.args:
        args = parent.args[parent.args.index(node) + 1:]
    else:
        return None
    if args:
        return args[0]
    return next((kw.value for kw in parent.keywords if kw.arg == "name"), None)


def _violations(module: str, source: str) -> list[str]:
    tree = ast.parse(source)
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    found: list[str] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Attribute) and node.attr in COLLECTION_METHODS):
            continue
        where = f"{module}:{node.lineno} .{node.attr}"
        if module not in ALLOWED_MODULES:
            found.append(f"{where} -- not a registered family's owner (add the family to COLLECTION_FAMILIES)")
            continue
        name = _name_argument(node, parents.get(node))
        if isinstance(name, ast.Constant) and isinstance(name.value, str):
            found.append(f"{where} -- a literal name {name.value!r} (resolve it through chroma_collections)")
        elif isinstance(name, ast.JoinedStr):
            first = name.values[0] if name.values else None
            is_dupdetect = (
                module == DUPDETECT_OWNER
                and isinstance(first, ast.Constant)
                and first.value == "dupdetect_"
            )
            if not is_dupdetect:
                found.append(f"{where} -- an f-string name (resolve it through chroma_collections)")
    return found


def _app_sources() -> list[tuple[str, str]]:
    return [(_module_name(path), path.read_text(encoding="utf-8")) for path in sorted(APP_DIR.rglob("*.py"))]


def test_no_collection_is_opened_outside_the_registered_sites():
    violations = [v for module, source in _app_sources() for v in _violations(module, source)]
    assert violations == []


def test_the_scan_sees_every_owners_site():
    seen: dict[str, int] = {}
    for module, source in _app_sources():
        tree = ast.parse(source)
        count = sum(
            1 for node in ast.walk(tree) if isinstance(node, ast.Attribute) and node.attr in COLLECTION_METHODS
        )
        if count:
            seen[module] = count
    # Every family's owner opens its collection somewhere; nothing else does.
    assert set(seen) - {REGISTRY_MODULE} == {f.owner for f in cc.COLLECTION_FAMILIES}


@pytest.mark.parametrize("snippet", [
    "client.get_or_create_collection('eighth_family')",
    "await asyncio.to_thread(client.get_or_create_collection, 'eighth_family')",
    "client.create_collection(name=f'eighth_{project_id}')",
    "client.get_collection(name)",
    "await asyncio.to_thread(client.delete_collection, name=name)",
])
def test_an_eighth_site_in_an_unregistered_module_fails_the_scan(snippet):
    assert len(_violations("app.services.eighth_family_service", snippet)) == 1


@pytest.mark.parametrize("snippet", [
    "client.get_or_create_collection('test_case_search')",
    "await asyncio.to_thread(client.get_or_create_collection, 'knowledge_chunks', metadata=m)",
    "client.get_or_create_collection(f'ai_analysis_cache_{project_id}')",
    "client.create_collection(name=f'other_{uuid.uuid4().hex}')",
])
def test_a_hard_coded_name_in_an_owner_fails_the_scan(snippet):
    assert len(_violations(DUPDETECT_OWNER, snippet)) == 1


def test_the_owners_resolved_calls_and_the_dupdetect_fstring_pass_the_scan():
    ok = (
        "client.get_or_create_collection(target.name, **target.create_kwargs())\n"
        "await asyncio.to_thread(client.get_or_create_collection, target.name, metadata=m)\n"
        "client.create_collection(name=f'dupdetect_{uuid.uuid4().hex}', **kw)\n"
    )
    assert _violations(DUPDETECT_OWNER, ok) == []
