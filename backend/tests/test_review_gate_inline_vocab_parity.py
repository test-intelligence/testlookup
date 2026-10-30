"""W23c: the review-gate / inline-input vocabulary the UI renders is the server's, and agrees with its sources.

* ``frontend/src/types/reviewGate.ts`` mirrors ``services/review_gate_vocab.py``
  (sources, channel codes, the window, the page sentences, the PUT refusals) and
  ``frontend/src/types/agentInvoke.ts`` mirrors ``services/inline_input_vocab.py``
  (agents, caps, categories, input kinds, the invoke refusals and the panel copy).
  Read as TEXT (no Node in the backend suite), like W22b's
  ``test_retention_export_vocab_frontend_parity.py``.
* The server-side copies agree with what they are derived from:
  ``WOULD_REFUSE_ACTION`` is the policy's would-refuse audit action; every
  distribution channel literal in ``backend/app`` is a ``GATED_CHANNELS`` code
  (an AST scan — docstrings never count — with the non-distribution literals
  recorded as data, each with its reason); ``REPOST_SKIP_REASONS`` covers both
  git-host resolvers' reasons; ``FAILURE_CATEGORIES`` is ``FailureCategory``;
  ``REPOST_KINDS`` are ``run_downstream_outbox`` operations (G-B registers
  them); ``WRAP_LIMITS`` equal the caps the unchanged downstream code applies
  (read from source, so a later change there fails loudly); the inline model's
  caps are the vocabulary's.
"""
from __future__ import annotations

import ast
import re
import string
import typing
from pathlib import Path

import pytest

from app.services import inline_input_vocab as iv
from app.services import review_gate_vocab as rv

BACKEND = Path(__file__).resolve().parents[1]
APP = BACKEND / "app"
FRONTEND_TYPES = BACKEND.parent / "frontend" / "src" / "types"
REVIEW_GATE_TS = FRONTEND_TYPES / "reviewGate.ts"
AGENT_INVOKE_TS = FRONTEND_TYPES / "agentInvoke.ts"

# A single- or double-quoted TS string literal (a value carrying an apostrophe is double-quoted).
_STR = r"""(?:'([^'\\]*)'|"([^"\\]*)")"""


def _read(path: Path) -> str:
    if not path.exists():
        pytest.fail(f"{path} is missing: the parity this test pins has nothing to read")
    return path.read_text(encoding="utf-8")


def _strings(text: str) -> tuple[str, ...]:
    return tuple(a or b for a, b in re.findall(_STR, text))


def _array(path: Path, name: str) -> tuple[str, ...]:
    match = re.search(rf"export const {name}\b[^=]*=\s*\[([^\]]*)\]\s*as const", _read(path))
    assert match, f"{name} is not declared as an `as const` array literal in {path.name}"
    return _strings(match.group(1))


def _number_array(path: Path, name: str) -> tuple[int, ...]:
    match = re.search(rf"export const {name}\b[^=]*=\s*\[([^\]]*)\]\s*as const", _read(path))
    assert match, f"{name} is not declared as an `as const` array literal in {path.name}"
    return tuple(int(n) for n in re.findall(r"\d+", match.group(1)))


def _number(path: Path, name: str) -> int:
    match = re.search(rf"export const {name}\b[^=]*=\s*(\d+)\s*$", _read(path), re.MULTILINE)
    assert match, f"{name} is not declared as a number literal in {path.name}"
    return int(match.group(1))


def _record(path: Path, name: str) -> dict[str, str]:
    # The values carry `{placeholder}`s, so the literal ends at the first column-0 `} as const`.
    match = re.search(rf"export const {name}\b[^=]*=\s*\{{(.*?)\n\}}\s*as const", _read(path), re.DOTALL)
    assert match, f"{name} is not declared as an `as const` object literal in {path.name}"
    body = match.group(1)
    entries = {
        key: a or b
        for key, a, b in re.findall(rf"^\s*'?([A-Za-z_.:]+)'?:\s*{_STR},?\s*$", body, re.MULTILINE)
    }
    lines = [line for line in body.splitlines() if line.strip()]
    assert len(entries) == len(lines), f"every {name} entry is one `key: 'value',` line"
    return entries


def _placeholders(sentence: str) -> set[str]:
    return {field for _, field, _, _ in string.Formatter().parse(sentence) if field}


