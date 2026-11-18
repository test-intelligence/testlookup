"""Every Chroma collection embeds with the function the backend chose, whatever the server says.

chromadb 1.x embeds with the caller's ``embedding_function`` only when it is
not a ``DefaultEmbeddingFunction``, which is also what the client uses when the
caller passes none. Otherwise ``Collection._embed`` builds the function from the
collection configuration the Chroma server returns. On the bundled embedder
(the default install) every call site passed none, so anything able to write to
Chroma, which has no authentication of its own, could name, say, the ``openai``
embedding function with its own ``api_base`` and ``api_key_env_var``. The
backend would then post every document and query, with that environment
variable as the bearer token, to an endpoint of its choosing. The chromadb
advisories GHSA-f4j7-r4q5-qw2c, GHSA-36p7-vc44-83pf, GHSA-2wm9-hf6c-p5cr and
GHSA-xph7-9rjv-w5fr have no fixed release; network isolation limits who can
write to Chroma (test_chroma_network_isolation.py), and this closes the client
side.

``ResolvedCollection.create_kwargs()`` now always names the function: on the
bundled embedder, ``local_embedding_function()``. The first tests hold that
every opener under ``app/`` goes through it, on any machine. The rest drive
real chromadb (where it is installed: CI and the images) through the app's own
``open_existing`` and ``create_kwargs``: a collection whose stored configuration
names a hostile function, an existing collection created the old way, and a new
one.
"""
from __future__ import annotations

import ast
import pathlib
import uuid

import pytest

from app.services import chroma_collections as cc
from app.services.embedding_function import LocalMiniLMEmbeddingFunction, local_embedding_function

pytestmark = pytest.mark.regression

BACKEND = pathlib.Path(__file__).resolve().parents[2]
APP = BACKEND / "app"
OPENERS = {"get_or_create_collection", "create_collection", "get_collection"}


def _bundled(name: str) -> cc.ResolvedCollection:
    return cc.ResolvedCollection(name=name, embedding_function=None, generation=cc.LEGACY_GENERATION)


def _opener_calls(tree: ast.AST) -> list[ast.Call]:
    """Each call that opens a collection: ``client.<opener>(...)`` or ``to_thread(client.<opener>, ...)``."""
    calls = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in OPENERS:
            calls.append(node)
        elif (
            getattr(func, "attr", getattr(func, "id", None)) == "to_thread"
            and node.args
            and isinstance(node.args[0], ast.Attribute)
            and node.args[0].attr in OPENERS
        ):
            calls.append(node)
    return calls


def _names_its_function(call: ast.Call) -> bool:
    """True when the call spreads ``**<resolved>.create_kwargs()`` or passes
    ``embedding_function=local_embedding_function()``: the only two forms that
    can never leave chromadb to build the server's function."""
    for kw in call.keywords:
        value = kw.value
        if not isinstance(value, ast.Call):
            continue
        if kw.arg is None and isinstance(value.func, ast.Attribute) and value.func.attr == "create_kwargs":
            return True
        if (
            kw.arg == "embedding_function"
            and isinstance(value.func, ast.Name)
            and value.func.id == "local_embedding_function"
        ):
            return True
    return False


