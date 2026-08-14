"""Resolve the environment a run executed against.

Phase 0 (P0-1) of ``architecture/TEST_INTELLIGENCE_PLAN.md``.

``test_runs.environment`` (migration 0129) is populated from an optional
ingestion field. Everything ingested before that migration — and every caller
that never sends it — has NULL, so the read path needs an answer that is honest
about not knowing.

The trap this module exists to avoid: coalescing NULL to a single literal like
``"default"``. That would make thousands of unrelated historical runs look like
one shared environment, and the flakiness score's *environment consistency*
signal would then read "perfectly consistent" for a corpus where the environment
was simply never recorded. A fabricated consistency is worse than no signal —
it is the same class of mistake as a fabricated confidence.

So: an explicit value is used as-is; otherwise we derive a **best-effort key**
from the CI/platform fields the run does carry, and mark it as derived. When
there is nothing to derive from, the answer is ``None`` and callers must treat
the environment dimension as *unknown for this run*, not as a group.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

# Maximum stored/derived length — matches String(100) on the column.
MAX_ENVIRONMENT_LEN = 100

# Source markers, so a consumer can tell a recorded environment from an inferred
# one and weight it accordingly.
SOURCE_EXPLICIT = "explicit"
SOURCE_DERIVED = "derived"
SOURCE_UNKNOWN = "unknown"


@dataclass(frozen=True)
class ResolvedEnvironment:
    """An environment answer plus how much to trust it."""

    key: Optional[str]
    source: str

    @property
    def is_known(self) -> bool:
        return self.key is not None

    def to_dict(self) -> dict[str, Any]:
        return {"environment": self.key, "environment_source": self.source}


def normalize_environment(value: Any) -> Optional[str]:
    """Normalize a caller-supplied environment label.

    Trims, collapses internal whitespace, lowercases, and truncates to the
    column width. Returns ``None`` for anything that is not a non-empty string,
    so ``""``/``"   "``/``None``/``123`` all mean "not supplied" rather than
    creating junk environment groups.
    """
    if not isinstance(value, str):
        return None
    collapsed = " ".join(value.split()).strip().lower()
    if not collapsed:
        return None
    return collapsed[:MAX_ENVIRONMENT_LEN]


def _branch_class(branch: Any) -> Optional[str]:
    """Bucket a branch into a coarse class.

    The raw branch is deliberately NOT used: a per-branch environment key would
    explode into thousands of one-run groups on any repo with feature branches,
    and every one of them would look "environment inconsistent" for no reason.
    """
    if not isinstance(branch, str):
        return None
    name = branch.strip().lower()
    if not name:
        return None
    if name in {"main", "master", "trunk", "develop", "development"}:
        return "mainline"
    if name.startswith(("release/", "hotfix/")) or name.startswith("release-"):
        return "release"
    return "topic"


def resolve_environment(run: Any) -> ResolvedEnvironment:
    """Resolve the environment for a run row (or any object with those attrs).

    Order: an explicitly recorded ``environment`` wins. Otherwise derive from
    the platform/CI fields the run carries — OpenShift namespace first (it is
    the most literally "an environment" of anything we store), then CI provider
    plus branch class. With none of those present the answer is unknown.
    """
    explicit = normalize_environment(getattr(run, "environment", None))
    if explicit:
        return ResolvedEnvironment(key=explicit, source=SOURCE_EXPLICIT)

    # NB: the column is ``ocp_namespace`` (siblings ocp_pod_name / ocp_node /
    # ocp_metadata). Spelling it ``oc_namespace`` here made this branch dead
    # code that failed silently, because getattr's default swallows the typo.
    namespace = normalize_environment(getattr(run, "ocp_namespace", None))
    if namespace:
        return ResolvedEnvironment(key=f"ocp:{namespace}"[:MAX_ENVIRONMENT_LEN],
                                   source=SOURCE_DERIVED)

    provider = normalize_environment(getattr(run, "ci_provider", None))
    branch_class = _branch_class(getattr(run, "branch", None))
    if provider and branch_class:
        return ResolvedEnvironment(key=f"{provider}:{branch_class}"[:MAX_ENVIRONMENT_LEN],
                                   source=SOURCE_DERIVED)
    if provider:
        return ResolvedEnvironment(key=provider, source=SOURCE_DERIVED)

    return ResolvedEnvironment(key=None, source=SOURCE_UNKNOWN)
