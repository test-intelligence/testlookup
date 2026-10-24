"""W12 SRCH-02: ``chroma_collections.collection_name`` -- the ONE naming function
(contract §1.2, §1.3, §2.4 "Names").

The legacy generation (no prefix, the bundled embedder) keeps today's names
BYTE-IDENTICAL; any other generation is ``[<prefix>_]`` + ``<base>`` (global)
or ``<code>_<uuid hex32>`` (per project) + ``__e`` + 8 hex of
``sha256(embedder key)`` (none for the bundled embedder), always inside
ChromaDB's rule. ``parse_collection_name`` reverses every form; ``prefix_of``
reads a stored ``chroma_collection`` value. Pure: no client, no network.
"""
from __future__ import annotations

import hashlib
import itertools
import re
import uuid

import pytest

from app.services.chroma_collections import (
    COLLECTION_FAMILIES,
    COLLECTION_PREFIX_MAX,
    COLLECTION_PREFIX_RE,
    LEGACY_COLLECTION_DEFAULT,
    LEGACY_GENERATION,
    PREFIX_INVALID,
    PREFIX_INVALID_NOTE,
    Generation,
    collection_name,
    parse_collection_name,
    prefix_change_is_valid,
    prefix_of,
)
from app.services.embedding_function import DEFAULT_EMBEDDER, EmbedderSpec

PROJECT = "0f3e9c1b-2a4d-4c6e-8f10-123456789abc"
PROJECT_HEX = uuid.UUID(PROJECT).hex
OLLAMA = EmbedderSpec("ollama", "nomic-embed-text:v1.5")
OPENAI = EmbedderSpec("openai", "text-embedding-3-large")
# ChromaDB's rule, written out independently of the module under test.
CHROMA_RULE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*[A-Za-z0-9]$")

PERSISTENT = [f for f in COLLECTION_FAMILIES if f.rebuild != "ephemeral"]
GLOBAL_BASES = [f.base for f in PERSISTENT if not f.per_project]
PER_PROJECT_BASES = [f.base for f in PERSISTENT if f.per_project]
PREFIXES = ["", "a", "0", "demo", "a-b", "abcdefg-", "1-------", "abcdefgh"]
EMBEDDERS = [DEFAULT_EMBEDDER, OLLAMA, OPENAI, EmbedderSpec("ollama", "m" * 190)]


def _embedder_suffix(spec: EmbedderSpec) -> str:
    if spec.key == "default":
        return ""
    return "__e" + hashlib.sha256(spec.key.encode("utf-8")).hexdigest()[:8]


def _non_legacy_generations():
    for prefix, spec in itertools.product(PREFIXES, EMBEDDERS):
        gen = Generation(prefix, spec)
        if not gen.is_legacy:
            yield gen


def _assert_chroma_valid(name: str) -> None:
    assert 3 <= len(name) <= 63, name
    assert CHROMA_RULE.match(name), name
    assert ".." not in name, name
    chroma_segment = pytest.importorskip("chromadb.api.segment")
    chroma_segment.check_index_name(name)   # ChromaDB's own validator accepts it


# ── the legacy generation: today's names, byte-identical ─────────────────────

def test_the_registry_partitions_into_the_expected_shapes():
    assert GLOBAL_BASES == ["test_case_search", "knowledge_chunks", "agent_memory_vectors", "failure_clusters"]
    assert PER_PROJECT_BASES == ["ai_analysis_cache", "open_defects"]


@pytest.mark.parametrize("base", GLOBAL_BASES)
def test_a_global_familys_legacy_name_is_todays_name(base):
    assert collection_name(base, generation=LEGACY_GENERATION) == base
    # A project id given to a global family changes nothing.
    assert collection_name(base, project_id=PROJECT, generation=LEGACY_GENERATION) == base


@pytest.mark.parametrize("base", PER_PROJECT_BASES)
def test_a_per_project_familys_legacy_name_keeps_the_project_id_as_given(base):
    assert collection_name(base, project_id=PROJECT, generation=LEGACY_GENERATION) == f"{base}_{PROJECT}"
    assert collection_name(base, project_id=uuid.UUID(PROJECT), generation=LEGACY_GENERATION) == f"{base}_{PROJECT}"
    upper = PROJECT.upper()
    assert collection_name(base, project_id=upper, generation=LEGACY_GENERATION) == f"{base}_{upper}"


