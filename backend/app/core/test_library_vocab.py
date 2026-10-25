"""Test-library vocabularies and sentences (W13: TCL-07, TCL-01, TCL-03, TCL-04, TCL-05, TCL-06).

One place for every word and sentence the test-library routes, services,
schemas and tests share, so a refusal the API sends and the sentence a test
asserts cannot drift apart. Pure: imports nothing from ``app`` (``schemas.py``,
the services, the routers and the tests import it).

Enum-like request / response fields are plain ``str`` everywhere; each
vocabulary below is the ONE definition of its values. Where a vocabulary
already lives elsewhere it is MIRRORED here and ``tests/test_test_library_vocab.py``
pins the two equal: ``LIFECYCLE_STATES`` (``TestCaseLifecycleState``),
``CASE_PRIORITIES`` (``test_case_editing_service.PRIORITIES``),
``RETIRED_STATES`` / ``RUNNABLE_STATES`` (``test_case_lifecycle_service``),
``FLAKY_SCORE_FLOOR`` / ``FLAKY_WEAK_CONFIDENCES``
(``failure_attribution_service``: one "flaky" bar in the product),
``FORMULA_PREFIXES`` (``test_management_exports._excel_text``) and
``IMPORT_MAX_CELL_CHARS`` (``schemas.MAX_LONG_TEXT``).

Nothing here logs; no function takes or returns anything but the text it is
given to render.
"""
from __future__ import annotations

import re
from typing import Any

# ── Mirrored vocabularies (pinned equal to their sources) ────────────────────

#: = ``TestCaseLifecycleState`` values, in the enum's order.
LIFECYCLE_STATES: tuple[str, ...] = (
    "draft", "review_requested", "under_review", "approved", "active", "rejected", "needs_update",
    "deprecated", "archived",
)
#: = ``test_case_editing_service.PRIORITIES``.
CASE_PRIORITIES: tuple[str, ...] = ("critical", "high", "medium", "low")
#: = ``test_case_lifecycle_service.RETIRED_STATES`` / ``RUNNABLE_STATES``.
RETIRED_STATES: tuple[str, ...] = ("deprecated", "archived")
RUNNABLE_STATES: tuple[str, ...] = ("approved", "active")
#: The review states (the review queue): requested or claimed.
REVIEW_STATES: tuple[str, ...] = ("review_requested", "under_review")
#: = ``failure_attribution_service.FLAKY_SCORE_FLOOR`` / ``WEAK_CONFIDENCES``.
FLAKY_SCORE_FLOOR = 0.5
FLAKY_WEAK_CONFIDENCES: frozenset[str] = frozenset({"none", "low"})

# ── TCL-01: the case list ────────────────────────────────────────────────────

