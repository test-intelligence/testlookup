"""Draft a release's test phases from a PRD (W11 REL-07).

Two drafters, one answer shape (``PhaseDraftResponse`` minus ``release_id``):

* the RULES draft — a deterministic heading parser (``draft_by_rules``) that
  ALWAYS runs, offline or not, and is a complete draft on its own;
* the MODEL draft — tried only when the frozen AI config's analysis mode is
  ``llm`` or ``auto``, through the REAL ``BudgetedLLM`` that ``get_llm()``
  returns (budget check, cost reservation, cluster slot, circuit breaker),
  charged to the release's project, bounded by ``PRD_LLM_TIMEOUT_SECONDS``.

Any model failure — construction refused by the provider policy, a timeout,
an error, an unparsable or schema-invalid reply, zero usable phases — returns
the RULES draft with a ``fallback_reason`` (``DRAFT_FALLBACK_REASONS``): a
small local model that ignores the schema costs the reader nothing but the
badge. Nothing here is stored: the reader edits the preview and saves it
through the bulk create.

Commit-free and session-free: the router hands its read connection back
(``release_read_connection``) BEFORE calling ``draft_phases``, so no database
connection is held while the model runs.

The PRD text is untrusted and private: it is never logged (lengths and a
12-hex hash only), never stored, and the prompt frames it as DATA.
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import re
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Optional

import structlog
from langchain_core.messages import HumanMessage, SystemMessage

from app.core.release_planning import (
    DRAFT_WARNING_NO_HEADINGS,
    DRAFT_WARNING_TRUNCATED,
    DRAFT_WARNING_UNTYPED,
    MAX_BULK_PHASES,
    MAX_DOCX_INFLATED_BYTES,
    MAX_DOCX_MEMBERS,
    MAX_PRD_BYTES,
    MAX_PRD_CHARS,
    PHASE_TYPES,
    PRD_BAD_DOCX,
    PRD_BAD_TYPE,
    PRD_BOTH,
    PRD_EMPTY,
    PRD_LLM_INPUT_CHARS,
    PRD_LLM_TIMEOUT_SECONDS,
    PRD_NOT_UTF8,
    PRD_TOO_LARGE,
)
from app.core.metrics import release_phase_drafts_total
from app.services.ai_config_resolver import valid_analysis_mode
from app.services.llm_cost_reservation import cost_budget_scope
from app.services.llm_factory import get_llm, llm_call_timeout
from app.services.llm_json_parser import parse_llm_json
from app.services.llm_policy_service import LLMPolicyViolation
from app.services.prompt_registry import get_prompt_text

logger = structlog.get_logger(__name__)

#: The registered system prompt (``prompt_registry``, id ``release_phase_draft``).
PROMPT_ID = "release_phase_draft"

NAME_MAX = 255
DESCRIPTION_MAX = 2000
EXIT_CRITERIA_MAX = 1000
DAYS_MIN, DAYS_MAX = 1, 90


@dataclass(frozen=True, slots=True)
class DraftContext:
    """What the drafter needs from the AI config, frozen ONCE before any model call.

    No key, token or base URL is copied in: the model is built by ``get_llm``,
    which reads its own credentials.
    """

    offline: bool
    analysis_mode: str
    model_hint: Optional[str]
    project_id: str

    @classmethod
    def from_config(cls, cfg: Mapping[str, Any], *, project_id: str) -> "DraftContext":
        model = cfg.get("model")
        return cls(
            offline=bool(cfg.get("offline_mode")),
            analysis_mode=valid_analysis_mode(cfg.get("analysis_mode")) or "auto",
            model_hint=model.strip() if isinstance(model, str) and model.strip() else None,
            project_id=str(project_id),
        )


class PrdInputError(ValueError):
    """A PRD that cannot be read; ``str(exc)`` is one of the ``PRD_*`` sentences."""


# ── Extraction ───────────────────────────────────────────────────────────────

_TEXT_SUFFIXES = (".md", ".txt")
_HEADING_STYLE = re.compile(r"^heading\s*([1-9])$", re.IGNORECASE)


def _suffix(filename: Optional[str]) -> str:
    name = (filename or "").strip().lower()
    dot = name.rfind(".")
    return name[dot:] if dot >= 0 else ""


def _docx_text(data: bytes) -> str:
    """The paragraphs of a .docx, its heading styles written as ATX headings.

    The ZIP is measured BEFORE python-docx opens it: a .docx is compressed
    XML, so a 200 KB upload can inflate to gigabytes (a zip bomb), and
    python-docx parses every part it loads.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            members = archive.infolist()
            if len(members) > MAX_DOCX_MEMBERS:
                raise PrdInputError(PRD_BAD_DOCX)
            if sum(max(0, m.file_size) for m in members) > MAX_DOCX_INFLATED_BYTES:
                raise PrdInputError(PRD_BAD_DOCX)
    except PrdInputError:
        raise
    except Exception as exc:  # BadZipFile, a truncated archive, an encrypted one
        raise PrdInputError(PRD_BAD_DOCX) from exc

    try:
        import docx  # python-docx

        document = docx.Document(io.BytesIO(data))
        lines: list[str] = []
        for paragraph in document.paragraphs:
            text = paragraph.text or ""
            style = getattr(getattr(paragraph, "style", None), "name", "") or ""
            match = _HEADING_STYLE.match(style.strip())
            if match and text.strip():
                lines.append("#" * min(int(match.group(1)), 6) + " " + text.strip())
            elif style.strip().lower() == "title" and text.strip():
                lines.append("# " + text.strip())
            else:
                lines.append(text)
        return "\n".join(lines)
    except PrdInputError:
        raise
    except Exception as exc:  # a damaged or unexpected document
        raise PrdInputError(PRD_BAD_DOCX) from exc