def test_the_examples_of_the_contract_are_byte_identical():
    assert collection_name("ai_analysis_cache", project_id=PROJECT, generation=LEGACY_GENERATION) == (
        "ai_analysis_cache_0f3e9c1b-2a4d-4c6e-8f10-123456789abc"
    )
    assert collection_name("open_defects", project_id=PROJECT, generation=LEGACY_GENERATION) == (
        "open_defects_0f3e9c1b-2a4d-4c6e-8f10-123456789abc"
    )
    assert collection_name("test_case_search", generation=Generation("demo", DEFAULT_EMBEDDER)) == (
        "demo_test_case_search"
    )
    suffix = _embedder_suffix(OLLAMA)
    assert collection_name("test_case_search", generation=Generation("", OLLAMA)) == f"test_case_search{suffix}"
    longest = collection_name("ai_analysis_cache", project_id=PROJECT, generation=Generation("abcdefgh", OLLAMA))
    assert longest == f"abcdefgh_aac_{PROJECT_HEX}{suffix}"
    assert len(longest) == 56


def test_names_need_a_known_family_and_a_project_where_the_family_is_per_project():
    with pytest.raises(ValueError):
        collection_name("eighth_family", generation=LEGACY_GENERATION)
    with pytest.raises(ValueError):
        collection_name("dupdetect", project_id=PROJECT, generation=LEGACY_GENERATION)   # in-memory: no name
    for base in PER_PROJECT_BASES:
        for missing in (None, ""):
            with pytest.raises(ValueError):
                collection_name(base, project_id=missing, generation=LEGACY_GENERATION)
            with pytest.raises(ValueError):
                collection_name(base, project_id=missing, generation=Generation("demo", OLLAMA))
        with pytest.raises(ValueError):
            # a non-legacy per-project name carries the uuid hex: a non-uuid id is refused
            collection_name(base, project_id="not-a-uuid", generation=Generation("demo", OLLAMA))


# ── every other generation: [<prefix>_] stem [__e<8 hex>], inside ChromaDB's rule ─

@pytest.mark.parametrize("gen", list(_non_legacy_generations()), ids=lambda g: f"{g.prefix or '-'}|{g.embedder.key[:20]}")
def test_every_non_legacy_name_has_the_shape_and_keeps_chromas_rule(gen):
    head = f"{gen.prefix}_" if gen.prefix else ""
    tail = _embedder_suffix(gen.embedder)
    for base in GLOBAL_BASES:
        name = collection_name(base, generation=gen)
        assert name == f"{head}{base}{tail}"
        _assert_chroma_valid(name)
        assert parse_collection_name(name) == (base, None, tail[2:])
    for base in PER_PROJECT_BASES:
        code = next(f.code for f in COLLECTION_FAMILIES if f.base == base)
        for project in (PROJECT, uuid.UUID(PROJECT), PROJECT.upper()):
            name = collection_name(base, project_id=project, generation=gen)
            assert name == f"{head}{code}_{PROJECT_HEX}{tail}"
            _assert_chroma_valid(name)
            assert parse_collection_name(name) == (base, PROJECT_HEX, tail[2:])


def test_the_longest_name_is_the_8_character_prefix_on_a_per_project_family():
    lengths = [
        len(collection_name(f.base, project_id=PROJECT if f.per_project else None, generation=gen))
        for gen in _non_legacy_generations()
        for f in PERSISTENT
    ]
    assert max(lengths) == 56 <= 63
    assert min(lengths) >= 3


def test_the_name_suffix_depends_on_the_embedder_alone_and_the_tag_on_both():
    plain, prefixed = Generation("", OLLAMA), Generation("demo", OLLAMA)
    assert plain.embedder_tag == prefixed.embedder_tag == _embedder_suffix(OLLAMA)[2:]
    assert re.fullmatch(r"e[0-9a-f]{8}", plain.tag) and re.fullmatch(r"e[0-9a-f]{8}", prefixed.tag)
    assert plain.tag != prefixed.tag   # the build's job row is keyed generation:<tag>
    assert LEGACY_GENERATION.tag == "" and LEGACY_GENERATION.embedder_tag == ""
    assert Generation("demo", DEFAULT_EMBEDDER).embedder_tag == ""
    assert Generation("demo", DEFAULT_EMBEDDER).tag != ""
    assert Generation("", DEFAULT_EMBEDDER).is_legacy and not Generation("demo", DEFAULT_EMBEDDER).is_legacy


