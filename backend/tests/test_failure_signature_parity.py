"""VIZ-207: the SQL failure signature equals ``flaky_signals.error_signature``.

``app.services.failure_signature`` ports the Python normaliser to SQL so a
failure group can be grouped (and its rows selected) in the statement. A port
that drifts is the worst kind of bug here: groups would still look plausible,
just wrong. Two layers:

* **No database** -- the generated character classes are exactly Python's
  (``str.isdecimal``, ``str.isalnum`` / ``_``, ``str.isspace``, the
  ``splitlines`` boundaries) over the whole Unicode range, and the SQL compiles
  to the four declared binds and nothing else (a stray ``:name`` in a pattern
  would become a bind).
* **Real PostgreSQL** (``TESTLOOKUP_POSTGRES_TEST_DSN``) -- a corpus of about
  2,000 messages (the seed's 30 templates, pytest / JUnit / Playwright / TRX /
  .NET samples, timestamps, UUIDs, hex, CRLF, empty, whitespace-only, Unicode
  digits, spaces and line separators, non-ASCII word boundaries, hostile
  markup, 10 kB lines, and a seeded random fuzz over the characters the regex
  cares about) goes through the SQL and through Python. Three assertions:

  1. EVERY message: SQL == the Python steps with the two documented caps
     applied and PostgreSQL's ``lower()``. This isolates the only two
     differences the module documents; everything else (strip, first line,
     every alternative of the regex, the boundary rule) must be exact.
  2. Every message within the caps whose first line lower-cases identically in
     both: SQL == ``error_signature`` exactly. At least 1,800 messages qualify,
     so the comparison cannot pass by excluding the corpus.
  3. The documented differences ARE differences (a capped 10 kB line,
     ``İ``): if one ever disappears, the docs are wrong.
"""
from __future__ import annotations

import os
import random
import re
import unicodedata
import uuid

import pytest
from sqlalchemy import text

from app.services import failure_signature as fs
from app.services.flaky_signals import _NOISE, error_signature

# ---------------------------------------------------------------------------
# Corpus
# ---------------------------------------------------------------------------

_HOSTILE = (
    '<img src=x onerror="window.__xss=1"> failed 3 times',
    "<script>alert(1)</script>",
    "constructor",
    "__proto__",
    "'; DROP TABLE test_cases; -- 42",
    ":fsig_noise :name %s %(x)s $1 \\x00 \\u2028",
    "Robert'); DROP TABLE Students;--",
    "{{7*7}} ${7*7} #{7*7}",
    "\u202eevil.exe\u202c deadbeef01",
    "\ufeffBOM-prefixed AssertionError 12",
)

_FRAMEWORK = (
    "AssertionError: assert 200 == 503\n +  where 503 = <Response [503]>.status_code",
    "E       AssertionError: assert {'a': 1, 'b': 2} == {'a': 1, 'b': 3}",
    "expected:<42> but was:<43>",
    "org.opentest4j.AssertionFailedError: expected: <true> but was: <false>",
    "java.lang.NullPointerException: Cannot invoke \"String.length()\" because \"s\" is null",
    "java.net.SocketTimeoutException: Read timed out after 30000 ms",
    "TimeoutError: page.click: Timeout 30000ms exceeded.\n=========================== logs ===",
    "Error: expect(received).toBe(expected) // Object.is equality\n\nExpected: 3\nReceived: 4",
    "Error: locator.click: Target closed\nCall log:\n  - waiting for locator('#submit')",
    "Assert.AreEqual failed. Expected:<1>. Actual:<2>.",
    "System.InvalidOperationException: Sequence contains no elements\r\n   at System.Linq.Enumerable.First",
    "Test method Foo.Bar threw exception: System.Exception: boom 0x80004005",
    "NUnit.Framework.AssertionException:   Expected: 5\n  But was:  6\n",
    "requests.exceptions.ConnectionError: HTTPConnectionPool(host='10.0.0.12', port=8080): Max retries",
    "psycopg2.errors.DeadlockDetected: deadlock detected\nDETAIL:  Process 12345 waits for ShareLock",
    "Element <button id=\"x-91\"> is not clickable at point (412, 96)",
    "FAILED tests/api/test_pay.py::test_refund[case-17] - KeyError: 'amount'",
    "Cucumber step failed: Then the cart has 3 items (features/cart.feature:27)",
    "Robot: Element 'id=login' did not appear in 5 seconds.",
    "Error: ENOENT: no such file or directory, open '/tmp/a1b2c3d4e5f6/out.json'",
)

