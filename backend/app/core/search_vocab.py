"""Search vocabularies and sentences (W12: SRCH-01, SRCH-02, SRCH-03, SRCH-04, SRCH-05).

One place for every word and sentence the search routes, services, schemas and
tests share, so a refusal the API sends and the sentence a test asserts cannot
drift apart. Pure: imports nothing from ``app`` (``schemas.py``, the services,
the routers and the tests import it).

Enum-like request / response fields are plain ``str`` everywhere; each
vocabulary below is the ONE definition of its values.

No function here takes or returns the raw search text beyond what it is given
to render (``ignored_message``'s ``token``, ``quote_term``'s ``text``): nothing
here logs.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

# ── Scopes, entity types, modes (SRCH-01) ────────────────────────────────────

#: = the /search page's ``SCOPES`` ids (frontend ``pages/SearchPage.tsx``);
#: G-B's ``tests/test_search_syntax_frontend_parity.py`` pins both.
SEARCH_SCOPES: tuple[str, ...] = ("all", "tests", "runs", "suites", "defects", "flaky", "releases")
SCOPE_ENTITY: dict[str, str] = {
    "tests": "test_case", "runs": "test_run", "suites": "suite", "defects": "defect",
    "flaky": "flaky_test", "releases": "release",
}
#: = ``global_search_service.ALL_ENTITY_TYPES`` (a set there; ordered here).
ENTITY_TYPES: tuple[str, ...] = ("test_case", "test_run", "suite", "defect", "flaky_test", "release")
ENTITY_LABELS: dict[str, str] = {
    "test_case": "Tests", "test_run": "Runs", "suite": "Suites", "defect": "Defects",
    "flaky_test": "Flaky", "release": "Releases",
}
#: The lower-case plural an "…, so no <noun> were searched." sentence uses.
_ENTITY_NOUNS: dict[str, str] = {
    "test_case": "tests", "test_run": "runs", "suite": "suites", "defect": "defects",
    "flaky_test": "flaky tests", "release": "releases",
}
SEARCH_MODES: tuple[str, ...] = ("keyword", "semantic", "hybrid")
SEMANTIC_MODES: tuple[str, ...] = ("semantic", "hybrid")

#: Why ``GET /search`` answered with keyword rows although a semantic mode
#: was asked for (``fallback_reason``); ``FALLBACK_TEXT`` is the banner the
#: page shows verbatim (``fallback_message``).
FALLBACK_REASONS: tuple[str, ...] = ("semantic_unavailable", "no_semantic_match", "no_text")
FALLBACK_TEXT: dict[str, str] = {
    "semantic_unavailable": "Semantic index unavailable — showing keyword results.",
    "no_semantic_match": "No semantic matches — showing keyword results.",
    "no_text": "Semantic search needs words to compare — showing keyword results for the filters.",
}
#: ``GET /search/similar/{id}``'s ``semantic_status``.
SIMILAR_SEMANTIC_STATUSES: tuple[str, ...] = ("ok", "not_configured", "unavailable")

#: ``GET /search/index-status``'s ``semantic_reason`` (None when available).
SEMANTIC_UNAVAILABLE_REASONS: tuple[str, ...] = (
    "not_configured", "unavailable", "embedder_missing", "index_empty", "unknown",
)
SEMANTIC_REASON_TEXT: dict[str, str] = {
    "not_configured": "Needs the semantic index: no ChromaDB server answers here.",
    "unavailable": "The semantic index answered with an error.",
    "embedder_missing": "The embedding model is not on this server, and offline mode forbids downloading it.",
    "index_empty": "The semantic index holds no test results here yet. Re-index it under Searchable data.",
    "unknown": "The semantic index status could not be read.",
}


def semantic_availability(status: Mapping[str, Any]) -> tuple[bool, str | None]:
    """Whether Hybrid / Semantic can answer, from ``get_index_status``'s dict.

    Pure. In order: a status other than ``healthy`` -> ``(False, reason)``
    (``not_configured`` / ``unavailable`` as reported, anything else
    ``unknown``); ``embedder_available is False`` -> ``embedder_missing``;
    ``document_count == 0`` -> ``index_empty``; else ``(True, None)``.

    ``embedder_available`` None (Ollama / OpenAI: not probed on a read) counts
    as available: the fallback banner covers a failure at query time. A
    missing or non-integer ``document_count`` is not read as empty.
    """
    state = status.get("status")
    if state != "healthy":
        if state in ("not_configured", "unavailable"):
            return False, str(state)
        return False, "unknown"
    if status.get("embedder_available") is False:
        return False, "embedder_missing"
    count = status.get("document_count")
    if isinstance(count, int) and not isinstance(count, bool) and count <= 0:
        return False, "index_empty"
    return True, None


# ── The query syntax (SRCH-04) ───────────────────────────────────────────────

MAX_QUERY_CHARS = 500
MAX_FILTER_TOKENS = 10
MAX_FILTER_VALUE_CHARS = 200
#: An ignored token is reported up to this many characters, then "…".
MAX_TOKEN_CHARS = 200
FILTER_KEYS: tuple[str, ...] = ("status", "suite", "branch", "env", "type", "since", "release", "owner")
#: ``status:`` value -> ``TestStatus`` name. There is no ``flaky`` status
#: (``flaky_test`` is an entity type: ``type:flaky``).
STATUS_VALUES: dict[str, str] = {
    "passed": "PASSED", "failed": "FAILED", "error": "BROKEN", "broken": "BROKEN", "skipped": "SKIPPED",
}
#: ``LaunchStatus`` has no error / skipped: runs apply these two only.
RUN_STATUS_VALUES: dict[str, str] = {"passed": "PASSED", "failed": "FAILED"}
#: ``type:`` value -> entity type.
TYPE_VALUES: dict[str, str] = {
    "test": "test_case", "tests": "test_case", "run": "test_run", "runs": "test_run", "suite": "suite",
    "suites": "suite", "defect": "defect", "defects": "defect", "flaky": "flaky_test", "release": "release",
    "releases": "release",
}
SINCE_MAX_DAYS = 365
#: entity type -> the filter keys it applies; an entity that cannot apply a
#: given key is NOT searched (and ``unsupported_sentence`` says so).
FILTER_SUPPORT: dict[str, frozenset[str]] = {
    "test_case": frozenset({"status", "suite", "branch", "env", "release", "owner", "since", "type"}),
    "test_run": frozenset({"status", "suite", "branch", "env", "release", "since", "type"}),   # status: RUN_STATUS_VALUES only
    "suite": frozenset({"suite", "branch", "env", "release", "since", "type"}),
    "defect": frozenset({"owner", "since", "type"}),
    "flaky_test": frozenset({"suite", "branch", "env", "release", "since", "type"}),
    "release": frozenset({"release", "since", "type"}),
}
#: What ``GET /search`` (the semantic / hybrid path, tests only) applies:
#: ``status:`` (ONE value), ``since:`` and ``type:`` naming test results.
SEMANTIC_FILTER_KEYS: frozenset[str] = frozenset({"status", "since", "type"})
IGNORED_REASONS: tuple[str, ...] = (
    "unknown_key", "invalid_value", "too_long", "too_many", "repeated", "unterminated_quote",
)

_STATUS_INVALID = "status: takes passed, failed, error or skipped"
_STATUS_FLAKY = "flaky is not a status: use type:flaky"
_TYPE_INVALID = "type: takes test, run, suite, defect, flaky or release"
_SINCE_INVALID = f"since: takes 7d, 24h or a date (YYYY-MM-DD) within the last {SINCE_MAX_DAYS} days"
_UNTERMINATED = "A quote was not closed: the rest of the query was read as one value"


def _clip(token: str) -> str:
    return token if len(token) <= MAX_TOKEN_CHARS else token[:MAX_TOKEN_CHARS] + "…"


def ignored_message(reason: str, *, key: str | None, token: str, value: str | None = None) -> str:
    """The sentence the page shows verbatim for one ignored token.

    ``key`` is the lower-cased key when the token had one; ``value`` the
    unquoted value (``status:flaky`` gets its own sentence). Never raises: a
    reason outside ``IGNORED_REASONS`` reads as a plain "was ignored".
    """
    name = key or ""
    if reason == "unknown_key":
        return f"unknown filter {name or _clip(token)}: searched as text"
    if reason == "invalid_value":
        if name == "status":
            if value is not None and value.strip().strip('"').lower() == "flaky":
                return _STATUS_FLAKY
            return _STATUS_INVALID
        if name == "type":
            return _TYPE_INVALID
        if name == "since":
            return _SINCE_INVALID
        return f"{name}: the value was not understood and was ignored" if name else f"{_clip(token)} was ignored"
    if reason == "too_long":
        return f"{name or 'filter'}: values are limited to {MAX_FILTER_VALUE_CHARS} characters"
    if reason == "too_many":
        return f"Only {MAX_FILTER_TOKENS} filters are applied: {_clip(token)} was ignored"
    if reason == "repeated":
        return f"{name or 'since'}: was given twice: the last one is used"
    if reason == "unterminated_quote":
        return _UNTERMINATED
    return f"{_clip(token)} was ignored"


def _key_list(keys: Sequence[str]) -> str:
    labels = [f"{k}:" for k in dict.fromkeys(keys)]
    if len(labels) == 1:
        return labels[0]
    return ", ".join(labels[:-1]) + " and " + labels[-1]


def unsupported_sentence(entity_type: str, keys: Sequence[str], *, semantic_mode: bool = False) -> str:
    """The note for an entity type that was NOT searched because of ``keys``.

    ``"branch: is not supported for Defects, so no defects were searched."``;
    two keys: ``"branch: and env: are not supported for Defects, ..."``.
    ``semantic_mode`` (test results under ``GET /search`` in Semantic /
    Hybrid): ``"suite: works in Keyword mode only, so no semantic search was
    run."``. No keys -> ``""``.
    """
    if not keys:
        return ""
    listed = _key_list(keys)
    plural = len(dict.fromkeys(keys)) > 1
    if semantic_mode:
        verb = "work" if plural else "works"
        return f"{listed} {verb} in Keyword mode only, so no semantic search was run."
    label = ENTITY_LABELS.get(entity_type, entity_type)
    noun = _ENTITY_NOUNS.get(entity_type, f"{entity_type} results")
    verb = "are" if plural else "is"
    return f"{listed} {verb} not supported for {label}, so no {noun} were searched."


def _and_list(items: Sequence[str]) -> str:
    """``a``; ``a and b``; ``a, b and c`` (no Oxford comma)."""
    distinct = list(dict.fromkeys(items))
    if len(distinct) <= 1:
        return "".join(distinct)
    return ", ".join(distinct[:-1]) + " and " + distinct[-1]


def _rank(order: Sequence[str], item: str) -> int:
    return order.index(item) if item in order else len(order)


def unsupported_key_sentence(key: str, not_searched: Sequence[str]) -> str:
    """ONE note for a key that kept TWO OR MORE kinds from being searched.

    ``"status: applies only to Tests and Runs, so suites, defects, flaky tests
    and releases were not searched."`` The kinds it applies to are those whose
    ``FILTER_SUPPORT`` holds the key, minus the ones it kept out (``status:error``
    cannot apply to runs, so it applies "only to Tests"). Kind labels are
    ``ENTITY_LABELS``; the kinds not searched are their lower-case plurals, in
    ``ENTITY_TYPES`` order. No kinds -> ``""``.
    """
    excluded = list(dict.fromkeys(not_searched))
    if not excluded:
        return ""
    applies = [
        ENTITY_LABELS[entity] for entity in ENTITY_TYPES
        if key in FILTER_SUPPORT.get(entity, frozenset()) and entity not in excluded
    ]
    nouns = [
        _ENTITY_NOUNS.get(entity, f"{entity} results")
        for entity in sorted(excluded, key=lambda e: _rank(ENTITY_TYPES, e))
    ]
    if not applies:
        return f"{key}: applies to none of these kinds, so {_and_list(nouns)} were not searched."
    return f"{key}: applies only to {_and_list(applies)}, so {_and_list(nouns)} were not searched."


def unsupported_notes(
    unsupported_by: Mapping[str, Sequence[str]], *, semantic_mode: bool = False,
) -> list[str]:
    """The notes for every kind that was NOT searched (``parsed.unsupported_by``), condensed per key.

    One kind not searched: today's sentence for it (``unsupported_sentence``,
    every key it could not apply in one sentence; ``semantic_mode`` words the
    test-result one for ``GET /search`` in Semantic / Hybrid). Several kinds:
    a key that kept two or more of them out gets ONE
    ``unsupported_key_sentence``; the keys that kept out only one kind are
    said per kind with ``unsupported_sentence``. Ordered by key
    (``FILTER_KEYS``), a condensed sentence before a per-kind one.
    """
    entities = sorted(
        (entity for entity, keys in unsupported_by.items() if keys),
        key=lambda e: _rank(ENTITY_TYPES, e),
    )

    def ordered(keys: Sequence[str]) -> list[str]:
        return sorted(dict.fromkeys(keys), key=lambda k: _rank(FILTER_KEYS, k))

    def sentence(entity: str, keys: Sequence[str]) -> str:
        return unsupported_sentence(entity, keys, semantic_mode=semantic_mode and entity == "test_case")

    if len(entities) == 1:
        return [sentence(entities[0], ordered(unsupported_by[entities[0]]))]
    kinds_by_key: dict[str, list[str]] = {}
    for entity in entities:
        for key in ordered(unsupported_by[entity]):
            kinds_by_key.setdefault(key, []).append(entity)
    ranked: list[tuple[tuple[int, int, int], str]] = []
    singles: dict[str, list[str]] = {}
    for key in ordered(list(kinds_by_key)):
        kinds = kinds_by_key[key]
        if len(kinds) >= 2:
            ranked.append(((_rank(FILTER_KEYS, key), 0, 0), unsupported_key_sentence(key, kinds)))
        else:
            singles.setdefault(kinds[0], []).append(key)
    for entity, keys in singles.items():
        ranked.append(((_rank(FILTER_KEYS, keys[0]), 1, _rank(ENTITY_TYPES, entity)), sentence(entity, keys)))
    return [text for _, text in sorted(ranked)]


#: The note for the first ``release:`` value that named no release in the
#: caller's scope (unknown, malformed and foreign read the same: no oracle).
RELEASE_UNRESOLVED_TEXT = 'No release named "{value}" in your projects.'
QUERY_TOO_LONG = f"Search text is limited to {MAX_QUERY_CHARS} characters."


def quote_term(text: str) -> str:
    """``text`` as one query term: quoted when it holds whitespace or ``:``.

    Inner ``"`` are removed (the syntax has no escape), the ends stripped, and
    the result is at most ``MAX_QUERY_CHARS`` characters (a clipped term still
    finds its row: matching is a substring match).
    """
    inner = text.replace('"', "").strip()
    if not inner:
        return ""
    if any(ch.isspace() for ch in inner) or ":" in inner:
        inner = inner[: MAX_QUERY_CHARS - 2].rstrip()
        return f'"{inner}"'
    return inner[:MAX_QUERY_CHARS]


# ── Recents, suggestions, saved searches (SRCH-03) ───────────────────────────

RECENT_MAX = 20
RECENT_TTL_DAYS = 30
RECENT_KEY_FMT = "search:recent:{user_id}"
SUGGESTION_KINDS: tuple[str, ...] = ("failing_test", "open_defect", "active_release")
SUGGESTED_MAX = 5
SUGGESTED_FAILING_DAYS = 7
#: ``saved_views.filters.kind`` of a saved search (``page = "search"``).
SAVED_SEARCH_KIND = "search"
SAVED_SEARCH_KEYS: frozenset[str] = frozenset({"kind", "q", "scope", "mode"})
SAVED_SEARCH_Q_SENTENCE = "A saved search needs its query text (1 to 500 characters)."
SAVED_SEARCH_SCOPE_SENTENCE = "scope must be one of: all, tests, runs, suites, defects, flaky, releases."
SAVED_SEARCH_MODE_SENTENCE = "mode must be one of: keyword, semantic, hybrid."
SAVED_SEARCH_KEYS_SENTENCE = "A saved search holds only kind, q, scope and mode."
SAVED_SEARCH_PAGE_SENTENCE = "A saved search belongs to the search page (page: search)."
SAVED_SEARCH_SHARE_NEEDS_PROJECT = "A search saved for all projects cannot be shared: pin a project first."

# ── Usage (SRCH-05) ──────────────────────────────────────────────────────────

#: ``GET /search/usage?days=``: 1 = today (UTC, from 00:00), 7 and 30 = rolling.
USAGE_DAYS: tuple[int, ...] = (1, 7, 30)
#: The FIXED window ``search_query_events`` rows are kept (not the audit clock).
USAGE_RETENTION_DAYS = 30
#: The HMAC key of ``q_hash`` is sha256(f"{USAGE_HASH_DOMAIN}:{APP_SECRET_KEY}").
USAGE_HASH_DOMAIN = "testlookup-search-usage-v1"
USAGE_PURGE_BATCH = 5000
