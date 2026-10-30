"""The untrusted-input boundary (W23c AGT-11, contract §2.2 I-6): the ONE path inline text takes to a model.

An inline agent input (``POST /api/v1/agents/{agent_id}/invoke`` with ``inline_input``) is
attacker-controlled text. Prompt TEXT may not change (the protected-prompt rule: no template,
no ``prompt_registry`` entry, no manifest), so the boundary is applied to the VALUES, once,
before they enter the graph state that the unchanged summary stage renders:

* :func:`scrub` removes secrets and personal data through the existing redaction helpers
  (``privacy_service.sanitize_for_llm`` → ``redaction_service.redact_text``) plus W23b's
  whole-token rule (``run_context_vocab.TOKEN_SHAPES``, imported, not copied). Its output is what
  is stored (``agent_invocations.input_payload``) and hashed (``input_sha256``).
* :func:`wrap_untrusted` / :func:`excerpt_slices` put a scrubbed, neutralised value between
  ``UNTRUSTED_OPEN`` and ``UNTRUSTED_CLOSE``, cut so the WRAPPED length equals the cap the
  unchanged downstream code applies (``inline_input_vocab.WRAP_LIMITS``): no later cut can land
  on a marker.

Why a value can never forge, close or escape a block:

1. ``<<`` / ``>>`` inside a value become ``‹‹`` / ``››``; invisible characters (C0 / C1
   controls except ``\\n`` ``\\t``, and the Unicode format characters — zero-width spaces,
   joiners, bidi controls, the BOM) are dropped first, so they cannot hide a marker either.
2. The redaction the model wrapper and ``_build_context`` re-apply to the WHOLE prompt cannot
   reach across a marker. Its patterns with an unbounded tail — ``Authorization:`` and
   ``Cookie:`` (to the end of the line), ``token`` / ``password`` / ``api_key`` + ``=`` or ``:``
   (to the next blank), and the connection-string rule (``://…:`` up to the next ``@``) — would
   otherwise start inside one value and end inside, or after, another. :func:`scrub` therefore
   replaces every remaining head of the first three with ``[REDACTED]`` (a fixed point), and the
   neutraliser turns ``@`` into the full-width ``＠`` so the connection-string rule finds no
   terminator in any value.

Pure: no ``app.*`` import beyond the redaction helpers and the vocabularies, no session, no I/O,
no logging of content. A W23d slice (AGT-15) may reuse ``scrub`` / ``wrap_untrusted`` rather
than invent a second boundary.
"""
from __future__ import annotations

import re
import unicodedata

from app.services.inline_input_vocab import UNTRUSTED_CLOSE, UNTRUSTED_OPEN
from app.services.privacy_service import sanitize_for_llm
from app.services.redaction_service import REDACTED, redact_text
from app.services.run_context_vocab import TOKEN_SHAPES

#: The two markers' combined length: a wrapped value of ``limit`` characters carries at most
#: ``limit - WRAP_OVERHEAD`` characters of content.
WRAP_OVERHEAD = len(UNTRUSTED_OPEN) + len(UNTRUSTED_CLOSE)

# W23b's provider-token shapes, the specific ones first (the generic 40+ run last), each matched
# run replaced WHOLE by "[REDACTED]".
_TOKEN_SHAPE_RES = tuple(re.compile(shape) for shape in TOKEN_SHAPES)

# The heads of the redaction rules whose tail is not bounded by a delimiter character (see the
# module docstring, point 2). Each match runs to where the redaction rule's own tail would stop
# inside THIS value, and is replaced whole, so no head survives to pair with the next value.
_UNBOUNDED_HEADS: tuple[re.Pattern[str], ...] = (
    re.compile(r"Authorization(?:\\?[\"'])?[ \t]*[:=][^\r\n]*", re.IGNORECASE),
    re.compile(r"(?:Set-)?Cookie:\s*[^\r\n]*", re.IGNORECASE),
    re.compile(r"(?:api[_-]?key|token|password)\s*[:=]\s*['\"]?[^\s'\",]*['\"]?", re.IGNORECASE),
)

