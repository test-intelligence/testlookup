"""Run-context collection for the TestLookup Python SDK (W23b FAIL-13).

A run context tells TestLookup WHAT a run ran on, so the run page's Changes tab
can say "poetry.lock changed · python 3.11.8 → 3.11.9" against the baseline
build. It is computed locally and sent beside the results as ``run_context``::

    {"v": 1,
     "runtime": {"python": "3.11.9", "implementation": "cpython", "os": "linux-x86_64",
                 "testlookup-reporter": "1.2.0", "pytest": "8.3.2"},
     "dependency_fingerprints": {"poetry.lock": "<sha256 of the raw bytes>"},
     "image_digests": {"app": "sha256:<64 hex>"},
     "env": {"TZ": "UTC", "LANG": "C.UTF-8"}}

Nothing is fetched over the network: the runtime comes from the interpreter,
the fingerprints from the lockfiles named in ``LOCKFILE_NAMES`` directly in ONE
root directory (no walk, no glob; regular files of at most 50 MB, hashed as raw
bytes — a CRLF checkout hashes differently from an LF one, by design), image
digests only from ``TESTLOOKUP_IMAGE_DIGESTS`` / the caller, and env values only
for the fixed 12-key ``ENV_ALLOWLIST``.

Secrets are never sent: an env value shaped like a token (``ghp_…``, ``glpat-…``,
``xoxb-…``, ``AKIA…``, any unbroken 40+ character run) is replaced WHOLE by
``[REDACTED]``, and ``scheme://user:password@`` loses its password. The server
re-applies both rules plus its own redactor before anything is stored.

The SDK never sends what the server would refuse (an over-long env value is
truncated to 200 characters, an extra entry is dropped, the whole object fits in
8 192 bytes) and never fails a test run because of metadata: every error here
yields ``None`` (debug-logged) and the field is simply omitted.

The allowlists, caps and patterns below are the server's
(``backend/app/services/run_context_vocab.py``) and the shared test vectors'
(``sdk-vectors/run_context.json``); ``cli/testlookup_cli/run_context.py`` is a
deliberate copy WITHOUT interpreter detection (the CLI's Python is not the
tests' runtime). Keep all of them in sync.

Controls (highest wins): the explicit argument > the environment variable >
the config file (``testlookup.run_context`` / ``testlookup.run_context_root``)
> the default.

  TESTLOOKUP_RUN_CONTEXT       ``0`` / ``false`` / ``no`` turns collection off
  TESTLOOKUP_RUN_CONTEXT_ROOT  the directory whose lockfiles are hashed (default: cwd)
  TESTLOOKUP_RUNTIME           ``java=21.0.2,node=20.11.1`` — beats detection per key
  TESTLOOKUP_IMAGE_DIGESTS     ``app=sha256:<64 hex>,db=sha256:<64 hex>``
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import re
import stat
import sys
from typing import Any, Mapping, Optional

__all__ = [
    "allowlisted_env",
    "collect_run_context",
    "dependency_fingerprints",
    "detect_runtime",
    "is_collection_enabled",
    "locates_run_context",
    "redact_env_value",
    "resolve_root",
]

logger = logging.getLogger("testlookup_reporter.run_context")

# ── The server's vocabulary (run_context_vocab.py; sdk-vectors/run_context.json) ──
CONTEXT_VERSION = 1
CONTEXT_MAX_BYTES = 8192
RUNTIME_MAX = 16
RUNTIME_KEY_RE = re.compile(r"^[a-z][a-z0-9._-]{0,31}$")
VERSION_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z.+_~-]{0,63}$")
LOCKFILE_NAMES = (
    "poetry.lock", "Pipfile.lock", "uv.lock", "pdm.lock", "requirements.txt", "requirements-dev.txt",
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "npm-shrinkwrap.json",
    "pom.xml", "build.gradle", "build.gradle.kts", "gradle.lockfile",
    "go.sum", "Cargo.lock", "Gemfile.lock", "composer.lock",
)
IMAGES_MAX = 8
IMAGE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._/-]{0,199}(:[A-Za-z0-9._-]{1,128})?$")
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
ENV_ALLOWLIST = (
    "TZ", "LANG", "LC_ALL", "PYTHONHASHSEED", "NODE_ENV",
    "ImageOS", "ImageVersion", "RUNNER_OS", "RUNNER_ARCH",
    "CI_RUNNER_VERSION", "CI_SERVER_VERSION",
    "BUILD_IMAGE",
)
ENV_VALUE_MAX = 200
TOKEN_SHAPES = (
    r"gh[pousr]_[A-Za-z0-9]{20,}", r"github_pat_[A-Za-z0-9_]{20,}", r"glpat-[A-Za-z0-9_-]{16,}",
    r"xox[abprs]-[A-Za-z0-9-]{10,}", r"sk-[A-Za-z0-9_-]{20,}", r"AKIA[A-Z0-9]{16}",
    r"[A-Za-z0-9+/=_-]{40,}",
)
USERINFO_RE = re.compile(r"(://[^:/@\s]+:)[^@\s]+@")
REDACTED = "[REDACTED]"

# ── The SDK's own limits ──
LOCKFILE_MAX_BYTES = 50 * 1024 * 1024
_CHUNK = 64 * 1024
_TOKEN_RES = tuple(re.compile(shape) for shape in TOKEN_SHAPES)
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_NOT_VERSION_CHAR = re.compile(r"[^0-9A-Za-z.+_~-]")
_FALSEY = frozenset({"0", "false", "no", "off"})
_TRUTHY = frozenset({"1", "true", "yes", "on"})

ENV_ENABLED = "TESTLOOKUP_RUN_CONTEXT"
ENV_ROOT = "TESTLOOKUP_RUN_CONTEXT_ROOT"
ENV_RUNTIME = "TESTLOOKUP_RUNTIME"
ENV_IMAGE_DIGESTS = "TESTLOOKUP_IMAGE_DIGESTS"
# Config-file keys (ConfigLoader's "ci" section) read from ``overrides``.
CONFIG_ENABLED_KEY = "collect_run_context"
CONFIG_ROOT_KEY = "run_context_root"
CONFIG_RUNTIME_KEY = "runtime"
CONFIG_IMAGES_KEY = "image_digests"


# ── Small pure helpers ────────────────────────────────────────────────────────

def _flag(value: Any) -> Optional[bool]:
    """Parse a tri-state boolean: True / False / None (unset or unparseable)."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in _TRUTHY:
        return True
    if text in _FALSEY:
        return False
    return None


