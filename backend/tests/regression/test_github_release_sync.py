"""Regression: GitHub release sync and ladder rung 2 (S3b).

Three properties, each of which fails in a way nobody would notice.

**The credential is guarded on the read path too.** ``api_base_url`` is
QA_LEAD-configurable and the project's PAT is sent to it, so an unguarded base
is an SSRF read primitive regardless of HTTP verb — the risk is where the
credential goes, not whether anything is written. It is easier to forget here
than on the write path precisely because nothing is being mutated.

**Sync keys on the external id, never the name.** A name-keyed sync orphans a
release's entire run history and mints a duplicate the moment somebody renames
a milestone — silently, because both rows look fine.

**Rung 2 never fails an ingest.** The test results are the thing of value;
attribution can be repaired later. A sync fault must degrade to "no external
match" and let the ladder continue, not propagate.
"""
from __future__ import annotations

import inspect
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.models.postgres import ASSERTED_LINK_SOURCES, LinkSource, Release
from app.services import github_release_sync as sync


# ── The guard that is easy to skip ───────────────────────────────────────────


def test_every_outbound_request_funnels_through_one_gate():
    """One chokepoint, so a future caller cannot bypass the three checks.

    If a second function issued its own httpx call, it would be trivially easy
    to omit the offline gate or the SSRF check — and nothing would fail, because
    the request would simply succeed against whatever host was configured.
    """
    src = inspect.getsource(sync)
    # The module makes exactly one HTTP call, inside _authorized_get.
    assert src.count("httpx.AsyncClient") == 1
    gate = inspect.getsource(sync._authorized_get)
    assert "httpx.AsyncClient" in gate


def test_the_gate_checks_offline_credential_and_ssrf_in_that_order():
    """Order matters: the cheapest and most absolute check comes first.

    ``AI_OFFLINE_MODE`` is a hard kill switch for air-gapped deployments, so it
    must short-circuit before anything reads a secret or resolves a hostname.
    """
    src = inspect.getsource(sync._authorized_get)
    assert "AI_OFFLINE_MODE" in src
    assert "_ssrf_block_reason" in src
    assert "read_secret" in src
    assert src.index("AI_OFFLINE_MODE") < src.index("read_secret")
    assert src.index("read_secret") < src.index("_ssrf_block_reason")


def test_ssrf_guard_is_the_same_one_the_write_path_uses():
    """Reused, not reimplemented.

    A second copy of this logic would drift from the original, and the copy
    that drifts is the one nobody is looking at.
    """
    from app.services import github_checks_service

    assert sync._ssrf_block_reason is github_checks_service._ssrf_block_reason


@pytest.mark.asyncio
async def test_offline_mode_blocks_before_any_network_or_secret_access():
    """Air-gapped deployments must not egress because someone enabled an
    integration. Nothing should even be read from the secret store.
    """
    with patch.object(sync.settings, "AI_OFFLINE_MODE", True):
        with patch.object(sync, "get_integration", new=AsyncMock()) as integ:
            with pytest.raises(sync.GitHubSyncUnavailable, match="OFFLINE"):
                await sync._authorized_get(AsyncMock(), uuid.uuid4(), "/milestones")
    integ.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_blocked_host_raises_instead_of_sending_the_pat():
    """The whole point of the guard: the credential must not leave.

    A QA_LEAD who points api_base_url at the cloud metadata endpoint or
    localhost would otherwise receive the response body back through the sync,
    turning a read path into an SSRF primitive.
    """
    db = AsyncMock()
    row = SimpleNamespace(
        enabled=True, api_base_url="http://169.254.169.254", repo_owner="o", repo_name="r"
    )
    with patch.object(sync.settings, "AI_OFFLINE_MODE", False), \
         patch.object(sync, "get_integration", new=AsyncMock(return_value=row)), \
         patch("app.services.secret_service.read_secret", new=AsyncMock(return_value="pat")), \
         patch.object(sync, "_ssrf_block_reason", new=AsyncMock(return_value="blocked_target:169.254.169.254")), \
         patch("httpx.AsyncClient") as client:
        with pytest.raises(sync.GitHubSyncUnavailable, match="not allowed"):
            await sync._authorized_get(db, uuid.uuid4(), "/milestones")
    client.assert_not_called(), "no HTTP client may be constructed for a blocked host"


# ── Identity, not name ───────────────────────────────────────────────────────


def test_sync_matches_on_external_id_not_name():
    """Renaming a milestone must UPDATE, not orphan-and-duplicate.

    A name-keyed sync loses the release's entire run history the moment someone
    tidies a title — and leaves two plausible-looking rows behind, so nothing
    reads as broken.
    """
    src = inspect.getsource(sync.sync_milestones)
    lookup = src[src.index("select(Release)"):src.index("if existing is None")]
    assert "Release.external_id" in lookup
    assert "Release.source_system" in lookup
    assert "Release.name" not in lookup, (
        "matching on name would orphan run history on any rename"
    )


