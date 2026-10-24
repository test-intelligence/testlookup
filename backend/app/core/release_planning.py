"""Release-planning vocabularies and sentences (W11: REL-05, REL-07, REL-06, REL-03).

One place for every word and sentence the release-planning routes, services,
schemas and tests share, so a refusal the API sends and the sentence a test
asserts cannot drift apart. Pure: imports nothing from ``app`` (``schemas.py``,
the services and the tests import it).
"""
from __future__ import annotations

# ── Phases (REL-05 clone, REL-07 draft, the bulk create) ─────────────────────

#: = ``ReleasePhase.phase_type``'s documented vocabulary (models/postgres.py)
#: and the New-phase form's options (frontend ``pages/ReleasesPage.tsx``
#: ``PHASE_TYPES``); ``tests/test_release_planning_vocab.py`` pins both.
PHASE_TYPES: tuple[str, ...] = (
    "planning", "development", "code_freeze", "qa_testing", "uat", "staging", "production",
)
#: Who drafted a bulk create: the PRD heading parser, the AI model, or a person.
PHASE_DRAFT_SOURCES: tuple[str, ...] = ("rules", "llm", "manual")
#: Why a PRD draft is the rules draft (``PhaseDraftResponse.fallback_reason``).
DRAFT_FALLBACK_REASONS: tuple[str, ...] = (
    "offline_mode", "rules_mode", "llm_unavailable", "llm_timeout", "llm_error",
    "llm_unparsable", "llm_empty",
)
MAX_BULK_PHASES = 30
MAX_PRD_BYTES = 200_000                 # upload / text body, before extraction
MAX_PRD_CHARS = 200_000                 # extracted text
MAX_DOCX_INFLATED_BYTES = 5_000_000     # sum of ZipInfo.file_size, checked before python-docx opens the file
MAX_DOCX_MEMBERS = 300
PRD_LLM_TIMEOUT_SECONDS = 45            # asyncio.wait_for around the model call: min(this, llm_call_timeout(llm))
PRD_LLM_INPUT_CHARS = 24_000            # the PRD text handed to the model is clipped here (the rules parser reads all of it)

PHASE_TYPE_SENTENCE = (
    "Phase type must be one of: planning, development, code_freeze, qa_testing, uat, staging, production."
)
PHASE_WINDOW_SENTENCE = "A phase cannot end before it starts."
#: ``release_service.add_phase``'s existing 409 text (``.format(name=...)``).
PHASE_NAME_TAKEN = "Phase '{name}' already exists in this release"
PHASE_NAME_REPEATED = "Phase '{name}' appears twice in this request"
TARGET_HAS_PHASES = "This release already has phases. Remove them first, or add phases one at a time."
SOURCE_HAS_NO_PHASES = "That release has no phases to copy."
SOURCE_IS_TARGET = "Choose another release to copy from."
NO_SHIFT_ANCHOR = "Choose when the first phase starts: this release has no cutoff start."
#: The 404 text of ``release_service.get_release_or_404``.
RELEASE_NOT_FOUND = "Release not found"

PRD_EMPTY = "Paste the PRD text or choose a .md, .txt or .docx file."
PRD_BOTH = "Send the PRD as text or as a file, not both."
PRD_TOO_LARGE = "The PRD is larger than 200 KB. Paste the part that describes the test phases."
PRD_BAD_TYPE = "Only .md, .txt and .docx files can be read."
PRD_NOT_UTF8 = "The file is not UTF-8 text."
PRD_BAD_DOCX = "The .docx file could not be read (it is damaged, encrypted or too large when unpacked)."

DRAFT_WARNING_UNTYPED = "Some headings name no phase type; they were drafted as QA testing. Check each type."
DRAFT_WARNING_TRUNCATED = "Only the first 30 phases were drafted."
DRAFT_WARNING_NO_HEADINGS = "No headings were found, so no phases were drafted. Use ## headings for the phases."

#: The draft badge's second half: "Drafted by rules — <text>".
FALLBACK_REASON_TEXT: dict[str, str] = {
    "offline_mode": "the deployment is offline (AI_OFFLINE_MODE)",
    "rules_mode": "AI analysis mode is set to rules",
    "llm_unavailable": "the AI model is not available",
    "llm_timeout": "the AI model did not answer in time",
    "llm_error": "the AI model call failed",
    "llm_unparsable": "the AI model's answer could not be read",
    "llm_empty": "the AI model drafted no phases",
}

