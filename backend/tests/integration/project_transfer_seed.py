"""The W24 project-transfer seed (ADM-05 phase 1) — NOT a test module.

``await seed_transfer_project(db, sentinels=make_sentinels())`` builds ONE
project carrying at least one row of EVERY exported entity
(``project_export_entities.EXPORTED``), including the edge cases the export and
the import must survive:

* a ``baseline_release_id`` chain (three releases, the provisioned active one
  first), a ``review_requested`` case, a Slack AND an e-mail team channel, a
  webhook WITH a signing secret, GitHub + GitLab integrations with tokens, a
  run cadence, a plan item for a case that has versions, a user referenced by
  ``author_id`` and another by ``assignee_id``;
* the rows that must NOT travel: a run with test cases (the canonical case
  and the plan item point at them), a defect, a retention hold, a knowledge
  source, a digest subscription, a project API key, members, instance secrets;
* NON-HEX, real-looking secret sentinels built at run time (string
  concatenation — never a literal token in a file), typed through the REAL
  write paths where one exists: ``webhook_service.create_subscription`` (the
  signing secret goes to ``secret_refs``), ``github_checks_service`` /
  ``gitlab_integration_service.upsert_integration`` (the PATs go to
  ``secret_refs``), the ``PUT /ownership/team-channels/{team}`` handler,
  ``test_management_service.create_managed_test_case``, ``secret_service``.

The webhook's SSRF guard (a DNS lookup of the sentinel host) and its
out-of-transaction settings-audit write are stubbed for the seed only; the
subscription row and its secret are the real service's writes.

The seed asserts its own coverage against ``EXPORTED`` (a new exported entity
cannot be forgotten here) and COMMITS once at the end (the team-channel
handler's own commit is deferred to it), so the export runner's own sessions
see the rows. Run it on a throwaway / group database only: it upserts the
instance ``smtp_config`` / ``ai_config`` / ``integrations_config`` secret rows.
"""
from __future__ import annotations

import random
import secrets as _secrets
import uuid
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
from typing import Any, Optional
from unittest.mock import AsyncMock, patch

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import postgres as m
from app.services.project_export_entities import EXPORTED, SCOPE_PROJECT, SCOPE_ROOT, EntitySpec

_TAG_LETTERS = "ghjkmnpqrstvwxyz"     # non-hex: a tag can never read as part of a uuid


@dataclass(frozen=True)
class Sentinels:
    """Real-looking, NON-HEX secret values (see ``make_sentinels``)."""

    github_pat: str
    gitlab_pat: str
    openrouter_key: str
    anthropic_key: str
    aws_key_id: str
    aws_secret: str
    jwt: str
    slack_hook: str
    slack_bot: str
    basic_pass: str
    query_token: str
    webhook_secret: str
    smtp_password: str
    vault_ref: str

    def values(self) -> dict[str, str]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


def make_sentinels() -> Sentinels:
    """Built by concatenation at run time: no secret-shaped literal sits in this file."""
    s = "SENTINEL"
    return Sentinels(
        github_pat="ghp" + "_" + s + "ghpTokenXyzzyQwertyPlughWxyz",
        gitlab_pat="glpat" + "-" + s + "glpatXyzzyQw",
        openrouter_key="sk-or" + "-v1-" + s + "openrouterXyzzyQwertyPlughWxyz" + s + "openrouterXyzzyQw",
        anthropic_key="sk-ant" + "-api03-" + s + "anthropicXyzzyQwertyPlughWxyz",
        aws_key_id="AKIA" + s + "AWSKEYXY",
        aws_secret=s + "awsSecretXyzzyQwertyPlughWxyzAbcdQ",
        jwt="eyJhbGciOiJIUzI1NiJ9" + "." + s + "jwtPayload" + "." + s + "jwtSig",
        slack_hook="https://hooks.slack.com" + "/services/T" + s + "/B" + s + "/" + s + "slackHookXyzzyQw",
        slack_bot="xoxb" + "-" + s + "slackBotXyzzyQwertyPlugh",
        basic_pass=s + "basicpass",
        query_token=s + "queryTok",
        webhook_secret="whsec" + "_" + s + "webhookSigningXyzzyQwerty",
        smtp_password=s + "smtpPasswordXyzzyQwerty",
        vault_ref="vault" + ":kv/testlookup#" + s + "vaultPath",
    )


