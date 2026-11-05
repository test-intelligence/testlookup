"""The W24 project-transfer seed (ADM-05 phase 1; W30 phase 2 adds run history) — NOT a test module.

``await seed_transfer_project(db, sentinels=make_sentinels())`` builds ONE
project carrying at least one row of EVERY catalog entity
(``project_export_entities.CATALOG``); with ``history=True, mongo=…, storage=…``
also every history entity, document collection and object class (see
``seed_transfer_project``), removed again by ``cleanup_transfer_seed``. The
catalog part covers the edge cases the export and the import must survive:

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

The seed asserts its own coverage against ``CATALOG`` (and, with history,
``HISTORY_TABLES`` + ``MONGO_COLLECTIONS``: a new exported class cannot be
forgotten here) and COMMITS once at the end (the team-channel
handler's own commit is deferred to it), so the export runner's own sessions
see the rows. Run it on a throwaway / group database only: it upserts the
instance ``smtp_config`` / ``ai_config`` / ``integrations_config`` secret rows.
"""
from __future__ import annotations

import hashlib
import os
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
from app.services.project_export_documents import MONGO_COLLECTIONS, canonical_run_uuid
from app.services.project_export_entities import (
    CATALOG,
    HISTORY,
    HISTORY_TABLES,
    SCOPE_PROJECT,
    SCOPE_ROOT,
    EntitySpec,
    spec_for,
)

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
    # (scope, key_name) → the instance secret row BEFORE the seed overwrote it (None = there was none), so
    # remove_transfer_data can put the shared database back.
    instance_secrets: dict[tuple[str, str], Optional[dict[str, Any]]] = field(default_factory=dict)
    # ── W30 run history (history=True) ──
    history: bool = False
    history_counts: dict[str, int] = field(default_factory=dict)  # history table → ALL rows of this project
    # table → [(row id, run key)]: the run key says which window carries the row (see expected_history_counts)
    history_rows: dict[str, list[tuple[uuid.UUID, Optional[str]]]] = field(default_factory=dict)
    # collection → [(key value, run key)] of every seeded document
    documents: dict[str, list[tuple[str, Optional[str]]]] = field(default_factory=dict)
    # stored key → its class: "minio_prefix" (travels), "ingest_input" / "refused_prefix" (must NOT travel)
    objects: dict[str, str] = field(default_factory=dict)
    object_sizes: dict[str, int] = field(default_factory=dict)
    live_slug: str = ""
    # table → rows that must NOT travel even in a history export (the IN_PROGRESS run, the non-terminal pipeline,
    # the outbox, the share link, the live session, the defect, the hold …) — ``excluded`` is the CATALOG export's
    history_excluded: dict[str, list[uuid.UUID]] = field(default_factory=dict)


# ── W30: which window carries a history row / document (run keys) ────────────
RUN_FAILED = "failed"            # FAILED run, 2 days old — in every window
RUN_W24 = "w24"                  # the W24 catalog run (FAILED, created by the seed) — in every window
RUN_OLD = "old"                  # PASSED run, 45 days old — outside a 30-day window, inside 90 / 365 / all
RUN_IN_PROGRESS = "in_progress"  # IN_PROGRESS run — never exported
PIPELINE_RUNNING = "running_pipeline"   # the non-terminal pipeline and its stage results — never exported
ALWAYS = None                    # canonical- / release- / project-scoped rows — every history export carries them
NEVER = "never"                  # e.g. project-level evidence (run_id NULL) — never in a run window


def window_run_keys(history_days: Optional[int]) -> set[Optional[str]]:
    """The run keys a history export with this window carries (None = all history)."""
    keys: set[Optional[str]] = {RUN_FAILED, RUN_W24, ALWAYS}
    if history_days is None or history_days > 45:
        keys.add(RUN_OLD)
    return keys


def expected_history_counts(seed: "SeededProject", *, history_days: Optional[int]) -> dict[str, int]:
    """Rows of each history entity a history export of ``seed`` with this window must carry (an oracle
    independent of the export code: built from how the seed tagged each row)."""
    keys = window_run_keys(history_days)
    return {table: sum(1 for _id, key in seed.history_rows.get(table, []) if key in keys) for table in HISTORY_TABLES}


def expected_document_counts(seed: "SeededProject", *, history_days: Optional[int]) -> dict[str, int]:
    """Documents of each collection a history export of ``seed`` with this window must carry."""
    keys = window_run_keys(history_days)
    return {spec.collection: sum(1 for _k, key in seed.documents.get(spec.collection, []) if key in keys)
            for spec in MONGO_COLLECTIONS}


def _tag() -> str:
    return "".join(random.choice(_TAG_LETTERS) for _ in range(10))


def _user(tag: str, role: str, label: str, full_name: Optional[str]) -> m.User:
    username = f"xfer-{label}-{tag}"
    return m.User(
        id=uuid.uuid4(), email=f"{username}@xfer.example.test", username=username, full_name=full_name,
        hashed_password="!" + _secrets.token_urlsafe(16), role=role, is_active=True,
    )