def extract_prd_text(*, text: Optional[str], filename: Optional[str], data: Optional[bytes]) -> str:
    """The PRD as plain text, from EXACTLY one of pasted text or an uploaded file.

    ``.md`` / ``.txt`` decode as UTF-8 (a BOM is dropped); ``.docx`` is read
    with its heading styles; anything else is refused. CRLF becomes LF and NUL
    is removed. Every refusal is a ``PrdInputError`` carrying the sentence the
    form shows.
    """
    has_text = text is not None and text.strip() != ""
    has_file = data is not None
    if has_text and has_file:
        raise PrdInputError(PRD_BOTH)
    if not has_text and not has_file:
        raise PrdInputError(PRD_EMPTY)

    if has_text:
        assert text is not None
        if len(text.encode("utf-8", errors="surrogatepass")) > MAX_PRD_BYTES:
            raise PrdInputError(PRD_TOO_LARGE)
        raw = text
    else:
        assert data is not None
        suffix = _suffix(filename)
        if suffix not in (*_TEXT_SUFFIXES, ".docx"):
            raise PrdInputError(PRD_BAD_TYPE)
        if len(data) > MAX_PRD_BYTES:
            raise PrdInputError(PRD_TOO_LARGE)
        if suffix == ".docx":
            raw = _docx_text(data)
        else:
            try:
                raw = data.decode("utf-8-sig")
            except UnicodeDecodeError as exc:
                raise PrdInputError(PRD_NOT_UTF8) from exc

    cleaned = raw.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    if cleaned.startswith("﻿"):
        cleaned = cleaned[1:]
    if len(cleaned) > MAX_PRD_CHARS:
        raise PrdInputError(PRD_TOO_LARGE)
    if not cleaned.strip():
        raise PrdInputError(PRD_EMPTY)
    return cleaned


# ── The rules draft ──────────────────────────────────────────────────────────

_ATX = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$")
_SETEXT_1 = re.compile(r"^ {0,3}=+[ \t]*$")
_SETEXT_2 = re.compile(r"^ {0,3}-+[ \t]*$")
_FENCE = re.compile(r"^ {0,3}(```|~~~)")
_NUMBERED = re.compile(r"^(\d{1,2})[.)][ \t]+(\S.*)$")
_LIST_ITEM = re.compile(r"^[ \t]*(?:[-*+]|\d{1,3}[.)])[ \t]+(.*)$")

