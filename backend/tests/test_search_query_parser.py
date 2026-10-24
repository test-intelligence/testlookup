"""W12 SRCH-04: the search box's structured syntax (``services/search_query_parser``).

A closed vocabulary: known keys filter, unknown keys stay text and are
reported, bad values are dropped and reported, and every reported token
carries offsets into the stripped query so the page can cut it out.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.core.search_vocab import (
    FILTER_KEYS,
    MAX_FILTER_TOKENS,
    RELEASE_UNRESOLVED_TEXT,
    ignored_message,
    unsupported_sentence,
)
from app.services.search_query_parser import (
    parse_query,
    parsed_block,
    type_outside_scope_note,
    whole_days_since,
)

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)


def _parse(q: str):
    return parse_query(q, now=NOW)


def _offsets_hold(q: str, parsed) -> None:
    source = q.strip()
    for f in parsed.filters:
        assert source[f.start:f.end] == f.raw, f
    for token in parsed.ignored:
        assert source[token.start:token.end] == token.token, token


# ── Filters, text and quoting ────────────────────────────────────────────────


def test_the_example_query_is_three_filters_and_one_word():
    q = 'status:failed suite:"API Regression" since:7d checkout'
    parsed = _parse(q)
    assert parsed.text == "checkout"
    assert [(f.key, f.value, f.raw) for f in parsed.filters] == [
        ("status", "failed", "status:failed"),
        ("suite", "API Regression", 'suite:"API Regression"'),
        ("since", "7d", "since:7d"),
    ]
    assert parsed.statuses == frozenset({"failed"})
    assert parsed.suites == ("API Regression",)
    assert parsed.since == NOW - timedelta(days=7)
    assert parsed.keys() == frozenset({"status", "suite", "since"})
    assert parsed.word_count == 1 and parsed.filter_count == 3
    assert parsed.ignored == ()
    _offsets_hold(q, parsed)


def test_keys_are_case_insensitive_and_values_keep_what_was_typed():
    parsed = _parse("Status:FAILED BRANCH:Main Env:QA")
    assert [(f.key, f.value) for f in parsed.filters] == [
        ("status", "FAILED"), ("branch", "Main"), ("env", "QA"),
    ]
    assert parsed.statuses == frozenset({"failed"})
    assert parsed.branches == ("Main",) and parsed.envs == ("QA",)


def test_a_quoted_phrase_is_one_text_term_without_its_quotes():
    parsed = _parse('"payment timeout" owner:"Team A"')
    assert parsed.text == "payment timeout"
    assert parsed.owners == ("Team A",)


def test_offsets_index_the_stripped_query():
    q = '   since:24h  "a b"  foo:bar  '
    parsed = _parse(q)
    _offsets_hold(q, parsed)
    assert parsed.filters[0].start == 0


def test_repeated_keys_are_an_or_within_the_key_and_each_is_a_chip():
    parsed = _parse("status:failed status:error type:test type:run suite:a suite:b")
    assert parsed.statuses == frozenset({"failed", "error"})
    assert parsed.types == frozenset({"test_case", "test_run"})
    assert parsed.suites == ("a", "b")
    assert len(parsed.filters) == 6


def test_status_aliases_normalise():
    assert _parse("status:broken").statuses == frozenset({"error"})
    assert _parse("status:passed status:skipped").statuses == frozenset({"passed", "skipped"})


def test_a_repeated_since_keeps_the_last_and_reports_the_earlier():
    q = "since:30d checkout since:7d"
    parsed = _parse(q)
    assert [f.raw for f in parsed.filters] == ["since:7d"]
    assert parsed.since == NOW - timedelta(days=7)
    assert [(t.token, t.reason) for t in parsed.ignored] == [("since:30d", "repeated")]
    _offsets_hold(q, parsed)
    block = parsed_block(parsed, unsupported_by={}, searched=["test_case"], release_unresolved=None)
    assert block["notes"] == ["since: was given twice: the last one is used"]


# ── Unknown keys stay text ───────────────────────────────────────────────────


@pytest.mark.parametrize("token", ["foo:bar", "TypeError:x", "http://host"])
def test_an_unknown_key_is_searched_as_text_and_reported(token):
    parsed = _parse(f"checkout {token}")
    assert parsed.text == f"checkout {token}"
    assert parsed.filters == ()
    [ignored] = parsed.ignored
    assert ignored.reason == "unknown_key"
    assert ignored.token == token
    assert ignored.key == token.split(":", 1)[0].lower()
    _offsets_hold(f"checkout {token}", parsed)


def test_unknown_key_message():
    block = parsed_block(_parse("foo:bar"), unsupported_by={}, searched=[], release_unresolved=None)
    assert block["notes"] == ["unknown filter foo: searched as text"]
    assert block["ignored_tokens"][0]["message"] == "unknown filter foo: searched as text"


def test_error_colon_space_and_a_url_stay_text():
    assert _parse("Error: timeout").text == "Error: timeout"
    assert _parse("Error: timeout").ignored == ()
    assert _parse("http://host").text == "http://host"


# ── Invalid values are dropped and reported ─────────────────────────────────


@pytest.mark.parametrize(
    ("token", "message"),
    [
        ("status:flaky", "flaky is not a status: use type:flaky"),
        ("status:green", "status: takes passed, failed, error or skipped"),
        ("type:widget", "type: takes test, run, suite, defect, flaky or release"),
        ("since:abc", "since: takes 7d, 24h or a date (YYYY-MM-DD) within the last 365 days"),
        ("since:400d", "since: takes 7d, 24h or a date (YYYY-MM-DD) within the last 365 days"),
        ("since:0d", "since: takes 7d, 24h or a date (YYYY-MM-DD) within the last 365 days"),
        ("since:9000h", "since: takes 7d, 24h or a date (YYYY-MM-DD) within the last 365 days"),
        ("since:2026-12-01", "since: takes 7d, 24h or a date (YYYY-MM-DD) within the last 365 days"),
        ("since:2024-01-01", "since: takes 7d, 24h or a date (YYYY-MM-DD) within the last 365 days"),
        ("since:2026-02-30", "since: takes 7d, 24h or a date (YYYY-MM-DD) within the last 365 days"),
    ],
)
def test_an_invalid_value_is_dropped_with_its_message(token, message):
    q = f"checkout {token}"
    parsed = _parse(q)
    assert parsed.text == "checkout", "an invalid filter must not be searched as text"
    assert parsed.filters == ()
    [ignored] = parsed.ignored
    assert ignored.reason == "invalid_value"
    block = parsed_block(parsed, unsupported_by={}, searched=["test_case"], release_unresolved=None)
    assert block["notes"] == [message]
    _offsets_hold(q, parsed)


@pytest.mark.parametrize(
    ("token", "expected"),
    [
        ("since:7d", NOW - timedelta(days=7)),
        ("since:24h", NOW - timedelta(hours=24)),
        ("since:365d", NOW - timedelta(days=365)),
        ("since:2026-10-01", datetime(2026, 10, 1, tzinfo=timezone.utc)),
        ("since:2026-10-24", datetime(2026, 10, 24, tzinfo=timezone.utc)),
    ],
)
def test_since_forms(token, expected):
    assert _parse(token).since == expected


def test_a_value_over_200_characters_is_too_long():
    q = "suite:" + "x" * 201
    parsed = _parse(q)
    assert parsed.filters == ()
    [ignored] = parsed.ignored
    assert ignored.reason == "too_long"
    block = parsed_block(parsed, unsupported_by={}, searched=[], release_unresolved=None)
    assert block["notes"] == ["suite: values are limited to 200 characters"]
    assert len(block["ignored_tokens"][0]["token"]) == 201  # 200 characters and "…"
    assert block["ignored_tokens"][0]["token"].endswith("…")
    assert _parse("suite:" + "x" * 200).suites == ("x" * 200,)


def test_the_eleventh_filter_is_too_many():
    tokens = [f"suite:s{i}" for i in range(MAX_FILTER_TOKENS + 1)]
    q = " ".join(tokens)
    parsed = _parse(q)
    assert len(parsed.filters) == MAX_FILTER_TOKENS
    [ignored] = parsed.ignored
    assert (ignored.reason, ignored.token) == ("too_many", tokens[-1])
    block = parsed_block(parsed, unsupported_by={}, searched=[], release_unresolved=None)
    assert block["notes"] == [f"Only 10 filters are applied: {tokens[-1]} was ignored"]
    _offsets_hold(q, parsed)


def test_an_unterminated_quote_reads_the_rest_as_one_value_and_is_reported_once():
    q = 'checkout suite:"API Regression since:7d'
    parsed = _parse(q)
    assert parsed.suites == ("API Regression since:7d",)
    assert parsed.since is None
    [ignored] = parsed.ignored
    assert ignored.reason == "unterminated_quote"
    block = parsed_block(parsed, unsupported_by={}, searched=[], release_unresolved=None)
    assert block["notes"] == ["A quote was not closed: the rest of the query was read as one value"]
    _offsets_hold(q, parsed)


@pytest.mark.parametrize(
    "q",
    ["'; DROP TABLE x; --", "%_\\", "<script>alert(1)</script>", "a' OR '1'='1"],
)
def test_injection_looking_input_is_literal_text(q):
    parsed = _parse(q)
    assert parsed.filters == ()
    assert parsed.text == " ".join(q.split())


def test_empty_and_whitespace_queries():
    for q in ("", "   "):
        parsed = _parse(q)
        assert parsed.text == "" and parsed.filters == () and parsed.ignored == ()
        assert parsed.keys() == frozenset()


def test_a_known_key_with_an_empty_quoted_value_is_invalid():
    parsed = _parse('suite:""')
    assert parsed.filters == () and parsed.text == ""
    assert parsed.ignored[0].reason == "invalid_value"


def test_every_vocabulary_key_parses():
    values = {
        "status": "failed", "suite": "s", "branch": "main", "env": "qa", "type": "test",
        "since": "7d", "release": "2026.11", "owner": "team",
    }
    assert set(values) == set(FILTER_KEYS)
    parsed = _parse(" ".join(f"{k}:{v}" for k, v in values.items()))
    assert parsed.keys() == frozenset(FILTER_KEYS)
    assert parsed.releases == ("2026.11",)


def test_whole_days_since_rounds_up_and_clamps():
    assert whole_days_since(NOW - timedelta(hours=24), NOW) == 1
    assert whole_days_since(NOW - timedelta(hours=25), NOW) == 2
    assert whole_days_since(NOW - timedelta(days=7), NOW) == 7
    assert whole_days_since(NOW, NOW) == 1
    assert whole_days_since(NOW - timedelta(days=900), NOW) == 365


# ── parsed_block renders the notes in order ──────────────────────────────────


def test_parsed_block_renders_notes_in_the_contract_order():
    parsed = _parse('foo:bar status:flaky branch:main release:"9.9" checkout')
    block = parsed_block(
        parsed,
        unsupported_by={"defect": ["branch", "release"], "suite": ["status"]},
        searched=["test_case", "test_run"],
        release_unresolved="9.9",
    )
    assert block["text"] == "foo:bar checkout"
    assert [f["key"] for f in block["filters"]] == ["branch", "release"]
    assert block["unsupported_by"] == {"suite": ["status"], "defect": ["branch", "release"]}
    assert block["searched_entity_types"] == ["test_case", "test_run"]
    assert block["release_unresolved"] == "9.9"
    assert block["notes"] == [
        ignored_message("unknown_key", key="foo", token="foo:bar"),
        ignored_message("invalid_value", key="status", token="status:flaky", value="flaky"),
        unsupported_sentence("suite", ["status"]),
        unsupported_sentence("defect", ["branch", "release"]),
        RELEASE_UNRESOLVED_TEXT.format(value="9.9"),
    ]
    assert block["notes"][2] == "status: is not supported for Suites, so no suites were searched."
    assert block["notes"][3] == "branch: and release: are not supported for Defects, so no defects were searched."
    assert block["notes"][4] == 'No release named "9.9" in your projects.'


def test_parsed_block_condenses_a_key_several_kinds_could_not_apply():
    parsed = _parse("status:failed branch:main checkout")
    by_kind = {
        "suite": ["status"], "defect": ["status", "branch"], "flaky_test": ["status"],
        "release": ["status", "branch"],
    }
    block = parsed_block(parsed, unsupported_by=by_kind, searched=["test_case", "test_run"], release_unresolved=None)
    assert block["unsupported_by"] == {
        "suite": ["status"], "defect": ["status", "branch"], "flaky_test": ["status"],
        "release": ["status", "branch"],
    }
    assert block["notes"] == [
        "status: applies only to Tests and Runs, so suites, defects, flaky tests and releases were not searched.",
        "branch: applies only to Tests, Runs, Suites and Flaky, so defects and releases were not searched.",
    ]


def test_semantic_mode_words_the_test_result_sentence():
    block = parsed_block(
        _parse("checkout branch:main"), unsupported_by={"test_case": ["branch"]}, searched=[],
        release_unresolved=None, semantic_mode=True,
    )
    assert block["notes"] == ["branch: works in Keyword mode only, so no semantic search was run."]


def test_the_type_outside_scope_note():
    parsed = _parse("type:run")
    assert type_outside_scope_note(parsed, ["defect"]) == (
        "type:run is outside the Defects scope: nothing was searched."
    )
    block = parsed_block(
        parsed, unsupported_by={}, searched=[], release_unresolved=None,
        extra_notes=[type_outside_scope_note(parsed, ["defect"])],
    )
    assert block["notes"] == ["type:run is outside the Defects scope: nothing was searched."]
