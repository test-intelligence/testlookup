"""Which stored objects travel with run history, and the key rule both sides apply (W30 ADM-05 phase 2).

Four object places exist (contract §0.11 / §2.3); only ONE travels: the run's
uploaded result files and attachments under its validated ``TestRun.minio_prefix``
(root ``{project_id}/…``), copied byte-for-byte. Raw ingest uploads
(``uploads/{project}/{run}/…``), pipeline scratch (the ``pipeline-artifacts``
bucket), knowledge content and compliance packs stay out.

R-1 the key rule: the export stores an object's key as a SUFFIX under its run's
validated root (``split_key``); the import rebuilds the root from NEW ids
(``rebuild_prefix``) and re-checks the suffix (``safe_suffix``) — so every key an
import writes starts with ``{new_project_id}/``, a root no other project owns,
and no member name, suffix or stored key is ever a filesystem path.

Pure: the vocabulary only — no storage client, no I/O (``STAGE_ONLY_SERVICES``).
"""
from __future__ import annotations

import re
from typing import Any, Mapping

from app.services.project_export_vocab import OBJECT_KEY_SUFFIX_MAX, OBJECT_KEY_SUFFIX_RE

ROOT_MINIO_PREFIX = "minio_prefix"          # objects/index "root": the only exported class

#: name → (class, sentence). The sentences are what the README / user guide / manifest say about each place.
OBJECT_CLASSES: Mapping[str, tuple[str, str]] = {
    ROOT_MINIO_PREFIX: ("exported", "The run's uploaded result files and attachments, copied byte-for-byte and unscanned."),
    "ingest_input": ("excluded", "Raw report uploads (uploads/<project>/<run>/) are ingest input; the parsed results travel instead."),
    "pipeline_scratch": ("excluded", "AI pipeline scratch files (the pipeline-artifacts bucket) are not run history."),
    "knowledge_content": ("excluded", "Knowledge-source content is not run history."),
    "compliance_pack": ("excluded", "Compliance packs and earlier exports are not run history."),
}
#: The manifest's ``history.excluded_objects`` lines (contract §1.3).
EXCLUDED_OBJECT_NOTES: tuple[str, ...] = (
    "uploads/<project>/<run>/ (ingest input)",
    "pipeline-artifacts (pipeline scratch)",
)
#: A key with one of these suffixes is written ZIP_STORED (already compressed / media); anything else DEFLATED.
STORED_SUFFIXES: tuple[str, ...] = (
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".mp4", ".webm", ".zip", ".gz", ".br", ".zst", ".pdf",
)
#: ``refusal_reason`` codes (the export counts them; ``unsafe_key`` is a SKIP_REASONS key).
REFUSALS = ("unsafe_key", "ingest_input", "refused_prefix")

_SUFFIX = re.compile(OBJECT_KEY_SUFFIX_RE)
_ROOT_MAX = 1000                            # == TestRun.minio_prefix / TestCase.minio_s3_prefix column length


def safe_suffix(suffix: Any) -> bool:
    """A source key's suffix under its run root: ``OBJECT_KEY_SUFFIX_RE`` over the WHOLE string (no leading
    ``/``, no ``.`` / ``..`` segment, no control character, no backslash) and ≤ ``OBJECT_KEY_SUFFIX_MAX``."""
    return (
        isinstance(suffix, str) and 0 < len(suffix) <= OBJECT_KEY_SUFFIX_MAX
        and _SUFFIX.fullmatch(suffix) is not None
    )


def _validated_root(project_id: Any, prefix: Any) -> str | None:
    """``prefix`` when it is a safe run root INSIDE ``project_id``'s own tree (``{project_id}/<at least one
    segment>/``), else None. ``uploads/{project}/…`` (ingest input), another project's tree, an unsafe path
    and the bare project root (one run would claim every object of the project) are refused."""
    if not isinstance(prefix, str):
        return None
    root = f"{project_id}/"
    candidate = prefix.strip()
    if not candidate.startswith(root) or not candidate.endswith("/") or len(candidate) > _ROOT_MAX:
        return None
    if not safe_suffix(candidate[len(root):]):
        return None
    return candidate


def refusal_reason(key: str, *, project_id: Any, run_id: Any, minio_prefix: Any) -> str | None:
    """Why ``key`` of a window run is NOT copied (a ``REFUSALS`` code), or None when it travels."""
    if key.startswith(f"uploads/{project_id}/"):
        return "ingest_input"
    root = _validated_root(project_id, minio_prefix)
    if root is None or not key.startswith(root):
        return "refused_prefix"
    if not safe_suffix(key[len(root):]):
        return "unsafe_key"
    return None


def split_key(key: str, *, project_id: Any, run_id: Any, minio_prefix: Any) -> tuple[str, str] | None:
    """``(root kind, suffix)`` for an object of a window run that travels, or None (refused, counted by the
    caller through :func:`refusal_reason`). The suffix is the key MINUS the run's validated root."""
    root = _validated_root(project_id, minio_prefix)
    if root is None or refusal_reason(key, project_id=project_id, run_id=run_id, minio_prefix=minio_prefix):
        return None
    return ROOT_MINIO_PREFIX, key[len(root):]


def rebuild_prefix(value: str | None, *, source_project_id: Any, new_project_id: Any) -> str | None:
    """A stored root / prefix (``test_runs.minio_prefix``, ``test_cases.minio_s3_prefix``) rebuilt under the
    NEW project: ``"{src}/rest/"`` → ``"{new}/rest/"``. ``uploads/{src}/{run}/…`` (ingest input does not
    travel), another project's tree or an unsafe path → None (the caller counts it)."""
    root = _validated_root(source_project_id, value)
    if root is None:
        return None
    return f"{new_project_id}/" + root[len(f"{source_project_id}/"):]


def object_key(new_root: str, suffix: str) -> str | None:
    """The key the import writes: the rebuilt root + the suffix RE-CHECKED (never trusted from the archive)."""
    if not new_root.endswith("/") or not safe_suffix(suffix):
        return None
    return new_root + suffix


def is_stored(key: str) -> bool:
    """True when the object is written ZIP_STORED (media / already compressed)."""
    return key.lower().endswith(STORED_SUFFIXES)