def _project_filter(spec: EntitySpec, project_id: uuid.UUID) -> Any:
    """The WHERE clause selecting ``spec``'s rows of ``project_id`` (a parent scope resolves through its
    parent's own scope, so ``ai_analysis`` → ``test_cases`` → ``test_runs`` → the project)."""
    table = m.Base.metadata.tables[spec.table]
    if spec.scope == SCOPE_ROOT:
        return table.c.id == project_id
    if spec.scope == SCOPE_PROJECT:
        return table.c.project_id == project_id
    parent_name, column = spec.parent  # type: ignore[misc]
    parent = m.Base.metadata.tables[parent_name]
    return table.c[column].in_(select(parent.c.id).where(_project_filter(spec_for(parent_name), project_id)))


async def _count(db: AsyncSession, spec: EntitySpec, project_id: uuid.UUID) -> int:
    table = m.Base.metadata.tables[spec.table]
    stmt = select(func.count()).select_from(table).where(_project_filter(spec, project_id))
    return int((await db.execute(stmt)).scalar_one())


async def exported_counts(
    db: AsyncSession, project_id: uuid.UUID, *, include_history: bool = False,
) -> dict[str, int]:
    """Rows of ``project_id`` per CATALOG table (phase 1's 29 — the seed's own coverage check, and the round
    trip's); with ``include_history`` the 27 history tables too (ALL rows of the project, unwindowed)."""
    specs = CATALOG + (HISTORY if include_history else ())
    return {spec.table: await _count(db, spec, project_id) for spec in specs}


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
    for scope, key_name in _INSTANCE_SECRETS:
        before = (await db.execute(select(m.SecretRef).where(
            m.SecretRef.scope == scope, m.SecretRef.key_name == key_name))).scalar_one_or_none()
        seed.instance_secrets[(scope, key_name)] = (
            None if before is None else {column: getattr(before, column) for column in _SECRET_COLUMNS})
    await secret_service.store_secret(db, "smtp_config", "password", s.smtp_password, seed.admin_id)
    await secret_service.store_secret(db, "ai_config", "anthropic_api_key", s.anthropic_key, seed.admin_id)
    await secret_service.store_secret(db, "integrations_config", "jira_api_token", s.vault_ref, seed.admin_id)
    await db.flush()


_INSTANCE_SECRETS = (("smtp_config", "password"), ("ai_config", "anthropic_api_key"),
                     ("integrations_config", "jira_api_token"))
_SECRET_COLUMNS = ("provider", "encrypted_value", "external_ref", "masked_value", "rotation_status")


# ── W30: run history (history=True) ──────────────────────────────────────────

def _row(seed: SeededProject, row: Any, key: Optional[str]) -> Any:
    """Tag a history row with the run key that decides which window carries it."""
    seed.history_rows.setdefault(row.__tablename__, []).append((row.id, key))
    return row