_EDGES = (
    "",
    " ",
    "\t\n\r ",
    "\u00a0\u3000\u2028",
    "\n\nsecond line is first",
    "first\r\nsecond",
    "first\rsecond",
    "first\x0bsecond",
    "first\x0csecond",
    "first\x1csecond",
    "first\x1dsecond",
    "first\x1esecond",
    "first\x1fsecond",  # \x1f is whitespace, NOT a line boundary
    "first\x85second",
    "first\u2028second",
    "first\u2029second",
    "trailing spaces   ",
    "trailing spaces then lines   \nnext",
    "trailing spaces then blank lines   \n \t \n",
    "  leading and trailing\u3000",
    "\u2003em-space lead 12",
    "0x",
    "0x1F",
    "0X1F upper X is not hex-prefixed",
    "0xdeadbeefcafebabe",
    "deadbee",  # 7 hex: not an id
    "deadbeef",  # 8 hex: an id
    "deadbeefg",  # word char after: not bounded
    "_deadbeef",  # underscore is a word character
    "deadbeef_",
    "x-deadbeef-y",
    "ABCDEF01",
    "abcdef0123456789",
    "id=7f3a9c2e-4b1d-4e8a-9f6b-1c2d3e4f5a6b done",
    "2026-09-12",
    "2026-09-12T10:20:30",
    "2026-09-12T10:20:30.123+00:00 then text",
    "2026-09-12 10:20:30,123 INFO",
    "2026-9-12 not a date",
    "12026-09-12T01 five-digit year",
    "at 2026-09-12T10:20:30Z (Z is not in the class)",
    "v1.2.3-rc4",
    "-12 +12 1e10 3.14159",
    "a1b2c3d4e5f6a7b8",
    "1234567",
    "12345678",
    "123456789abcdefABCDEF",
    "caf\u00e9 deadbeef1",
    "caf\u00e9deadbeef1",  # \u00e9 is a word character: no boundary before d
    "deadbeef1\u00e9",  # ... nor after 1
    "\u65e5\u672cdeadbeef1",  # CJK is a word character too
    "\u0663\u0664\u0665 Arabic-Indic digits",
    "\uff11\uff12\uff13 fullwidth digits",
    "\U0001d7ce\U0001d7cf mathematical digits",
    "\u0661\u0662\u0663\u0664-\u0660\u0661-\u0660\u0662 Arabic-Indic date",
    "count \u00b2 superscript two is not a decimal",
    "\u2460 circled one is not a decimal",
    "emoji \U0001f600 12 \U0001f4a5",
    "TAB\tinside 12",
    "x" * 80,
    "x" * 81,
    "y" * 299 + "1",
    "z" * 300 + "1",
)


def _seed_messages(rng: random.Random) -> list[str]:
    from datetime import datetime, timezone

    from scripts.seed_large_dataset import SIGNATURE_TEMPLATES, render_signature

    at = datetime(2026, 9, 12, 10, 20, 30, tzinfo=timezone.utc)
    out = []
    for index in range(len(SIGNATURE_TEMPLATES)):
        for _ in range(5):
            out.append(render_signature(index, rng, at)[0])
    return out


def _structured(rng: random.Random) -> list[str]:
    out = []
    for _ in range(150):
        out.append(f"request {uuid.UUID(int=rng.getrandbits(128))} failed with {rng.randint(400, 599)}")
        out.append(f"segfault at 0x{rng.getrandbits(48):x} in worker {rng.randint(1, 64)}")
        out.append(
            f"stale at 2026-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}T"
            f"{rng.randint(0, 23):02d}:{rng.randint(0, 59):02d}:{rng.randint(0, 59):02d}.{rng.randint(0, 999)}"
            f"+0{rng.randint(0, 9)}:00 retry"
        )
    return out


def _long_lines(rng: random.Random) -> list[str]:
    # 10 kB lines: a realistic pytest assertion dump, a log line, and the
    # crafted shapes that DO cross the cap (asserted separately).
    return [
        "AssertionError: assert " + repr({f"k{i}": i * 7 for i in range(900)}),
        "E   " + " ".join(f"item{i}=0x{rng.getrandbits(32):08x}" for i in range(900)),
        "Error: " + "lorem ipsum dolor sit amet " * 400,
        "Payload: " + "".join(rng.choice("abcdefghijklmnopqrstuvwxyz ,.:") for _ in range(10_000)),
    ]


# Characters the regex and the line split care about, weighted toward the
# interesting ones. Cased non-ASCII letters are left out ON PURPOSE: their
# lower-casing is the documented collation difference, checked separately.
_FUZZ_ALPHABET = (
    "0123456789" * 3
    + "abcdefABCDEF" * 2
    + "xXgGzT_-:.,+ "
    + "\t\n\r\x0b\x0c\x1c\x1f\x85\u2028\u00a0"
    + "\u0663\uff15\U0001d7d9"  # non-ASCII decimals
    + "\u00e9\u65e5\u00df\u00b2\u2460"  # non-ASCII word characters (lower-case or uncased) + non-decimals
    + "<>\"'&;#%$\\"
)


