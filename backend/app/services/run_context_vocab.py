"""The run-context and configuration-drift vocabulary (W23b FAIL-13), in ONE place.

A reporter may send a ``run_context`` beside its results: the runtime versions,
the sha256 of the lockfiles at its root, image digests and a FIXED allowlist of
environment variables (D05). It is attacker-controlled input from an API key,
so every key is allowlisted or pattern-checked, every section and the whole
object are size-capped, and env VALUES are redacted before the object reaches
storage (``schemas.RunContext`` applies these rules; ``run_context_service``
re-applies them on every stored copy). The drift diff
(``run_diff_service.config_drift``) uses the section / change / state names
below; the frontend mirrors them in ``frontend/src/types/configDrift.ts``.

The allowlists, caps and token shapes ALSO live in ``sdk-vectors/run_context.json``
(the cross-language specification the Python SDK, the CLI and — later — the
Java / JS / Go SDKs build against); ``tests/test_bisect_drift_vocab_parity.py``
pins the file equal to this module.

Absence is not health: a run without a context reads "Not recorded by this
reporter", never "no drift".

Constants only: no ``app.*`` import, no session, no I/O.
"""
from __future__ import annotations

CONTEXT_VERSION = 1                          # RunContext.v; anything else is 422
CONTEXT_MAX_BYTES = 8192                     # the whole object, compact JSON, UTF-8 (checked after validation)
RUNTIME_MAX = 16
RUNTIME_KEY_RE = r"^[a-z][a-z0-9._-]{0,31}$"
VERSION_RE = r"^[0-9A-Za-z][0-9A-Za-z.+_~-]{0,63}$"        # "3.11.9", "21.0.2+13", "linux-x86_64", "8.3.2"
LOCKFILE_NAMES = (                           # dependency_fingerprints keys: basenames at the SDK's root ONLY
    "poetry.lock", "Pipfile.lock", "uv.lock", "pdm.lock", "requirements.txt", "requirements-dev.txt",
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "npm-shrinkwrap.json",
    "pom.xml", "build.gradle", "build.gradle.kts", "gradle.lockfile",
    "go.sum", "Cargo.lock", "Gemfile.lock", "composer.lock",
)
SHA256_RE = r"^[0-9a-f]{64}$"                # sha256 of the file's raw bytes, lower-case hex
IMAGES_MAX = 8
IMAGE_NAME_RE = r"^[a-z0-9][a-z0-9._/-]{0,199}(:[A-Za-z0-9._-]{1,128})?$"   # no "@", no credentials
DIGEST_RE = r"^sha256:[0-9a-f]{64}$"
ENV_ALLOWLIST = (                            # the ONLY env keys a context may carry (D05); values are free text
    "TZ", "LANG", "LC_ALL", "PYTHONHASHSEED", "NODE_ENV",
    "ImageOS", "ImageVersion", "RUNNER_OS", "RUNNER_ARCH",      # GitHub-hosted runner image
    "CI_RUNNER_VERSION", "CI_SERVER_VERSION",                   # GitLab
    "BUILD_IMAGE",                                              # a team's own image label
)
ENV_VALUE_MAX = 200                          # characters; no control characters (\x00-\x1f, \x7f)
TOKEN_SHAPES = (                             # an env VALUE matching any of these is stored as "[REDACTED]" whole
    r"gh[pousr]_[A-Za-z0-9]{20,}", r"github_pat_[A-Za-z0-9_]{20,}", r"glpat-[A-Za-z0-9_-]{16,}",
    r"xox[abprs]-[A-Za-z0-9-]{10,}", r"sk-[A-Za-z0-9_-]{20,}", r"AKIA[A-Z0-9]{16}",
    r"[A-Za-z0-9+/=_-]{40,}",                # any unbroken 40+ character run (a key, a hash, a token)
)
USERINFO_RE = r"(://[^:/@\s]+:)[^@\s]+@"     # the SDK's userinfo rule: replaced by r"\1[REDACTED]@"
REDACTED = "[REDACTED]"                      # == redaction_service.REDACTED (parity test)
DRIFT_SECTIONS = ("dependencies", "runtime", "images", "environment", "env", "run")   # display order, stable
DRIFT_CHANGES = ("changed", "added", "removed")
CONTEXT_STATES = ("recorded", "run_missing", "baseline_missing", "both_missing")
DRIFT_ENTRIES_MAX = 60                       # entries returned; entries_total is the full count
DRIFT_HASH_PREFIX = 12                       # dependencies entries show the first 12 hex of each hash
DRIFT_SENTENCES = {
    "run_missing": "Not recorded by this reporter: this run sent no runtime or dependency context.",
    "baseline_missing": (
        "Not recorded for the baseline build {baseline}: its reporter sent no runtime or dependency context."
    ),
    "both_missing": (
        "Not recorded by this reporter: neither this run nor build {baseline} sent a runtime or dependency context."
    ),
    "no_drift": "No configuration drift against build {baseline}.",       # ONLY when context_state == "recorded" and entries == []
    "only_run_fields": "Runtime and dependency drift: not recorded by this reporter.",   # under the run-field entries when a context is missing
    "no_baseline": "No baseline build to compare with.",
}
RUN_CONTEXT_ERRORS = {   # the 422 sentences (JSON route: Pydantic's msg; /ingest/file: detail {"code","message"})
    "bad_version": "run_context.v must be 1.",
    "too_large": "run_context is larger than 8192 bytes.",
    "env_key": "run_context.env may only carry these keys: {keys}.",
    # W23b addition (G-A): §1.1 names the 200-character / no-control-character rule but no sentence for it.
    "env_value": "run_context.env values are at most 200 characters, without control characters.",
    "lockfile_name": "run_context.dependency_fingerprints keys must be lockfile names such as poetry.lock.",
    "bad_hash": "run_context.dependency_fingerprints values must be sha256 hex digests.",
    "bad_runtime": "run_context.runtime keys are lower-case names and values are version strings.",
    "bad_image": "run_context.image_digests maps an image name to sha256:<64 hex>.",
    "too_many": "run_context has too many entries in {section} (at most {max}).",
    "bad_json": "run_context is not a JSON object.",                          # /ingest/file only
}