async def _seed_history_runs(db: AsyncSession, seed: SeededProject, s: Sentinels, now: datetime) -> dict[str, Any]:
    """The runs (a FAILED one keyed by a live-session slug, a 45-day-old PASSED one under a REFUSED prefix, an
    IN_PROGRESS one), their pipelines (terminal, a child, a NON-terminal one with a lease) and test cases."""
    pid, tag = seed.project_id, seed.tag
    seed.live_slug = f"xfer-live-{tag}"
    failed = _row(seed, m.TestRun(
        id=canonical_run_uuid(seed.live_slug), project_id=pid, build_number=f"xfer-{tag}-failed", status="FAILED",
        ingestion_source="file", branch="main", total_tests=2, passed_tests=1, failed_tests=1,
        ci_run_url="https://ci:" + s.basic_pass + "@ci.example.test/job/payments/1",
        minio_prefix=f"{pid}/builds/xfer-{tag}-failed/", primary_release_id=seed.ids["release_child"],
        event_archive={"events": [{"token": s.jwt}]}, run_context={"ci_token": s.query_token},
        created_at=now - timedelta(days=2), exec_started_at=now - timedelta(days=2, minutes=5),
    ), RUN_FAILED)
    old = _row(seed, m.TestRun(
        id=uuid.uuid4(), project_id=pid, build_number=f"xfer-{tag}-old", status="PASSED", ingestion_source="file",
        total_tests=1, passed_tests=1, minio_prefix=f"shared-{tag}/builds/old/",          # refused: not under {pid}/
        created_at=now - timedelta(days=45),
    ), RUN_OLD)
    live = _row(seed, m.TestRun(
        id=uuid.uuid4(), project_id=pid, build_number=f"xfer-{tag}-live", status="IN_PROGRESS",
        ingestion_source="live", total_tests=1, created_at=now - timedelta(days=1),
    ), RUN_IN_PROGRESS)
    db.add_all([failed, old, live])
    await db.flush()
    seed.history_rows.setdefault("test_runs", []).append((seed.ids["run"], RUN_W24))
    seed.ids.update(run_failed=failed.id, run_old=old.id, run_in_progress=live.id)
    seed.history_excluded.setdefault("test_runs", []).append(live.id)

    lease = {"lease_owner": "worker-qz", "lease_expires_at": now + timedelta(minutes=5), "fencing_token": "fence-qz",
             "heartbeat_at": now, "next_retry_at": now + timedelta(minutes=1)}
    done = _row(seed, m.AgentPipelineRun(id=uuid.uuid4(), test_run_id=failed.id, requested_by=seed.author_id,
                                         status="completed", started_at=now - timedelta(days=2),
                                         completed_at=now - timedelta(days=2) + timedelta(minutes=3), **lease),
                RUN_FAILED)
    running = _row(seed, m.AgentPipelineRun(id=uuid.uuid4(), test_run_id=failed.id, status="running",
                                            started_at=now - timedelta(minutes=10), **lease), PIPELINE_RUNNING)
    db.add_all([done, running])
    await db.flush()
    child = _row(seed, m.AgentPipelineRun(id=uuid.uuid4(), test_run_id=failed.id, status="failed", spawn_depth=1,
                                          parent_pipeline_run_id=done.id, rerun_of=running.id,
                                          completed_at=now - timedelta(days=2)), RUN_FAILED)
    db.add(child)
    seed.ids.update(pipeline_done=done.id, pipeline_running=running.id, pipeline_child=child.id)

    canonical_fp = f"fpcharge{tag}"
    canonical_id = (await db.execute(select(m.CanonicalTestCase.id).where(
        m.CanonicalTestCase.project_id == pid, m.CanonicalTestCase.test_fingerprint == canonical_fp))).scalar_one()
    seed.ids["canonical"] = canonical_id
    case_failed = _row(seed, m.TestCase(
        id=uuid.uuid4(), test_run_id=failed.id, test_fingerprint=canonical_fp, canonical_test_case_id=canonical_id,
        test_name="test_checkout_charges", status="FAILED", suite_name="Checkout",
        error_message="Gateway refused token " + s.github_pat,
        stack_trace="at Gateway.charge(Bearer " + s.jwt + ")\nkey=" + s.openrouter_key,
        assigned_to_user_id=seed.assignee_id, triage_updated_by_user_id=seed.author_id,
        minio_s3_prefix=f"{pid}/builds/xfer-{tag}-failed/allure/", source_uuid=f"allure-{tag}-qz",
        created_at=now - timedelta(days=2),
    ), RUN_FAILED)
    case_passed = _row(seed, m.TestCase(id=uuid.uuid4(), test_run_id=failed.id, test_fingerprint=f"fprefund{tag}",
                                        test_name="test_refund_reverses", status="PASSED"), RUN_FAILED)
    case_old = _row(seed, m.TestCase(id=uuid.uuid4(), test_run_id=old.id, test_fingerprint=canonical_fp,
                                     test_name="test_checkout_charges", status="PASSED"), RUN_OLD)
    case_live = _row(seed, m.TestCase(id=uuid.uuid4(), test_run_id=live.id, test_fingerprint=canonical_fp,
                                      test_name="test_checkout_charges", status="UNKNOWN"), RUN_IN_PROGRESS)
    db.add_all([case_failed, case_passed, case_old, case_live])
    for case_id in seed.excluded.get("test_cases", []):                 # the W24 run's two cases travel now
        seed.history_rows.setdefault("test_cases", []).append((case_id, RUN_W24))
    await db.flush()
    seed.ids.update(case_failed=case_failed.id, case_passed=case_passed.id, case_old=case_old.id,
                    case_in_progress=case_live.id)
    seed.history_excluded.setdefault("agent_pipeline_runs", []).append(running.id)
    seed.history_excluded.setdefault("test_cases", []).append(case_live.id)
    return {
        "failed": failed, "old": old, "live": live, "done": done, "running": running,
        "case_failed": case_failed, "case_passed": case_passed, "case_old": case_old, "case_live": case_live,
    }