def _token_shaped(value: str) -> bool:
    return any(pattern.search(value) for pattern in _TOKEN_RES)


def redact_env_value(value: str) -> str:
    """One allowlisted env value as the SDK sends it.

    Control characters removed; a token-shaped value replaced WHOLE by
    ``[REDACTED]``; ``://user:password@`` -> ``://user:[REDACTED]@``; then
    truncated to 200 characters (checked again, so a cut can never leave a
    token-shaped tail).
    """
    text = _CONTROL_RE.sub("", str(value))
    if _token_shaped(text):
        return REDACTED
    text = USERINFO_RE.sub(r"\1" + REDACTED + "@", text)[:ENV_VALUE_MAX]
    if _token_shaped(text):
        return REDACTED
    return text


def _pairs(value: Any) -> list[tuple[str, str]]:
    """``"a=1,b=2"``, ``["a=1", "b=2"]`` or a mapping -> ``[("a", "1"), ("b", "2")]``
    (blank / malformed entries skipped)."""
    if value is None:
        return []
    if isinstance(value, Mapping):
        return [(str(k).strip(), str(v).strip()) for k, v in value.items() if v is not None]
    out: list[tuple[str, str]] = []
    items = value if isinstance(value, (list, tuple)) else str(value).split(",")
    for item in items:
        key, sep, val = str(item).partition("=")
        if sep and key.strip() and val.strip():
            out.append((key.strip(), val.strip()))
    return out


def _valid_runtime(key: str, version: str) -> bool:
    return bool(RUNTIME_KEY_RE.match(key) and VERSION_RE.match(version))


def _valid_image(name: str, digest: str) -> bool:
    return bool(IMAGE_NAME_RE.match(name) and DIGEST_RE.match(digest))


def _version_text(text: str) -> Optional[str]:
    """Coerce a detected string into ``VERSION_RE`` (characters outside it -> ``-``)."""
    cleaned = _NOT_VERSION_CHAR.sub("-", str(text).strip().lower()).lstrip(".+_~-")[:64]
    return cleaned if cleaned and VERSION_RE.match(cleaned) else None


