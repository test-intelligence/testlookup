"""A total order over release version strings.

Why not just sort by name
-------------------------
Release comparison ("2.4.0 versus its predecessor") needs an ordering, and the
obvious candidates all fail on real data:

* ``ORDER BY name`` puts ``2.10.0`` before ``2.9.0`` — lexicographic order is
  not numeric order.
* ``ORDER BY created_at`` is wrong for any team that pre-creates releases, and
  for a hotfix branched from an older line.
* A zero-padded numeric encoding alone still gets pre-releases backwards:
  ``2.4.0-rc1`` sorts *after* ``2.4.0`` because the extra suffix makes the
  string longer, when an RC must come *before* its own GA.

So this module produces a single sortable string with three deliberate parts.

The encoding
------------
``<kind>|<numeric segments>|<pre-release>``

**kind** — one character, and the reason the whole scheme is a total order.
``1`` for anything parsed as a version, ``9`` for everything else. Without it,
``Unreleased`` and ``2.4.0`` interleave unpredictably: a project holding both
would order them by whatever their first characters happen to be. Keeping the
two populations in separate bands means an unparseable name always sorts after
every real version, which is where a placeholder belongs.

**numeric segments** — four groups zero-padded to ``SEGMENT_WIDTH`` digits, so
``2.10.0`` (``000002.000010.000000.000000``) correctly follows ``2.9.0``
(``000002.000009.000000.000000``). Six digits covers any plausible version
component; four segments covers the ``1.2.3.4`` builds some teams ship. A
version with fewer segments is padded with zeros, which is what makes ``2.4``
and ``2.4.0`` compare equal — they are the same release named two ways.

The width is stated as ``SEGMENT_WIDTH`` rather than spelled out, because this
paragraph said "five digits" and gave five-digit examples while the constant
had been 6 — so the documentation and the code disagreed about the one thing
this module exists to define. The full key is
``<band>|<segments>|<suffix>``; ``compute_sort_key("2.9.0", ...)`` returns
``1|000002.000009.000000.000000|~``.

**pre-release** — the subtle one. ``~`` (0x7E) sorts after every alphanumeric
ASCII character, so encoding "has no pre-release" as ``~`` makes GA outrank its
own release candidates: ``2.4.0-rc1`` → ``…|rc1`` sorts before ``2.4.0`` →
``…|~``. This is the same trick Debian's version comparison uses, for the same
reason. Encoding it as an empty string would invert the order.

Build metadata (``+sha``) is discarded: semver says it does not participate in
precedence, and two builds of the same version are the same release.

The result is stored on ``releases.sort_key`` and computed on write, so ordering
is an index scan rather than a Python sort over every release in a project.
"""
from __future__ import annotations

import re
from typing import Optional

#: Width each numeric segment is padded to. The width must be FIXED — the whole
#: scheme is a plain string comparison, so a segment that overflows its pad is
#: longer than its siblings and sorts by its first character instead of its
#: value. Six digits covers any plausible version component; anything larger is
#: clamped (see SEGMENT_MAX) rather than allowed to widen the field.
SEGMENT_WIDTH = 6

#: How many numeric segments are encoded. Four covers ``1.2.3.4``-style builds;
#: shorter versions are zero-padded, so ``2.4`` == ``2.4.0`` == ``2.4.0.0``.
SEGMENT_COUNT = 4

#: Band prefix for a parsed version. Lower than the fallback band, so every
#: real version sorts before every unparseable name.
KIND_VERSION = "1"

#: Band prefix for a name no version could be read out of.
KIND_TEXT = "9"

#: Stands in for "no pre-release". Must sort AFTER every character that can
#: appear in a pre-release tag, or a GA release would sort before its own RCs.
NO_PRERELEASE = "~"

#: Largest value a segment can represent. A version numbered beyond this is not
#: a real version, and clamping keeps the encoding a total order: every
#: out-of-range segment collapses to the same key rather than producing a
#: longer string that sorts BELOW smaller versions. Documented rather than
#: silently truncated — ``1.0.99999999`` and ``1.0.1000000`` compare equal.
SEGMENT_MAX = 10 ** SEGMENT_WIDTH - 1

#: Longest pre-release tag encoded. ``releases.sort_key`` is String(64) and the
#: band prefix plus four padded segments already spend 26 characters, so this
#: keeps the worst case inside the column. Chosen with headroom rather than to
#: the byte, so widening a segment later does not silently start truncating.
PRERELEASE_MAX = 30

#: Leading ``v``/``V`` and surrounding whitespace are noise, not version data.
_VERSION_RE = re.compile(
    r"""
    ^\s*[vV]?
    (?P<nums>\d+(?:\.\d+)*)
    (?:-(?P<pre>[0-9A-Za-z.\-]+))?
    (?:\+[0-9A-Za-z.\-]+)?        # build metadata: parsed, then discarded
    \s*$
    """,
    re.VERBOSE,
)


def _text_band(raw: str) -> str:
    """Encode an unparseable name so it still orders deterministically.

    Lower-cased so ``Unreleased`` and ``unreleased`` do not straddle each
    other, and truncated so one pathological name cannot bloat the column.
    """
    return f"{KIND_TEXT}|{raw.strip().lower()[:48]}"


def compute_sort_key(version: Optional[str], name: Optional[str] = None) -> str:
    """Return the sortable encoding for a release.

    ``version`` is tried first and ``name`` second, because ``Release.version``
    is NULL on every release the ingest linker auto-creates — those carry the
    version (if any) in ``name``. A caller that passes neither gets the text
    band for the empty string, which sorts last and is stable.
    """
    for candidate in (version, name):
        if not candidate or not candidate.strip():
            continue
        match = _VERSION_RE.match(candidate)
        if match is None:
            continue

        segments = [int(part) for part in match.group("nums").split(".")]
        # Truncate rather than reject: a five-segment version is exotic, and
        # ordering it by its first four is better than dropping it to the text
        # band where it would sort after every real version.
        segments = (segments + [0] * SEGMENT_COUNT)[:SEGMENT_COUNT]
        numeric = ".".join(
            str(min(s, SEGMENT_MAX)).zfill(SEGMENT_WIDTH) for s in segments
        )

        pre = match.group("pre")
        # Truncated to fit ``releases.sort_key`` (String(64)). The prefix and
        # numeric segments account for 26 characters, so the cap leaves
        # headroom. Two pre-release tags identical in their first
        # PRERELEASE_MAX characters therefore sort equal — acceptable, because
        # a tag that long is already pathological and sorting two exotic RCs
        # together is far better than raising on write, which is what an
        # unbounded value does the first time someone uses one.
        prerelease = pre.lower()[:PRERELEASE_MAX] if pre else NO_PRERELEASE
        return f"{KIND_VERSION}|{numeric}|{prerelease}"

    return _text_band(name or version or "")
