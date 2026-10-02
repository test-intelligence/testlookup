"""VIZ-207 / VIZ-208 -- the failure signature, computed IN SQL.

A failure group is "every failing execution whose error message has the same
signature", and the signature is the one the flaky-test signals already use:
:func:`app.services.flaky_signals.error_signature` (the first line of the
message, run-specific noise -- hex addresses, long hex ids, timestamps, bare
numbers -- replaced by ``#``, lower-cased, 80 characters).

**Why SQL and not Python.** The epic grouped on the RAW first line in SQL and
normalised in Python afterwards. Real CI messages carry a count, a request id
or a timestamp on that line, so the raw lines are almost all distinct (the
1M-row seed has 29,938 distinct raw lines for 29,938 failing rows, and 30
signatures): a cap on raw buckets would have seen a sixth of the failures and
ranked by arbitrary ties. Grouping on the signature in the statement makes the
cap bite on signatures, and it lets the rows endpoint select "the executions of
this group" with the SAME expression, so a group and its rows reconcile
exactly. :data:`FAILURE_SIGNATURE_SQL` is that one expression; nothing else may
spell the normalisation again.

**What "the same as Python" means here.** ``tests/test_failure_signature_parity.py``
runs a corpus through both and requires equality. To get there the port does
not use PostgreSQL's locale-dependent classes; it spells Python's own:

* ``\\d`` is Python's Unicode decimal digit (``str.isdecimal``), and the
  ``\\b`` around the long-hex alternative is Python's word boundary
  (``str.isalnum`` or ``_``), both generated from the running interpreter's
  Unicode tables into explicit bracket expressions, with ``\\b`` written as a
  lookbehind plus a lookahead. PostgreSQL's ``\\d`` / ``\\y`` would follow the
  database's ctype (a ``C`` database treats ``é`` as a non-word character).
* ``str.strip()`` is ``btrim`` over every character ``str.isspace`` accepts,
  and ``str.splitlines()[0]`` is "up to the first of the eleven line
  boundaries ``splitlines`` knows" (``\\r`` alone and ``\\x1c`` included).
* The alternatives keep Python's order. PostgreSQL takes the LONGEST match at
  the leftmost position where Python takes the FIRST alternative that matches,
  but no two alternatives can match at one position with different lengths in
  a way that changes the output (hex-prefixed, long-hex, date and number runs
  start differently or nest), and every match becomes the same ``#``; the
  corpus pins it.

Two differences are inherent and documented, not hidden (each has a corpus
case asserting the KNOWN outcome, so a change in either is visible):

1. ``lower()`` follows the database collation. Python's ``str.lower`` applies
   the special casings (``İ`` -> ``i`` + U+0307, a final ``Σ`` -> ``ς``) that
   PostgreSQL's per-character lower-casing does not. ASCII is identical
   everywhere.
2. Bounded reads. The statement reads at most :data:`READ_CAP` characters of a
   message and normalises at most :data:`LINE_CAP` characters of its first
   line, because a pytest assertion can put kilobytes on one line and the
   regex runs once per failing row. Python normalises the whole first line.
   The two agree whenever the first line fits (the overwhelming case: a
   signature is 80 characters), and beyond it they differ only when the
   normalised prefix is shorter than 80 characters or a hex/number/date run
   crosses character :data:`LINE_CAP`.

**The empty signature.** A NULL, empty or whitespace-only message has the
signature ``''`` on both sides (Python returns ``""``), and only such a message
does: a non-blank first line never normalises to nothing. Responses name that
bucket :data:`NO_MESSAGE_ID`, which no real signature can equal (a signature is
lower-cased, so it never holds an ASCII capital).

**Hostile text.** A signature and a first line are raw error text from an
ingested CI file: attacker-influenced and possibly sensitive. This module only
computes them; every reader renders them as text.

Binds: the three patterns are passed as BOUND parameters
(:data:`FAILURE_SIGNATURE_PARAMS`), never spliced into the SQL. They hold
colons and backslashes, which ``text()`` and the escape-string rules would
otherwise reinterpret, and binding keeps every statement that uses them short.
"""
from __future__ import annotations

from typing import Callable

#: Characters of the signature (``flaky_signals._error_signature``'s ``[:80]``).
SIGNATURE_LENGTH = 80
#: Characters of the message read before stripping. Generous on purpose: the
#: strip and the first-line split must see past leading whitespace.
READ_CAP = 1000
#: Characters of the first line handed to the regex (see "Bounded reads").
LINE_CAP = 300
#: Characters of the RAW first line a group label is drawn from.
LABEL_LINE_CAP = 300

#: Response ids for the two roll-ups. Upper case on purpose: a signature is
#: lower-cased, so neither can collide with a real group's id.
NO_MESSAGE_ID = "__NO_MESSAGE__"
SINGLETONS_ID = "__SINGLETONS__"