def detect_runtime() -> dict[str, str]:
    """The interpreter this process runs: python, implementation, os, and -- when
    already imported (never imported for this) -- the reporter and pytest versions."""
    candidates = {
        "python": platform.python_version(),
        "implementation": platform.python_implementation().lower(),
        "os": f"{platform.system()}-{platform.machine()}",
    }
    reporter = sys.modules.get("testlookup_reporter")
    if reporter is not None and getattr(reporter, "__version__", None):
        candidates["testlookup-reporter"] = str(reporter.__version__)
    pytest_module = sys.modules.get("pytest")
    if pytest_module is not None and getattr(pytest_module, "__version__", None):
        candidates["pytest"] = str(pytest_module.__version__)
    found: dict[str, str] = {}
    for key, raw in candidates.items():
        version = _version_text(raw)
        if version:
            found[key] = version
    return found


def locates_run_context(body: Any) -> bool:
    """Whether a 422 body blames ``run_context`` (so a retry without it can succeed).

    FastAPI's list form (``detail[*].loc == ["body", "run_context", ...]``, or
    ``["body", "meta", "run_context", ...]`` on ``/stream/ingest``) and the
    ``/ingest/file`` form (``detail.code`` = ``bad_json`` / ``invalid_run_context``).
    """
    if not isinstance(body, Mapping):
        return False
    detail = body.get("detail")
    if isinstance(detail, Mapping):
        return detail.get("code") in ("bad_json", "invalid_run_context")
    if not isinstance(detail, list):
        return False
    for item in detail:
        loc = item.get("loc") if isinstance(item, Mapping) else None
        if not isinstance(loc, (list, tuple)) or len(loc) < 2:
            continue
        if loc[1] == "run_context" or (loc[1] == "meta" and len(loc) > 2 and loc[2] == "run_context"):
            return True
    return False


# ── The four sections ─────────────────────────────────────────────────────────

def _hash_file(path: str) -> Optional[str]:
    """sha256 of a REGULAR file's raw bytes (<= 50 MB), else None. Symlinks are skipped."""
    try:
        info = os.lstat(path)
    except OSError:
        return None
    if not stat.S_ISREG(info.st_mode) or info.st_size > LOCKFILE_MAX_BYTES:
        return None
    digest = hashlib.sha256()
    read = 0
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(_CHUNK)
            if not chunk:
                break
            read += len(chunk)
            if read > LOCKFILE_MAX_BYTES:  # grew after the stat
                return None
            digest.update(chunk)
    return digest.hexdigest()


def dependency_fingerprints(root: str) -> dict[str, str]:
    """``{lockfile name: sha256}`` for the ``LOCKFILE_NAMES`` directly in ``root``."""
    if not root or not os.path.isdir(root):
        return {}
    found: dict[str, str] = {}
    for name in LOCKFILE_NAMES:
        try:
            digest = _hash_file(os.path.join(root, name))
        except OSError:
            digest = None
        if digest:
            found[name] = digest
    return found


def _merge_runtime(*layers: Any) -> dict[str, str]:
    """Layers highest-precedence FIRST; invalid entries dropped; at most ``RUNTIME_MAX``."""
    merged: dict[str, str] = {}
    for layer in layers:
        for key, version in _pairs(layer):
            if key in merged or not _valid_runtime(key, version):
                continue
            if len(merged) >= RUNTIME_MAX:
                return merged
            merged[key] = version
    return merged


def _merge_images(*layers: Any) -> dict[str, str]:
    """The first non-empty layer wins (no per-entry mixing); invalid entries dropped."""
    for layer in layers:
        images: dict[str, str] = {}
        for name, digest in _pairs(layer):
            if name in images or not _valid_image(name, digest):
                continue
            if len(images) >= IMAGES_MAX:
                break
            images[name] = digest
        if images:
            return images
    return {}


def allowlisted_env(environ: Mapping[str, str]) -> dict[str, str]:
    """Each ``ENV_ALLOWLIST`` key present in ``environ`` (exact case), redacted."""
    found: dict[str, str] = {}
    for key in ENV_ALLOWLIST:
        value = environ.get(key)
        if value is None:
            continue
        cleaned = redact_env_value(value)
        if cleaned:
            found[key] = cleaned
    return found