@dataclass
class SeededProject:
    project_id: uuid.UUID
    project_name: str
    project_slug: str
    tag: str
    admin_id: uuid.UUID
    author_id: uuid.UUID              # referenced by author_id / created_by_id (user_refs)
    assignee_id: uuid.UUID            # referenced by assignee_id / assigned_to_id / owner_user_id (cleared)
    usernames: dict[str, str]         # role in the seed → username
    sentinels: Sentinels
    api_key_plaintext: str            # a project API key's plaintext — must appear nowhere in an archive
    counts: dict[str, int] = field(default_factory=dict)          # exported table → rows of this project
    placements: dict[str, tuple[str, str]] = field(default_factory=dict)   # sentinel → (table, column) it was typed into
    ids: dict[str, uuid.UUID] = field(default_factory=dict)       # named rows (releases, cases, run, …)
    excluded: dict[str, list[uuid.UUID]] = field(default_factory=dict)     # table → rows that must NOT travel


def _tag() -> str:
    return "".join(random.choice(_TAG_LETTERS) for _ in range(10))


def _user(tag: str, role: str, label: str, full_name: Optional[str]) -> m.User:
    username = f"xfer-{label}-{tag}"
    return m.User(
        id=uuid.uuid4(), email=f"{username}@xfer.example.test", username=username, full_name=full_name,
        hashed_password="!" + _secrets.token_urlsafe(16), role=role, is_active=True,
    )


async def _count(db: AsyncSession, spec: EntitySpec, project_id: uuid.UUID) -> int:
    table = m.Base.metadata.tables[spec.table]
    stmt = select(func.count()).select_from(table)
    if spec.scope == SCOPE_ROOT:
        stmt = stmt.where(table.c.id == project_id)
    elif spec.scope == SCOPE_PROJECT:
        stmt = stmt.where(table.c.project_id == project_id)
    else:
        parent_name, column = spec.parent  # type: ignore[misc]
        parent = m.Base.metadata.tables[parent_name]
        stmt = stmt.where(table.c[column].in_(select(parent.c.id).where(parent.c.project_id == project_id)))
    return int((await db.execute(stmt)).scalar_one())


async def exported_counts(db: AsyncSession, project_id: uuid.UUID) -> dict[str, int]:
    """Rows of ``project_id`` per EXPORTED table (the seed's own coverage check, and the round trip's)."""
    return {spec.table: await _count(db, spec, project_id) for spec in EXPORTED}


async def _seed_project_and_people(db: AsyncSession, seed: SeededProject, s: Sentinels) -> m.User:
    """The project (settings row with typed JSON secrets) through ``provision_new_project`` — members,
    the synthetic QA lead and the active release — plus two more members the catalog references."""
    from app.services.project_provisioning import provision_new_project

    admin = await db.get(m.User, seed.admin_id)
    assert admin is not None
    project = m.Project(
        id=seed.project_id, name=seed.project_name, slug=seed.project_slug,
        description="Payments catalog. Deploy hook token: Bearer " + s.jwt,
        jira_project_key="PAY", splunk_index="payments", ocp_namespace="payments-prod",
        jenkins_job_pattern="payments-*",
        component_owner_map={"checkout": "team-checkout", "api_token": s.jwt},
        source_roots=["src/main/java", "services/payments"], tags=["payments", "tier-1"],
        automation_target_pct=80, require_case_review=True,
        manager_user_id=seed.assignee_id,
    )
    db.add(project)
    await db.flush()
    await provision_new_project(db, project=project, creator=admin)
    for user_id, role in ((seed.author_id, "QA_ENGINEER"), (seed.assignee_id, "QA_ENGINEER")):
        db.add(m.ProjectMember(user_id=user_id, project_id=seed.project_id, role=role))
    await db.flush()
    seed.placements["jwt"] = ("projects", "component_owner_map")
    return admin