#: Bind-parameter names. Prefixed so a statement that embeds the expression
#: cannot collide with its own binds.
_P_SPACE = "fsig_space"
_P_FIRST_LINE = "fsig_first_line"
_P_NOISE_TEMPLATE = "fsig_noise_template"
_P_WORD = "fsig_word"
_P_DIGIT = "fsig_digit"
_P_NOISE_ASCII = "fsig_noise_ascii"

#: ``str.splitlines`` boundaries (Python docs, "str.splitlines"): \n \r \v \f
#: \x1c \x1d \x1e \x85 \u2028 \u2029 (``\r\n`` is two of them back to back,
#: which ends the first line at the same place).
LINE_BOUNDARIES = "\n\r\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029"

# Unicode tables end below this: the last alphanumeric is U+323AF (CJK
# extension H); planes 4-16 hold no letters, digits or spaces. The parity test
# re-scans the whole range so a newer interpreter cannot outgrow the bound
# silently.
_SCAN_LIMIT = 0x40000


def _ranges(predicate: Callable[[str], bool]) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    start: int | None = None
    for code in range(_SCAN_LIMIT):
        hit = predicate(chr(code))
        if hit and start is None:
            start = code
        elif not hit and start is not None:
            out.append((start, code - 1))
            start = None
    if start is not None:
        out.append((start, _SCAN_LIMIT - 1))
    return out


def _escape(code: int) -> str:
    # ARE character-entry escapes: exactly 4 or exactly 8 hex digits. Every
    # character is escaped, so no bracket metacharacter (] ^ - \) can occur raw.
    return f"\\u{code:04X}" if code <= 0xFFFF else f"\\U{code:08X}"


def _bracket_body(ranges: list[tuple[int, int]]) -> str:
    return "".join(
        _escape(lo) if lo == hi else f"{_escape(lo)}-{_escape(hi)}" for lo, hi in ranges
    )


def _bracket_body_raw(ranges: list[tuple[int, int]]) -> str:
    """The same class spelled with the characters themselves: half the bytes
    of the escapes. Only for classes that hold no bracket metacharacter and no
    template mark -- true of word characters and decimal digits (asserted)."""
    out: list[str] = []
    for lo, hi in ranges:
        if lo == hi:
            out.append(chr(lo))
        elif hi == lo + 1:
            out.append(chr(lo) + chr(hi))
        else:
            out.append(f"{chr(lo)}-{chr(hi)}")
    body = "".join(out)
    if set(body) & set("]^\\[@"):
        raise ValueError("a raw class must not hold ] ^ \\ [ or @")
    return body


DIGIT_RANGES = _ranges(str.isdecimal)
WORD_RANGES = _ranges(lambda ch: ch.isalnum() or ch == "_")
SPACE_CHARS = "".join(chr(lo + i) for lo, hi in _ranges(str.isspace) for i in range(hi - lo + 1))

_DIGIT = _bracket_body_raw(DIGIT_RANGES)
_WORD = _bracket_body_raw(WORD_RANGES)
_HEX = "0-9a-fA-F"


def _noise_pattern(word: str, digit: str) -> str:
    r"""``flaky_signals._NOISE`` in PostgreSQL ARE syntax, alternative for alternative:
    ``0x[0-9a-fA-F]+`` | ``\b[0-9a-fA-F]{8,}\b`` | ``\d{4}-\d{2}-\d{2}[T \d:.,+]*`` | ``\d+``.
    ``\b`` is spelled as a lookbehind and a lookahead over ``word``, ``\d`` as ``digit``.
    """
    return (
        f"0x[{_HEX}]+"
        f"|(?<![{word}])[{_HEX}]{{8,}}(?![{word}])"
        f"|[{digit}]{{4}}-[{digit}]{{2}}-[{digit}]{{2}}[T {digit}:.,+]*"
        f"|[{digit}]+"
    )


#: The exact port, Unicode classes and all (about 13 kB). It is never sent
#: whole: the statement assembles it from :data:`NOISE_TEMPLATE` and the two
#: classes, each sent ONCE (about 6 kB of binds in all). Sent whole (27 kB with
#: escapes), every statement's bind message passed 16 kB, and on the measured
#: host any message that size costs a flat ~44 ms round trip whatever the query
#: does (a TCP delayed-ACK stall; ``docs/viz-work/w3/BE3.md``).
NOISE_PATTERN = _noise_pattern(_WORD, _DIGIT)
_WORD_MARK, _DIGIT_MARK = "@W@", "@D@"
#: :data:`NOISE_PATTERN` with the classes left as marks.
NOISE_TEMPLATE = _noise_pattern(_WORD_MARK, _DIGIT_MARK)
if NOISE_TEMPLATE.replace(_WORD_MARK, _WORD).replace(_DIGIT_MARK, _DIGIT) != NOISE_PATTERN:
    raise RuntimeError("the noise template does not rebuild the pattern")