async def _seed_history_results(db: AsyncSession, seed: SeededProject, s: Sentinels, r: dict[str, Any],
                                now: datetime) -> None:
    """Every run- / case- / pipeline-scoped history entity, with the edge cases (a cluster listing a case
    outside the archive, a stage result of the NON-terminal pipeline, pointers into excluded runs)."""
    pid, tag = seed.project_id, seed.tag
    failed, old, live, done, running = r["failed"], r["old"], r["live"], r["done"], r["running"]
    case, case_live = r["case_failed"], r["case_live"]
    seed.ids["gate_policy"] = (await db.execute(select(m.ReleaseGatePolicy.id).where(
        m.ReleaseGatePolicy.project_id == pid))).scalars().first()
    seed.ids["phase"] = (await db.execute(select(m.ReleasePhase.id).where(
        m.ReleasePhase.release_id == seed.ids["release_child"]))).scalars().first()
    rows = [
        _row(seed, m.TestCaseHistory(id=uuid.uuid4(), test_case_id=case.id, test_run_id=failed.id,
                                     test_fingerprint=case.test_fingerprint, status="FAILED"), RUN_FAILED),
        _row(seed, m.TestCaseHistory(id=uuid.uuid4(), test_case_id=case_live.id, test_run_id=live.id,
                                     test_fingerprint=case_live.test_fingerprint, status="UNKNOWN"), RUN_IN_PROGRESS),
        _row(seed, m.SuiteExecution(id=uuid.uuid4(), project_id=pid, test_run_id=failed.id, suite_name="Checkout",
                                    suite_key="checkout", source="report", run_created_at=failed.created_at,
                                    test_count=2, passed=1, failed=1), RUN_FAILED),
        _row(seed, m.SuiteRunReview(id=uuid.uuid4(), project_id=pid, suite_name="Checkout", test_run_id=failed.id,
                                    reviewer_user_id=seed.author_id, state="reviewed", note="Gateway flake"), RUN_FAILED),
        _row(seed, m.TestExecutionReview(
            id=uuid.uuid4(), test_case_id=case.id, project_id=pid, state="defect_filed",
            reviewed_by_user_id=seed.author_id,
            defect_link="https://jira:" + s.basic_pass + "@jira.example.test/browse/PAY-7"), RUN_FAILED),
        _row(seed, m.TestStepRun(id=uuid.uuid4(), canonical_test_case_id=seed.ids["canonical"],
                                 source_test_run_id=failed.id, ordinal=1, name="Open checkout", status="failed"),
             RUN_FAILED),
        _row(seed, m.TestStepRun(id=uuid.uuid4(), canonical_test_case_id=seed.ids["canonical"],
                                 source_test_run_id=live.id, ordinal=1, name="Open checkout", status="unknown"),
             RUN_IN_PROGRESS),
        _row(seed, m.AIAnalysis(id=uuid.uuid4(), test_case_id=case.id, root_cause_summary="The gateway timed out",
                                failure_category="INFRASTRUCTURE", confidence_score=80,
                                recommended_actions=[{"action": "retry", "token": s.slack_bot}]), RUN_FAILED),
        _row(seed, m.AIAnalysisVersion(
            id=uuid.uuid4(), test_case_id=case.id, test_run_id=failed.id, project_id=pid,
            test_fingerprint=case.test_fingerprint, version=1, trigger="pipeline", pipeline_run_id=done.id,
            content_sha256="c" * 64, root_cause_summary="The gateway timed out"), RUN_FAILED),
        _row(seed, m.AIAnalysisVersion(
            id=uuid.uuid4(), test_case_id=case.id, test_run_id=failed.id, project_id=pid,
            test_fingerprint=case.test_fingerprint, version=2, trigger="manual", pipeline_run_id=running.id,
            actor_user_id=seed.author_id, content_sha256="d" * 64), RUN_FAILED),
        _row(seed, m.FailureAttribution(id=uuid.uuid4(), project_id=pid, test_case_id=case.id, test_run_id=failed.id,
                                        test_fingerprint=case.test_fingerprint, verdict="infrastructure"), RUN_FAILED),
        _row(seed, m.DeepFinding(id=uuid.uuid4(), test_run_id=failed.id, cluster_id="c1",
                                 root_cause="Gateway pool exhausted"), RUN_FAILED),
        _row(seed, m.ReleaseDecision(
            id=uuid.uuid4(), test_run_id=failed.id, pipeline_run_id=done.id, recommendation="NO_GO", risk_score=70,
            overridden_by=seed.author_id, input_snapshot={"webhook": s.slack_hook},
            policy_id=seed.ids["gate_policy"]), RUN_FAILED),
        _row(seed, m.ReleaseTestRunLink(id=uuid.uuid4(), release_id=seed.ids["release_child"], test_run_id=failed.id,
                                        phase_id=seed.ids["phase"], is_primary=True, linked_by_id=seed.author_id,
                                        project_id=pid), RUN_FAILED),
        _row(seed, m.RunBaseline(id=uuid.uuid4(), run_id=failed.id, baseline_run_id=old.id,
                                 selection_reason="previous_passing", classification="regression"), RUN_FAILED),
        _row(seed, m.RunDiff(id=uuid.uuid4(), run_id=failed.id, baseline_run_id=live.id,
                             diff_payload={"new_failures": [case.test_fingerprint]}), RUN_FAILED),
        _row(seed, m.RunCommitRange(id=uuid.uuid4(), run_id=failed.id, project_id=pid, base_run_id=old.id,
                                    commits=[{"sha": "q" * 7}]), RUN_FAILED),
        _row(seed, m.RunIntelligenceSnapshot(id=uuid.uuid4(), run_id=failed.id,
                                             payload={"summary": "One gateway failure"}), RUN_FAILED),
        _row(seed, m.TestHealthRecommendation(id=uuid.uuid4(), test_run_id=failed.id, test_case_id=case.id,
                                              test_name=case.test_name, health_score=70), RUN_FAILED),
    ]
    db.add_all(rows)
    await db.flush()

    cluster = _row(seed, m.FailureCluster(
        id=uuid.uuid4(), test_run_id=failed.id, pipeline_run_id=done.id, cluster_id="c1", label="Gateway timeouts",
        member_test_ids=[str(case.id), str(uuid.uuid4())], size=2), RUN_FAILED)    # one member is NOT in the archive
    cluster_live = _row(seed, m.FailureCluster(
        id=uuid.uuid4(), test_run_id=failed.id, pipeline_run_id=running.id, cluster_id="c2", label="Pool",
        member_test_ids=[str(case.id)], size=1), RUN_FAILED)        # its pipeline is not exported → NULL
    db.add_all([cluster, cluster_live])
    await db.flush()
    # W32 F-W30-8: the run-scoped artifact the seeded decision report cites (by artifact_id and by a claim).
    evidence = _row(seed, m.EvidenceArtifact(
        id=uuid.uuid4(), run_id=failed.id, project_id=pid, producer_pipeline_run_id=done.id, test_case_id=case.id,
        artifact_type="log", source_system="allure", summary_excerpt="timeout after 30s",
        uri_or_ref=f"s3://pipeline-artifacts/pipeline/2026/11/01/{done.id}/triage/q.json",
        idempotency_key=f"xfer-evidence-{tag}"), RUN_FAILED)
    db.add_all([
        _row(seed, m.AgentStageResult(
            id=uuid.uuid4(), pipeline_run_id=done.id, stage_name="triage", status="completed", task_key="triage",
            failure_cluster_id=cluster.id, result_data={"api_key": s.openrouter_key, "verdict": "infra"},
            idempotency_key=hashlib.sha256(tag.encode()).hexdigest(), lease_expires_at=now + timedelta(minutes=5)),
             RUN_FAILED),
        _row(seed, m.AgentStageResult(id=uuid.uuid4(), pipeline_run_id=running.id, stage_name="triage",
                                      status="running"), PIPELINE_RUNNING),
        evidence,
        _row(seed, m.EvidenceArtifact(id=uuid.uuid4(), run_id=None, project_id=pid, artifact_type="note",
                                      source_system="manual", idempotency_key=f"xfer-project-{tag}"), NEVER),
    ])
    await db.flush()
    seed.ids.update(cluster=cluster.id, evidence=evidence.id)