# C0 controls except \t and \n, DEL, and the C1 block.
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def _replace_token_shapes(text: str) -> str:
    for shape in _TOKEN_SHAPE_RES:
        text = shape.sub(REDACTED, text)
    return text


def scrub(text: str) -> str:
    """Secrets and personal data out of one inline string — what is stored, hashed and seeded.

    (a token pass) → ``sanitize_for_llm`` → ``redact_text`` → every ``TOKEN_SHAPES`` run →
    ``[REDACTED]`` → no unbounded redaction head left. Idempotent. The result can be LONGER than the input (a
    ``[REDACTED_EMAIL]`` placeholder is longer than ``a@b.io``): callers that enforce a length
    cap clip after scrubbing (``inline_invocation.prepare``).
    """
    if not text:
        return ""
    # The token pass runs before the generic rules too: a phone-number rule that rewrites the
    # digits inside "xoxb-1234567890-…" first would leave the rest of the token behind.
    result = _replace_token_shapes(text)
    result = _replace_token_shapes(redact_text(sanitize_for_llm(result)))
    # A replacement never creates a new head ("[" and "]" break every keyword), but loop to a
    # fixed point anyway: the property the boundary relies on is "no head remains".
    for _ in range(8):
        before = result
        for head in _UNBOUNDED_HEADS:
            result = head.sub(REDACTED, result)
        if result == before:
            break
    return result


def neutralise(text: str) -> str:
    """Make a scrubbed value unable to open, close or reach across a data block.

    Invisible characters are dropped FIRST (so ``<`` + zero-width space + ``<`` cannot survive
    as a marker), then ``<<`` → ``‹‹``, ``>>`` → ``››`` and ``@`` → ``＠``.
    """
    if not text:
        return ""
    visible = "".join(ch for ch in text if unicodedata.category(ch) != "Cf")
    visible = _CONTROL_CHARS.sub("", visible)
    return visible.replace("<<", "‹‹").replace(">>", "››").replace("@", "＠")


def _check_limit(limit: int) -> int:
    width = int(limit) - WRAP_OVERHEAD
    if width < 0:
        raise ValueError(f"limit must be at least {WRAP_OVERHEAD} (the two markers)")
    return width


def wrap_untrusted(text: str, *, limit: int) -> str:
    """``UNTRUSTED_OPEN + neutralise(scrub(text))[: limit - 17] + UNTRUSTED_CLOSE``; ``len <= limit``.

    The content is scrubbed and neutralised BEFORE the cut, so a token is never half-kept and
    the cut can only shorten the content — it can never produce a marker.
    """
    width = _check_limit(limit)
    body = neutralise(scrub(text or ""))[:width]
    return f"{UNTRUSTED_OPEN}{body}{UNTRUSTED_CLOSE}"


def excerpt_slices(text: str, *, limit: int, parts: int) -> list[str]:
    """A log excerpt as at most ``parts`` wrapped slices, each ``len <= limit``.

    Scrubbed and neutralised ONCE over the whole excerpt (a token spanning a slice boundary is
    still recognised), then cut into consecutive slices; blank slices are skipped and an empty
    excerpt gives ``[]``.
    """
    width = _check_limit(limit)
    if width == 0 or parts <= 0:
        return []
    body = neutralise(scrub(text or "")).strip()
    slices: list[str] = []
    for start in range(0, len(body), width):
        if len(slices) >= parts:
            break
        chunk = body[start:start + width]
        if chunk.strip():
            slices.append(f"{UNTRUSTED_OPEN}{chunk}{UNTRUSTED_CLOSE}")
    return slices


__all__ = ["WRAP_OVERHEAD", "excerpt_slices", "neutralise", "scrub", "wrap_untrusted"]