OWNER_ME = "me"
#: ``is_automated IS TRUE`` / ``coalesce(is_automated, false) = false``.
CASE_AUTOMATION_FILTERS: tuple[str, ...] = ("automated", "not_automated")
CASE_FILTER_MAX_VALUES = 20
TEST_CASE_VIEW_KIND = "test_case_view"
TEST_CASE_VIEW_PAGE = "test_management"
CASE_VIEW_KEYS: frozenset[str] = frozenset({
    "kind", "statuses", "priorities", "types", "owner", "automation", "suite", "q", "ai_generated",
    "requirement",
})
#: A saved case view's string bounds (characters).
CASE_VIEW_TYPE_MAX = 50
CASE_VIEW_SUITE_MAX = 500
CASE_VIEW_REQUIREMENT_MAX = 200
CASE_VIEW_Q_MAX = 200
#: The Views chips; the frontend mirrors them in ``caseFilterState.ts``
#: (``tests/test_case_presets_frontend_parity.py`` pins both).
CASE_PRESETS: dict[str, dict[str, Any]] = {
    "my_drafts": {"owner": "me", "statuses": ["draft"]},
    "my_p0_p1": {"owner": "me", "priorities": ["critical", "high"]},
    "unautomated": {"automation": "not_automated"},
    "ai_drafts": {"statuses": ["draft"], "ai_generated": True},
}
CASE_PRESET_LABELS: dict[str, str] = {
    "my_drafts": "My drafts", "my_p0_p1": "My P0/P1", "unautomated": "Unautomated", "ai_drafts": "AI drafts",
}
OWNER_INVALID_SENTENCE = "owner must be me or a user id."
OWNER_NEEDS_PROJECT_SENTENCE = "Choose a project to filter by another person."
OWNER_NOT_MEMBER_SENTENCE = "The owner filter names someone who is not an active member of this project."
AUTOMATION_INVALID_SENTENCE = "automation must be automated or not_automated."
#: The case list / export QUERY filters (W13 G-B, after the local demo): a
#: priority is one of ``CASE_PRIORITIES`` or a ``PRIORITY_ALIASES`` key (any
#: case); a status one of ``LIFECYCLE_STATES``; a type free text up to the
#: column's 50 characters (cases carry types beyond the chip's nine).
PRIORITY_INVALID_SENTENCE = "priority must be critical, high, medium or low (or P0 to P3)."
STATUS_INVALID_SENTENCE = (
    "status must be draft, review_requested, under_review, approved, active, rejected, needs_update, "
    "deprecated or archived."
)
TYPE_INVALID_SENTENCE = "test_type values are 1 to 50 characters."
TOO_MANY_VALUES_SENTENCE = "Choose at most 20 values per filter."
CASE_VIEW_KEYS_SENTENCE = (
    "A saved case view holds only statuses, priorities, types, owner, automation, suite, q, ai_generated "
    "and requirement."
)
CASE_VIEW_VALUE_SENTENCE = "The saved view's {key} is not valid."
CASE_VIEW_PAGE_SENTENCE = "A saved case view belongs to the Test Management page."
CASE_VIEW_SHARE_NEEDS_PROJECT = "Pick a project to share this view with its members."
AUTOMATION_ROWS_EXCLUDED_TEXT = "Authored cases only: automation-ingested tests are left out under these filters."

# ── TCL-03: library health ───────────────────────────────────────────────────

HEALTH_COMPONENTS: tuple[str, ...] = ("automation", "stale_drafts", "review_freshness", "retired_in_open_plans")
HEALTH_COMPONENT_LABELS: dict[str, str] = {
    "automation": "Automated", "stale_drafts": "Stale drafts", "review_freshness": "Review freshness",
    "retired_in_open_plans": "Retired cases in open plans",
}
#: Today's weights (the page's old synthesis, TestManagementPage.tsx:1312-1324).
HEALTH_WEIGHTS: dict[str, float] = {
    "automation": 0.35, "stale_drafts": 0.20, "review_freshness": 0.20, "retired_in_open_plans": 0.25,
}
#: score >= 85 / >= 70 / else (today's thresholds).
HEALTH_TAGS: tuple[str, ...] = ("Healthy", "Needs attention", "At risk")
HEALTH_TAG_THRESHOLDS: tuple[int, ...] = (85, 70)
STALE_DRAFT_DAYS = 30
#: The "Aging reviews over SLA" gap: the oldest review waits >= 7 days.
REVIEW_SLA_DAYS = 7
#: review score = max(0, 100 - oldest / 14 * 100).
REVIEW_FRESHNESS_DAYS = 14
OLD_CASE_DAYS = 180
AUTOMATION_TARGET_DEFAULT = 60
OPEN_PLAN_EXCLUDED_STATUSES: tuple[str, ...] = ("completed", "archived")
HEALTH_LIST_PREVIEW = 5
SUITE_NAMES_CAP = 500
EVIDENCE_GAP_KINDS: tuple[str, ...] = ("never_executed", "automation_vanished", "flaky_active", "no_owner")
EVIDENCE_GAP_KIND_PATTERN = "^(never_executed|automation_vanished|flaky_active|no_owner)$"
#: Listed in this order, each only when its count > 0.
STRATEGY_GAP_KINDS: tuple[str, ...] = (
    "retired_in_open_plans", "aging_reviews", "automation_below_target", "stale_drafts", "flaky_active", "no_owner",
)
STRATEGY_GAP_TITLES: dict[str, str] = {
    "retired_in_open_plans": "Deprecated cases in active plans", "aging_reviews": "Aging reviews over SLA",
    "automation_below_target": "Automation below target", "stale_drafts": "Stale drafts ≥ 30d",
    "flaky_active": "Flaky and active", "no_owner": "No owner",
}
HEALTH_NEEDS_PROJECT_SENTENCE = "Choose a project: library health is per project."
FLAKY_UNMEASURED_NO_SCORES = "No flaky scores have been computed for this project yet."
FLAKY_UNMEASURED_NO_LINKS = "No approved or active case is linked to an automated test yet."
LIBRARY_EMPTY_SENTENCE = "No authored test cases yet."