# ── review gate: the frontend copies ─────────────────────────────────────────

def test_review_gate_arrays_equal_the_server():
    assert _array(REVIEW_GATE_TS, "GATE_SOURCES") == rv.GATE_SOURCES
    assert _array(REVIEW_GATE_TS, "GATED_CHANNEL_CODES") == tuple(code for code, _ in rv.GATED_CHANNELS)


def test_review_gate_window_equals_the_server():
    assert _number(REVIEW_GATE_TS, "WOULD_REFUSE_WINDOW_DAYS") == rv.WOULD_REFUSE_WINDOW_DAYS


@pytest.mark.parametrize(("name", "server"), [
    ("GATE_SENTENCES", rv.GATE_SENTENCES),
    ("REVIEW_GATE_ERRORS", rv.REVIEW_GATE_ERRORS),
])
def test_review_gate_records_equal_the_server(name, server):
    assert _record(REVIEW_GATE_TS, name) == server


# ── review gate: the server's own consistency ────────────────────────────────

def test_would_refuse_action_is_the_policy_audit_action():
    from app.services import report_distribution_policy as policy

    assert rv.WOULD_REFUSE_ACTION == policy._AUDIT_ACTIONS[policy.WOULD_REFUSE]
    assert policy.WOULD_REFUSE == "not_enforced_would_refuse"


def test_gate_sentences_carry_exactly_their_placeholders():
    assert _placeholders(rv.GATE_SENTENCES["unset"]) == {"runs", "decisions"}
    assert _placeholders(rv.GATE_SENTENCES["scheduled"]) == {"date", "runs", "decisions"}
    assert _placeholders(rv.GATE_SENTENCES["date"]) == {"date"}
    for key in ("env", "not_measured", "past_date_warning"):
        assert _placeholders(rv.GATE_SENTENCES[key]) == set(), key
    assert set(rv.GATE_SENTENCES) == {*rv.GATE_SOURCES, "not_measured", "past_date_warning"}


def test_gated_channels_are_unique_and_labelled():
    codes = [code for code, _ in rv.GATED_CHANNELS]
    assert len(codes) == len(set(codes)) == 13
    assert all(label and label.strip() == label for _, label in rv.GATED_CHANNELS)


#: Literals the scan finds that are NOT distribution-decision channels. Recorded as data with the reason,
#: so a new literal has to be either a GATED_CHANNELS code or justified here.
_NOT_DISTRIBUTION_CHANNELS: dict[str, str] = {
    "release_readiness_advisory": (
        "report_distribution_policy records the opt-in advisory read only as INCLUDE_UNREVIEWED, never as a "
        "refused / would-refuse decision; the page lists the readiness value under 'Also changes'"
    ),
    "#qa-alerts": "core/config.py SLACK_DEFAULT_CHANNEL — a Slack channel name, not a distribution decision",
    "in_app": "activity/live_vocab.py IN_APP_CHANNEL — the in-app mention delivery channel",
    "no_team_channel": "notification_routing.py FALLBACK_NO_TEAM_CHANNEL — a routing fallback reason",
}
_CHANNEL_ARGS = {"channel", "distribution_channel"}


def _str_constant(node: ast.AST | None) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _channel_literals() -> dict[str, list[str]]:
    """Every ``channel="…"`` / ``distribution_channel="…"`` keyword, ``channel: str = "…"`` parameter default
    and ``*_CHANNEL = "…"`` constant in backend/app (code only: a docstring is never a call)."""
    found: dict[str, list[str]] = {}
    for path in sorted(APP.rglob("*.py")):
        where = path.relative_to(APP).as_posix()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            values: list[str | None] = []
            if isinstance(node, ast.Call):
                values = [_str_constant(kw.value) for kw in node.keywords if kw.arg in _CHANNEL_ARGS]
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                a = node.args
                positional = a.posonlyargs + a.args
                pairs = list(zip(positional[len(positional) - len(a.defaults):], a.defaults))
                pairs += list(zip(a.kwonlyargs, a.kw_defaults))
                values = [_str_constant(default) for arg, default in pairs if arg.arg in _CHANNEL_ARGS]
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                if any(isinstance(t, ast.Name) and t.id.endswith("_CHANNEL") for t in targets):
                    values = [_str_constant(node.value)]
            for value in values:
                if value is not None:
                    found.setdefault(value, []).append(f"{where}:{node.lineno}")
    return found


