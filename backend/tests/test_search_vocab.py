"""``app.core.search_vocab`` (W12): the vocabularies, the sentences and the four pure helpers.

``semantic_availability`` (the index-status reading behind the Hybrid /
Semantic chips), ``ignored_message`` and ``unsupported_sentence`` (the notes
the page shows verbatim), ``quote_term`` (the suggested searches' queries).
The module is pure: it imports nothing from ``app``.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.core import search_vocab as v
from app.core.search_vocab import (
    ENTITY_LABELS,
    ENTITY_TYPES,
    FALLBACK_REASONS,
    FALLBACK_TEXT,
    FILTER_KEYS,
    FILTER_SUPPORT,
    MAX_QUERY_CHARS,
    SCOPE_ENTITY,
    SEARCH_SCOPES,
    SEMANTIC_REASON_TEXT,
    SEMANTIC_UNAVAILABLE_REASONS,
    ignored_message,
    quote_term,
    semantic_availability,
    unsupported_sentence,
)

MODULE = Path(v.__file__)


def test_the_module_is_pure():
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    imported = {
        (node.module or "") if isinstance(node, ast.ImportFrom) else alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert not any(name == "app" or name.startswith("app.") for name in imported), imported
    assert "logging" not in imported and "structlog" not in imported, "nothing here logs"


def test_the_vocabularies_agree_with_each_other():
    assert SEARCH_SCOPES[0] == "all" and set(SCOPE_ENTITY) == set(SEARCH_SCOPES) - {"all"}
    assert set(SCOPE_ENTITY.values()) == set(ENTITY_TYPES) == set(ENTITY_LABELS) == set(FILTER_SUPPORT)
    assert set(FALLBACK_TEXT) == set(FALLBACK_REASONS)
    assert set(SEMANTIC_REASON_TEXT) == set(SEMANTIC_UNAVAILABLE_REASONS)
    for keys in FILTER_SUPPORT.values():
        assert keys <= set(FILTER_KEYS) and {"since", "type"} <= keys
    assert FILTER_SUPPORT["test_case"] == frozenset(FILTER_KEYS), "test results apply every key"
    assert "flaky" not in v.STATUS_VALUES, "flaky is an entity type, not a status"
    assert set(v.RUN_STATUS_VALUES.values()) == {"PASSED", "FAILED"}
    assert set(v.TYPE_VALUES.values()) == set(ENTITY_TYPES)
    assert v.SEMANTIC_FILTER_KEYS == frozenset({"status", "since", "type"})


def test_the_global_search_entity_types_match():
    from app.services.global_search_service import ALL_ENTITY_TYPES

    assert set(ENTITY_TYPES) == set(ALL_ENTITY_TYPES)


# ── semantic_availability ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ({"status": "healthy", "document_count": 12, "embedder_available": True}, (True, None)),
        ({"status": "healthy", "document_count": 12, "embedder_available": None}, (True, None)),
        ({"status": "healthy", "document_count": 12}, (True, None)),
        ({"status": "not_configured", "document_count": 0}, (False, "not_configured")),
        ({"status": "unavailable", "document_count": 0}, (False, "unavailable")),
        ({"status": "unknown", "document_count": 0}, (False, "unknown")),
        ({"document_count": 4}, (False, "unknown")),
        ({"status": "something-new", "document_count": 4}, (False, "unknown")),
        ({"status": "healthy", "document_count": 12, "embedder_available": False}, (False, "embedder_missing")),
        ({"status": "healthy", "document_count": 0, "embedder_available": False}, (False, "embedder_missing")),
        ({"status": "healthy", "document_count": 0, "embedder_available": True}, (False, "index_empty")),
        ({"status": "healthy", "document_count": 0}, (False, "index_empty")),
        # a count that is not an integer is not read as "empty"
        ({"status": "healthy", "document_count": None}, (True, None)),
    ],
)
def test_semantic_availability(status, expected):
    available, reason = semantic_availability(status)
    assert (available, reason) == expected
    assert reason is None or reason in SEMANTIC_UNAVAILABLE_REASONS


# ── ignored_message ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("reason", "key", "token", "value", "expected"),
    [
        ("unknown_key", "foo", "foo:bar", "bar", "unknown filter foo: searched as text"),
        ("invalid_value", "status", "status:nope", "nope", "status: takes passed, failed, error or skipped"),
        ("invalid_value", "status", "status:flaky", "flaky", "flaky is not a status: use type:flaky"),
        ("invalid_value", "status", "status:FLAKY", "FLAKY", "flaky is not a status: use type:flaky"),
        ("invalid_value", "type", "type:bug", "bug", "type: takes test, run, suite, defect, flaky or release"),
        ("invalid_value", "since", "since:abc", "abc",
         "since: takes 7d, 24h or a date (YYYY-MM-DD) within the last 365 days"),
        ("too_long", "suite", "suite:" + "x" * 201, "x" * 201, "suite: values are limited to 200 characters"),
        ("too_many", "env", "env:qa", "qa", "Only 10 filters are applied: env:qa was ignored"),
        ("repeated", "since", "since:7d", "7d", "since: was given twice: the last one is used"),
        ("unterminated_quote", "suite", 'suite:"API', "API",
         "A quote was not closed: the rest of the query was read as one value"),
    ],
)
def test_ignored_message(reason, key, token, value, expected):
    assert ignored_message(reason, key=key, token=token, value=value) == expected


def test_ignored_message_clips_a_long_token_and_never_raises():
    long_token = "env:" + "y" * 400
    message = ignored_message("too_many", key="env", token=long_token)
    assert message.endswith("… was ignored") and len(message) < 260
    assert ignored_message("a-new-reason", key=None, token="zz") == "zz was ignored"
    for reason in v.IGNORED_REASONS:
        assert ignored_message(reason, key="branch", token="branch:x", value="x")


# ── unsupported_sentence ─────────────────────────────────────────────────────


def test_unsupported_sentence():
    assert unsupported_sentence("defect", ["branch"]) == (
        "branch: is not supported for Defects, so no defects were searched."
    )
    assert unsupported_sentence("defect", ["branch", "env"]) == (
        "branch: and env: are not supported for Defects, so no defects were searched."
    )
    assert unsupported_sentence("release", ["suite", "branch", "owner"]) == (
        "suite:, branch: and owner: are not supported for Releases, so no releases were searched."
    )
    assert unsupported_sentence("flaky_test", ["owner"]) == (
        "owner: is not supported for Flaky, so no flaky tests were searched."
    )
    assert unsupported_sentence("test_case", ["suite"], semantic_mode=True) == (
        "suite: works in Keyword mode only, so no semantic search was run."
    )
    assert unsupported_sentence("test_case", ["branch"], semantic_mode=True) == (
        "branch: works in Keyword mode only, so no semantic search was run."
    )
    assert unsupported_sentence("test_case", ["branch", "suite"], semantic_mode=True) == (
        "branch: and suite: work in Keyword mode only, so no semantic search was run."
    )
    assert unsupported_sentence("defect", ["branch", "branch"]) == unsupported_sentence("defect", ["branch"])
    assert unsupported_sentence("defect", []) == ""


# ── Condensed per key (several kinds not searched) ───────────────────────────


@pytest.mark.parametrize(
    ("key", "kinds", "sentence"),
    [
        ("branch", ["defect", "release"],
         "branch: applies only to Tests, Runs, Suites and Flaky, so defects and releases were not searched."),
        ("owner", ["test_run", "suite", "release"],
         "owner: applies only to Tests and Defects, so runs, suites and releases were not searched."),
        ("status", ["suite", "defect", "flaky_test", "release"],
         "status: applies only to Tests and Runs, so suites, defects, flaky tests and releases were not searched."),
        # status:error cannot apply to runs either: it then applies only to Tests.
        ("status", ["release", "test_run"],
         "status: applies only to Tests, so runs and releases were not searched."),
    ],
    ids=["2_kinds", "3_kinds", "4_kinds", "kept_out_of_a_supporting_kind"],
)
def test_unsupported_key_sentence(key, kinds, sentence):
    assert v.unsupported_key_sentence(key, kinds) == sentence
    assert v.unsupported_key_sentence(key, []) == ""


def test_unsupported_notes_condense_a_key_shared_by_several_kinds():
    by_kind = {kind: ["status"] for kind in ("suite", "defect", "flaky_test", "release")}
    assert v.unsupported_notes(by_kind) == [
        "status: applies only to Tests and Runs, so suites, defects, flaky tests and releases were not searched."
    ]


def test_unsupported_notes_keep_the_single_kind_sentence_verbatim():
    assert v.unsupported_notes({"defect": ["branch"]}) == [
        "branch: is not supported for Defects, so no defects were searched."
    ]
    assert v.unsupported_notes({"defect": ["env", "branch"]}) == [
        "branch: and env: are not supported for Defects, so no defects were searched."
    ]
    assert v.unsupported_notes({"test_case": ["branch"]}, semantic_mode=True) == [
        "branch: works in Keyword mode only, so no semantic search was run."
    ]
    assert v.unsupported_notes({}) == [] and v.unsupported_notes({"defect": []}) == []


def test_unsupported_notes_mix_condensed_and_per_kind_sentences_in_key_order():
    # status kept out four kinds (condensed); branch kept out defects AND releases (condensed);
    # owner kept out only runs, env only suites (per kind).
    by_kind = {
        "release": ["status", "branch"], "test_run": ["owner"], "defect": ["branch", "status"],
        "suite": ["status", "env"], "flaky_test": ["status"],
    }
    assert v.unsupported_notes(by_kind) == [
        "status: applies only to Tests and Runs, so suites, defects, flaky tests and releases were not searched.",
        "branch: applies only to Tests, Runs, Suites and Flaky, so defects and releases were not searched.",
        "env: is not supported for Suites, so no suites were searched.",
        "owner: is not supported for Runs, so no runs were searched.",
    ]
    # Two keys that each kept out the SAME single kind read as one per-kind sentence.
    assert v.unsupported_notes({"defect": ["branch", "env"], "suite": ["owner"]}) == [
        "branch: and env: are not supported for Defects, so no defects were searched.",
        "owner: is not supported for Suites, so no suites were searched.",
    ]


# ── quote_term ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("checkout", "checkout"),
        ("API Regression", '"API Regression"'),
        ("a:b", '"a:b"'),
        ("PAY-123", "PAY-123"),
        ('say "hi" there', '"say hi there"'),
        ('"quoted"', "quoted"),
        ("  padded  ", "padded"),
        ("tab\tinside", '"tab\tinside"'),
        ("", ""),
        ('""', ""),
    ],
)
def test_quote_term(text, expected):
    assert quote_term(text) == expected


def test_quote_term_stays_within_the_query_limit():
    assert len(quote_term("w" * 900)) == MAX_QUERY_CHARS
    spaced = quote_term("word " * 300)
    assert len(spaced) <= MAX_QUERY_CHARS and spaced.startswith('"') and spaced.endswith('"')
    assert not spaced[1:-1].endswith(" "), "a clipped quoted term does not end in a space"


def test_the_saved_search_sentences_name_the_vocabularies():
    assert v.SAVED_SEARCH_SCOPE_SENTENCE == "scope must be one of: " + ", ".join(SEARCH_SCOPES) + "."
    assert v.SAVED_SEARCH_MODE_SENTENCE == "mode must be one of: " + ", ".join(v.SEARCH_MODES) + "."
    assert str(MAX_QUERY_CHARS) in v.SAVED_SEARCH_Q_SENTENCE
    assert v.QUERY_TOO_LONG == "Search text is limited to 500 characters."
    assert v.RELEASE_UNRESOLVED_TEXT.format(value="9.9") == 'No release named "9.9" in your projects.'