# ── parse_collection_name: every legacy form, and what it is not ─────────────

@pytest.mark.parametrize("base", GLOBAL_BASES)
def test_a_legacy_global_name_parses(base):
    assert parse_collection_name(base) == (base, None, "")


@pytest.mark.parametrize("base", PER_PROJECT_BASES)
def test_a_legacy_per_project_name_parses_to_the_project_as_given(base):
    assert parse_collection_name(f"{base}_{PROJECT}") == (base, PROJECT, "")
    assert parse_collection_name(f"{base}_legacy-id") == (base, "legacy-id", "")


def test_the_legacy_unscoped_analysis_cache_is_recognised():
    assert parse_collection_name("ai_analysis_cache") == ("ai_analysis_cache", None, "")


@pytest.mark.parametrize("name", [
    "", "testlookup_embeddings", "dupdetect_" + PROJECT_HEX, "dd_" + PROJECT_HEX, "unrelated",
    "demo_unrelated", "aac_not-hex", "test_case_search__eXYZ", "toolongprefix_test_case_search",
    "Demo_test_case_search",
])
def test_a_name_outside_the_registry_parses_to_none(name):
    assert parse_collection_name(name) is None


def test_a_prefix_that_spells_a_code_or_a_base_still_parses_unambiguously():
    for prefix in ("aac", "od", "tcs", "kc", "open", "ai", "test", "agent"):
        for spec in (DEFAULT_EMBEDDER, OLLAMA):
            gen = Generation(prefix, spec)
            tag = gen.embedder_tag
            for f in PERSISTENT:
                project = PROJECT if f.per_project else None
                name = collection_name(f.base, project_id=project, generation=gen)
                assert parse_collection_name(name) == (f.base, PROJECT_HEX if f.per_project else None, tag), name


# ── the prefix: <= 8 of [a-z0-9-], alphanumeric first; "" = none ────────────

@pytest.mark.parametrize("value", ["a", "0", "demo", "a-b", "abcdefg-", "abcdefgh", "12345678"])
def test_a_valid_prefix(value):
    assert COLLECTION_PREFIX_RE.match(value)
    assert prefix_of(value) == (value, None)
    assert prefix_change_is_valid(value)


@pytest.mark.parametrize("value", [
    "Demo_1", "DEMO", "de_mo", "-demo", "abcdefghi", "demo.1", "dé", "demo!", "a b",
])
def test_an_invalid_prefix_is_refused_and_a_stored_one_reads_as_none(value):
    assert not COLLECTION_PREFIX_RE.match(value)
    assert not prefix_change_is_valid(value)
    assert prefix_of(value) == ("", PREFIX_INVALID_NOTE.format(value=value))
    with pytest.raises(ValueError) as caught:
        collection_name("test_case_search", generation=Generation(value, OLLAMA))
    assert str(caught.value) == PREFIX_INVALID
    with pytest.raises(ValueError):
        Generation.from_dict({"prefix": value, "provider": "ollama", "model": "m"})


@pytest.mark.parametrize("value", ["", None, "   ", LEGACY_COLLECTION_DEFAULT])
def test_no_prefix_the_legacy_default_and_blank_mean_none(value):
    assert prefix_of(value) == ("", None)
    assert prefix_change_is_valid(value)


def test_the_prefix_constants_and_sentences():
    assert COLLECTION_PREFIX_MAX == 8
    assert LEGACY_COLLECTION_DEFAULT == "testlookup_embeddings"
    assert PREFIX_INVALID == (
        "A collection prefix is up to 8 lower-case letters, digits or '-', starting with a letter or digit."
    )
    assert PREFIX_INVALID_NOTE.format(value="Demo_1") == (
        "The stored value 'Demo_1' is not a valid prefix and is ignored: collections carry no prefix."
    )
    # A very long stored value is cut in the note (it is echoed to the page).
    assert prefix_of("X" * 200) == ("", PREFIX_INVALID_NOTE.format(value="X" * 80))
    # A stored value is read stripped.
    assert prefix_of("  demo ") == ("demo", None)
