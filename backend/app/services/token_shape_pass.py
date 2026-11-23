"""The server-side token-shape pass that keeps digests and paths (D-12 / S-C-1 / S-C-2).

``run_context_vocab.TOKEN_SHAPES`` ends with a generic rule, ``[A-Za-z0-9+/=_-]{40,}``: any unbroken
40+ character run is read as a key. That rule is the SDKs' contract (the parity vectors pin it) and is
used as is where the SDKs use it (``http_exchange_redaction.text_pass``). Applied to stored history and
to model input it also destroyed data that is not secret:

* a digest with its algorithm prefix (``sha256:<64 hex>``, an image digest) or a Subresource-Integrity
  value (``sha512-<base64>``, a lockfile hash);
* a file path whose characters before the first dot run past 40 (``backend/app/services/commit_attri…``),
  because the class includes ``/``.

:func:`token_pass` applies the provider-specific shapes to the whole text exactly as before, then the
generic rule with two carve-outs: a run that IS a prefixed digest / SRI value of the algorithm's exact
length is kept, and a run that reads as a path (``/``-separated segments that each look like a word, a
snake / kebab name or a CamelCase name) gets the generic rule per segment, as
``http_exchange_redaction._path_token_pass`` does for URL paths. Everything else is ``[REDACTED]`` whole.

Deliberately NOT exempt: a bare hex run (no algorithm prefix). ``openssl rand -hex 32`` secrets and
legacy 40-hex OAuth tokens have that shape; callers that know a field holds a digest (a commit SHA under
``commits[].sha``) exempt it by key, never by shape alone. A segment mixing upper case with digits, or
upper case past a third of its letters, is not a word: random standard base64 (an AWS secret key holds
``/``) fails the path test and stays redacted whole.

PURE: no I/O, no logging. Idempotent: ``token_pass(token_pass(x)) == token_pass(x)``.
"""
from __future__ import annotations

import re

from app.services.run_context_vocab import REDACTED, TOKEN_SHAPES

#: The generic rule of ``TOKEN_SHAPES`` (checked at import: a vocabulary change must be looked at here).
GENERIC_SHAPE = r"[A-Za-z0-9+/=_-]{40,}"
if GENERIC_SHAPE not in TOKEN_SHAPES:  # pragma: no cover - a vocabulary change
    raise RuntimeError("run_context_vocab.TOKEN_SHAPES no longer carries the generic rule token_shape_pass expects")

_SPECIFIC_RES = tuple(re.compile(shape) for shape in TOKEN_SHAPES if shape != GENERIC_SHAPE)
_GENERIC_RE = re.compile(GENERIC_SHAPE)

#: ``<algorithm>:<hex>``: the hex length each algorithm produces (lower-case hex, as ``DIGEST_RE``).
_HEX_DIGEST_LENGTHS = {"sha1": 40, "sha224": 56, "sha256": 64, "sha384": 96, "sha512": 128}
_DIGEST_PREFIX = re.compile(r"(?<![A-Za-z0-9])(sha1|sha224|sha256|sha384|sha512):\Z")
_LOWER_HEX = re.compile(r"[0-9a-f]+")
#: Subresource Integrity (``package-lock.json`` ``integrity``): ``sha<N>-`` + base64 of the exact digest size.
_SRI = re.compile(r"sha256-[A-Za-z0-9+/]{43}=|sha384-[A-Za-z0-9+/]{64}|sha512-[A-Za-z0-9+/]{86}==")
#: A path segment never carries these (they are base64 padding / alphabet, not file-name characters).
_NOT_PATH_CHARS = frozenset("+=")
_UPPER_RUN = re.compile(r"[A-Z]{2,}")


def _is_prefixed_digest(match: re.Match[str]) -> bool:
    run = match.group(0)
    head = match.string[max(0, match.start() - 8): match.start()]
    prefix = _DIGEST_PREFIX.search(head)
    return (
        prefix is not None
        and len(run) == _HEX_DIGEST_LENGTHS[prefix.group(1)]
        and _LOWER_HEX.fullmatch(run) is not None
    )


def _segment_is_word(segment: str) -> bool:
    """A path segment a person or a build tool named: lower case / digits / ``_`` / ``-`` (snake, kebab,
    a version, a uuid), a CamelCase name without digits, or a short upper-case name (``META-INF``)."""
    upper = sum(1 for ch in segment if ch.isupper())
    lower = sum(1 for ch in segment if ch.islower())
    digit = any(ch.isdigit() for ch in segment)
    if not upper:
        return True
    if digit:
        return False
    if lower:
        # CamelCase: upper case at most a third of the letters, and at most ONE acronym run (``HTTPClient``).
        return upper * 3 <= upper + lower and len(_UPPER_RUN.findall(segment)) <= 1
    return len(segment) <= 12


def is_path_like(run: str) -> bool:
    """A ``/``-separated run of at least two non-empty segments, each :func:`_segment_is_word`."""
    if "/" not in run or _NOT_PATH_CHARS.intersection(run):
        return False
    segments = [segment for segment in run.split("/") if segment]
    return len(segments) >= 2 and all(_segment_is_word(segment) for segment in segments)


def _generic(match: re.Match[str]) -> str:
    run = match.group(0)
    if _is_prefixed_digest(match) or _SRI.fullmatch(run):
        return run
    if is_path_like(run):
        return "/".join(_GENERIC_RE.sub(REDACTED, segment) for segment in run.split("/"))
    return REDACTED


def token_pass(text: str) -> str:
    """Every provider-specific ``TOKEN_SHAPES`` run → ``[REDACTED]``, then the generic 40+ rule with the
    digest and path carve-outs (module docstring). Idempotent."""
    if not text:
        return text
    for shape in _SPECIFIC_RES:
        text = shape.sub(REDACTED, text)
    return _GENERIC_RE.sub(_generic, text)