#: Sections a PRD has that are never a test phase (whole words, any case): the
#: contract's list, plus three found on real PRDs ("Acceptance criteria" would
#: otherwise draft a UAT phase, "User stories" / "Success metrics" a QA one).
_NON_PHASE = re.compile(
    r"\b(?:overview|background|goals?|non-goals|scope|out of scope|requirements|appendix|glossary|"
    r"references|open questions|risks|assumptions|summary|introduction|table of contents|"
    r"acceptance criteria|user stories|success metrics)\b",
    re.IGNORECASE,
)

#: The FIRST matching table wins, in this order. A keyword matches at the start
#: of a word ("develop" in "development", "test" in "testing").
_TYPE_TABLE: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("production", re.compile(r"\b(?:production|go-live|launch|release to prod|cutover)", re.IGNORECASE)),
    ("staging", re.compile(r"\b(?:staging|pre-prod|preprod|dress rehearsal)", re.IGNORECASE)),
    ("uat", re.compile(r"\b(?:uat|user acceptance|acceptance|beta|pilot)", re.IGNORECASE)),
    ("code_freeze", re.compile(r"\b(?:code freeze|freeze|stabili[sz]ation|hardening)", re.IGNORECASE)),
    ("qa_testing", re.compile(
        r"\b(?:smoke|regression|integration|system test|e2e|end-to-end|qa|test|verification|validation|"
        r"performance|load|security)",
        re.IGNORECASE,
    )),
    ("development", re.compile(r"\b(?:develop|implementation|build|coding|sprint)", re.IGNORECASE)),
    ("planning", re.compile(r"\b(?:plan|planning|kickoff|design|discovery)", re.IGNORECASE)),
)

_DAYS = re.compile(r"(?<!\d)(\d{1,2})\s*(?:business\s+|working\s+)?days?\b", re.IGNORECASE)
_WEEKS = re.compile(r"(?<!\d)(\d)\s*weeks?\b", re.IGNORECASE)
_DURATION_PAREN = re.compile(
    r"\s*\(\s*\d{1,2}\s*(?:business\s+|working\s+)?(?:days?|weeks?)\s*\)", re.IGNORECASE
)
_EXIT_START = re.compile(
    r"^(?:exit criteria|done when|definition of done|acceptance criteria)\b[ \t]*[:\-–—]?[ \t]*(.*)$",
    re.IGNORECASE,
)
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_EMPHASIS_STAR = re.compile(r"(\*\*|\*|~~)(?=\S)(.+?)(?<=\S)\1")
#: Underscore emphasis only at word edges, so ``test_pay_flow`` keeps its underscores.
_EMPHASIS_UNDERSCORE = re.compile(r"(?<!\w)(__|_)(?=\S)(.+?)(?<=\S)\1(?!\w)")


def _clean_inline(text: str) -> str:
    """Markdown links to their text, emphasis and backticks removed, spaces squeezed."""
    out = _LINK.sub(r"\1", text)
    for _ in range(3):  # nested emphasis
        out = _EMPHASIS_STAR.sub(r"\2", out)
        out = _EMPHASIS_UNDERSCORE.sub(r"\2", out)
    out = out.replace("`", "")
    return re.sub(r"\s+", " ", out).strip()


def _clean_name(heading: str) -> str:
    name = _clean_inline(heading)
    name = re.sub(r"^\d{1,2}[.)]\s+", "", name)   # "1. Smoke testing"
    name = _DURATION_PAREN.sub("", name)            # "Regression (5 days)"
    name = name.strip().rstrip(":").strip()
    return name[:NAME_MAX]


def _phase_type(heading: str) -> Optional[str]:
    for phase_type, pattern in _TYPE_TABLE:
        if pattern.search(heading):
            return phase_type
    return None


