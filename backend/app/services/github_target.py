"""The per-project GitHub / GitHub Enterprise target: ONE resolver (W10 FAIL-09, INT-04).

A project's GitHub integration (Settings > GitHub) names an API base
(``https://api.github.com``, or ``https://ghe.corp.example.com/api/v3``), a
repository and a PAT stored in ``secret_service``. Every caller that talks to
that project's GitHub reads the target through here, as plain values, on a
session it closes (or releases) before any HTTP:

* ``resolve_github_target`` -- DB reads only, NO HTTP; decrypts the PAT. The
  commit range connector (``commit_attribution_service._connector_target``),
  the project probes (Integration Health) and the build-changes tool use it.
* ``github_target_status`` -- the same order and gate, but NEVER decrypts the
  PAT (it reads ``GitHubIntegration.has_pat``): for read-time reason lines
  such as the run Changes tab.
* ``target_label`` -- what a page may show of the base: host (and port) plus
  path, never a scheme, a user name or a password.

The reasons, in the order they are decided, are ``GITHUB_UNAVAILABLE_REASONS``.
The env ``GITHUB_TOKEN`` / ``GITHUB_REPO`` are not read here: they feed only
the deployment-wide GitHub probe row.

W19 hand-off: INT-06 replaces the PAT read inside ``resolve_github_target``
with ``github_auth.get_github_token(db, project_id)`` and nothing else; INT-05
adds ``resolve_git_host_target(db, run)`` here, returning a GitHub or a GitLab
target and wrapping ``gitlab_integration_service.load_gitlab_target``.

Commit-free and recording-free (the transaction-boundary and activity
ratchets): it only reads.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import urlsplit

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.services import github_checks_service

GITHUB_UNAVAILABLE_REASONS = ("offline_mode", "github_checks_off", "no_integration", "integration_disabled",
                              "repo_mismatch", "no_token")

_LABEL_MAX = 120


@dataclass(frozen=True)
class GitHubTarget:
    """Plain-value connector coordinates (EXACTLY these three fields: tests
    construct ``commit_attribution_service._ConnectorTarget(api_base=, repo=, pat=)``)."""

    api_base: str                           # integration.api_base_url.rstrip("/")
    repo: str                               # "owner/name"
    pat: str = field(repr=False)


@dataclass(frozen=True)
class GitHubTargetResolution:
    project_id: str
    target: Optional[GitHubTarget]
    reason: Optional[str]                   # GITHUB_UNAVAILABLE_REASONS when target is None, else None
    api_base: Optional[str] = None          # also when unavailable but a row exists (for the label / the reason line)
    repo: Optional[str] = None


def _project_uuid(project_id: Any) -> Any:
    """A string id becomes a UUID for the column comparison; anything else
    (a UUID, a test's sentinel) passes through unchanged."""
    if isinstance(project_id, str):
        try:
            return uuid.UUID(project_id)
        except ValueError:
            return project_id
    return project_id


def _coordinates(row: Any) -> tuple[str, str]:
    api_base = str(getattr(row, "api_base_url", "") or "").rstrip("/")
    repo = f"{row.repo_owner}/{row.repo_name}"
    return api_base, repo


def _repo_mismatch(ci_repo: Optional[str], repo: str) -> bool:
    wanted = (ci_repo or "").strip()
    return bool(wanted) and wanted.lower() != repo.lower()


async def resolve_github_target(
    db: AsyncSession,
    project_id: Any,
    *,
    ci_repo: Optional[str] = None,
    check_gate: bool = True,
    require_enabled: bool = True,
) -> GitHubTargetResolution:
    """The project's GitHub target, or the reason there is none. DB reads only, NO HTTP.

    In order: ``check_gate`` and ``AI_OFFLINE_MODE`` -> ``offline_mode`` (no
    read); no integration row -> ``no_integration``; ``require_enabled`` and the
    row disabled -> ``integration_disabled``; ``ci_repo`` given and not the
    row's ``owner/name`` (case-insensitive) -> ``repo_mismatch``; ``check_gate``
    and the ``github_checks`` flag off for the project -> ``github_checks_off``;
    no stored PAT -> ``no_token``.

    The row is read through ``github_checks_service.get_integration`` (the
    MODULE attribute, so a patch of it applies) and the PAT through
    ``secret_service.read_secret``.
    """
    pid = str(project_id)
    if check_gate and settings.AI_OFFLINE_MODE:
        return GitHubTargetResolution(project_id=pid, target=None, reason="offline_mode")
    key = _project_uuid(project_id)
    row = await github_checks_service.get_integration(db, key)
    if row is None:
        return GitHubTargetResolution(project_id=pid, target=None, reason="no_integration")
    api_base, repo = _coordinates(row)

    def unavailable(reason: str) -> GitHubTargetResolution:
        return GitHubTargetResolution(project_id=pid, target=None, reason=reason, api_base=api_base, repo=repo)

    if require_enabled and not row.enabled:
        return unavailable("integration_disabled")
    if _repo_mismatch(ci_repo, repo):
        return unavailable("repo_mismatch")
    if check_gate and not await github_checks_service._post_allowed(db, project_id=key):
        return unavailable("github_checks_off")

    from app.services import secret_service

    pat = await secret_service.read_secret(
        db, github_checks_service.SECRET_SCOPE, github_checks_service._secret_key(project_id),
    )
    if not pat:
        return unavailable("no_token")
    return GitHubTargetResolution(
        project_id=pid,
        target=GitHubTarget(api_base=api_base, repo=repo, pat=pat),
        reason=None,
        api_base=api_base,
        repo=repo,
    )


async def github_target_status(
    db: AsyncSession,
    project_id: Any,
    *,
    ci_repo: Optional[str] = None,
) -> tuple[Optional[str], Optional[str]]:
    """``(reason, api_base)`` in ``resolve_github_target``'s order with the
    gate ON, but NEVER decrypting the PAT: a stored token is read off
    ``GitHubIntegration.has_pat`` (kept in step by
    ``github_checks_service.upsert_integration``). ``(None, api_base)`` when
    the target would resolve."""
    if settings.AI_OFFLINE_MODE:
        return "offline_mode", None
    key = _project_uuid(project_id)
    row = await github_checks_service.get_integration(db, key)
    if row is None:
        return "no_integration", None
    api_base, repo = _coordinates(row)
    if not row.enabled:
        return "integration_disabled", api_base
    if _repo_mismatch(ci_repo, repo):
        return "repo_mismatch", api_base
    if not await github_checks_service._post_allowed(db, project_id=key):
        return "github_checks_off", api_base
    if not getattr(row, "has_pat", False):
        return "no_token", api_base
    return None, api_base


def target_label(api_base: Optional[str]) -> Optional[str]:
    """What a page may show of an API base: ``api.github.com``,
    ``ghe.corp.example.com/api/v3``, ``10.0.0.5:18093/api/v3``.

    The scheme, any user name / password, the query and the fragment are
    dropped; host, port and path are kept; at most 120 characters. None for
    an empty value."""
    if not isinstance(api_base, str) or not api_base.strip():
        return None
    raw = api_base.strip()
    if "://" not in raw:
        raw = "//" + raw
    try:
        parts = urlsplit(raw)
    except ValueError:
        return None
    host = parts.netloc.rsplit("@", 1)[-1]
    label = (host + parts.path.rstrip("/"))[:_LABEL_MAX]
    return label or None