async def _seed_history_scoped(db: AsyncSession, seed: SeededProject, s: Sentinels, r: dict[str, Any],
                               now: datetime) -> None:
    """The canonical- / release- / project-scoped history (every row travels), and the run-pointing rows
    that must NOT travel: the live session (its slug keys live events), an outbox row, a share link."""
    pid, tag = seed.project_id, seed.tag
    failed, live = r["failed"], r["live"]
    step = _row(seed, m.TestStep(id=uuid.uuid4(), canonical_test_case_id=seed.ids["canonical"],
                                 source_test_run_id=failed.id, ordinal=1, name="Open checkout", status="failed",
                                 expected_value="Bearer " + s.jwt, assertion_message="expected 200"), ALWAYS)
    db.add(step)
    await db.flush()
    db.add_all([
        _row(seed, m.TestStep(id=uuid.uuid4(), canonical_test_case_id=seed.ids["canonical"], parent_step_id=step.id,
                              source_test_run_id=live.id, ordinal=2, depth=1, name="Pay", status="unknown"), ALWAYS),
        _row(seed, m.TestAttachment(id=uuid.uuid4(), canonical_test_case_id=seed.ids["canonical"],
                                    test_step_id=step.id, source_test_run_id=failed.id, name="checkout.png",
                                    source_ref="attachments/checkout.png", media_type="image/png"), ALWAYS),
        _row(seed, m.ReleaseOutcome(id=uuid.uuid4(), release_id=seed.ids["release_child"], project_id=pid,
                                    outcome_kind="rollback", reason="Rolled back after the gateway incident",
                                    marked_by_user_id=seed.author_id), ALWAYS),
        _row(seed, m.ReleaseGateDecision(
            id=uuid.uuid4(), release_id=seed.ids["release_child"], phase_id=seed.ids["phase"], is_current=True,
            verdict="NO_GO", run_ids=[str(failed.id), str(live.id)], incomplete_runs=[str(live.id)],
            policy_id=seed.ids["gate_policy"], baseline_release_id=seed.ids["release_active"],
            created_by_id=seed.author_id, blocking_reasons=[{"reason": "gateway", "hook": s.slack_hook}]), ALWAYS),
        _row(seed, m.TimelineNote(id=uuid.uuid4(), project_id=pid, suite_name="Checkout",
                                  starts_on=(now - timedelta(days=3)).date(), ends_on=(now - timedelta(days=1)).date(),
                                  created_by_id=seed.author_id, text="Gateway migration week"), ALWAYS),
    ])
    must_not_travel: dict[str, Any] = {
        "live_sessions": m.LiveSession(project_id=pid, run_id=seed.live_slug, client_name="xfer-sdk",
                                       session_token_hash=hashlib.sha256(("live-" + tag).encode()).hexdigest(),
                                       status="completed", started_at=now - timedelta(days=2)),
        "run_downstream_outbox": m.RunDownstreamOutbox(run_id=failed.id, project_id=pid, operation="agent_pipeline",
                                                       input_version=f"xfer-{tag}", status="pending"),
        "report_share_links": m.ReportShareLink(run_id=failed.id, project_id=pid,
                                                token_hash=hashlib.sha256(("share-" + tag).encode()).hexdigest(),
                                                expires_at=now + timedelta(days=7)),
    }
    for table, row in must_not_travel.items():
        db.add(row)
        await db.flush()
        seed.history_excluded.setdefault(table, []).append(row.id)
    for table in ("defects", "retention_holds"):
        seed.history_excluded.setdefault(table, []).extend(seed.excluded.get(table, []))
    await db.flush()