#: The same pattern restricted to ASCII, used when the message prefix IS ASCII
#: (so its first line is): there it matches exactly what :data:`NOISE_PATTERN`
#: matches (an ASCII string holds no other digit or word character), at a
#: fraction of the cost. PostgreSQL's regex executor builds its DFA states per
#: call, sized by the character classes of the pattern, so the 700-range Unicode
#: word class costs microseconds a row even on a short line (measured: 30k
#: seed-shaped rows, ~340 ms with the Unicode pattern alone, ~200 ms with the
#: ASCII fast path, `docs/viz-work/w3/BE3.md`).
NOISE_PATTERN_ASCII = _noise_pattern("0-9A-Za-z_", "0-9")
#: Everything before the first line boundary.
FIRST_LINE_PATTERN = "^[^" + _bracket_body([(ord(c), ord(c)) for c in LINE_BOUNDARIES]) + "]*"

#: Merge into the parameters of every statement that uses the SQL below.
FAILURE_SIGNATURE_PARAMS: dict[str, str] = {
    _P_SPACE: SPACE_CHARS,
    _P_FIRST_LINE: FIRST_LINE_PATTERN,
    _P_NOISE_TEMPLATE: NOISE_TEMPLATE,
    _P_WORD: _WORD,
    _P_DIGIT: _DIGIT,
    _P_NOISE_ASCII: NOISE_PATTERN_ASCII,
}


def _prefix_sql(column: str) -> str:
    return f"left(COALESCE({column}, ''), {READ_CAP})"


def first_line_sql(column: str = "tc.error_message", cap: int = LINE_CAP) -> str:
    """SQL for the RAW first line of ``column`` (stripped, <= ``cap`` chars).

    ``column`` is a trusted, hard-coded column reference (never request data).
    ``''`` for NULL / blank messages, exactly as Python's ``error_signature``.
    """
    return (
        f"left(substring(btrim({_prefix_sql(column)}, CAST(:{_P_SPACE} AS text)) "
        f"FROM CAST(:{_P_FIRST_LINE} AS text)), {int(cap)})"
    )


def failure_signature_sql(column: str = "tc.error_message") -> str:
    """SQL for the failure signature of ``column`` (a trusted column reference).

    The pattern is picked by whether the read prefix is ASCII
    (``octet_length = length`` in UTF-8), not the first line itself: the line
    is the expensive part and would be evaluated once per reference, while the
    prefix test is two cheap passes. An ASCII prefix has an ASCII first line;
    a non-ASCII prefix takes the full pattern, which is exact for every line.
    """
    prefix = _prefix_sql(column)
    # Immutable over binds: a custom plan folds it to one constant per statement.
    full = (
        f"replace(replace(CAST(:{_P_NOISE_TEMPLATE} AS text), '{_WORD_MARK}', "
        f"CAST(:{_P_WORD} AS text)), '{_DIGIT_MARK}', CAST(:{_P_DIGIT} AS text))"
    )
    pattern = (
        f"CASE WHEN octet_length({prefix}) = length({prefix}) "
        f"THEN CAST(:{_P_NOISE_ASCII} AS text) ELSE {full} END"
    )
    return (
        f"left(lower(regexp_replace({first_line_sql(column, LINE_CAP)}, {pattern}, '#', 'g')), "
        f"{SIGNATURE_LENGTH})"
    )


#: The one expression: the signature of ``tc.error_message``. Use with
#: ``text(...)`` and ``params(**FAILURE_SIGNATURE_PARAMS)``.
FAILURE_SIGNATURE_SQL = failure_signature_sql("tc.error_message")


def signature_bucket_id(signature: str | None) -> str:
    """The response id of a signature: itself, or :data:`NO_MESSAGE_ID` for ''."""
    return signature if signature else NO_MESSAGE_ID


def signature_from_bucket_id(bucket_id: str) -> str:
    """Inverse of :func:`signature_bucket_id` (a selector value -> the SQL value)."""
    return "" if bucket_id == NO_MESSAGE_ID else bucket_id


__all__ = [
    "FAILURE_SIGNATURE_PARAMS",
    "FAILURE_SIGNATURE_SQL",
    "FIRST_LINE_PATTERN",
    "LABEL_LINE_CAP",
    "LINE_BOUNDARIES",
    "LINE_CAP",
    "NOISE_PATTERN",
    "NOISE_PATTERN_ASCII",
    "NO_MESSAGE_ID",
    "READ_CAP",
    "SIGNATURE_LENGTH",
    "SINGLETONS_ID",
    "failure_signature_sql",
    "first_line_sql",
    "signature_bucket_id",
    "signature_from_bucket_id",
]