def test_external_identity_is_unique_per_project_and_source():
    """Partial unique index, and partial for a specific reason.

    Most releases are local and carry NULL in both columns. Postgres treats
    NULLs as distinct, so an unfiltered unique index would happily allow
    unlimited local releases (correct) while constraining nothing about the
    synced ones it was written for (useless).
    """
    index = next(
        (i for i in Release.__table__.indexes if i.name == "ix_releases_external_identity"),
        None,
    )
    assert index is not None
    assert index.unique is True
    assert [c.name for c in index.columns] == ["project_id", "source_system", "external_id"]
    where = str(index.dialect_options["postgresql"]["where"])
    assert "external_id" in where, "must be partial, or it constrains nothing"


def test_a_milestone_without_a_title_is_skipped_not_invented():
    """A release nobody can refer to is worse than no release."""
    assert sync._milestone_fields({"number": 3, "title": "  "})["name"] == ""
    assert sync._milestone_fields({"number": 3})["name"] == ""
    # And a milestone with no number has no stable key to sync on.
    assert sync._milestone_fields({"title": "2.5.0"})["external_id"] == ""


def test_synced_releases_are_not_flagged_auto_named():
    """A person named this in GitHub.

    Flagging it auto-named would put it behind the "name your release" prompt
    and — more consequentially — exclude it from the rotation successor pool,
    so a real, human-named release could never become active.
    """
    src = inspect.getsource(sync.sync_milestones)
    assert "is_auto_named=False" in src


# ── Rung 2 must never fail an ingest ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_no_pr_number_returns_none_without_touching_the_network():
    """Most runs have no PR. That is the common path and must be free."""
    with patch.object(sync, "_authorized_get", new=AsyncMock()) as get:
        got = await sync.resolve_release_for_run(
            AsyncMock(), uuid.uuid4(), SimpleNamespace(pr_number=None)
        )
    assert got is None
    get.assert_not_awaited()


@pytest.mark.asyncio
async def test_an_unavailable_integration_degrades_to_none():
    """Not configured, offline, no credential — all ordinary, none an error."""
    with patch.object(
        sync, "_authorized_get",
        new=AsyncMock(side_effect=sync.GitHubSyncUnavailable("offline")),
    ):
        got = await sync.resolve_release_for_run(
            AsyncMock(), uuid.uuid4(), SimpleNamespace(pr_number=7)
        )
    assert got is None


@pytest.mark.asyncio
async def test_an_unexpected_fault_still_degrades_to_none():
    """A network fault or malformed JSON must not fail the ingest.

    The test results are the thing of value; attribution can be repaired
    afterwards by the sweep or by hand. Propagating here would lose the run.
    """
    with patch.object(
        sync, "_authorized_get", new=AsyncMock(side_effect=RuntimeError("boom")),
    ):
        got = await sync.resolve_release_for_run(
            AsyncMock(), uuid.uuid4(), SimpleNamespace(pr_number=7)
        )
    assert got is None


@pytest.mark.asyncio
async def test_a_pr_with_no_milestone_returns_none():
    for payload in ({}, {"milestone": None}, {"milestone": {"number": None}}):
        with patch.object(sync, "_authorized_get", new=AsyncMock(return_value=payload)):
            got = await sync.resolve_release_for_run(
                AsyncMock(), uuid.uuid4(), SimpleNamespace(pr_number=7)
            )
        assert got is None, payload


# ── Ladder placement ─────────────────────────────────────────────────────────


def test_external_match_ranks_as_an_assertion():
    """GitHub maintains the milestone-to-PR link itself.

    So this is read from a system of record, not derived from a pattern or a
    date range. Ranking it as an inference would make a fully-attributed
    release report as inferred and understate its own evidence.
    """
    assert LinkSource.EXTERNAL_MATCH.value in ASSERTED_LINK_SOURCES


def test_rung_2_sits_between_the_client_name_and_the_rules():
    """Order encodes evidence strength.

    An explicit client name is the most direct statement. A GitHub milestone is
    next — maintained by a system of record. A rule is a pattern somebody wrote
    once, and a window is an inference from dates.
    """
    from app.services import release_linker

    src = inspect.getsource(release_linker.link_run_or_default)
    for earlier, later in (
        ("EXPLICIT_CLIENT", "EXTERNAL_MATCH"),
        ("EXTERNAL_MATCH", "RULE_MATCH"),
        ("RULE_MATCH", "CUTOFF_WINDOW"),
        ("CUTOFF_WINDOW", "ACTIVE_RELEASE"),
    ):
        assert src.index(earlier) < src.index(later), f"{earlier} must precede {later}"


def test_rung_2_attributes_a_phase_like_every_other_rung():
    """Evidence coverage must not depend on which rung fired.

    The rung-count invariant itself lives in
    ``test_release_attribution_rules.test_every_ladder_rung_attributes_a_phase``,
    derived rather than hardcoded. This one only pins the S3b-specific half:
    that rung 2 in particular routes through the helper rather than linking
    directly.
    """
    from app.services import release_linker

    src = inspect.getsource(release_linker.link_run_or_default)
    external_at = src.index("EXTERNAL_MATCH")
    # The helper call for this rung sits within its own block, before the next
    # rung's marker.
    block = src[src.index("Rung 2"):src.index("Rung 3")]
    assert "_link_with_phase" in block, (
        "rung 2 links directly and would never attribute a phase"
    )
    assert external_at > 0