def _history_documents(seed: SeededProject, r: dict[str, Any], s: Sentinels, now: datetime) -> dict[str, list[dict]]:
    """≥ 1 document per MongoSpec collection in its writer's shape (+ one of the IN_PROGRESS run, which must
    not travel, and slug-keyed live events of the FAILED run)."""
    pid = str(seed.project_id)
    run, live_run = str(r["failed"].id), str(r["live"].id)
    case, done = str(r["case_failed"].id), str(r["done"].id)
    evidence = str(seed.ids["evidence"])
    return {
        "raw_allure_json": [
            {"test_case_id": f"allure-{seed.tag}-qz", "test_run_id": run, "test_name": "test_checkout_charges",
             "raw_result": {"parameters": [{"name": "password", "value": s.basic_pass}],
                            "statusDetails": {"message": "Gateway refused"}}, "updated_at": now},
            {"test_case_id": f"allure-{seed.tag}-live", "test_run_id": live_run, "test_name": "t",
             "raw_result": {}, "updated_at": now},
        ],
        "rest_api_payloads": [{
            "v": 1, "project_id": pid, "test_run_id": run, "build_number": r["failed"].build_number,
            "run_created_at": r["failed"].created_at, "test_case_id": case, "seq": 0,
            "request": {"method": "POST", "url": "https://api.example.test/charge?token=" + s.query_token,
                        "headers": {"Authorization": "Bearer " + s.jwt}},
            "response": {"status": 502, "body": "upstream timeout"},
        }],
        "live_execution_events": [
            {"run_id": seed.live_slug, "session_id": seed.live_slug, "event_type": "run_start", "seq": 1,
             "project_id": pid},
            {"run_id": run, "session_id": seed.live_slug, "event_type": "test_result", "seq": 2},
        ],
        "run_summaries": [{"test_run_id": run, "project_id": pid, "build_number": r["failed"].build_number,
                           "summary": "One gateway failure", "generated_at": now}],
        "ai_run_versions": [{"version_id": str(uuid.uuid4()), "test_run_id": run, "project_id": pid,
                             "kind": "run_summary", "version": 1, "pipeline_run_id": done,
                             "content_sha256": "e" * 64, "generated_at": now, "payload": {"text": "summary"}}],
        "decision_evidence_snapshots": [{"project_id": pid, "test_run_id": run, "pipeline_run_id": done,
                                         "schema_version": 3, "content_sha256": "f" * 64, "created_at": now}],
        "ai_analysis_payloads": [{"test_case_id": case, "prompt": "why did it fail", "analysis": {"root": "gateway"},
                                  "intermediate_steps": [], "updated_at": now}],
        "pipeline_event_log": [{"pipeline_run_id": done, "event_type": "stage_completed", "timestamp": now,
                                "stage_name": "triage", "test_case_id": case, "detail": {"ms": 120}}],
        # W32 F-W30-8: the published report cites the run-scoped artifact by artifact_id (an evidence
        # projection) and by a claim {"type": "artifact"} — the bodies the retention purge walks.
        "decision_reports": [{"report_id": str(uuid.uuid4()), "project_id": pid, "test_run_id": run,
                              "pipeline_run_id": done, "report_version": 1, "status": "published",
                              "generated_at": now,
                              "decision_intelligence": {
                                  "evidence": [{"artifact_id": evidence, "kind": "log",
                                                "scope": {"project_id": pid, "test_run_id": run,
                                                          "test_case_id": case},
                                                "producer_pipeline_run_id": done}],
                                  "claims": [{"text": "Gateway timeout", "evidence": [
                                      {"type": "artifact", "id": evidence,
                                       "evidence_id": hashlib.sha256(evidence.encode()).hexdigest()}]}],
                              },
                              "verification": {}, "markdown_report": "# NO-GO"}],
        "decision_report_attempts": [{"attempt_id": str(uuid.uuid4()), "project_id": pid, "test_run_id": run,
                                      "pipeline_run_id": done, "status": "failed", "attempted_at": now,
                                      "reason": "critic rejected", "verification": {}}],
    }


def _run_key_of(seed: SeededProject, value: str) -> Optional[str]:
    """The run key of a document's key value (a run / case / pipeline id, or the live slug)."""
    for table in ("test_runs", "test_cases", "agent_pipeline_runs"):
        for row_id, key in seed.history_rows.get(table, []):
            if str(row_id) == value or (table == "test_runs" and str(row_id) == str(canonical_run_uuid(value))):
                return key
    return NEVER


