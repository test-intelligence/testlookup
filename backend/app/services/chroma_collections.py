"""The ONE naming function and registry for every ChromaDB collection (W12 SRCH-02).

A **generation** is ``(prefix, embedder)``: the Collection prefix (Settings ->
Storage, ``chroma_collection``) and the embedder key (``default`` = ChromaDB's
bundled MiniLM, ``ollama:<model>``, ``openai:<model>``). Every read and every
write uses the ACTIVE generation, which is durable state in ``app_settings``
row ``embedding_index`` (``services/embedding_index_state.py``); an absent row
is the legacy generation ``("", default)`` -- exactly the collections that
existed before W12.

**Names.** The legacy generation keeps today's names BYTE-IDENTICAL
(``test_case_search``, ``knowledge_chunks``, ``agent_memory_vectors``,
``failure_clusters``, ``ai_analysis_cache_<project id as given>``,
``open_defects_<project id as given>``), and its ``create_kwargs()`` is ``{}``,
so every call site makes exactly the call it made before. Any other generation
is ``[<prefix>_]`` + ``<base>`` (global family) or ``<code>_<uuid hex32>``
(per-project family) + ``""`` (bundled embedder) or ``__e`` + 8 hex of
``sha256(embedder key)``, within ChromaDB's rule (3-63 characters of
``[A-Za-z0-9._-]``, alphanumeric at both ends). The longest possible name is
56 characters (an 8-character prefix on ``aac_<hex32>__e<hex8>``).

**Seven families** (``COLLECTION_FAMILIES``): each names the module whose
helper opens it; ``tests/test_chroma_collection_registry.py`` scans
``app/**/*.py`` so an eighth collection site fails until it is registered here.

Commit-free. ``resolve`` never touches a ChromaDB client.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

import structlog

from app.services.embedding_function import (
    DEFAULT_EMBEDDER,
    EmbedderSpec,
)

logger = structlog.get_logger("services.chroma_collections")

COLLECTION_PREFIX_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,7}$")
COLLECTION_PREFIX_MAX = 8
LEGACY_COLLECTION_DEFAULT = "testlookup_embeddings"   # config.CHROMA_COLLECTION's default: read as "no prefix"
PREFIX_INVALID = (
    "A collection prefix is up to 8 lower-case letters, digits or '-', starting with a letter or digit."
)
PREFIX_INVALID_NOTE = (
    "The stored value '{value}' is not a valid prefix and is ignored: collections carry no prefix."
)
FAMILY_STATES = ("pending", "running", "done", "skipped")
REBUILD_KINDS = ("postgres", "copy", "drop", "ephemeral")
GENERATION_SLICE_SECONDS = 480        # one task invocation's work budget; it re-enqueues itself after
RETIRE_GRACE_SECONDS = 600            # >= every cache TTL below: the old generation is dropped this long after the switch
COPY_PAGE = 500
STATE_CACHE_KEY = "config:embedding_index:v1"
STATE_CACHE_TTL_SECONDS = 60
STATE_MEMO_SECONDS = 15

_CHROMA_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]*[a-zA-Z0-9]$")
_EMBEDDER_SUFFIX_RE = re.compile(r"^(?P<rest>.+)__(?P<tag>e[0-9a-f]{8})$")
_HEX32_RE = re.compile(r"^[0-9a-f]{32}$")
_PREFIXED_RE = re.compile(r"^(?P<prefix>[a-z0-9][a-z0-9-]{0,7})_(?P<rest>.+)$")


@dataclass(frozen=True, slots=True)
class CollectionFamily:
    base: str            # the legacy name / name stem
    code: str            # the short stem of a per-project name in a non-legacy generation
    per_project: bool
    rebuild: str         # REBUILD_KINDS: postgres | copy | drop | ephemeral
    owner: str           # the module whose helper opens it (the registry test imports it)
    hnsw_space: str | None


COLLECTION_FAMILIES = (
    CollectionFamily("test_case_search",     "tcs", False, "postgres",  "app.services.semantic_search",             None),
    CollectionFamily("knowledge_chunks",     "kc",  False, "copy",      "app.services.knowledge_chunking_service",  "cosine"),
    CollectionFamily("agent_memory_vectors", "amv", False, "copy",      "app.services.agent_memory_service",        "cosine"),
    CollectionFamily("ai_analysis_cache",    "aac", True,  "drop",      "app.services.semantic_cache",              "cosine"),
    CollectionFamily("open_defects",         "od",  True,  "drop",      "app.services.defect_promotion_service",    None),
    CollectionFamily("failure_clusters",     "fc",  False, "drop",      "app.tools.embed_and_cluster",              None),
    CollectionFamily("dupdetect",            "dd",  True,  "ephemeral", "app.services.duplicate_detection_service", None),
)

_BY_BASE = {f.base: f for f in COLLECTION_FAMILIES}
_BY_CODE = {f.code: f for f in COLLECTION_FAMILIES}


def family(base: str) -> CollectionFamily:
    try:
        return _BY_BASE[base]
    except KeyError:
        raise ValueError(f"unknown collection family {base!r}") from None


def _short_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]


@dataclass(frozen=True, slots=True)
class Generation:
    prefix: str
    embedder: EmbedderSpec

    @property
    def is_legacy(self) -> bool:
        return self.prefix == "" and self.embedder.key == "default"

    @property
    def tag(self) -> str:
        """``""`` for the legacy generation, else ``e`` + 8 hex of the generation's identity.

        The identity is ``<prefix>|<embedder key>``, so two generations that
        differ only by prefix still have different tags (the build's job row
        is keyed ``generation:<tag>``). The NAME suffix is the embedder's own
        tag (:attr:`embedder_tag`), which depends on the embedder alone.
        """
        if self.is_legacy:
            return ""
        return "e" + _short_hash(f"{self.prefix}|{self.embedder.key}")

    @property
    def embedder_tag(self) -> str:
        """``""`` for the bundled embedder, else ``e`` + 8 hex of ``sha256(embedder key)``."""
        if self.embedder.key == "default":
            return ""
        return "e" + _short_hash(self.embedder.key)

    @property
    def label(self) -> str:
        return f"{self.embedder.label} ({self.embedder.model})"

    def as_dict(self) -> dict[str, str]:
        return {"prefix": self.prefix, "provider": self.embedder.provider, "model": self.embedder.model}

    @classmethod
    def from_dict(cls, value: Any) -> "Generation":
        """Strict: a malformed value raises ``ValueError`` (the state parser decides)."""
        if not isinstance(value, Mapping):
            raise ValueError("generation is not a mapping")
        prefix = value.get("prefix", "")
        provider = value.get("provider")
        model = value.get("model")
        if not isinstance(prefix, str) or not isinstance(provider, str) or not isinstance(model, (str, type(None))):
            raise ValueError("generation fields have the wrong types")
        if prefix and not COLLECTION_PREFIX_RE.match(prefix):
            raise ValueError("generation prefix is not a valid prefix")
        return cls(prefix, EmbedderSpec.from_settings(provider, model))

    def read_model(self) -> dict[str, str]:
        """The ``EmbeddingGenerationRead`` body."""
        return {
            "provider": self.embedder.provider,
            "model": self.embedder.model,
            "prefix": self.prefix,
            "key": self.embedder.key,
            "tag": self.tag,
        }


LEGACY_GENERATION = Generation("", DEFAULT_EMBEDDER)


def _check_chroma_name(name: str) -> str:
    if (
        len(name) < 3
        or len(name) > 63
        or not _CHROMA_NAME_RE.match(name)
        or ".." in name
    ):
        raise ValueError(f"collection name {name!r} breaks ChromaDB's naming rule")
    return name


def collection_name(
    base: str,
    *,
    project_id: str | uuid.UUID | None = None,
    generation: Generation,
) -> str:
    """The physical collection name of ``base`` in ``generation``. Pure.

    The legacy generation answers today's names exactly (a per-project name
    keeps the project id as the caller gives it). Raises ``ValueError`` for an
    ephemeral family, a per-project family without a project, an invalid
    prefix, or a name outside ChromaDB's rule.
    """
    fam = family(base)
    if fam.rebuild == "ephemeral":
        raise ValueError(f"the {base} family is ephemeral: it has no persistent name")
    if fam.per_project and (project_id is None or str(project_id) == ""):
        raise ValueError(f"the {base} family is per project: a project id is required")
    if generation.is_legacy:
        return f"{fam.base}_{project_id}" if fam.per_project else fam.base
    if generation.prefix and not COLLECTION_PREFIX_RE.match(generation.prefix):
        raise ValueError(PREFIX_INVALID)
    stem = f"{fam.code}_{uuid.UUID(str(project_id)).hex}" if fam.per_project else fam.base
    head = f"{generation.prefix}_" if generation.prefix else ""
    tail = f"__{generation.embedder_tag}" if generation.embedder_tag else ""
    return _check_chroma_name(f"{head}{stem}{tail}")


def _parse_stem(rest: str, *, embedder_tag: str, allow_legacy: bool) -> tuple[str, str | None] | None:
    fam = _BY_BASE.get(rest)
    if fam is not None and fam.rebuild != "ephemeral":
        if not fam.per_project:
            return fam.base, None
        if allow_legacy and not embedder_tag:
            return fam.base, None        # the legacy UNSCOPED collection (e.g. "ai_analysis_cache")
        return None
    code, sep, project = rest.partition("_")
    if sep:
        by_code = _BY_CODE.get(code)
        if by_code is not None and by_code.per_project and by_code.rebuild != "ephemeral" and _HEX32_RE.match(project):
            return by_code.base, project
    if allow_legacy and not embedder_tag:
        for fam in COLLECTION_FAMILIES:
            if fam.per_project and fam.rebuild != "ephemeral" and rest.startswith(f"{fam.base}_"):
                project_given = rest[len(fam.base) + 1:]
                if project_given:
                    return fam.base, project_given
    return None


def parse_collection_name(name: str) -> tuple[str, str | None, str] | None:
    """Reverse :func:`collection_name`: ``(family base, project, embedder tag)`` or ``None``.

    ``project`` is the uuid hex of a non-legacy per-project name, the project
    id as given for a legacy one, and ``None`` for a global family (or the
    legacy unscoped ``ai_analysis_cache``). The third element is the name's
    EMBEDDER tag (``""`` for the bundled embedder), not the generation tag: a
    prefix is not returned, so match a generation with
    ``collection_name(base, project_id=project, generation=g) == name``.
    The registry's closed set of bases and codes makes the parse unambiguous.
    """
    if not isinstance(name, str) or not name:
        return None
    embedder_tag = ""
    rest = name
    match = _EMBEDDER_SUFFIX_RE.match(name)
    if match:
        rest, embedder_tag = match.group("rest"), match.group("tag")
    parsed = _parse_stem(rest, embedder_tag=embedder_tag, allow_legacy=True)
    if parsed is None:
        prefixed = _PREFIXED_RE.match(rest)
        if prefixed:
            parsed = _parse_stem(prefixed.group("rest"), embedder_tag=embedder_tag, allow_legacy=False)
    if parsed is None:
        return None
    return parsed[0], parsed[1], embedder_tag


def prefix_of(stored: str | None) -> tuple[str, str | None]:
    """``(prefix, note)`` for a stored ``chroma_collection`` value.

    ``""``, ``None`` and the legacy default ``testlookup_embeddings`` mean "no
    prefix"; a valid prefix is itself; anything else reads as no prefix with
    ``PREFIX_INVALID_NOTE`` (a stored value is never re-validated into a 422:
    ``storage_config_service`` re-validates every stored row).
    """
    value = str(stored or "").strip()
    if value in ("", LEGACY_COLLECTION_DEFAULT):
        return "", None
    if COLLECTION_PREFIX_RE.match(value):
        return value, None
    return "", PREFIX_INVALID_NOTE.format(value=value[:80])


def prefix_change_is_valid(value: str | None) -> bool:
    """What PUT /settings/storage accepts on a CHANGE: ``""``, the legacy default, or a prefix."""
    stripped = str(value or "").strip()
    return stripped in ("", LEGACY_COLLECTION_DEFAULT) or bool(COLLECTION_PREFIX_RE.match(stripped))


@dataclass(frozen=True, slots=True)
class Generations:
    active: Generation
    target: Generation | None
    retiring: tuple[Generation, ...]
    status: str = "idle"
    processed: int = 0
    total: int | None = None
    fence: int = 0
    known: bool = True    # False: the state could not be read and nothing was known (legacy assumed)

    def visit_order(self) -> tuple[Generation, ...]:
        """Active, target, then every retiring generation -- what a purge visits (no repeats)."""
        seen: list[Generation] = []
        for gen in (self.active, self.target, *self.retiring):
            if gen is not None and gen not in seen:
                seen.append(gen)
        return tuple(seen)


LEGACY_GENERATIONS = Generations(LEGACY_GENERATION, None, (), known=False)

_memo_lock = threading.Lock()
_memo: tuple[float, Generations] | None = None
_unreadable_logged = False


def _generations_from_value(value: Any) -> Generations:
    from app.services.embedding_index_state import parse_state

    state = parse_state(value)
    return state.generations()


async def _read_state_value() -> Any:
    """ONE ``SELECT`` of the state row on its own short session (the storage-config precedent)."""
    from sqlalchemy import select

    from app.db.postgres import AsyncSessionLocal
    from app.models.postgres import AppSetting
    from app.services.embedding_index_state import EMBEDDING_STATE_KEY

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(AppSetting.value).where(AppSetting.key == EMBEDDING_STATE_KEY)
        )
        return result.scalar_one_or_none()


async def current_generations() -> Generations:
    """The active / target / retiring generations: memo 15 s -> Redis 60 s -> the database.

    Never raises. On a miss the state row is read on its own short session
    (``get_effective_storage_config``'s precedent). When nothing is known and
    the read fails, the LEGACY generation is the answer (logged once per
    process): until a non-legacy generation has ever been activated that is the
    truth, and afterwards the Redis copy or this process's memo knows better.
    """
    global _memo, _unreadable_logged

    now = time.monotonic()
    with _memo_lock:
        memo = _memo
    if memo is not None and now - memo[0] < STATE_MEMO_SECONDS:
        return memo[1]

    generations: Generations | None = None
    try:
        from app.db.redis_client import get_redis

        cached = await get_redis().get(STATE_CACHE_KEY)
        if cached:
            generations = _generations_from_value(json.loads(cached))
    except Exception:  # noqa: BLE001 - Redis is an optional cache
        generations = None

    if generations is None:
        try:
            value = await _read_state_value()
            generations = _generations_from_value(value)
            try:
                from app.db.redis_client import get_redis

                await get_redis().setex(
                    STATE_CACHE_KEY, STATE_CACHE_TTL_SECONDS, json.dumps(value, default=str)
                )
            except Exception:  # noqa: BLE001 - Redis is an optional cache
                pass
        except Exception as exc:  # noqa: BLE001 - a search must never fail on this read
            if memo is not None:
                generations = memo[1]
            else:
                generations = LEGACY_GENERATIONS
                if not _unreadable_logged:
                    _unreadable_logged = True
                    logger.warning(
                        "embedding_generation_state_unreadable",
                        error_type=type(exc).__name__,
                    )

    with _memo_lock:
        _memo = (time.monotonic(), generations)
    return generations


async def invalidate_generations_cache() -> None:
    """Drop this process's memo and the shared Redis copy (after a state commit)."""
    global _memo
    with _memo_lock:
        _memo = None
    try:
        from app.db.redis_client import get_redis

        await get_redis().delete(STATE_CACHE_KEY)
    except Exception:  # noqa: BLE001 - the 60 s TTL bounds staleness
        pass


def reset_generations_memo() -> None:
    """Forget this process's memo (tests; never needed in production)."""
    global _memo, _unreadable_logged
    with _memo_lock:
        _memo = None
    _unreadable_logged = False


@dataclass(frozen=True, slots=True)
class ResolvedCollection:
    name: str
    embedding_function: Any | None
    generation: Generation
    metadata: dict[str, str] | None = field(default=None)

    def create_kwargs(self) -> dict[str, Any]:
        """``{}`` for the bundled embedder (the legacy generation's calls are
        byte-identical); ``{"embedding_function": ef}`` otherwise."""
        if self.embedding_function is None:
            return {}
        return {"embedding_function": self.embedding_function}


async def embedding_function_for(generation: Generation) -> Any | None:
    """The generation's ChromaDB embedding function (``None`` = the bundled one).

    Reads ``offline_mode``, the Ollama origin and the OpenAI key from the
    effective AI configuration (``get_effective_ai_config``, Redis-cached) and
    builds the function off the event loop on a per-process memo miss (the
    policy check resolves a host name). Raises ``EmbedderRefused``.
    """
    from app.services.embedding_function import (
        cached_embedding_function,
        get_chroma_embedding_function,
        ollama_base_url,
    )

    spec = generation.embedder
    if spec.provider == "default":
        return None
    from app.services.ai_config_resolver import get_effective_ai_config

    cfg = await get_effective_ai_config()
    offline = bool(cfg.get("offline_mode", True))
    base_url = ollama_base_url(cfg)
    api_key = cfg.get("openai_api_key") if spec.provider == "openai" else None
    found, ef = cached_embedding_function(
        spec, offline=offline, ollama_base_url=base_url, openai_api_key=api_key
    )
    if found:
        return ef
    return await asyncio.to_thread(
        get_chroma_embedding_function,
        spec,
        offline=offline,
        ollama_base_url=base_url,
        openai_api_key=api_key,
    )


async def resolve(
    base: str,
    *,
    project_id: str | uuid.UUID | None = None,
    generation: Generation | None = None,
) -> ResolvedCollection:
    """Name + embedding function of ``base`` in ``generation`` (default: the ACTIVE one).

    Touches no ChromaDB client. An ephemeral family's name is ``""`` (its
    owner names its own in-memory collection).
    """
    fam = family(base)
    gen = generation if generation is not None else (await current_generations()).active
    name = "" if fam.rebuild == "ephemeral" else collection_name(base, project_id=project_id, generation=gen)
    ef = await embedding_function_for(gen)
    metadata = {"hnsw:space": fam.hnsw_space} if fam.hnsw_space else None
    return ResolvedCollection(name=name, embedding_function=ef, generation=gen, metadata=metadata)


def _is_not_found(exc: BaseException) -> bool:
    """ChromaDB's "no such collection" (1.x ``NotFoundError``; older ``ValueError`` text)."""
    try:
        from chromadb.errors import NotFoundError
    except Exception:  # noqa: BLE001 - chromadb is optional
        pass
    else:
        if isinstance(exc, NotFoundError):
            return True
    text = str(exc).lower()
    return "does not exist" in text or "not found" in text


async def open_existing(client: Any, resolved: ResolvedCollection) -> Any | None:
    """``client.get_collection`` of a resolved name, or ``None`` when it does not exist."""
    try:
        return await asyncio.to_thread(client.get_collection, resolved.name, **resolved.create_kwargs())
    except Exception as exc:
        if _is_not_found(exc):
            return None
        raise


async def _open_target(client: Any, resolved: ResolvedCollection) -> Any:
    kwargs: dict[str, Any] = dict(resolved.create_kwargs())
    if resolved.metadata:
        kwargs["metadata"] = dict(resolved.metadata)
    return await asyncio.to_thread(client.get_or_create_collection, resolved.name, **kwargs)


def _page_items(payload: Mapping[str, Any], *, with_embeddings: bool) -> list[dict[str, Any]]:
    ids = list(payload.get("ids") or [])
    documents = payload.get("documents")
    metadatas = payload.get("metadatas")
    embeddings = payload.get("embeddings") if with_embeddings else None
    items: list[dict[str, Any]] = []
    for index, item_id in enumerate(ids):
        item: dict[str, Any] = {"id": str(item_id)}
        item["document"] = documents[index] if documents is not None and index < len(documents) else None
        item["metadata"] = metadatas[index] if metadatas is not None and index < len(metadatas) else None
        if embeddings is not None and index < len(embeddings):
            item["embedding"] = embeddings[index]
        items.append(item)
    return items


async def _upsert_items(collection: Any, items: list[dict[str, Any]], *, with_embeddings: bool) -> int:
    """Upsert copied items; groups by which fields are present (ChromaDB wants
    parallel lists). Re-embedding needs a document: an item without one is skipped."""
    groups: dict[tuple[bool, bool], list[dict[str, Any]]] = {}
    for item in items:
        if with_embeddings:
            if item.get("embedding") is None:
                continue
        elif not item.get("document"):
            continue
        has_doc = item.get("document") is not None
        has_meta = bool(item.get("metadata"))
        groups.setdefault((has_doc, has_meta), []).append(item)
    written = 0
    for (has_doc, has_meta), group in groups.items():
        kwargs: dict[str, Any] = {"ids": [i["id"] for i in group]}
        if with_embeddings:
            kwargs["embeddings"] = [i["embedding"] for i in group]
        if has_doc:
            kwargs["documents"] = [i["document"] for i in group]
        if has_meta:
            kwargs["metadatas"] = [i["metadata"] for i in group]
        await asyncio.to_thread(collection.upsert, **kwargs)
        written += len(group)
    return written


def _include(with_embeddings: bool) -> list[str]:
    return ["embeddings", "documents", "metadatas"] if with_embeddings else ["documents", "metadatas"]


async def source_count(client: Any, base: str, *, source: Generation) -> int:
    """Documents in ``base``'s collection of ``source`` (0 when it does not exist)."""
    src = await open_existing(client, await resolve(base, generation=source))
    if src is None:
        return 0
    return int(await asyncio.to_thread(src.count))


async def copy_family(
    client: Any,
    base: str,
    *,
    source: Generation,
    target: Generation,
    offset: int,
    deadline: float,
) -> tuple[int, bool]:
    """Copy ``base`` from ``source`` into ``target`` in pages of ``COPY_PAGE`` by offset.

    Same embedder -> the vectors are copied (no model call); otherwise the
    documents and metadatas are upserted and the target's embedding function
    embeds them. Returns ``(copied, finished)``; stops after the page that
    passes ``deadline``. A source collection that does not exist -> ``(0, True)``.
    """
    fam = family(base)
    if fam.per_project or fam.rebuild != "copy":
        raise ValueError(f"the {base} family is not a copy family")
    src = await open_existing(client, await resolve(base, generation=source))
    if src is None:
        return 0, True
    tgt = await _open_target(client, await resolve(base, generation=target))
    with_embeddings = source.embedder.key == target.embedder.key
    copied = 0
    cursor = max(0, int(offset))
    while True:
        payload = await asyncio.to_thread(
            src.get, limit=COPY_PAGE, offset=cursor, include=_include(with_embeddings)
        )
        items = _page_items(payload, with_embeddings=with_embeddings)
        if not items:
            return copied, True
        await _upsert_items(tgt, items, with_embeddings=with_embeddings)
        copied += len(items)
        cursor += len(items)
        if len(items) < COPY_PAGE:
            return copied, True
        if time.monotonic() >= deadline:
            return copied, False


def _chunks(values: list[str], size: int) -> Iterable[list[str]]:
    for start in range(0, len(values), size):
        yield values[start:start + size]


async def catch_up_family(
    client: Any,
    base: str,
    *,
    source: Generation,
    target: Generation,
) -> int:
    """Copy the ids that are in ``source`` and not in ``target``; returns how many."""
    src = await open_existing(client, await resolve(base, generation=source))
    if src is None:
        return 0
    tgt = await _open_target(client, await resolve(base, generation=target))
    src_ids = set(str(i) for i in ((await asyncio.to_thread(src.get, include=[])).get("ids") or []))
    tgt_ids = set(str(i) for i in ((await asyncio.to_thread(tgt.get, include=[])).get("ids") or []))
    missing = sorted(src_ids - tgt_ids)
    with_embeddings = source.embedder.key == target.embedder.key
    for chunk in _chunks(missing, COPY_PAGE):
        payload = await asyncio.to_thread(src.get, ids=chunk, include=_include(with_embeddings))
        await _upsert_items(tgt, _page_items(payload, with_embeddings=with_embeddings),
                            with_embeddings=with_embeddings)
    return len(missing)


def generation_owns(name: str, generation: Generation) -> bool:
    """True when ``name`` is one of ``generation``'s persistent collections."""
    parsed = parse_collection_name(name)
    if parsed is None:
        return False
    base, project, _embedder_tag = parsed
    fam = family(base)
    if fam.rebuild == "ephemeral" or (fam.per_project and project is None):
        return False
    try:
        return collection_name(base, project_id=project, generation=generation) == name
    except ValueError:
        return False


async def drop_generation(client: Any, generation: Generation) -> int:
    """Delete every persistent collection of ``generation`` (list + parse); returns how many."""
    listed = await asyncio.to_thread(client.list_collections)
    names = [item.name if hasattr(item, "name") else str(item) for item in listed]
    dropped = 0
    for name in names:
        if generation_owns(name, generation):
            await asyncio.to_thread(client.delete_collection, name=name)
            dropped += 1
    return dropped