# ── TCL-04: import ───────────────────────────────────────────────────────────

IMPORT_FORMATS: tuple[str, ...] = ("csv", "xlsx")
IMPORT_MAX_BYTES = 5 * 1024 * 1024
IMPORT_MAX_ROWS = 2000
#: The sum of the archive members' sizes, checked before parsing.
IMPORT_MAX_XLSX_UNCOMPRESSED = 50 * 1024 * 1024
#: = ``schemas.MAX_LONG_TEXT``.
IMPORT_MAX_CELL_CHARS = 50_000
IMPORT_OUTCOMES: tuple[str, ...] = ("create", "duplicate", "invalid", "created")
#: header (case-insensitive, trimmed, spaces collapsed) -> ManagedTestCaseCreate field.
IMPORT_HEADERS: dict[str, str] = {
    "title": "title", "type": "test_type", "priority": "priority", "severity": "severity",
    "feature area": "feature_area", "objective": "objective", "preconditions": "preconditions", "steps": "steps",
    "expected result": "expected_result", "automated": "is_automated", "automation status": "automation_status",
    "tags": "tags", "suite": "suite_name", "description": "description", "test data": "test_data",
}
IMPORT_IGNORED_HEADERS: tuple[str, ...] = (
    "status", "ai generated", "ai quality score", "last execution status", "version", "created at", "id",
)
#: Columns 19-21 of the Excel export (W13 §0.13: appended, never prepended).
EXPORT_APPENDED_HEADERS: tuple[str, ...] = ("ID", "Suite", "Description")
PRIORITY_ALIASES: dict[str, str] = {"p0": "critical", "p1": "high", "p2": "medium", "p3": "low"}
CASE_SEVERITIES: tuple[str, ...] = ("blocker", "critical", "major", "minor", "trivial")
AUTOMATION_STATUSES: tuple[str, ...] = ("not_automated", "in_progress", "automated", "broken")
#: = ``test_management_exports._excel_text``'s prefixes (pinned).
FORMULA_PREFIXES: tuple[str, ...] = ("=", "+", "-", "@", "\t", "\r")
IMPORT_NOT_A_FILE = "Upload a .csv or .xlsx file."
IMPORT_TOO_LARGE = "The file is larger than 5 MB."
IMPORT_TOO_MANY_ROWS = "The file has more than 2,000 rows: split it and import each part."
IMPORT_NO_TITLE_COLUMN = "The first row must name the columns, and one of them must be Title."
IMPORT_UNREADABLE = "The file could not be read as CSV (UTF-8) or as an Excel workbook."
IMPORT_XLSX_TOO_BIG = "The workbook expands to more than 50 MB."
#: Mirrored in ``frontend/src/pages/test-management/importCasesModel.ts`` (G-E),
#: pinned by ``tests/test_import_template_parity.py``.
IMPORT_TEMPLATE_HEADERS: tuple[str, ...] = (
    "Title", "Type", "Priority", "Severity", "Feature Area", "Objective", "Preconditions", "Steps",
    "Expected Result", "Automated", "Automation Status", "Tags", "Suite", "Description", "Test Data",
)
IMPORT_ROW_TITLE = "Title is required (3 to 500 characters)."
IMPORT_ROW_PRIORITY = "Priority must be critical, high, medium or low (or P0 to P3)."
IMPORT_ROW_AUTOMATED = "Automated must be Yes or No."
IMPORT_ROW_AUTOMATION_STATUS = "Automation Status must be not_automated, in_progress, automated or broken."
IMPORT_ROW_TYPE = "Type must be 1 to 50 letters, digits, spaces, '-' or '_'."
IMPORT_ROW_CELL_TOO_LONG = "{column} is longer than 50,000 characters."
IMPORT_ROW_TAGS = "Tags must be at most 20, each 1 to 100 characters."
IMPORT_DUPLICATE_EXISTING = "Same title, objective, steps and expected result as an existing case."
IMPORT_DUPLICATE_ROW = "Same title, objective, steps and expected result as row {row}."
IMPORT_WARN_STATUS = "Status '{value}' is ignored: imported cases start as Draft."
IMPORT_WARN_SEVERITY = "Severity '{value}' is not one of blocker, critical, major, minor, trivial: kept as written."
IMPORT_WARN_QUOTE = "{column}: the leading ' an export adds before a formula character was removed."
IMPORT_SAVE_FAILED = "This row could not be saved: {detail}"