def _size(ctx: Mapping[str, Any]) -> int:
    return len(json.dumps(ctx, separators=(",", ":")).encode("utf-8"))


def _fit(ctx: dict[str, Any], keep_runtime: tuple[str, ...]) -> dict[str, Any]:
    """Drop env, then image_digests, then runtime extras until the object fits."""
    for section in ("env", "image_digests"):
        if _size(ctx) <= CONTEXT_MAX_BYTES:
            return ctx
        ctx.pop(section, None)
    runtime = ctx.get("runtime") or {}
    for key in [k for k in reversed(list(runtime)) if k not in keep_runtime]:
        if _size(ctx) <= CONTEXT_MAX_BYTES:
            break
        runtime.pop(key, None)
    return ctx


# ── Public API ────────────────────────────────────────────────────────────────

def is_collection_enabled(
    env: Optional[Mapping[str, str]] = None,
    *,
    overrides: Optional[Mapping[str, Any]] = None,
    explicit: Optional[bool] = None,
) -> bool:
    """explicit > ``TESTLOOKUP_RUN_CONTEXT`` > ``testlookup.run_context`` > on."""
    if explicit is not None:
        return bool(explicit)
    environ: Mapping[str, str] = os.environ if env is None else env
    flag = _flag(environ.get(ENV_ENABLED))
    if flag is None and overrides:
        flag = _flag(overrides.get(CONFIG_ENABLED_KEY))
    return True if flag is None else flag


def resolve_root(
    root: Optional[str] = None,
    env: Optional[Mapping[str, str]] = None,
    *,
    overrides: Optional[Mapping[str, Any]] = None,
) -> str:
    """explicit ``root`` > ``TESTLOOKUP_RUN_CONTEXT_ROOT`` > ``testlookup.run_context_root`` > cwd."""
    environ: Mapping[str, str] = os.environ if env is None else env
    for candidate in (root, environ.get(ENV_ROOT), (overrides or {}).get(CONFIG_ROOT_KEY)):
        if candidate is not None and str(candidate).strip():
            return str(candidate).strip()
    return os.getcwd()


def collect_run_context(
    *,
    root: Optional[str] = None,
    enabled: Optional[bool] = None,
    overrides: Optional[Mapping[str, Any]] = None,
    runtime: Any = None,
    image_digests: Any = None,
    env: Optional[Mapping[str, str]] = None,
) -> Optional[dict[str, Any]]:
    """Build this run's ``run_context``, or None to send nothing. Never raises.

    ``root`` / ``enabled`` / ``runtime`` / ``image_digests`` are the caller's
    explicit values (``runtime`` and ``image_digests`` as a mapping or as
    ``"k=v,k=v"``); ``overrides`` is the config file's ``ci`` section
    (``collect_run_context``, ``run_context_root``, ``runtime``, ``image_digests``);
    ``env`` is the process environment to read (default ``os.environ``).
    """
    try:
        environ: Mapping[str, str] = os.environ if env is None else env
        cfg: Mapping[str, Any] = overrides or {}
        if not is_collection_enabled(environ, overrides=cfg, explicit=enabled):
            return None
        detected = detect_runtime()
        ctx: dict[str, Any] = {"v": CONTEXT_VERSION}
        runtime_map = _merge_runtime(
            runtime, environ.get(ENV_RUNTIME), cfg.get(CONFIG_RUNTIME_KEY), detected,
        )
        if runtime_map:
            ctx["runtime"] = runtime_map
        fingerprints = dependency_fingerprints(resolve_root(root, environ, overrides=cfg))
        if fingerprints:
            ctx["dependency_fingerprints"] = fingerprints
        images = _merge_images(
            image_digests, environ.get(ENV_IMAGE_DIGESTS), cfg.get(CONFIG_IMAGES_KEY),
        )
        if images:
            ctx["image_digests"] = images
        env_values = allowlisted_env(environ)
        if env_values:
            ctx["env"] = env_values
        if len(ctx) == 1:
            return None
        return _fit(ctx, keep_runtime=tuple(detected))
    except Exception as exc:  # never fail a test run because of metadata
        logger.debug("run context not collected: %s", type(exc).__name__)
        return None
