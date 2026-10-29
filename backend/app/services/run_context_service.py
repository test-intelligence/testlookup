"""Run context (FAIL-13): redaction and staging of a run's runtime / dependency context.

A run context is ``{v, runtime{}, dependency_fingerprints{}, image_digests{}, env{}}``
as accepted by ``schemas.RunContext``: the interpreter and tool versions a run used,
the sha256 of its lockfiles, its image digests and a fixed allowlist of environment
values. It arrives with an API key, so it is attacker-controlled input:

* the SHAPE rules (the size cap, the per-section caps, the lockfile / env allowlists,
  the value patterns) live on ``schemas.RunContext`` and refuse with a 422 whose
  sentence is one of ``run_context_vocab.RUN_CONTEXT_ERRORS``;
* the free-text env VALUES are redacted here (``redact_env_value``), called by the
  model itself, so every validated copy -- object storage, a Celery kwarg,
  ``LiveSession.extra_metadata`` and the ``test_runs.run_context`` column -- holds the
  redacted form only;
* ``validated_or_none`` re-validates every stored copy before it reaches the column
  (defence in depth: an ``extra_metadata`` written by a path that skipped the schema).

Not to be confused with the analysis prompt variable ``{run_context}``: the column is
never a prompt, agent or LLM input. Stage-only: nothing here commits. A log line
carries ids and counts, never a context value.
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional

import structlog

from app.services.redaction_service import redact_text
from app.services import run_context_vocab
from app.services.run_context_vocab import REDACTED, RUN_CONTEXT_ERRORS, TOKEN_SHAPES

_slog = structlog.get_logger(__name__)

#: ``scheme://user:password@host`` -> ``scheme://user:[REDACTED]@host`` (the SDK's rule,
#: re-applied here: ``redact_text``'s own connection-string rule wants 4+ characters).
USERINFO_RE = re.compile(run_context_vocab.USERINFO_RE)
_TOKEN_SHAPE_RES = tuple(re.compile(shape) for shape in TOKEN_SHAPES)
_VALUE_ERROR_PREFIX = "Value error, "

#: The ``/ingest/file`` refusal codes (the JSON routes answer FastAPI's own 422).
BAD_JSON = "bad_json"
INVALID_RUN_CONTEXT = "invalid_run_context"


class RunContextRefused(ValueError):
    """A ``run_context`` form value the server refuses (rendered as a 422 by the router)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    def detail(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


def _token_shaped(value: str) -> bool:
    return any(pattern.search(value) for pattern in _TOKEN_SHAPE_RES)


def redact_env_value(value: str) -> str:
    """Redact one allowlisted env VALUE before it is stored anywhere.

    A value shaped like a provider token (``ghp_…``, ``glpat-…``, ``xoxb-…``,
    ``AKIA…``, any unbroken 40+ character run) is replaced WHOLE by ``[REDACTED]`` --
    checked on the value as sent and again after the other rules. Otherwise the
    URL-userinfo rule and ``redaction_service.redact_text`` (credentials, JWTs,
    e-mails, phone / card / IPv4 shapes) rewrite the secret part in place.
    Idempotent: a redacted value redacts to itself.
    """
    if not isinstance(value, str) or not value:
        return value
    if _token_shaped(value):
        return REDACTED
    result = redact_text(USERINFO_RE.sub(r"\1" + REDACTED + "@", value))
    if _token_shaped(result):
        return REDACTED
    return result


def refusal_sentence(exc: Exception) -> str:
    """The first validation error's sentence, without Pydantic's ``Value error, `` prefix."""
    errors = getattr(exc, "errors", None)
    message = ""
    if callable(errors):
        try:
            first = (errors() or [{}])[0]
            message = str(first.get("msg") or "")
        except Exception:  # pragma: no cover - a malformed error list
            message = ""
    if not message:
        message = str(exc)
    if message.startswith(_VALUE_ERROR_PREFIX):
        message = message[len(_VALUE_ERROR_PREFIX):]
    return message


def _model() -> Any:
    from app.models.schemas import RunContext  # noqa: PLC0415 - schemas imports this module lazily

    return RunContext


def dump_context(ctx: Any) -> Optional[dict]:
    """A validated ``RunContext`` (or None) as the stored dict: ``exclude_none``."""
    if ctx is None:
        return None
    if isinstance(ctx, dict):
        return validated_or_none(ctx)
    dump = ctx.model_dump(exclude_none=True)
    return dump if isinstance(dump, dict) else None


def validated_or_none(raw: Any, *, run_id: Any = None) -> Optional[dict]:
    """``RunContext.model_validate(raw).model_dump(exclude_none=True)``, or None.

    Never raises: a value that fails validation is dropped and logged as
    ``run_context_dropped`` with the run id and the error count -- never the value.
    """
    if raw is None:
        return None
    try:
        ctx = _model().model_validate(raw)
    except Exception as exc:
        errors = getattr(exc, "errors", None)
        try:
            count = len(errors()) if callable(errors) else 1
        except Exception:  # pragma: no cover - a malformed error list
            count = 1
        _slog.warning(
            "run_context_dropped",
            run_id=str(run_id) if run_id is not None else None,
            error_count=count,
            error_type=type(exc).__name__,
        )
        return None
    dump = ctx.model_dump(exclude_none=True)
    return dump if isinstance(dump, dict) else None


def parse_form_run_context(raw: Optional[str]) -> Optional[dict]:
    """The ``/ingest/file`` form field: JSON text -> the validated, redacted dict.

    Raises ``RunContextRefused`` (``bad_json`` / ``invalid_run_context``) with the
    fixed sentence; the router renders it as a 422 ``{"code", "message"}`` BEFORE
    any storage write or Celery kwarg, so neither ever sees a raw value.
    """
    # Not a string = not sent: a direct handler call (tests, scripts) receives
    # FastAPI's truthy ``Form(None)`` default object, never a client value.
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        raise RunContextRefused(BAD_JSON, RUN_CONTEXT_ERRORS["bad_json"]) from None
    if not isinstance(parsed, dict):
        raise RunContextRefused(BAD_JSON, RUN_CONTEXT_ERRORS["bad_json"])
    try:
        ctx = _model().model_validate(parsed)
    except ValueError as exc:  # pydantic.ValidationError is a ValueError
        raise RunContextRefused(INVALID_RUN_CONTEXT, refusal_sentence(exc)) from None
    dump = ctx.model_dump(exclude_none=True)
    return dump if isinstance(dump, dict) else None


def stamp_run_context(run: Any, ctx: Optional[dict]) -> bool:
    """Fill-if-null: set ``run.run_context`` when it is empty. True when it set it.

    A stored context is never overwritten (a retried or resumed ingest of the same
    run keeps the context its first delivery recorded). Staged only: no flush.
    """
    if not isinstance(ctx, dict) or not ctx:
        return False
    if getattr(run, "run_context", None) is not None:
        return False
    run.run_context = ctx
    return True


def fold_run_context(metadata: Optional[dict], payload: Any) -> dict:
    """``extra_metadata`` without any caller-sent ``"run_context"``, plus the typed field.

    A ``metadata["run_context"]`` sent through the free ``metadata`` dict is never
    trusted -- it is DROPPED (the opposite of the CI keys' "explicit metadata wins",
    deliberately: only the typed ``run_context`` field was validated and redacted).
    ``payload`` is a ``LiveSessionCreate`` / ``LiveStreamMeta`` (or None). When there
    is nothing to drop and nothing to add, ``metadata`` itself is returned.
    """
    source = metadata if metadata is not None else {}
    ctx = dump_context(getattr(payload, "run_context", None)) if payload is not None else None
    if "run_context" not in source and not ctx:
        return source
    folded = {key: value for key, value in source.items() if key != "run_context"}
    if ctx:
        folded["run_context"] = ctx
    return folded