def _days_in(text: str) -> Optional[int]:
    match = _DAYS.search(text)
    if match:
        days = int(match.group(1))
    else:
        weeks = _WEEKS.search(text)
        if not weeks:
            return None
        days = int(weeks.group(1)) * 5   # a week is five working days (the guide says so)
    return max(DAYS_MIN, min(DAYS_MAX, days))


@dataclass
class _Heading:
    line: int            # the heading's text line
    level: int           # 1..6 (Setext: 1 for ===, 2 for ---)
    text: str
    span: int            # lines it occupies (Setext: 2)
    numbered: bool = False


def _headings(lines: list[str]) -> list[_Heading]:
    """Every ATX and Setext heading outside fenced code blocks."""
    found: list[_Heading] = []
    in_fence = False
    previous_is_text = False
    for i, line in enumerate(lines):
        if _FENCE.match(line):
            in_fence = not in_fence
            previous_is_text = False
            continue
        if in_fence:
            continue
        atx = _ATX.match(line)
        if atx:
            found.append(_Heading(i, len(atx.group(1)), atx.group(2).strip(), 1))
            previous_is_text = False
            continue
        if previous_is_text and (_SETEXT_1.match(line) or _SETEXT_2.match(line)):
            title = lines[i - 1].strip()
            found.append(_Heading(i - 1, 1 if _SETEXT_1.match(line) else 2, title, 2))
            previous_is_text = False
            continue
        stripped = line.strip()
        # A Setext underline only follows a paragraph line, never a list item.
        previous_is_text = bool(stripped) and not _LIST_ITEM.match(line)
    return found


def _numbered_lines(lines: list[str]) -> list[_Heading]:
    """Top-level numbered lines (``1. Smoke testing``) outside fenced code blocks."""
    found: list[_Heading] = []
    in_fence = False
    for i, line in enumerate(lines):
        if _FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = _NUMBERED.match(line)
        if match:
            found.append(_Heading(i, 2, match.group(2).strip(), 1, numbered=True))
    return found


def _body_parts(body: list[str]) -> tuple[Optional[str], Optional[str]]:
    """(description, exit_criteria_text) from a phase's body lines."""
    exit_items: list[str] = []
    exit_at: Optional[int] = None
    for i, line in enumerate(body):
        candidate = _clean_inline(re.sub(r"^\s*(?:#{1,6}\s+|[-*+]\s+|\d{1,3}[.)]\s+)?", "", line))
        match = _EXIT_START.match(candidate)
        if not match:
            continue
        exit_at = i
        if match.group(1).strip():
            exit_items.append(match.group(1).strip())
        started = False
        for follow in body[i + 1:]:
            if not follow.strip():
                if started:
                    break
                continue
            item = _LIST_ITEM.match(follow)
            if not item:
                break
            started = True
            text = _clean_inline(item.group(1))
            if text:
                exit_items.append(text)
        break

    description: Optional[str] = None
    paragraph: list[str] = []
    for i, line in enumerate(body):
        if exit_at is not None and i >= exit_at:
            break
        stripped = line.strip()
        if not stripped:
            if paragraph:
                break
            continue
        if re.match(r"^#{1,6}\s", stripped):   # a sub-heading inside the phase
            if paragraph:
                break
            continue
        item = _LIST_ITEM.match(line)
        paragraph.append(_clean_inline(item.group(1) if item else stripped))
    if paragraph:
        description = " ".join(p for p in paragraph if p)[:DESCRIPTION_MAX] or None

    exit_text = "; ".join(exit_items)[:EXIT_CRITERIA_MAX] or None
    return description, exit_text


