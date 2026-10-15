"""The ONE defect-status vocabulary, and the only code that changes a defect's status.

Every reader that asks "is this defect still live?" uses :data:`ACTIVE_STATUSES`
(an ``IN_PROGRESS`` defect is not fixed: it blocks the release gate and counts as
open exactly like an ``OPEN`` one). Every writer that changes the status of an
EXISTING row calls :func:`apply_status`, which keeps ``resolved_at`` honest
(stamped on close, cleared on re-open); a regression guard
(``tests/regression/test_defect_status_writers_go_through_apply_status.py``)
pins that no other module assigns ``resolution_status``. Creation writes the
literal ``"OPEN"`` and needs no helper.

The priority rule (P0-P3) also lives here, once, as SQL and as Python, so the
Defects page's table, its KPI totals and its trend read the same derivation.
It is the page's former client-side rule (``DefectsPage.tsx`` ``classifySeverity``),
byte for byte.

Enum-like values stay plain ``str`` on the wire: a request field that takes one
of these vocabularies is validated here with one fixed refusal text, never by a
Pydantic ``Literal`` that could drift from the column.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:  # pragma: no cover - typing only (avoids an import cycle with models)
    from app.models.postgres import Defect

OPEN: Final = "OPEN"
IN_PROGRESS: Final = "IN_PROGRESS"
RESOLVED: Final = "RESOLVED"
INVALID: Final = "INVALID"

#: What a person may set.
DEFECT_STATUSES: Final = (OPEN, IN_PROGRESS, RESOLVED, INVALID)
#: Still live: blocks gates, counts as open.
ACTIVE_STATUSES: Final = (OPEN, IN_PROGRESS)
CLOSED_STATUSES: Final = (RESOLVED, INVALID)
#: A word older rows (and the visual fixture) carry; it is closed, but nobody may write it any more.
_LEGACY_CLOSED: Final = ("CLOSED",)

STATUS_LABELS: Final = {OPEN: "Open", IN_PROGRESS: "In progress", RESOLVED: "Resolved", INVALID: "Invalid"}
STATUS_INVALID_DETAIL: Final = "resolution_status must be one of OPEN, IN_PROGRESS, RESOLVED, INVALID."

#: ``GET /analytics/defects?resolution_status=ACTIVE`` means ``IN ('OPEN', 'IN_PROGRESS')``.
ACTIVE_QUERY_VALUE: Final = "ACTIVE"

#: A Jira issue key after ``.strip().upper()``; at most 50 characters (the column is ``String(50)``).
JIRA_KEY_RE: Final = re.compile(r"^[A-Z][A-Z0-9_]+-[0-9]+$")
JIRA_KEY_MAX_LEN: Final = 50

PRIORITIES: Final = ("P0", "P1", "P2", "P3")

#: DEF-03 floor: with fewer attributed defects the escape rate is not measured (rate None).
ESCAPE_RATE_MIN_SAMPLE: Final = 5

# The stored severity column's values in the P0-P3 vocabulary.
_PRIORITY_FROM_SEVERITY: Final = {"CRITICAL": "P0", "HIGH": "P1", "MEDIUM": "P2", "LOW": "P3"}
# The page's category regexes, byte for byte (JS ``RegExp.test`` and Postgres ``~``
# are both unanchored searches; both run on the upper-cased category).
_PRODUCT_BUG_RE: Final = "PRODUCT.?BUG"
_INFRA_RE: Final = "INFRA|RUNNER|CI"
_FLAKY_RE: Final = "FLAKY"
_CONFIDENT_PRODUCT_BUG: Final = 80


def _canonical(value: str | None) -> str:
    return (value or "").strip().upper()


def normalize_status(value: str) -> str:
    """``value`` stripped and upper-cased; ``ValueError(STATUS_INVALID_DETAIL)`` outside :data:`DEFECT_STATUSES`.

    The legacy word ``CLOSED`` is refused: no writer may produce it.
    """
    if not isinstance(value, str):
        raise ValueError(STATUS_INVALID_DETAIL)
    status = _canonical(value)
    if status not in DEFECT_STATUSES:
        raise ValueError(STATUS_INVALID_DETAIL)
    return status


def is_active(status: str | None) -> bool:
    """Whether a stored status still counts as live (``OPEN`` / ``IN_PROGRESS``, any case)."""
    return _canonical(status) in ACTIVE_STATUSES


def _is_closed_word(status: str) -> bool:
    return status in CLOSED_STATUSES or status in _LEGACY_CLOSED


def apply_status(defect: "Defect", target: str, *, now: datetime) -> bool:
    """Move ``defect`` to ``target``; return whether anything changed.

    The ONLY code that changes ``resolution_status`` on an existing row:

    * same value: nothing changes, ``False`` (no event, no write-back);
    * live -> closed: the status, and ``resolved_at = now``;
    * closed -> the other closed status (RESOLVED <-> INVALID): the status;
      ``resolved_at`` kept, or ``now`` when it was NULL (a legacy row);
    * anything -> live (a re-open, or OPEN <-> IN_PROGRESS): the status, and
      ``resolved_at = None``;
    * a target outside :data:`DEFECT_STATUSES` (including ``CLOSED``):
      ``ValueError(STATUS_INVALID_DETAIL)``, nothing changes.

    A stored word this module does not know is treated as live (the page shows
    it as Open), so closing it stamps ``now``.
    """
    status = normalize_status(target)
    current = _canonical(defect.resolution_status)
    if current == status:
        return False
    if status in CLOSED_STATUSES:
        if _is_closed_word(current):
            if defect.resolved_at is None:
                defect.resolved_at = now
        else:
            defect.resolved_at = now
    else:
        defect.resolved_at = None
    defect.resolution_status = status
    return True


def priority_of(severity: str | None, failure_category: str | None, ai_confidence_score: int | None) -> str:
    """``P0``..``P3`` for a defect: its stored severity, else a rule on its failure category.

    The Python twin of :func:`priority_sql` (a shared case table pins both).
    """
    stored = _PRIORITY_FROM_SEVERITY.get((severity or "").upper())
    if stored:
        return stored
    category = str(failure_category or "").upper()
    confidence = ai_confidence_score or 0
    if re.search(_PRODUCT_BUG_RE, category):
        return "P0" if confidence >= _CONFIDENT_PRODUCT_BUG else "P1"
    if re.search(_INFRA_RE, category):
        return "P2"
    if re.search(_FLAKY_RE, category):
        return "P3"
    return "P2"


def priority_sql(alias: str = "d") -> str:
    """A SQL ``CASE`` producing ``'P0'``..``'P3'`` for the defects row ``alias`` (the :func:`priority_of` rule).

    Plain text with no bind parameters and no ``SELECT``, so it can sit in a
    select list as ``{priority_sql} AS priority``.
    """
    if not alias.isidentifier():
        raise ValueError(f"not a SQL alias: {alias!r}")
    severity = f"upper({alias}.severity)"
    category = f"upper(CAST({alias}.failure_category AS text))"
    whens = " ".join(f"WHEN {severity} = '{word}' THEN '{p}'" for word, p in _PRIORITY_FROM_SEVERITY.items())
    return (
        f"(CASE {whens} "
        f"WHEN {category} ~ '{_PRODUCT_BUG_RE}' THEN "
        f"(CASE WHEN COALESCE({alias}.ai_confidence_score, 0) >= {_CONFIDENT_PRODUCT_BUG} THEN 'P0' ELSE 'P1' END) "
        f"WHEN {category} ~ '{_INFRA_RE}' THEN 'P2' "
        f"WHEN {category} ~ '{_FLAKY_RE}' THEN 'P3' "
        "ELSE 'P2' END)"
    )


def parse_jira_key(value: str) -> str | None:
    """``value`` stripped and upper-cased when it is a Jira issue key, else ``None``.

    The triage service turns ``None`` into its 422 text; this module stays free of HTTP.
    """
    if not isinstance(value, str):
        return None
    key = value.strip().upper()
    if len(key) > JIRA_KEY_MAX_LEN or not JIRA_KEY_RE.match(key):
        return None
    return key