async def _seed_catalog(db: AsyncSession, seed: SeededProject, s: Sentinels, now: datetime) -> None:
    """Suites, managed cases (+ their versions), links, a dismissed pair, a canonical case, a plan + item,
    a strategy — and the run they point at (which must NOT travel)."""
    from app.models.schemas import ManagedTestCaseCreate, TestCaseParameterSchema, TestCaseStepSchema
    from app.services.test_case_lifecycle_service import transition
    from app.services.test_management_service import create_managed_test_case

    author = await db.get(m.User, seed.author_id)
    assert author is not None
    pid = seed.project_id
    case_a = await create_managed_test_case(db, ManagedTestCaseCreate(
        project_id=pid, title="Checkout charges the saved card", suite_name="Checkout",
        description="Use the sandbox token " + s.github_pat + " for the gateway.",
        steps=[TestCaseStepSchema(step_number=1, action="Authorize with Bearer " + s.jwt, expected_result="200")],
        parameters=[TestCaseParameterSchema(name="api_token", value=s.gitlab_pat, masked=True)],
        test_data="aws_access_key_id=" + s.aws_key_id + "\naws_secret_access_key=" + s.aws_secret,
        assignee_id=seed.assignee_id,
    ), author)
    case_b = await create_managed_test_case(db, ManagedTestCaseCreate(
        project_id=pid, title="Refund reverses the ledger entry", suite_name="Checkout",
    ), author)
    case_c = await create_managed_test_case(db, ManagedTestCaseCreate(
        project_id=pid, title="Refund reverses the ledger entries", suite_name="Ledger",
    ), author)
    await db.flush()
    await transition(db, case_b.id, "request_review", author)       # arrives as "draft" on import
    seed.placements.update({
        "github_pat": ("managed_test_cases", "description"), "gitlab_pat": ("managed_test_cases", "parameters"),
        "aws_secret": ("managed_test_cases", "test_data"), "aws_key_id": ("managed_test_cases", "test_data"),
    })
    seed.ids.update(case_author_assignee=case_a.id, case_review_requested=case_b.id, case_other=case_c.id)

    db.add(m.TestCaseRequirementLink(
        project_id=pid, test_case_id=case_a.id, requirement_key="PAY-101", requirement_key_norm="PAY-101",
        requirement_title="Saved cards", created_by_id=seed.author_id,
    ))
    low, high = sorted((case_a.id, case_c.id))
    db.add(m.DismissedDuplicatePair(project_id=pid, case_a_id=low, case_b_id=high, dismissed_by_user_id=seed.author_id))

    # the run history the catalog points at — EXCLUDED, must not travel
    run = m.TestRun(id=uuid.uuid4(), project_id=pid, build_number=f"xfer-{seed.tag}", status="FAILED",
                    ingestion_source="upload", branch="main", total_tests=2, passed_tests=1, failed_tests=1)
    db.add(run)
    await db.flush()
    run_cases = [
        m.TestCase(id=uuid.uuid4(), test_run_id=run.id, test_name="test_checkout_charges", status="PASSED",
                   test_fingerprint=f"fpcharge{seed.tag}"),
        m.TestCase(id=uuid.uuid4(), test_run_id=run.id, test_name="test_refund_reverses", status="FAILED",
                   test_fingerprint=f"fprefund{seed.tag}"),
    ]
    db.add_all(run_cases)
    await db.flush()
    seed.ids["run"] = run.id
    seed.excluded.setdefault("test_runs", []).append(run.id)
    seed.excluded.setdefault("test_cases", []).extend(tc.id for tc in run_cases)

    suite_id = case_a.test_suite_id
    assert suite_id is not None, "create_managed_test_case(suite_name=…) must anchor a suite"
    db.add(m.CanonicalTestCase(
        project_id=pid, test_suite_id=suite_id, test_fingerprint=f"fpcharge{seed.tag}",
        test_name="test_checkout_charges", class_name="CheckoutTest", first_seen_run_id=run.id,
        last_seen_run_id=run.id, managed_test_case_id=case_a.id, retirement_confirmed_by_id=seed.author_id,
    ))
    plan = m.TestPlan(project_id=pid, name="Release 2026.11 regression", created_by_id=seed.author_id,
                      assigned_to_id=seed.assignee_id, total_cases=1, executed_cases=1, passed_cases=1,
                      actual_start_date=now - timedelta(days=2))
    db.add(plan)
    await db.flush()
    db.add(m.TestPlanItem(plan_id=plan.id, test_case_id=case_a.id, execution_status="passed",
                          executed_by_id=seed.assignee_id, executed_at=now - timedelta(days=1),
                          executed_case_version=1, test_case_result_id=run_cases[0].id))
    db.add(m.TestStrategy(project_id=pid, name="Payments strategy", objective="Cover every money path",
                          created_by_id=seed.author_id, approved_by_id=seed.admin_id, approved_at=now))
    await db.flush()