def _fuzz(rng: random.Random, count: int) -> list[str]:
    out = []
    for _ in range(count):
        length = rng.choice((1, 2, 4, 8, 12, 20, 40, 90, 140))
        out.append("".join(rng.choice(_FUZZ_ALPHABET) for _ in range(length)))
    return out


def corpus() -> list[str]:
    rng = random.Random(20261001)
    messages = (
        _seed_messages(rng)
        + list(_FRAMEWORK)
        + list(_HOSTILE)
        + list(_EDGES)
        + _structured(rng)
        + _long_lines(rng)
        + _fuzz(rng, 1320)
    )
    # PostgreSQL text cannot hold NUL; ingestion never stores one.
    return [m.replace("\x00", "") for m in messages]


# ---------------------------------------------------------------------------
# The Python reference with the module's documented caps (and no lower-casing:
# PostgreSQL lower-cases in the statement, so collation is isolated).
# ---------------------------------------------------------------------------


def _capped_denoised(message: str | None) -> str:
    if not message:
        return ""
    stripped = message[: fs.READ_CAP].strip()
    if not stripped:
        return ""
    first = stripped.splitlines()[0][: fs.LINE_CAP]
    return _NOISE.sub("#", first)


def _within_caps(message: str) -> bool:
    """True when neither cap can change the result: the statement reads the
    whole message and normalises the whole first line."""
    stripped = message.strip()
    return len(message) <= fs.READ_CAP and (not stripped or len(stripped.splitlines()[0]) <= fs.LINE_CAP)


# ---------------------------------------------------------------------------
# No database
# ---------------------------------------------------------------------------


def _expand(ranges) -> set[int]:
    return {code for lo, hi in ranges for code in range(lo, hi + 1)}


def test_digit_class_is_python_decimal_over_all_of_unicode():
    expected = {c for c in range(0x110000) if chr(c).isdecimal()}
    assert _expand(fs.DIGIT_RANGES) == expected
    # and that is what ``\d`` means to the reference regex
    assert all(re.fullmatch(r"\d", chr(c)) for c in sorted(expected)[::7])


def test_word_class_is_python_word_over_all_of_unicode():
    expected = {c for c in range(0x110000) if chr(c).isalnum() or chr(c) == "_"}
    assert _expand(fs.WORD_RANGES) == expected
    assert all(re.fullmatch(r"\w", chr(c)) for c in sorted(expected)[::97])


def test_space_set_is_python_isspace_over_all_of_unicode():
    assert set(fs.SPACE_CHARS) == {chr(c) for c in range(0x110000) if chr(c).isspace()}


def test_line_boundaries_are_exactly_splitlines():
    expected = {chr(c) for c in range(0x110000) if len(f"a{chr(c)}b".splitlines()) == 2}
    assert set(fs.LINE_BOUNDARIES) == expected


def test_unicode_tables_fit_the_scan_limit():
    # The classes are scanned up to _SCAN_LIMIT for speed at import; a newer
    # interpreter with a letter beyond it must fail here, not drift silently.
    beyond = [
        c
        for c in range(fs._SCAN_LIMIT, 0x110000)
        if chr(c).isalnum() or chr(c).isspace() or chr(c).isdecimal()
    ]
    assert beyond == [], unicodedata.unidata_version


def test_the_sql_declares_exactly_its_binds():
    # A ``:word`` inside a pattern would become a bind; the patterns are binds
    # themselves, so the statement text must hold only these names.
    compiled = text(f"SELECT {fs.FAILURE_SIGNATURE_SQL}").compile()
    assert set(compiled.params) == set(fs.FAILURE_SIGNATURE_PARAMS)
    assert set(fs.FAILURE_SIGNATURE_PARAMS) == {
        "fsig_space",
        "fsig_first_line",
        "fsig_noise_template",
        "fsig_word",
        "fsig_digit",
        "fsig_noise_ascii",
    }
    assert "tc.error_message" in fs.FAILURE_SIGNATURE_SQL


def test_the_binds_stay_small_and_rebuild_the_pattern():
    # Under 12 kB in all: a bind message past ~16 kB cost a flat 44 ms per
    # statement on the measured host (BE3.md); the whole pattern is ~13 kB.
    assert sum(len(v.encode()) for v in fs.FAILURE_SIGNATURE_PARAMS.values()) < 12_000
    p = fs.FAILURE_SIGNATURE_PARAMS
    rebuilt = p["fsig_noise_template"].replace("@W@", p["fsig_word"]).replace("@D@", p["fsig_digit"])
    assert rebuilt == fs.NOISE_PATTERN


def test_column_is_substituted_and_nothing_else():
    sql = fs.failure_signature_sql("f.msg")
    assert "tc.error_message" not in sql
    assert sql == fs.FAILURE_SIGNATURE_SQL.replace("tc.error_message", "f.msg")