def test_every_distribution_channel_literal_in_app_is_a_gated_channel_and_none_is_invented():
    found = _channel_literals()
    codes = {code for code, _ in rv.GATED_CHANNELS}
    distribution = set(found) - set(_NOT_DISTRIBUTION_CHANNELS)
    assert distribution - codes == set(), {c: found[c] for c in distribution - codes}
    assert codes - distribution == set(), "a GATED_CHANNELS code no decision is recorded under (invented)"
    stale = set(_NOT_DISTRIBUTION_CHANNELS) - set(found)
    assert stale == set(), f"stale exclusions (the literal is gone from app/): {stale}"


def test_repost_skip_reasons_cover_both_git_host_resolvers():
    from app.services.github_target import GITHUB_UNAVAILABLE_REASONS
    from app.services.gitlab_integration_service import GITLAB_UNAVAILABLE_REASONS

    assert set(rv.REPOST_SKIP_REASONS) >= set(GITHUB_UNAVAILABLE_REASONS) | set(GITLAB_UNAVAILABLE_REASONS)
    assert len(rv.REPOST_SKIP_REASONS) == len(set(rv.REPOST_SKIP_REASONS))


def test_every_repost_outcome_and_skip_reason_has_its_sentence():
    expected = {"updated", "timed_out", "timed_out_final", "failed"} | {
        f"skipped:{reason}" for reason in rv.REPOST_SKIP_REASONS
    }
    assert set(rv.REPOST_SENTENCES) == expected
    assert set(rv.REPOST_OUTCOMES) == {"updated", "skipped", "timed_out", "failed"}
    for key, sentence in rv.REPOST_SENTENCES.items():
        assert sentence.startswith("{host} "), key
        assert _placeholders(sentence) <= {"host", "provider", "status"}, key
    assert _placeholders(rv.REPOST_SENTENCES["failed"]) == {"host", "status"}


def test_the_reviewed_line_names_a_time_and_never_a_person():
    assert _placeholders(rv.REVIEWED_LINE) == {"reviewed_at"}
    assert rv.REVIEWED_LINE.format(reviewed_at="2026-11-02 09:14 UTC") == "_Reviewed by a person · 2026-11-02 09:14 UTC_"


def test_repost_kinds_are_downstream_outbox_operations():
    # Waiting for G-B until run_downstream_outbox._OPERATIONS registers the two kinds (§2.1 R-2).
    from app.services import run_downstream_outbox

    assert set(rv.REPOST_KINDS) <= set(run_downstream_outbox._OPERATIONS)


# ── inline input: the frontend copies ────────────────────────────────────────

@pytest.mark.parametrize(("name", "server"), [
    ("INLINE_INPUT_AGENTS", iv.INLINE_INPUT_AGENTS),
    ("FAILURE_CATEGORIES", iv.FAILURE_CATEGORIES),
    ("INPUT_KINDS", iv.INPUT_KINDS),
])
def test_inline_arrays_equal_the_server(name, server):
    assert _array(AGENT_INVOKE_TS, name) == tuple(server)


@pytest.mark.parametrize(("name", "server"), [
    ("INLINE_INPUT_MAX_BYTES", iv.INLINE_INPUT_MAX_BYTES),
    ("INLINE_FAILURES_MAX", iv.INLINE_FAILURES_MAX),
    ("TEST_NAME_MAX", iv.TEST_NAME_MAX),
    ("SUITE_NAME_MAX", iv.SUITE_NAME_MAX),
    ("ROOT_CAUSE_MAX", iv.ROOT_CAUSE_MAX),
    ("LOG_EXCERPT_MAX", iv.LOG_EXCERPT_MAX),
])
def test_inline_numbers_equal_the_server(name, server):
    assert _number(AGENT_INVOKE_TS, name) == server


def test_inline_confidence_range_equals_the_server():
    assert _number_array(AGENT_INVOKE_TS, "CONFIDENCE_RANGE") == iv.CONFIDENCE_RANGE