async def _seed_releases_and_policies(db: AsyncSession, seed: SeededProject, s: Sentinels, now: datetime) -> None:
    """A three-release baseline chain on top of the provisioned active release, a phase, the gate /
    attribution / quality / quarantine / transition policies (the last two carrying senders that
    must arrive OFF)."""
    pid = seed.project_id
    active = (await db.execute(
        select(m.Release).where(m.Release.project_id == pid, m.Release.is_active.is_(True))
    )).scalar_one()
    child = m.Release(project_id=pid, name="2026.11", version="2026.11.0", status="planning", is_active=False,
                      is_default=False, baseline_release_id=active.id, created_by_id=seed.author_id,
                      owner_user_id=seed.assignee_id,
                      external_url="https://deploy:" + s.basic_pass + "@releases.example.test/r/2026.11")
    db.add(child)
    await db.flush()
    grandchild = m.Release(project_id=pid, name="2026.12", status="planning", is_active=False, is_default=False,
                           baseline_release_id=child.id, created_by_id=seed.author_id)
    db.add(grandchild)
    await db.flush()
    seed.ids.update(release_active=active.id, release_child=child.id, release_grandchild=grandchild.id)
    seed.placements["basic_pass"] = ("releases", "external_url")
    db.add(m.ReleasePhase(release_id=child.id, name="Hardening", notes="Freeze the payment gateway"))
    db.add(m.ReleaseGatePolicy(
        project_id=pid, version=1, name="Payments gate", rules={"min_pass_rate": 95, "notify_token": s.slack_bot},
        is_active=True, is_draft=False, created_by=seed.admin_id, activated_by=seed.admin_id, activated_at=now,
    ))
    seed.placements["slack_bot"] = ("release_gate_policies", "rules")
    db.add(m.ReleaseAttributionRule(project_id=pid, name="Release branches", match_field="branch",
                                    match_pattern="release/*", target_release_name="2026.11",
                                    created_by_id=seed.author_id))
    db.add(m.QualityGate(project_id=pid, name="Pass rate", rules={"pass_rate": 90}))
    db.add(m.QuarantineLifecyclePolicy(project_id=pid, auto_create_defect=True))                 # → off
    db.add(m.NotificationTransitionPolicy(project_id=pid, transitions_enabled=True, per_run_events_enabled=True))
    await db.flush()