def test_sentinel_ids_cannot_be_signatures():
    # A signature is lower-cased; the sentinels hold capitals, so a message that
    # literally reads "__no_message__" never lands in the no-message bucket.
    for sentinel in (fs.NO_MESSAGE_ID, fs.SINGLETONS_ID):
        assert sentinel != sentinel.lower()
        assert error_signature(sentinel) != sentinel
    assert fs.signature_bucket_id("") == fs.NO_MESSAGE_ID
    assert fs.signature_bucket_id(None) == fs.NO_MESSAGE_ID
    assert fs.signature_bucket_id("__no_message__") == "__no_message__"
    assert fs.signature_from_bucket_id(fs.NO_MESSAGE_ID) == ""
    assert fs.signature_from_bucket_id("abc #") == "abc #"


def test_corpus_size_and_reference_caps():
    messages = corpus()
    assert len(messages) >= 2000
    # the oracle: inside the caps the capped reference IS error_signature
    inside = [m for m in messages if _within_caps(m)]
    assert len(inside) >= 1900
    for m in inside:
        assert _capped_denoised(m).lower()[: fs.SIGNATURE_LENGTH] == error_signature(m)


# ---------------------------------------------------------------------------
# Real PostgreSQL
# ---------------------------------------------------------------------------


def _dsn() -> str:
    value = os.environ.get("TESTLOOKUP_POSTGRES_TEST_DSN", "").strip()
    if not value:
        pytest.skip("TESTLOOKUP_POSTGRES_TEST_DSN is not configured")
    return value


async def _run_sql(messages: list[str | None], references: list[str]) -> list[tuple[str, str]]:
    """(SQL signature, PostgreSQL ``lower`` of the capped Python reference) per message."""
    pytest.importorskip("asyncpg")
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(_dsn(), pool_size=1, max_overflow=0)
    statement = text(
        f"SELECT {fs.failure_signature_sql('m.msg')} AS sig, "
        f"left(lower(r.ref), {fs.SIGNATURE_LENGTH}) AS ref "
        "FROM unnest(CAST(:msgs AS text[])) WITH ORDINALITY AS m(msg, i) "
        "JOIN unnest(CAST(:refs AS text[])) WITH ORDINALITY AS r(ref, i) ON r.i = m.i "
        "ORDER BY m.i"
    )
    try:
        async with engine.connect() as conn:
            rows = (
                await conn.execute(statement, {"msgs": messages, "refs": references, **fs.FAILURE_SIGNATURE_PARAMS})
            ).all()
    finally:
        await engine.dispose()
    return [(row.sig, row.ref) for row in rows]


@pytest.mark.integration
async def test_sql_signature_matches_python_on_the_corpus():
    messages: list[str | None] = [None, *corpus()]
    references = [_capped_denoised(m) for m in messages]
    results = await _run_sql(messages, references)
    assert len(results) == len(messages)

    # 1. every message: only the caps and lower() may differ from Python
    drift = [(m, sig, ref) for m, (sig, ref) in zip(messages, results) if sig != ref]
    assert drift == [], drift[:5]

    # 2. inside the caps, with an identical lower-casing: exactly error_signature
    exact = 0
    mismatches = []
    for message, (sig, ref) in zip(messages, results):
        if message is not None and not _within_caps(message):
            continue
        python_lower = _capped_denoised(message).lower()[: fs.SIGNATURE_LENGTH]
        if python_lower != ref:  # PostgreSQL's lower() differs (collation): documented
            continue
        exact += 1
        if sig != error_signature(message):
            mismatches.append((message, sig, error_signature(message)))
    assert mismatches == [], mismatches[:5]
    assert exact >= 1800


@pytest.mark.integration
async def test_realistic_long_lines_match_python_exactly():
    # The cap does not change a realistic 10 kB line: its first 80 output
    # characters come from far fewer than LINE_CAP input characters.
    rng = random.Random(7)
    lines = _long_lines(rng)
    results = await _run_sql(lines, [_capped_denoised(m) for m in lines])
    for message, (sig, _ref) in zip(lines, results):
        assert len(message) > 9_000
        assert sig == error_signature(message)


@pytest.mark.integration
async def test_documented_differences_are_real():
    capped = "1" * (fs.LINE_CAP + 50) + " tail"  # one run straddling the cap
    dotted = "\u0130STANBUL failed 3 times"  # \u0130 lower-cases to i + U+0307 in Python
    results = await _run_sql([capped, dotted], [_capped_denoised(capped), _capped_denoised(dotted)])
    (capped_sig, _), (dotted_sig, _) = results
    assert error_signature(capped) == "# tail"
    assert capped_sig == "#"
    assert dotted_sig != error_signature(dotted)
    assert dotted_sig.endswith("stanbul failed # times")