async def _seed_history_documents(mongo: Any, seed: SeededProject, docs: dict[str, list[dict]]) -> None:
    for spec in MONGO_COLLECTIONS:
        batch = [dict(doc) for doc in docs[spec.collection]]          # insert_many sets _id on what it is given
        await mongo[spec.collection].insert_many(batch)
        seed.documents[spec.collection] = [(str(d[spec.key]), _run_key_of(seed, str(d[spec.key]))) for d in batch]


_PNG_MAGIC = bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A])


async def _seed_history_objects(storage: Any, seed: SeededProject, r: dict[str, Any], object_bytes: int) -> None:
    """Two objects under the FAILED run's prefix (a JSON result and a PNG of ``object_bytes``), one ingest input
    under ``uploads/{pid}/{run}/queued/`` and one under the OLD run's refused prefix — the last two never travel."""
    pid, tag = seed.project_id, seed.tag
    root = r["failed"].minio_prefix
    payloads = {
        f"{root}allure/{tag}-result.json": (b'{"name": "test_checkout_charges", "status": "failed"}',
                                            "application/json", "minio_prefix"),
        f"{root}allure/attachments/checkout.png": (_PNG_MAGIC + os.urandom(max(0, object_bytes - len(_PNG_MAGIC))),
                                                   "image/png", "minio_prefix"),
        f"uploads/{pid}/{r['failed'].id}/queued/{tag}qz.json": (b'{"queued": true}', "application/json",
                                                               "ingest_input"),
        f"{r['old'].minio_prefix}allure/old-result.json": (b'{"name": "old"}', "application/json", "refused_prefix"),
    }
    for key, (content, content_type, klass) in payloads.items():
        await storage.put_object(key, content, content_type=content_type)
        seed.objects[key] = klass
        seed.object_sizes[key] = len(content)


async def remove_transfer_data(
    sessions: Any, seed: SeededProject, *, project_ids: Any = (), user_ids: Any = (),
) -> None:
    """Remove EVERYTHING the seed (and the imports made from it) left in a SHARED database, and put the instance
    secrets it overwrote back. CI runs every ``*_postgres.py`` module in ONE database, in order: on PR #308 the
    seeded project's GitHub integration survived the export module and the W10 integration-health sweep, which
    probes every project's integration, read it as ``auth_error`` three modules later.

    Order: the per-project secret rows (``secret_refs`` has no FK to a project), the jobs, the canonical cases
    (RESTRICT on suites), the projects (everything project-scoped cascades), the instance secrets, the people.
    Raises on a failure — a leak must fail THIS module, not a later one."""
    from sqlalchemy import delete, or_, update

    from app.services import github_checks_service, gitlab_integration_service, webhook_service

    projects = sorted({seed.project_id, *project_ids}, key=str)
    async with sessions() as db:
        leads = [lead for lead in (await db.execute(
            select(m.Project.default_qa_lead_user_id).where(m.Project.id.in_(projects)))).scalars().all() if lead]
        hooks = (await db.execute(
            select(m.WebhookSubscription.id).where(m.WebhookSubscription.project_id.in_(projects)))).scalars().all()
    secret_keys = [(github_checks_service.SECRET_SCOPE, github_checks_service._secret_key(p)) for p in projects]
    secret_keys += [(gitlab_integration_service.SECRET_SCOPE, gitlab_integration_service._secret_key(p)) for p in projects]
    secret_keys += [(webhook_service.SECRET_SCOPE, webhook_service._secret_key(h)) for h in hooks]
    people = sorted({seed.admin_id, seed.author_id, seed.assignee_id, *user_ids, *leads}, key=str)
    async with sessions() as db:
        await db.execute(delete(m.SecretRef).where(or_(*[
            (m.SecretRef.scope == scope) & (m.SecretRef.key_name == key) for scope, key in secret_keys])))
        await db.execute(delete(m.ProjectDataJob).where(or_(
            m.ProjectDataJob.project_id.in_(projects), m.ProjectDataJob.requested_by_id.in_(people))))
        await db.execute(delete(m.CanonicalTestCase).where(m.CanonicalTestCase.project_id.in_(projects)))
        await db.execute(delete(m.Project).where(m.Project.id.in_(projects)))
        for (scope, key_name), before in seed.instance_secrets.items():
            where = (m.SecretRef.scope == scope) & (m.SecretRef.key_name == key_name)
            if before is None:
                await db.execute(delete(m.SecretRef).where(where))
            else:
                await db.execute(update(m.SecretRef).where(where).values(**before))
        await db.execute(delete(m.User).where(m.User.id.in_(people)))
        await db.commit()


