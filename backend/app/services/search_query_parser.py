"""The search box's structured syntax (W12 SRCH-04).

``status:failed suite:"API Regression" since:7d checkout`` is three filters and
the free text ``checkout``. The vocabulary is CLOSED (``core/search_vocab``):

* a token ``key:value`` with a known key is a filter; a value may be
  ``"quoted with spaces"``; keys are case-insensitive;
* an UNKNOWN key stays in the text verbatim and is reported (``foo:bar``,
  ``TypeError:x`` and ``http://host`` are searched as typed);
* a known key with a value it cannot take is DROPPED from the text and
  reported (``status:flaky`` -> "flaky is not a status: use type:flaky");
* repeated keys combine as OR within the key; a repeated ``since:`` keeps the
  LAST one; at most ``MAX_FILTER_TOKENS`` filters apply, each value at most
  ``MAX_FILTER_VALUE_CHARS`` characters.

Pure: no I/O, no logging, never raises. Nothing here is evaluated: every value
reaches SQL only as a bind parameter (the text through ``like_contains``), in
``global_search_service`` and ``search_service``.

Offsets (``start`` / ``end``) index the STRIPPED query, so ``q[start:end] ==
raw`` for every filter and every ignored token; the page removes a chip by
cutting that slice.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

from app.core.search_vocab import (
    ENTITY_LABELS,
    ENTITY_TYPES,
    FILTER_KEYS,
    MAX_FILTER_TOKENS,
    MAX_FILTER_VALUE_CHARS,
    MAX_TOKEN_CHARS,
    RELEASE_UNRESOLVED_TEXT,
    SINCE_MAX_DAYS,
    STATUS_VALUES,
    TYPE_VALUES,
    ignored_message,
    unsupported_notes,
)

#: A filter candidate: a 2-20 letter key, a colon, a non-empty value.
_FILTER_RE = re.compile(r"^([A-Za-z_]{2,20}):(.+)$", re.DOTALL)
_SINCE_RELATIVE_RE = re.compile(r"^(\d{1,5})([dh])$", re.IGNORECASE)
_SINCE_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_SINCE_MAX_HOURS = SINCE_MAX_DAYS * 24

#: ``TestStatus`` name -> the normalised ``status:`` name ``ParsedQuery.statuses`` holds.
_STATUS_NAMES = {"PASSED": "passed", "FAILED": "failed", "BROKEN": "error", "SKIPPED": "skipped"}
#: The normalised ``status:`` name -> the ``TestStatus`` value a statement binds.
STATUS_NAME_TO_DB = {"passed": "PASSED", "failed": "FAILED", "error": "BROKEN", "skipped": "SKIPPED"}

#: The note when ``type:`` names nothing inside the scope the request asked for.
TYPE_OUTSIDE_SCOPE = "{types} {verb} outside the {scope} scope: nothing was searched."


@dataclass(frozen=True, slots=True)
class ParsedFilter:
    key: str
    value: str
    raw: str
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class IgnoredToken:
    token: str
    key: str | None
    reason: str
    start: int
    end: int
    value: str | None = None


@dataclass(frozen=True, slots=True)
class ParsedQuery:
    text: str
    filters: tuple[ParsedFilter, ...] = ()
    ignored: tuple[IgnoredToken, ...] = ()
    #: normalised names: "passed" | "failed" | "error" | "skipped"
    statuses: frozenset[str] = frozenset()
    suites: tuple[str, ...] = ()
    branches: tuple[str, ...] = ()
    envs: tuple[str, ...] = ()
    owners: tuple[str, ...] = ()
    #: the raw ``release:`` values (resolved by ``global_search_service.resolve_release_values``)
    releases: tuple[str, ...] = ()
    #: entity types named by ``type:`` (empty = no type filter)
    types: frozenset[str] = frozenset()
    since: datetime | None = None
    _keys: frozenset[str] = field(default=frozenset(), repr=False)

    def keys(self) -> frozenset[str]:
        """The keys of the APPLIED filters."""
        return self._keys

    @property
    def word_count(self) -> int:
        return len(self.text.split())

    @property
    def filter_count(self) -> int:
        return len(self.filters)


def _tokens(source: str) -> list[tuple[int, int, bool]]:
    """``(start, end, unterminated)`` per token: whitespace splits outside double quotes."""
    out: list[tuple[int, int, bool]] = []
    i, n = 0, len(source)
    while i < n:
        while i < n and source[i].isspace():
            i += 1
        if i >= n:
            break
        start = i
        in_quote = False
        while i < n:
            ch = source[i]
            if ch == '"':
                in_quote = not in_quote
            elif ch.isspace() and not in_quote:
                break
            i += 1
        out.append((start, i, in_quote))
    return out


def _unquote(text: str) -> str:
    return text.replace('"', "")


def _parse_since(value: str, now: datetime) -> datetime | None:
    relative = _SINCE_RELATIVE_RE.match(value)
    if relative:
        amount = int(relative.group(1))
        if relative.group(2).lower() == "d":
            return now - timedelta(days=amount) if 1 <= amount <= SINCE_MAX_DAYS else None
        return now - timedelta(hours=amount) if 1 <= amount <= _SINCE_MAX_HOURS else None
    dated = _SINCE_DATE_RE.match(value)
    if not dated:
        return None
    try:
        day = datetime(int(dated.group(1)), int(dated.group(2)), int(dated.group(3)), tzinfo=timezone.utc)
    except ValueError:
        return None
    if day.date() > now.date() or day.date() < (now - timedelta(days=SINCE_MAX_DAYS)).date():
        return None
    return day


def _utc(now: datetime) -> datetime:
    return now.replace(tzinfo=timezone.utc) if now.tzinfo is None else now.astimezone(timezone.utc)


def parse_query(q: str, *, now: datetime) -> ParsedQuery:
    """Split ``q`` into free text, applied filters and reported tokens. Never raises.

    The router refuses a query over ``MAX_QUERY_CHARS`` first; this works on
    whatever it is given (stripped here, so offsets index the stripped text).
    """
    source = (q or "").strip()
    current = _utc(now)
    text_parts: list[str] = []
    filters: list[ParsedFilter] = []
    ignored: list[IgnoredToken] = []
    since_filter: ParsedFilter | None = None
    since_value: datetime | None = None
    values: dict[str, list[str]] = {}

    for start, end, unterminated in _tokens(source):
        raw = source[start:end]
        match = _FILTER_RE.match(raw)
        key = match.group(1).lower() if match else None
        if unterminated:
            ignored.append(IgnoredToken(raw, key, "unterminated_quote", start, end))
        if match is None or key not in FILTER_KEYS:
            if match is not None:
                ignored.append(IgnoredToken(raw, key, "unknown_key", start, end))
            words = _unquote(raw)
            if words.strip():
                text_parts.append(words)
            continue

        value = _unquote(match.group(2)).strip()
        if len(value) > MAX_FILTER_VALUE_CHARS:
            ignored.append(IgnoredToken(raw, key, "too_long", start, end))
            continue
        normalised: Any = None
        if value:
            lowered = value.lower()
            if key == "status":
                normalised = _STATUS_NAMES[STATUS_VALUES[lowered]] if lowered in STATUS_VALUES else None
            elif key == "type":
                normalised = TYPE_VALUES.get(lowered)
            elif key == "since":
                normalised = _parse_since(value, current)
            else:
                normalised = value
        if normalised is None:
            ignored.append(IgnoredToken(raw, key, "invalid_value", start, end, value=value))
            continue

        parsed_filter = ParsedFilter(key=key, value=value, raw=raw, start=start, end=end)
        if key == "since" and since_filter is not None:
            # A repeated since: keeps the LAST one; the earlier one is reported.
            filters.remove(since_filter)
            ignored.append(IgnoredToken(
                since_filter.raw, "since", "repeated", since_filter.start, since_filter.end,
                value=since_filter.value,
            ))
        elif len(filters) >= MAX_FILTER_TOKENS:
            ignored.append(IgnoredToken(raw, key, "too_many", start, end, value=value))
            continue
        filters.append(parsed_filter)
        if key == "since":
            since_filter, since_value = parsed_filter, normalised
        else:
            values.setdefault(key, []).append(normalised)

    def _distinct(key: str) -> tuple[str, ...]:
        return tuple(dict.fromkeys(values.get(key, ())))

    return ParsedQuery(
        text=" ".join(text_parts),
        filters=tuple(filters),
        ignored=tuple(sorted(ignored, key=lambda token: (token.start, token.end))),
        statuses=frozenset(values.get("status", ())),
        suites=_distinct("suite"),
        branches=_distinct("branch"),
        envs=_distinct("env"),
        owners=_distinct("owner"),
        releases=_distinct("release"),
        types=frozenset(values.get("type", ())),
        since=since_value,
        _keys=frozenset(f.key for f in filters),
    )


def whole_days_since(since: datetime, now: datetime) -> int:
    """``since`` as a whole number of days back from ``now`` (rounded UP), clamped 1..365.

    ``GET /search``'s keyword and semantic paths take ``days``; ``since:24h``
    reads as one day there.
    """
    seconds = (_utc(now) - _utc(since)).total_seconds()
    return max(1, min(SINCE_MAX_DAYS, math.ceil(seconds / 86400)))


def _ordered_keys(keys: Sequence[str]) -> list[str]:
    distinct = list(dict.fromkeys(keys))
    return sorted(distinct, key=lambda k: FILTER_KEYS.index(k) if k in FILTER_KEYS else len(FILTER_KEYS))


def _clip(token: str) -> str:
    return token if len(token) <= MAX_TOKEN_CHARS else token[:MAX_TOKEN_CHARS] + "…"


def type_outside_scope_note(parsed: ParsedQuery, requested: Sequence[str]) -> str:
    """``type:run is outside the Defects scope: nothing was searched.``"""
    raws = [f.raw for f in parsed.filters if f.key == "type"]
    named = raws[0] if len(raws) == 1 else ", ".join(raws[:-1]) + " and " + raws[-1]
    labels = [ENTITY_LABELS.get(t, t) for t in ENTITY_TYPES if t in set(requested)]
    scope = labels[0] if len(labels) == 1 else ", ".join(labels[:-1]) + " and " + labels[-1]
    return TYPE_OUTSIDE_SCOPE.format(types=named, verb="is" if len(raws) == 1 else "are", scope=scope)


def parsed_block(
    parsed: ParsedQuery,
    *,
    unsupported_by: Mapping[str, Sequence[str]],
    searched: Sequence[str],
    release_unresolved: str | None,
    semantic_mode: bool = False,
    extra_notes: Sequence[str] = (),
) -> dict:
    """The ``parsed`` block of ``GET /search`` and ``GET /search/global`` (a ``SearchParsed`` dict).

    ``notes`` are rendered here, in order: the ignored tokens' messages (source
    order), the sentences for the entity types that were NOT searched
    (``core/search_vocab.unsupported_notes``: one kind -> its own sentence;
    several -> ONE sentence per key that kept two or more of them out, e.g.
    ``status: applies only to Tests and Runs, so suites, defects, flaky tests
    and releases were not searched.``; ``semantic_mode`` words the
    test-result sentence for ``GET /search`` in Semantic / Hybrid),
    ``extra_notes`` (the ``type:`` outside-scope sentence), then the release
    sentence. ``unsupported_by`` stays per kind.
    """
    unsupported = {
        entity: _ordered_keys(keys)
        for entity, keys in sorted(
            unsupported_by.items(),
            key=lambda item: ENTITY_TYPES.index(item[0]) if item[0] in ENTITY_TYPES else len(ENTITY_TYPES),
        )
        if keys
    }
    ignored_tokens = [
        {
            "token": _clip(token.token),
            "key": token.key,
            "reason": token.reason,
            "message": ignored_message(token.reason, key=token.key, token=token.token, value=token.value),
            "start": token.start,
            "end": token.end,
        }
        for token in parsed.ignored
    ]
    notes = [entry["message"] for entry in ignored_tokens]
    notes += unsupported_notes(unsupported, semantic_mode=semantic_mode)
    notes += [note for note in extra_notes if note]
    if release_unresolved is not None:
        notes.append(RELEASE_UNRESOLVED_TEXT.format(value=_clip(release_unresolved)))
    return {
        "text": parsed.text,
        "filters": [
            {"key": f.key, "value": f.value, "raw": f.raw, "start": f.start, "end": f.end}
            for f in parsed.filters
        ],
        "ignored_tokens": ignored_tokens,
        "unsupported_by": unsupported,
        "searched_entity_types": [entity for entity in ENTITY_TYPES if entity in set(searched)],
        "release_unresolved": release_unresolved,
        "notes": notes,
    }