def draft_by_rules(text: str) -> tuple[list[dict], list[str]]:
    """The heading parser: a ``PhaseDraft`` dict per test-phase heading, and the warnings.

    Pure and deterministic, so the same PRD always drafts the same phases.

    * Candidates: ATX ``##`` / ``###`` and Setext ``---`` headings; the
      level-1 ones (``#``, Setext ``===``) only when the document has no
      level-2 / level-3 heading (a lone ``#`` is usually the title). When the
      document has no heading at all, its top-level numbered lines
      (``1. Smoke testing``) are the candidates instead.
    * A candidate naming a non-phase section (overview, scope, requirements,
      risks, appendix, ...) is skipped.
    * Each kept heading is a phase named by its text (Markdown links /
      emphasis / backticks, a leading number, a trailing colon and a
      parenthesised duration removed); its type is the first keyword table
      that matches, else ``qa_testing`` with ``DRAFT_WARNING_UNTYPED``.
    * Its body (to the next heading of level <= 3, or the next candidate) gives
      the description (the first paragraph) and the exit criteria (a line
      starting "Exit criteria" / "Done when" / "Definition of done" /
      "Acceptance criteria" and the bullets under it, joined "; ").
    * ``planned_days``: the first "N days" (business / working allowed) in the
      heading, else "N weeks" (5 working days a week), else the same in the
      body; clamped 1..90.
    * At most ``MAX_BULK_PHASES`` (then ``DRAFT_WARNING_TRUNCATED``); none ->
      ``[]`` and ``DRAFT_WARNING_NO_HEADINGS``.
    """
    lines = text.split("\n")
    headings = _headings(lines)
    if headings:
        if any(h.level in (2, 3) for h in headings):
            candidates = [h for h in headings if h.level in (2, 3)]
        else:
            candidates = [h for h in headings if h.level == 1]
        boundaries = [h for h in headings if h.level <= 3]
    else:
        candidates = _numbered_lines(lines)
        boundaries = candidates

    boundary_lines = sorted({h.line for h in boundaries} | {h.line for h in candidates})
    phases: list[dict] = []
    untyped: list[bool] = []
    for heading in candidates:
        if _NON_PHASE.search(_clean_inline(heading.text)):
            continue
        name = _clean_name(heading.text)
        if not name:
            continue
        start = heading.line + heading.span
        end = next((b for b in boundary_lines if b > heading.line), len(lines))
        body = lines[start:end]
        if heading.numbered:
            # A numbered phase's body is its indented continuation lines only.
            body = [ln for ln in body if not ln.strip() or ln[:1] in (" ", "\t")]
        description, exit_text = _body_parts(body)
        phase_type = _phase_type(_clean_inline(heading.text))
        untyped.append(phase_type is None)
        if phase_type is None:
            phase_type = "qa_testing"
        days = _days_in(heading.text)
        if days is None:
            days = _days_in("\n".join(body))
        phases.append({
            "name": name,
            "phase_type": phase_type,
            "description": description,
            "exit_criteria_text": exit_text,
            "planned_days": days,
            "heading": _clean_inline(heading.text)[:NAME_MAX],
        })

    if not phases:
        return [], [DRAFT_WARNING_NO_HEADINGS]
    truncated = len(phases) > MAX_BULK_PHASES
    phases, untyped = phases[:MAX_BULK_PHASES], untyped[:MAX_BULK_PHASES]
    warnings: list[str] = []
    if any(untyped):
        warnings.append(DRAFT_WARNING_UNTYPED)
    if truncated:
        warnings.append(DRAFT_WARNING_TRUNCATED)
    return phases, warnings


# ── The model draft ──────────────────────────────────────────────────────────


def _text_of(reply: Any) -> str:
    """The text of a chat reply (a string, or a list of content parts)."""
    content = getattr(reply, "content", reply)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, Mapping) and isinstance(part.get("text"), str):
                parts.append(part["text"])
        return "".join(parts)
    return str(content or "")


def _clip(value: Any, limit: int) -> Optional[str]:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned[:limit] or None


def _days(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, int) and DAYS_MIN <= value <= DAYS_MAX:
        return value
    return None