async def seed_transfer_project(
    db: AsyncSession, *, sentinels: Optional[Sentinels] = None, now: Optional[datetime] = None,
    history: bool = False, mongo: Any = None, storage: Any = None, object_bytes: int = 2048,
) -> SeededProject:
    """Seed ONE project with ≥ 1 row of every CATALOG entity (+ the edge cases and the rows that must not
    travel, module docstring), assert that coverage, COMMIT, and describe it. ``now`` defaults to the clock.

    W30 ``history=True`` (needs ``mongo`` — a Motor database — and ``storage`` — e.g. a ``LocalStorageProvider``
    on ``tmp_path``) adds ≥ 1 row of EVERY history entity (``HISTORY_TABLES``), ≥ 1 document of every
    ``MongoSpec`` collection and the objects, with the edge cases: a FAILED run (analyses, versions, clusters,
    stage results) keyed by a live-session slug, a NON-terminal pipeline, an IN_PROGRESS run, a run outside a
    30-day window under a refused prefix, an ingest-input object, an outbox row, a share link, a live session
    (+ W24's defect and hold). ``object_bytes`` sizes the PNG attachment (60 MiB in the local demo makes the
    archive take the parted upload). Every history row / document is tagged with the window that carries it
    (``expected_history_counts`` / ``expected_document_counts``). Remove it all with ``cleanup_transfer_seed``."""
    if history:
        assert mongo is not None and storage is not None, "seed_transfer_project(history=True) needs mongo= and storage="
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

    seed.history = history
    runs: dict[str, Any] = {}
    if history:
        runs = await _seed_history_runs(db, seed, s, now)
        await _seed_history_results(db, seed, s, runs, now)
        await _seed_history_scoped(db, seed, s, runs, now)

    seed.counts = await exported_counts(db, seed.project_id)
    missing = sorted(table for table, n in seed.counts.items() if n == 0)
    assert not missing, (
        f"seed_transfer_project wrote no row of {missing}: every CATALOG entity needs ≥ 1 seeded row "
        "(add it to tests/integration/project_transfer_seed.py)"
    )
    assert seed.counts["releases"] >= 3 and seed.counts["team_notification_channels"] >= 2
    if history:
        all_counts = await exported_counts(db, seed.project_id, include_history=True)
        seed.history_counts = {table: all_counts[table] for table in HISTORY_TABLES}
        in_window = expected_history_counts(seed, history_days=30)
        thin = sorted(t for t in HISTORY_TABLES if seed.history_counts[t] == 0 or in_window[t] == 0)
        assert not thin, (
            f"seed_transfer_project(history=True) has no in-window row of {thin}: every HISTORY entity needs ≥ 1 "
            "(add it to tests/integration/project_transfer_seed.py)"
        )
    await db.commit()
    if history:
        await _seed_history_documents(mongo, seed, _history_documents(seed, runs, s, now))
        await _seed_history_objects(storage, seed, runs, object_bytes)
        thin_docs = sorted(c for c, n in expected_document_counts(seed, history_days=30).items() if n == 0)
        assert not thin_docs, f"no in-window document of {thin_docs}: every MongoSpec collection needs ≥ 1"
        assert sum(1 for klass in seed.objects.values() if klass == "minio_prefix") >= 2
    return seed


async def cleanup_transfer_seed(
    sessions: Any, seed: SeededProject, *, mongo: Any = None, storage: Any = None, project_ids: Any = (),
    user_ids: Any = (),
) -> None:
    """Remove EVERY row, document and object the seed created and every one an import of it created (the copies'
    ``project_ids``), then the people and the instance secrets (``remove_transfer_data``). CI shares ONE Postgres
    AND one Mongo database across modules, so documents go by EVERY id field of every ``MongoSpec`` (the runs,
    cases and pipelines of the source and of each copy, read BEFORE the rows go, plus the live slug and the
    project ids), objects by each project's two roots and the seed's refused-prefix key. Raises on a failure."""
    projects = sorted({seed.project_id, *project_ids}, key=str)
    runs: list[str] = []
    cases: list[str] = []
    pipelines: list[str] = []
    async with sessions() as db:
        run_ids = (await db.execute(select(m.TestRun.id).where(m.TestRun.project_id.in_(projects)))).scalars().all()
        runs = [str(r) for r in run_ids]
        if run_ids:
            cases = [str(c) for c in (await db.execute(
                select(m.TestCase.id).where(m.TestCase.test_run_id.in_(run_ids)))).scalars().all()]
            pipelines = [str(p) for p in (await db.execute(
                select(m.AgentPipelineRun.id).where(m.AgentPipelineRun.test_run_id.in_(run_ids)))).scalars().all()]
    by_table = {"test_runs": runs + ([seed.live_slug] if seed.live_slug else []), "test_cases": cases,
                "agent_pipeline_runs": pipelines}
    if mongo is not None:
        project_strs = [str(p) for p in projects]
        for spec in MONGO_COLLECTIONS:
            fields = {spec.key: spec.key_map, **dict(spec.id_fields), **dict(spec.added_fields)}
            clauses = [{name: {"$in": by_table[table]}} for name, table in fields.items() if by_table.get(table)]
            if spec.project_field:
                clauses.append({spec.project_field: {"$in": project_strs}})
            clauses += [{name: {"$in": by_table["test_runs"]}} for name in spec.run_fields]
            if clauses:
                await mongo[spec.collection].delete_many({"$or": clauses})
    if storage is not None:
        for pid in projects:
            await storage.delete_prefix(f"{pid}/")
            await storage.delete_prefix(f"uploads/{pid}/")
        for key, klass in seed.objects.items():
            if klass == "refused_prefix":
                await storage.delete_object(key)
    await remove_transfer_data(sessions, seed, project_ids=project_ids, user_ids=user_ids)