@pytest.mark.parametrize(("name", "server"), [
    ("INLINE_ERRORS", iv.INLINE_ERRORS),
    ("INLINE_SENTENCES", iv.INLINE_SENTENCES),
])
def test_inline_records_equal_the_server(name, server):
    assert _record(AGENT_INVOKE_TS, name) == server


# ── inline input: the server's own consistency ───────────────────────────────

def test_failure_categories_are_the_orm_enum():
    from app.models.postgres import FailureCategory

    assert iv.FAILURE_CATEGORIES == tuple(category.value for category in FailureCategory)


def test_the_size_refusal_names_the_cap():
    assert str(iv.INLINE_INPUT_MAX_BYTES) in iv.INLINE_ERRORS["inline_input_too_large"]
    assert _placeholders(iv.INLINE_ERRORS["inline_input_unsupported"]) == {"agent_id"}
    assert iv.UNTRUSTED_OPEN == "<<data>>" and iv.UNTRUSTED_CLOSE == "<</data>>"


def test_the_inline_model_applies_the_vocabulary_caps():
    from app.models.inline_agent_input import InlineFailureV1, InlineSummaryInputV1

    schema = InlineSummaryInputV1.model_json_schema()
    failure = schema["$defs"]["InlineFailureV1"]["properties"]

    def max_length(prop: dict) -> int:
        if "maxLength" in prop:
            return prop["maxLength"]
        return next(option["maxLength"] for option in prop["anyOf"] if "maxLength" in option)

    assert max_length(failure["test_name"]) == iv.TEST_NAME_MAX
    assert max_length(failure["suite_name"]) == iv.SUITE_NAME_MAX
    assert max_length(failure["root_cause"]) == iv.ROOT_CAUSE_MAX
    assert max_length(failure["log_excerpt"]) == iv.LOG_EXCERPT_MAX
    assert (failure["confidence_score"]["minimum"], failure["confidence_score"]["maximum"]) == iv.CONFIDENCE_RANGE
    assert tuple(failure["failure_category"]["enum"]) == iv.FAILURE_CATEGORIES
    assert schema["properties"]["failures"]["maxItems"] == iv.INLINE_FAILURES_MAX
    literal = typing.get_args(InlineFailureV1.model_fields["failure_category"].annotation)
    assert literal == iv.FAILURE_CATEGORIES
    assert schema["additionalProperties"] is False
    assert schema["$defs"]["InlineFailureV1"]["additionalProperties"] is False


def _source_int(path: Path, pattern: str) -> int:
    match = re.search(pattern, path.read_text(encoding="utf-8"), re.MULTILINE)
    assert match, f"{pattern!r} no longer appears in {path.relative_to(BACKEND)}: re-derive WRAP_LIMITS"
    return int(match.group(1))


def test_wrap_limits_equal_the_downstream_caps_read_from_source():
    catalogue = APP / "services" / "evidence_catalogue.py"
    summary = APP / "agents" / "summary_agent.py"
    assert iv.WRAP_LIMITS["test_name"] == _source_int(catalogue, r'_text\(analysis\.get\("test_name"\),\s*(\d+)\)')
    assert iv.WRAP_LIMITS["excerpt"] == _source_int(catalogue, r"^_EXCERPT_LIMIT\s*=\s*(\d+)\s*$")
    assert iv.WRAP_LIMITS["root_cause"] == _source_int(summary, r"_usable_root_cause\(analysis\)\[:(\d+)\]")
    assert iv.EXCERPTS_PER_FAILURE == _source_int(catalogue, r"^PER_TEST_LIMIT\s*=\s*(\d+)\s*$")
    # No downstream cap exists for the suite name: it is wrapped to the test name's.
    assert iv.WRAP_LIMITS["suite_name"] == iv.WRAP_LIMITS["test_name"]
    assert set(iv.WRAP_LIMITS) == {"test_name", "suite_name", "root_cause", "excerpt"}
    # Every wrapped value must still hold both markers and at least one character of content.
    assert min(iv.WRAP_LIMITS.values()) > len(iv.UNTRUSTED_OPEN) + len(iv.UNTRUSTED_CLOSE)


@pytest.mark.parametrize("module", [rv, iv])
def test_the_vocabularies_import_no_application_code(module):
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    imported = [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names]
    imported += [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    assert not [name for name in imported if name == "app" or name.startswith("app.")], imported