def _normalise_llm_items(items: list[Any]) -> list[dict]:
    """The model's phases, validated one by one; an invalid item is dropped."""
    phases: list[dict] = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        raw_type = item.get("phase_type")
        phase_type = (
            re.sub(r"[\s\-]+", "_", raw_type.strip().lower()) if isinstance(raw_type, str) else ""
        )
        if phase_type not in PHASE_TYPES:
            phase_type = "qa_testing"
        phases.append({
            "name": name.strip()[:NAME_MAX],
            "phase_type": phase_type,
            "description": _clip(item.get("description"), DESCRIPTION_MAX),
            "exit_criteria_text": _clip(item.get("exit_criteria"), EXIT_CRITERIA_MAX),
            "planned_days": _days(item.get("planned_days")),
            "heading": None,
        })
    return phases


async def _model_draft(ctx: DraftContext, text: str) -> tuple[Optional[list[dict]], Optional[str], Optional[str]]:
    """(phases, model_name, fallback_reason): phases is None when the model was not usable."""
    with cost_budget_scope(ctx.project_id):
        try:
            llm = await get_llm()
        except LLMPolicyViolation:
            return None, None, "offline_mode" if ctx.offline else "llm_unavailable"
        except Exception:  # noqa: BLE001 -- any construction failure is "not available"
            return None, None, "llm_unavailable"
        try:
            timeout = min(PRD_LLM_TIMEOUT_SECONDS, llm_call_timeout(llm))
            reply = await asyncio.wait_for(
                llm.ainvoke([
                    SystemMessage(content=get_prompt_text(PROMPT_ID)),
                    HumanMessage(content=text[:PRD_LLM_INPUT_CHARS]),
                ]),
                timeout=timeout,
            )
        except (asyncio.TimeoutError, TimeoutError):
            return None, None, "llm_timeout"
        except Exception:  # noqa: BLE001 -- a failed call is a fallback, never an error
            return None, None, "llm_error"

    parsed, error = parse_llm_json(_text_of(reply), expected_keys=["phases"], context=PROMPT_ID)
    items = parsed.get("phases") if isinstance(parsed, Mapping) else None
    if error is not None or not isinstance(items, list):
        return None, None, "llm_unparsable"
    phases = _normalise_llm_items(items)
    if not phases:
        return None, None, "llm_empty"
    model_name = getattr(llm, "model_label", None)
    if not isinstance(model_name, str) or not model_name.strip() or model_name == "unknown":
        model_name = ctx.model_hint
    return phases, model_name, None


async def draft_phases(ctx: DraftContext, text: str) -> dict:
    """A ``PhaseDraftResponse`` body minus ``release_id``.

    The rules draft is computed first, always; the model is asked only in
    ``llm`` / ``auto`` analysis mode, and any failure there returns the rules
    draft with the reason. Counted once per call in
    ``testlookup_release_phase_drafts_total{source, fallback_reason}``.
    """
    rules, rule_warnings = draft_by_rules(text)
    source = "rules"
    model_name: Optional[str] = None
    fallback: Optional[str]
    phases, warnings = rules, rule_warnings

    if ctx.analysis_mode in ("rules", "ml"):
        fallback = "rules_mode"
    else:
        drafted, name, fallback = await _model_draft(ctx, text)
        if drafted is not None:
            source, model_name = "llm", name
            warnings = []
            if len(drafted) > MAX_BULK_PHASES:
                drafted = drafted[:MAX_BULK_PHASES]
                warnings.append(DRAFT_WARNING_TRUNCATED)
            phases = drafted

    release_phase_drafts_total.labels(source=source, fallback_reason=fallback or "").inc()
    logger.info(
        "release_phase_draft",
        source=source,
        fallback_reason=fallback,
        text_chars=len(text),
        phases=len(phases),
        text_sha256=hashlib.sha256(text.encode("utf-8", errors="surrogatepass")).hexdigest()[:12],
    )
    return {
        "source": source,
        "model_name": model_name,
        "fallback_reason": fallback,
        "phases": phases,
        "warnings": warnings,
        "text_chars": len(text),
    }


__all__ = [
    "DraftContext",
    "PROMPT_ID",
    "PrdInputError",
    "draft_by_rules",
    "draft_phases",
    "extract_prd_text",
]