# ── TCL-05: timeline ─────────────────────────────────────────────────────────

#: ALSO the kind tiebreak order (ASC) at an equal ``occurred_at``.
TIMELINE_KINDS: tuple[str, ...] = (
    "version", "review_requested", "review_decided", "comment", "plan_execution", "automated_execution",
    "defect", "requirement",
)
TIMELINE_PRESETS: dict[str, tuple[str, ...]] = {
    "changes": ("version", "requirement"), "reviews": ("review_requested", "review_decided"),
    "comments": ("comment",), "executions": ("plan_execution", "automated_execution"), "defects": ("defect",),
}
TIMELINE_LINK_TYPES: tuple[str, ...] = ("run_result", "run", "plan", "defect")
TIMELINE_LIMIT_DEFAULT = 50
TIMELINE_LIMIT_MAX = 100
TIMELINE_DETAIL_CHARS = 300
TIMELINE_CURSOR_INVALID = "This timeline cursor is not valid for this test case."
TIMELINE_KIND_INVALID = (
    "kinds takes version, review_requested, review_decided, comment, plan_execution, automated_execution, "
    "defect or requirement."
)
#: ``test_case_versions.change_type`` -> the version row's title. Any other
#: change type (a lifecycle action: ``approve``, ``deprecate`` ...) reads
#: ``Moved to <status>`` when the row's status is known.
TIMELINE_VERSION_TITLES: dict[str, str] = {
    "created": "Created as v{version}", "promoted": "Created as v{version}", "updated": "Saved as v{version}",
    "bulk_updated": "Bulk edit", "restored": "Restored from v{restored_from}",
}
#: ``test_case_reviews.status`` of a decided review -> its title.
TIMELINE_REVIEW_DECISION_TITLES: dict[str, str] = {
    "approved": "Approved", "changes_requested": "Changes requested", "rejected": "Rejected",
}
#: ``test_case_comments.comment_type`` -> its title (``general`` and anything else: "Comment").
TIMELINE_COMMENT_TITLES: dict[str, str] = {
    "review": "Review comment", "suggestion": "Suggestion", "question": "Question",
}


def _status_words(status: str) -> str:
    return status.replace("_", " ")


def _version_title(facts: dict[str, Any]) -> str:
    change_type = facts.get("change_type") or "updated"
    version = facts.get("version")
    if change_type == "restored" and not facts.get("restored_from"):
        title = f"Restored as v{version}" if version is not None else "Restored"
    elif change_type in TIMELINE_VERSION_TITLES:
        template = TIMELINE_VERSION_TITLES[change_type]
        if "{version}" in template and version is None:
            title = template.split(" as ")[0] if " as " in template else template
        else:
            title = template.format(version=version, restored_from=facts.get("restored_from"))
    elif facts.get("status"):
        title = f"Moved to {_status_words(str(facts['status']))}"
    else:
        title = f"Saved as v{version}" if version is not None else "Saved"
    source = facts.get("source")
    return f"{title} ({source})" if source else title