async def _seed_senders(db: AsyncSession, seed: SeededProject, s: Sentinels, admin: m.User) -> None:
    """Team channels through the real PUT handler (it commits, as in production), a webhook WITH a secret
    through the webhook service, GitHub / GitLab integrations with PATs through their services."""
    from app.models.schemas import TeamChannelUpsert
    from app.routers.ownership import upsert_team_channel
    from app.services import github_checks_service, gitlab_integration_service, webhook_service

    pid = seed.project_id
    # The handler commits; inside the seed that commit is deferred to the seed's ONE commit (a mid-seed
    # commit would expire every loaded row on a default session and split the seed in two transactions).
    with patch.object(db, "commit", db.flush):
        await upsert_team_channel(pid, "Payments", TeamChannelUpsert(channel_type="slack", target=s.slack_hook),
                                  db, admin, admin)
        await upsert_team_channel(pid, "Ledger", TeamChannelUpsert(channel_type="email", target="ledger@example.test"),
                                  db, admin, admin)
    seed.placements["slack_hook"] = ("team_notification_channels", "target")
    target = "https://ops:" + s.basic_pass + "@hooks.example.test/in?token=" + s.query_token
    with patch.object(webhook_service, "_assert_safe_target_url", AsyncMock(return_value=None)), \
            patch.object(webhook_service, "_audit", AsyncMock(return_value=None)):
        hook = await webhook_service.create_subscription(
            db, project_id=pid, actor=admin, name="Ops intake", target_url=target, events=["run.completed"],
            enabled=True, max_retries=3, secret=s.webhook_secret,
        )
    hook.failure_count, hook.total_delivered, hook.last_error = 2, 40, "timeout"   # delivery history (dropped)
    seed.ids["webhook"] = hook.id
    seed.placements["query_token"] = ("webhook_subscriptions", "target_url")
    github = await github_checks_service.upsert_integration(
        db, project_id=pid, actor=admin, enabled=True, repo_owner="acme", repo_name="payments",
        api_base_url="https://api.github.com", pat=s.github_pat,
    )
    github.installation_id = 424242                                      # the SOURCE's App installation (dropped)
    await gitlab_integration_service.upsert_integration(
        db, project_id=pid, actor=admin, enabled=True, base_url="https://gitlab.example.test",
        project_path="acme/payments", commit_status_enabled=True, token=s.gitlab_pat,
    )
    await db.flush()


async def _seed_settings(db: AsyncSession, seed: SeededProject, s: Sentinels, now: datetime) -> None:
    """Ownership, retention, workflows, agent config, cadence, value assumptions, hints, the LLM quota."""
    pid = seed.project_id
    db.add(m.ServiceOwnershipRule(project_id=pid, match_type="suite_name", match_pattern="Checkout*",
                                  service_name="checkout", team_name="Payments", created_by=seed.author_id))
    db.add(m.ProjectRetentionPolicy(project_id=pid, enabled=True, runs_days=90, keep_last_runs=10,
                                    purge_paused_until=now + timedelta(days=1), updated_by_user_id=seed.admin_id))
    db.add(m.WorkflowDefinition(
        project_id=pid, workflow_id="wf.payments-triage", version=1, name="Payments triage", base="offline",
        definition={"steps": [{"id": "fetch", "headers": {"Authorization": "Bearer " + s.jwt}}]},
        status="published", published_at=now, created_by=seed.author_id, updated_by=seed.admin_id,
    ))
    db.add(m.ProjectWorkflowDefault(project_id=pid, base="offline", workflow_id="wf.payments-triage",
                                    workflow_version=1, updated_by=seed.admin_id))
    db.add(m.AgentConfig(project_id=pid, agent_id="triage", config={"openrouter_api_key": s.openrouter_key,
                                                                    "max_items": 20}, updated_by=seed.admin_id))
    seed.placements["openrouter_key"] = ("agent_configs", "config")
    db.add(m.RunCadence(project_id=pid, mode="weekly", weekdays=31, expected_by=time(9, 0), timezone="UTC",
                        effective_from=now - timedelta(days=30), checked_through=now - timedelta(days=1),
                        created_by_id=seed.author_id))                                     # → paused 90 days
    db.add(m.ValueMetricAssumptions(project_id=pid, updated_by_user_id=seed.admin_id))
    db.add(m.ClassificationHint(project_id=pid, match_field="error_message", pattern="Connection refused",
                                category="INFRASTRUCTURE", created_by=seed.author_id))
    db.add(m.ProjectLlmQuota(project_id=pid, enabled=True, updated_by_user_id=seed.admin_id))
    await db.flush()