# ── Schedule export (REL-06) ─────────────────────────────────────────────────

SCHEDULE_FORMATS: tuple[str, ...] = ("ics", "csv")
SCHEDULE_MAX_RELEASES = 500
SCHEDULE_TOO_MANY = "More than 500 releases match. Narrow the dates (from / to) or pick one project."

# ── Portfolio trend (REL-03) ─────────────────────────────────────────────────

PORTFOLIO_WEEKS_DEFAULT, PORTFOLIO_WEEKS_MIN, PORTFOLIO_WEEKS_MAX = 12, 4, 26
#: Series built on TODAY's release status (no status history exists yet).
PORTFOLIO_APPROXIMATED_SERIES: tuple[str, ...] = ("in_progress", "ready_to_ship", "blocked")

# Release.status -> stage: the frontend's ``mapStage``
# (``components/releases/mapping.ts``), restated ONCE here and pinned against it
# by ``tests/test_release_planning_vocab.py``.
STAGE_RELEASED: tuple[str, ...] = ("released", "shipped", "complete", "completed")
STAGE_CANCELLED: tuple[str, ...] = ("cancelled", "canceled", "archived")
STAGE_IN_PROGRESS: tuple[str, ...] = ("in_progress", "in-progress", "active", "running")
#: The frontend's ``mapPhaseState`` 'failed' (``components/releases/mapping.ts``).
PHASE_FAILED_STATUSES: tuple[str, ...] = ("failed", "fail")


def release_stage(status: str | None) -> str:
    """``"released"`` | ``"cancelled"`` | ``"in_progress"`` | ``"planning"`` (lower-cased match).

    The same order as the frontend's ``mapStage``: anything it does not name,
    including ``None`` and the empty string, is ``"planning"``.
    """
    lower = (status or "").lower()
    if lower in STAGE_RELEASED:
        return "released"
    if lower in STAGE_CANCELLED:
        return "cancelled"
    if lower in STAGE_IN_PROGRESS:
        return "in_progress"
    return "planning"


__all__ = [
    "DRAFT_FALLBACK_REASONS",
    "DRAFT_WARNING_NO_HEADINGS",
    "DRAFT_WARNING_TRUNCATED",
    "DRAFT_WARNING_UNTYPED",
    "FALLBACK_REASON_TEXT",
    "MAX_BULK_PHASES",
    "MAX_DOCX_INFLATED_BYTES",
    "MAX_DOCX_MEMBERS",
    "MAX_PRD_BYTES",
    "MAX_PRD_CHARS",
    "NO_SHIFT_ANCHOR",
    "PHASE_DRAFT_SOURCES",
    "PHASE_FAILED_STATUSES",
    "PHASE_NAME_REPEATED",
    "PHASE_NAME_TAKEN",
    "PHASE_TYPES",
    "PHASE_TYPE_SENTENCE",
    "PHASE_WINDOW_SENTENCE",
    "PORTFOLIO_APPROXIMATED_SERIES",
    "PORTFOLIO_WEEKS_DEFAULT",
    "PORTFOLIO_WEEKS_MAX",
    "PORTFOLIO_WEEKS_MIN",
    "PRD_BAD_DOCX",
    "PRD_BAD_TYPE",
    "PRD_BOTH",
    "PRD_EMPTY",
    "PRD_LLM_INPUT_CHARS",
    "PRD_LLM_TIMEOUT_SECONDS",
    "PRD_NOT_UTF8",
    "PRD_TOO_LARGE",
    "RELEASE_NOT_FOUND",
    "SCHEDULE_FORMATS",
    "SCHEDULE_MAX_RELEASES",
    "SCHEDULE_TOO_MANY",
    "SOURCE_HAS_NO_PHASES",
    "SOURCE_IS_TARGET",
    "STAGE_CANCELLED",
    "STAGE_IN_PROGRESS",
    "STAGE_RELEASED",
    "TARGET_HAS_PHASES",
    "release_stage",
]