def _app_opener_sites() -> list[tuple[str, ast.Call]]:
    sites = []
    for path in sorted(APP.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        sites += [(f"{path.relative_to(BACKEND).as_posix()}:{call.lineno}", call) for call in _opener_calls(tree)]
    return sites


def test_the_scan_finds_every_family_owner():
    """An empty scan would pass the next test vacuously."""
    files = {site.split(":")[0] for site, _ in _app_opener_sites()}
    owners = {f"app/{f.owner.removeprefix('app.').replace('.', '/')}.py" for f in cc.COLLECTION_FAMILIES}
    assert owners <= files, owners - files


def test_every_collection_opener_names_its_embedding_function():
    missing = [site for site, call in _app_opener_sites() if not _names_its_function(call)]
    assert not missing, (
        "these open a Chroma collection without **<resolved>.create_kwargs() or "
        "embedding_function=local_embedding_function(), so chromadb may embed with whatever the "
        f"server's collection configuration names: {missing}"
    )


def test_the_scan_refuses_an_opener_without_create_kwargs():
    for bad in (
        "client.get_or_create_collection(target.name)",
        "client.get_collection(name, embedding_function=ef) if ef else client.get_collection(name)",
        "await asyncio.to_thread(client.get_or_create_collection, target.name, metadata=m)",
        "client.create_collection(name=f'dupdetect_{uuid.uuid4().hex}', **({'embedding_function': ef} if ef else {}))",
        "client.get_collection(name, embedding_function=ef)",
        "client.get_collection(name, embedding_function=None)",
        "kw = target.create_kwargs()\nclient.get_or_create_collection(target.name, **kw)",
    ):
        calls = _opener_calls(ast.parse(bad))
        assert calls and not all(_names_its_function(c) for c in calls), bad
    for good in (
        "await asyncio.to_thread(client.get_collection, resolved.name, **resolved.create_kwargs())",
        "client.get_or_create_collection(r.name, **metadata, **r.create_kwargs())",
        "await asyncio.to_thread(client.get_collection, name, embedding_function=local_embedding_function())",
    ):
        assert all(_names_its_function(c) for c in _opener_calls(ast.parse(good))), good


def test_the_bundled_embedder_opens_with_the_local_function():
    """Same name, configuration and space as chromadb's default, so existing
    collections still open and new ones are stored exactly as before."""
    ef = _bundled("test_case_search").create_kwargs()["embedding_function"]
    assert isinstance(ef, LocalMiniLMEmbeddingFunction)
    assert (ef.name(), ef.get_config(), ef.default_space(), ef.is_legacy()) == ("default", {}, "l2", False)
    chosen = object()
    passed = cc.ResolvedCollection(name="x", embedding_function=chosen, generation=cc.LEGACY_GENERATION)
    assert passed.create_kwargs() == {"embedding_function": chosen}


# ── Real chromadb ──────────────────────────────────────────────────────────


@pytest.fixture
def chroma(monkeypatch):
    """An in-process client, the ONNX model replaced by a recorder (no download),
    and a registered hostile embedding function that only records its use."""
    chromadb = pytest.importorskip("chromadb")
    from chromadb.api.types import Documents, EmbeddingFunction
    from chromadb.config import Settings
    from chromadb.utils import embedding_functions
    from chromadb.utils.embedding_functions.onnx_mini_lm_l6_v2 import ONNXMiniLM_L6_V2

    calls: list[str] = []

    def fake_onnx(self, input):
        calls.append("local")
        return [[0.1] * 384 for _ in input]

    class Hostile(EmbeddingFunction[Documents]):
        def __init__(self, api_base: str = "http://attacker.invalid") -> None:
            self.api_base = api_base

        def __call__(self, input):
            calls.append(f"hostile:{self.api_base}")
            return [[0.2] * 384 for _ in input]

        @staticmethod
        def name() -> str:
            return "testlookup_hostile_probe"

        def get_config(self):
            return {"api_base": self.api_base}

        @staticmethod
        def build_from_config(config):
            return Hostile(config["api_base"])

    monkeypatch.setattr(ONNXMiniLM_L6_V2, "__call__", fake_onnx)
    monkeypatch.setitem(embedding_functions.known_embedding_functions, Hostile.name(), Hostile)
    # create_collection registers the passed function's class under its name: keep "default" as found.
    monkeypatch.setitem(
        embedding_functions.known_embedding_functions,
        "default",
        embedding_functions.known_embedding_functions.get("default"),
    )
    client = chromadb.EphemeralClient(Settings(anonymized_telemetry=False))
    names: list[str] = []

    def name(prefix: str) -> str:
        names.append(f"{prefix}_{uuid.uuid4().hex[:12]}")
        return names[-1]

    yield client, calls, Hostile, name
    for n in names:
        try:
            client.delete_collection(n)
        except Exception:  # noqa: BLE001 - cleanup of a collection a test may not have created
            pass


def _use(collection) -> None:
    collection.upsert(ids=["1"], documents=["an error message"])
    collection.query(query_texts=["a query"], n_results=1)


async def test_a_hostile_server_configuration_is_ignored(chroma):
    client, calls, hostile, name = chroma
    victim = name("victim")
    client.create_collection(victim, embedding_function=hostile())

    # The app's own path: open_existing (get_collection) and get_or_create_collection with create_kwargs().
    _use(await cc.open_existing(client, _bundled(victim)))
    _use(client.get_or_create_collection(victim, **_bundled(victim).create_kwargs()))
    assert calls and set(calls) == {"local"}, calls


def test_without_it_the_hostile_configuration_is_used(chroma):
    """Why the class exists: no embedding_function (main before this change), or
    chromadb's own DefaultEmbeddingFunction, both embed with what the server names."""
    from chromadb.api.types import DefaultEmbeddingFunction

    client, calls, hostile, name = chroma
    victim = name("victim")
    client.create_collection(victim, embedding_function=hostile())
    for kwargs in ({}, {"embedding_function": DefaultEmbeddingFunction()}):
        calls.clear()
        _use(client.get_or_create_collection(victim, **kwargs))
        assert calls and all(c.startswith("hostile:") for c in calls), (kwargs, calls)


async def test_a_collection_created_the_old_way_still_opens(chroma):
    """Every collection created before this change was made with no function: it stores "default"."""
    client, calls, _, name = chroma
    existing = name("existing")
    client.create_collection(existing, metadata={"hnsw:space": "cosine"})
    _use(await cc.open_existing(client, _bundled(existing)))
    assert calls and set(calls) == {"local"}, calls


def test_a_new_collection_is_stored_as_the_old_way_stored_it(chroma):
    client, _, _, name = chroma
    for metadata in ({"hnsw:space": "cosine"}, None):
        old = client.get_or_create_collection(name("old"), metadata=metadata)
        new = client.get_or_create_collection(name("new"), metadata=metadata, **_bundled("x").create_kwargs())
        assert new.configuration_json == old.configuration_json
        assert new.metadata == old.metadata


def test_the_local_function_embeds_through_the_bundled_model(chroma):
    """Through ONNXMiniLM_L6_V2, so the offline weight guard (local_embedder_guard) still covers it."""
    _client, calls, _, _ = chroma
    assert local_embedding_function()(["a document"]) == [[0.1] * 384]
    assert calls == ["local"]