async def _seed_what_must_not_travel(db: AsyncSession, seed: SeededProject, s: Sentinels) -> None:
    """A defect, a retention hold, a knowledge source, a digest subscription, a project API key and the
    instance secrets (an SMTP password, an AI key, a Vault reference) — none of them may reach an archive."""
    from app.routers.api_keys import _hash_key
    from app.services import secret_service

    pid = seed.project_id
    rows: dict[str, Any] = {
        "defects": m.Defect(project_id=pid, test_case_id=seed.excluded["test_cases"][1]),
        "retention_holds": m.RetentionHold(project_id=pid, entity_type="project", reason="Litigation hold on payments",
                                           placed_by_id=seed.admin_id),
        "knowledge_sources": m.KnowledgeSource(project_id=pid, source_type="confluence", title="Payments runbook",
                                               canonical_url=f"https://wiki.example.test/payments/{seed.tag}"),
        "digest_subscriptions": m.DigestSubscription(user_id=seed.admin_id, project_id=pid, name="Weekly payments"),
    }
    # Hash the freshly minted value, not the attribute: CodeQL reads `seed.api_key_plaintext` as a password and flagged
    # the product's SHA-256 key hash (py/weak-sensitive-data-hashing) through this flow on PR #308.
    minted = "qai" + "_" + _secrets.token_urlsafe(32)
    seed.api_key_plaintext = minted
    rows["api_keys"] = m.ApiKey(user_id=seed.admin_id, project_id=pid, name="CI pipeline",
                                key_hash=_hash_key(minted), key_hint=minted[:8] + "...")
    for table, row in rows.items():
        db.add(row)
        await db.flush()
        seed.excluded.setdefault(table, []).append(row.id)
    await secret_service.store_secret(db, "smtp_config", "password", s.smtp_password, seed.admin_id)
    await secret_service.store_secret(db, "ai_config", "anthropic_api_key", s.anthropic_key, seed.admin_id)
    await secret_service.store_secret(db, "integrations_config", "jira_api_token", s.vault_ref, seed.admin_id)
    await db.flush()


async def seed_transfer_project(
    db: AsyncSession, *, sentinels: Optional[Sentinels] = None, now: Optional[datetime] = None,
) -> SeededProject:
    """Seed ONE project with ≥ 1 row of every EXPORTED entity (+ the edge cases and the rows that must not
    travel, module docstring), assert that coverage, COMMIT, and describe it. ``now`` defaults to the clock."""
    s = sentinels or make_sentinels()
    now = now or datetime.now(timezone.utc)
    tag = _tag()
    admin = _user(tag, "ADMIN", "admin", "Avery Admin")
    author = _user(tag, "QA_ENGINEER", "author", "Ada Author")
    assignee = _user(tag, "QA_ENGINEER", "assignee", None)
    db.add_all([admin, author, assignee])
    await db.flush()
    seed = SeededProject(
        project_id=uuid.uuid4(), project_name=f"Payment Service {tag}", project_slug=f"payment-service-{tag}",
        tag=tag, admin_id=admin.id, author_id=author.id, assignee_id=assignee.id,
        usernames={"admin": admin.username, "author": author.username, "assignee": assignee.username},
        sentinels=s, api_key_plaintext="",
    )
    admin = await _seed_project_and_people(db, seed, s)
    await _seed_catalog(db, seed, s, now)
    await _seed_releases_and_policies(db, seed, s, now)
    await _seed_senders(db, seed, s, admin)
    await _seed_settings(db, seed, s, now)
    await _seed_what_must_not_travel(db, seed, s)

    seed.counts = await exported_counts(db, seed.project_id)
    missing = sorted(table for table, n in seed.counts.items() if n == 0)
    assert not missing, (
        f"seed_transfer_project wrote no row of {missing}: every EXPORTED entity needs ≥ 1 seeded row "
        "(add it to tests/integration/project_transfer_seed.py)"
    )
    assert seed.counts["releases"] >= 3 and seed.counts["team_notification_channels"] >= 2
    await db.commit()
    return seed