def timeline_title(kind: str, **facts: Any) -> str:
    """The one server-rendered sentence of a timeline entry (``TimelineEntry.title``).

    Facts by kind (each optional unless said):
      version              change_type, version, status, restored_from, source (appended "(person)")
      review_requested     none
      review_decided       status (``approved`` | ``changes_requested`` | ``rejected``)
      comment              comment_type (``review`` | ``suggestion`` | ``question`` | ``general``)
      plan_execution       plan_name (required), status, version
      automated_execution  status
      defect               jira_key
      requirement          requirement_key (required), link_type, linked (default True)

    "Saved as v4 (person)", "Review requested", "Approved", "Comment",
    "Executed in plan Sprint 42 · PASSED (v3)", "Automated run · FAILED",
    "Defect PAY-123 opened", "Linked REQ-PAY-07 (verifies)", "Unlinked REQ-PAY-07".
    An unknown kind is a programming error (ValueError).
    """
    if kind == "version":
        return _version_title(facts)
    if kind == "review_requested":
        return "Review requested"
    if kind == "review_decided":
        return TIMELINE_REVIEW_DECISION_TITLES.get(str(facts.get("status") or ""), "Review completed")
    if kind == "comment":
        return TIMELINE_COMMENT_TITLES.get(str(facts.get("comment_type") or ""), "Comment")
    if kind == "plan_execution":
        title = f"Executed in plan {facts.get('plan_name') or 'a test plan'}"
        if facts.get("status"):
            title += f" · {str(facts['status']).upper()}"
        if facts.get("version") is not None:
            title += f" (v{facts['version']})"
        return title
    if kind == "automated_execution":
        return f"Automated run · {str(facts['status']).upper()}" if facts.get("status") else "Automated run"
    if kind == "defect":
        return f"Defect {facts['jira_key']} opened" if facts.get("jira_key") else "Defect opened"
    if kind == "requirement":
        key = facts.get("requirement_key") or ""
        if facts.get("linked", True) is False:
            return f"Unlinked {key}"
        return f"Linked {key} ({facts['link_type']})" if facts.get("link_type") else f"Linked {key}"
    raise ValueError(f"unknown timeline kind: {kind}")


# ── TCL-06: requirement traceability ─────────────────────────────────────────

TRACEABILITY_FLAG = "test_case_requirement_traceability"
REQUIREMENT_LINK_TYPES: tuple[str, ...] = ("verifies", "partially_verifies", "related")
REQUIREMENT_GAP_REASONS: tuple[str, ...] = ("no_link", "related_only", "partly_verified")
REQUIREMENT_KEY_SOURCES: tuple[str, ...] = ("link", "generation_batch", "knowledge_source")
REQUIREMENT_KEY_MAX = 200
REQUIREMENT_TITLE_MAX = 500
#: Per source, distinct normalised keys.
KNOWN_KEYS_CAP = 5000
#: A control character anywhere in a typed key (C0, DEL) makes it invalid.
REQUIREMENT_KEY_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
REQUIREMENT_KEY_INVALID = "A requirement key is 1 to 200 characters with no control characters."
REQUIREMENT_LINK_TYPE_INVALID = "link_type must be verifies, partially_verifies or related."
REQUIREMENT_ALREADY_LINKED = "This case already links {key}."
REQUIREMENT_LINK_NOT_FOUND = "Requirement link not found"
#: W28's sentence for a retired case under a metadata change.
REQUIREMENT_CASE_RETIRED = "Reinstate it to edit it"
TRACEABILITY_OFF = "Requirement traceability is off for this project."
TRACEABILITY_NEEDS_PROJECT = "Choose a project: traceability is per project."
REQUIREMENT_GAP_TEXT: dict[str, str] = {
    "no_link": "No case links it.", "related_only": "Only \"related\" links: no case verifies it.",
    "partly_verified": "Only partly verified.",
}
REQUIREMENTS_UNMEASURED_SENTENCE = "No requirement keys are known to this project yet: add one to a test case."

_SURROUNDING_PAIRS: tuple[tuple[str, str], ...] = (("[", "]"), ("(", ")"))


def normalize_requirement_key(raw: str) -> str:
    """The ONE form every requirement-key comparison uses.

    Strip; drop ONE pair of surrounding ``[]`` or ``()`` (``[REQ-12]`` from a
    knowledge chunk equals a typed ``req-12``); collapse any whitespace run to
    one space; upper-case. ``""`` when nothing is left. Control characters are
    NOT removed here: a typed key holding one is refused by the service
    (``REQUIREMENT_KEY_CONTROL_RE``) before it is normalised.
    """
    text = raw.strip()
    for opening, closing in _SURROUNDING_PAIRS:
        if len(text) >= 2 and text.startswith(opening) and text.endswith(closing):
            text = text[1:-1]
            break
    return " ".join(text.split()).upper()
