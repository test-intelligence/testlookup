"""Roles, ranks and permissions (enterprise roles, epic E00).

TestLookup has two ideas that used to be one:

* **Rank** -- the authority ladder ``VIEWER < TESTER < QA_ENGINEER < QA_LEAD <
  ADMIN``. Every ``require_role(X)`` compares ranks. The ladder has exactly
  five rungs and never grows.
* **Role** -- what a person is assigned. The five ladder roles are their own
  rank. The job roles added for release teams (RELEASE_MANAGER,
  PROJECT_MANAGER, PRODUCT_MANAGER, DEVELOPER) and the AGENT principal are not
  rungs: each has a *base rank* of VIEWER, used by every guard that names no
  permission (fail closed), and an explicit *permission set*, used by guards
  that do (``require_permission`` / ``require_role(..., permission=...)``).

Every permission records ``legacy_min_rank``: the rank that grants it on the
ladder. The ladder roles' permission sets are COMPUTED from it, so for them
"has permission P" is exactly "rank >= P.legacy_min_rank" -- a route that moves
from ``require_role(X)`` to ``require_permission(P)`` (with
``P.legacy_min_rank == X``) answers every ladder role as before.

A person may hold extra job roles (``users.additional_roles``,
``project_members.additional_roles``): the primary ``role`` gives the rank, the
extra roles only add permissions. On a write into a project the caller's
instance permissions and project permissions INTERSECT -- the generalisation
of "the lower of the two roles decides" (security audit 2026-11-18).

Nothing here touches the database or FastAPI; ``app.core.deps`` builds the
guards on top of it. Keep this module import-light: ``deps`` imports it at
module load.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional

from app.models.postgres import UserRole

# ── Ranks ────────────────────────────────────────────────────────────────────

#: The authority ladder, lowest first. Exactly five rungs.
LADDER: tuple[UserRole, ...] = (
    UserRole.VIEWER,
    UserRole.TESTER,
    UserRole.QA_ENGINEER,
    UserRole.QA_LEAD,
    UserRole.ADMIN,
)

#: Job roles a person may hold in addition to their primary role.
JOB_ROLES: frozenset[UserRole] = frozenset({
    UserRole.RELEASE_MANAGER,
    UserRole.PROJECT_MANAGER,
    UserRole.PRODUCT_MANAGER,
    UserRole.DEVELOPER,
})

#: The extra roles the API accepts, in enum order (forms, messages, the OpenAPI enum).
EXTRA_ROLE_CHOICES: tuple[UserRole, ...] = tuple(role for role in UserRole if role in JOB_ROLES)

#: Every role's rank on the ladder. Job roles and AGENT count as VIEWER on a
#: guard that names no permission, so a new role never gains a write through an
#: old guard by accident.
ROLE_BASE_RANK: Mapping[UserRole, UserRole] = {
    **{role: role for role in LADDER},
    **{role: UserRole.VIEWER for role in (*JOB_ROLES, UserRole.AGENT)},
}


def normalize_role(value: UserRole | str) -> UserRole:
    """A stored role string as ``UserRole``; ``ValueError`` for an unknown string.

    The ``UserRole.`` prefix guard is a safety net for rows migration 0045
    did not clean.
    """
    if isinstance(value, UserRole):
        return value
    raw = str(value).strip()
    if raw.startswith("UserRole."):
        raw = raw.split(".", 1)[1]
    return UserRole(raw)


def is_ladder_role(role: UserRole | str) -> bool:
    try:
        return normalize_role(role) in LADDER
    except ValueError:
        return False


def role_rank(role: UserRole | str) -> UserRole:
    """The ladder rung ``role`` counts as (itself for ladder roles, VIEWER for the rest)."""
    return ROLE_BASE_RANK[normalize_role(role)]


def rank_index(role: UserRole | str) -> int:
    """Position of ``role``'s rank on the ladder (0 = VIEWER).

    The ONLY place that indexes the ladder. Never raises for a ``UserRole``
    member; raises ``ValueError`` for a string that is not a role, which callers
    already treat as "refuse".
    """
    return LADDER.index(role_rank(role))


# ── Permissions ──────────────────────────────────────────────────────────────


class AgentCeiling(str, Enum):
    """How far an AGENT principal may ever hold a permission (epic E01)."""

    YES = "yes"          # may hold it outright
    PROPOSE = "propose"  # its writes become proposals a human approves
    DRAFT = "draft"      # it may only create drafts / dry runs
    NO = "no"            # never granted to an agent
    NEVER = "never"      # never, by separation of duties (sign, decide, approve, administer)


@dataclass(frozen=True)
class PermissionSpec:
    description: str
    legacy_min_rank: UserRole
    agent_ceiling: AgentCeiling


class Permission(str, Enum):
    PROJECT_READ = "project.read"
    REQUIREMENT_READ = "requirement.read"
    RUN_INGEST = "run.ingest"
    RUN_DELETE = "run.delete"
    FAILURE_TRIAGE_ASSIGNED = "failure.triage.assigned"
    FAILURE_TRIAGE_ANY = "failure.triage.any"
    CLASSIFICATION_CORRECT = "classification.correct"
    ANALYSIS_TRIGGER = "analysis.trigger"
    DEFECT_CREATE = "defect.create"
    QUARANTINE_PROPOSE = "quarantine.propose"
    QUARANTINE_DECIDE = "quarantine.decide"
    TESTCASE_WRITE = "testcase.write"
    TESTCASE_REVIEW = "testcase.review"
    TESTCASE_APPROVE = "testcase.approve"
    TESTPLAN_WRITE = "testplan.write"
    TESTPLAN_SCHEDULE = "testplan.schedule"
    TESTPLAN_EXECUTE = "testplan.execute"
    OWNERSHIP_MANAGE = "ownership.manage"
    TAXONOMY_MANAGE = "taxonomy.manage"
    REQUIREMENT_WRITE = "requirement.write"
    REQUIREMENT_LINK = "requirement.link"
    UAT_MANAGE = "uat.manage"
    ACCEPTANCE_SIGNOFF = "acceptance.signoff"
    RISK_WRITE = "risk.write"
    RISK_ACCEPT = "risk.accept"
    RELEASE_WRITE = "release.write"
    RELEASE_SCHEDULE = "release.schedule"
    RELEASE_GATE_EVALUATE = "release.gate.evaluate"
    RELEASE_CHECKLIST_MANAGE = "release.checklist.manage"
    RELEASE_SIGNOFF = "release.signoff"
    RELEASE_DECIDE = "release.decide"
    RELEASE_OVERRIDE_REQUEST = "release.override.request"
    RELEASE_OVERRIDE_APPROVE = "release.override.approve"
    RELEASE_CALENDAR_MANAGE = "release.calendar.manage"
    RELEASE_DELETE = "release.delete"
    GATE_POLICY_WRITE = "gate_policy.write"
    GATE_POLICY_PUBLISH = "gate_policy.publish"
    REPORT_PUBLISH = "report.publish"
    AUDIT_READ_PROJECT = "audit.read.project"
    AI_CHAT = "ai.chat"
    AGENT_PROPOSAL_APPROVE = "agent.proposal.approve"
    AGENT_MANAGE = "agent.manage"
    APIKEY_CREATE_OWN = "apikey.create.own"
    PROJECT_SETTINGS = "project.settings"
    MEMBER_MANAGE = "member.manage"
    INSTANCE_ADMIN = "instance.admin"

    @property
    def spec(self) -> PermissionSpec:
        return PERMISSION_SPECS[self]

    @property
    def legacy_min_rank(self) -> UserRole:
        return PERMISSION_SPECS[self].legacy_min_rank

    @property
    def agent_ceiling(self) -> AgentCeiling:
        return PERMISSION_SPECS[self].agent_ceiling


_V, _T, _E, _L, _A = (
    UserRole.VIEWER, UserRole.TESTER, UserRole.QA_ENGINEER, UserRole.QA_LEAD, UserRole.ADMIN,
)
_YES, _PROPOSE, _DRAFT, _NO, _NEVER = (
    AgentCeiling.YES, AgentCeiling.PROPOSE, AgentCeiling.DRAFT, AgentCeiling.NO, AgentCeiling.NEVER,
)

#: The whole vocabulary, including permissions only later epics put on routes,
#: so parallel epics never edit this table at the same time.
PERMISSION_SPECS: Mapping[Permission, PermissionSpec] = {
    Permission.PROJECT_READ: PermissionSpec("Read a project's runs, results, clusters, releases and reports", _V, _YES),
    Permission.REQUIREMENT_READ: PermissionSpec("Read requirements and the coverage matrix", _V, _YES),
    Permission.RUN_INGEST: PermissionSpec("Ingest, upload or stream test results", _E, _YES),
    Permission.RUN_DELETE: PermissionSpec("Delete runs", _A, _NEVER),
    Permission.FAILURE_TRIAGE_ASSIGNED: PermissionSpec("Triage failures assigned to me or in code I own", _E, _PROPOSE),
    Permission.FAILURE_TRIAGE_ANY: PermissionSpec("Assign, reassign or resolve any failure in the project", _L, _NO),
    Permission.CLASSIFICATION_CORRECT: PermissionSpec("Correct an AI root-cause classification", _E, _PROPOSE),
    Permission.ANALYSIS_TRIGGER: PermissionSpec("Trigger AI analysis, investigations and pipelines", _E, _YES),
    Permission.DEFECT_CREATE: PermissionSpec("Promote a cluster to a defect or file an issue", _E, _DRAFT),
    Permission.QUARANTINE_PROPOSE: PermissionSpec("Propose quarantining a flaky test", _L, _YES),
    Permission.QUARANTINE_DECIDE: PermissionSpec("Approve, reject or release a quarantine", _L, _NO),
    Permission.TESTCASE_WRITE: PermissionSpec("Create and edit managed test cases and suites", _E, _DRAFT),
    Permission.TESTCASE_REVIEW: PermissionSpec("Review managed test cases", _E, _NO),
    Permission.TESTCASE_APPROVE: PermissionSpec("Approve managed test cases", _L, _NO),
    Permission.TESTPLAN_WRITE: PermissionSpec("Create and edit test plans and their items", _T, _DRAFT),
    Permission.TESTPLAN_SCHEDULE: PermissionSpec("Set dates, owners and assignees on plans, phases and UAT cycles", _L, _NO),
    Permission.TESTPLAN_EXECUTE: PermissionSpec("Record manual results on assigned items", _T, _NO),
    Permission.OWNERSHIP_MANAGE: PermissionSpec("Manage suite owners, ownership rules and the default QA lead", _L, _NO),
    Permission.TAXONOMY_MANAGE: PermissionSpec("Manage test level and type classification rules", _L, _NO),
    Permission.REQUIREMENT_WRITE: PermissionSpec("Create and edit requirements and acceptance criteria", _L, _DRAFT),
    Permission.REQUIREMENT_LINK: PermissionSpec("Link tests to requirements", _E, _PROPOSE),
    Permission.UAT_MANAGE: PermissionSpec("Create UAT cycles and assign testers", _L, _NO),
    Permission.ACCEPTANCE_SIGNOFF: PermissionSpec("Accept or reject a requirement or UAT cycle", _L, _NEVER),
    Permission.RISK_WRITE: PermissionSpec("Write the risks and blockers register", _E, _YES),
    Permission.RISK_ACCEPT: PermissionSpec("Accept a known issue as a release exception", _L, _NEVER),
    Permission.RELEASE_WRITE: PermissionSpec("Create, edit and activate releases, phases and run links", _L, _NO),
    Permission.RELEASE_SCHEDULE: PermissionSpec("Change release and phase dates and milestones", _L, _NO),
    Permission.RELEASE_GATE_EVALUATE: PermissionSpec("Evaluate a release or phase gate", _L, _YES),
    Permission.RELEASE_CHECKLIST_MANAGE: PermissionSpec("Manage readiness checklist items and evidence", _L, _PROPOSE),
    Permission.RELEASE_SIGNOFF: PermissionSpec("Sign a release as a required signer", _L, _NEVER),
    Permission.RELEASE_DECIDE: PermissionSpec("Record the go / no-go decision", _L, _NEVER),
    Permission.RELEASE_OVERRIDE_REQUEST: PermissionSpec("Request a gate override", _L, _NO),
    Permission.RELEASE_OVERRIDE_APPROVE: PermissionSpec("Approve someone else's gate override", _L, _NEVER),
    Permission.RELEASE_CALENDAR_MANAGE: PermissionSpec("Manage the release calendar and freeze windows", _L, _NO),
    Permission.RELEASE_DELETE: PermissionSpec("Delete releases, phases and run links", _A, _NEVER),
    Permission.GATE_POLICY_WRITE: PermissionSpec("Draft release gate policies", _A, _NO),
    Permission.GATE_POLICY_PUBLISH: PermissionSpec("Publish or deactivate release gate policies", _A, _NEVER),
    Permission.REPORT_PUBLISH: PermissionSpec("Publish status and completion reports and compliance packs", _L, _DRAFT),
    Permission.AUDIT_READ_PROJECT: PermissionSpec("Read one project's audit trail", _L, _NO),
    Permission.AI_CHAT: PermissionSpec("Ask AI (feature flags still apply)", _V, _YES),
    Permission.AGENT_PROPOSAL_APPROVE: PermissionSpec("Approve or reject an agent's proposal", _L, _NEVER),
    Permission.AGENT_MANAGE: PermissionSpec("Register agents, grant their permissions, revoke them", _A, _NEVER),
    Permission.APIKEY_CREATE_OWN: PermissionSpec("Mint my own API keys", _E, _NEVER),
    Permission.PROJECT_SETTINGS: PermissionSpec("Change one project's settings and integrations", _L, _NO),
    Permission.MEMBER_MANAGE: PermissionSpec("Add and remove project members and set their roles", _L, _NEVER),
    Permission.INSTANCE_ADMIN: PermissionSpec("Users, SSO/SCIM, flags, retention, storage, maintenance", _A, _NEVER),
}

#: An AGENT can never hold these, whatever its grants say (SoD-1).
AGENT_FORBIDDEN: frozenset[Permission] = frozenset(
    p for p, spec in PERMISSION_SPECS.items() if spec.agent_ceiling in (AgentCeiling.NO, AgentCeiling.NEVER)
)

_P = Permission

#: The job roles' and AGENT's explicit permission sets (03-role-model.md §6).
#: Conditional grants are listed here AND in ``ROLE_PERMISSION_CONDITIONS``.
_EXPLICIT_ROLE_PERMISSIONS: Mapping[UserRole, frozenset[Permission]] = {
    UserRole.RELEASE_MANAGER: frozenset({
        _P.PROJECT_READ, _P.REQUIREMENT_READ, _P.ANALYSIS_TRIGGER, _P.DEFECT_CREATE, _P.TESTPLAN_SCHEDULE,
        _P.RISK_WRITE, _P.RISK_ACCEPT, _P.RELEASE_WRITE, _P.RELEASE_SCHEDULE, _P.RELEASE_GATE_EVALUATE,
        _P.RELEASE_CHECKLIST_MANAGE, _P.RELEASE_SIGNOFF, _P.RELEASE_DECIDE, _P.RELEASE_OVERRIDE_REQUEST,
        _P.RELEASE_OVERRIDE_APPROVE, _P.RELEASE_CALENDAR_MANAGE, _P.GATE_POLICY_WRITE, _P.REPORT_PUBLISH,
        _P.AUDIT_READ_PROJECT, _P.AGENT_PROPOSAL_APPROVE, _P.APIKEY_CREATE_OWN, _P.AI_CHAT,
    }),
    UserRole.PROJECT_MANAGER: frozenset({
        _P.PROJECT_READ, _P.REQUIREMENT_READ, _P.TESTPLAN_SCHEDULE, _P.RISK_WRITE, _P.RELEASE_SCHEDULE,
        _P.RELEASE_SIGNOFF, _P.RELEASE_CALENDAR_MANAGE, _P.REPORT_PUBLISH, _P.AI_CHAT,
    }),
    UserRole.PRODUCT_MANAGER: frozenset({
        _P.PROJECT_READ, _P.REQUIREMENT_READ, _P.DEFECT_CREATE, _P.TESTPLAN_SCHEDULE, _P.TESTPLAN_EXECUTE,
        _P.REQUIREMENT_WRITE, _P.REQUIREMENT_LINK, _P.UAT_MANAGE, _P.ACCEPTANCE_SIGNOFF, _P.RISK_WRITE,
        _P.RISK_ACCEPT, _P.RELEASE_SIGNOFF, _P.AI_CHAT,
    }),
    UserRole.DEVELOPER: frozenset({
        _P.PROJECT_READ, _P.REQUIREMENT_READ, _P.RUN_INGEST, _P.FAILURE_TRIAGE_ASSIGNED,
        _P.CLASSIFICATION_CORRECT, _P.ANALYSIS_TRIGGER, _P.DEFECT_CREATE, _P.QUARANTINE_PROPOSE,
        _P.TESTCASE_WRITE, _P.TESTCASE_REVIEW, _P.REQUIREMENT_LINK, _P.RISK_WRITE, _P.AGENT_PROPOSAL_APPROVE,
        _P.APIKEY_CREATE_OWN, _P.AI_CHAT,
    }),
    # An agent's base: it reads. Everything else comes from its registration
    # grants (epic E01), never beyond its sponsor, never AGENT_FORBIDDEN.
    UserRole.AGENT: frozenset({_P.PROJECT_READ, _P.REQUIREMENT_READ}),
}


def _ladder_permissions(role: UserRole) -> frozenset[Permission]:
    idx = LADDER.index(role)
    return frozenset(p for p, spec in PERMISSION_SPECS.items() if idx >= LADDER.index(spec.legacy_min_rank))


#: Every role's permission set: computed for ladder roles, explicit for the rest.
ROLE_PERMISSIONS: Mapping[UserRole, frozenset[Permission]] = {
    **{role: _ladder_permissions(role) for role in LADDER},
    **_EXPLICIT_ROLE_PERMISSIONS,
}

#: Grants that hold only for some resources. The route guard admits the holder
#: only when the route declares that its handler checks the condition
#: (``conditions_checked_in_handler=True``); the handler then calls
#: ``check_condition``. Anywhere else a conditional grant counts as not held.
ROLE_PERMISSION_CONDITIONS: Mapping[tuple[UserRole, Permission], str] = {
    (UserRole.DEVELOPER, _P.FAILURE_TRIAGE_ASSIGNED): "owns_failure",
    (UserRole.DEVELOPER, _P.QUARANTINE_PROPOSE): "owns_failure",
    (UserRole.DEVELOPER, _P.AGENT_PROPOSAL_APPROVE): "own_domain_proposal",
    (UserRole.RELEASE_MANAGER, _P.AGENT_PROPOSAL_APPROVE): "own_domain_proposal",
    (UserRole.PROJECT_MANAGER, _P.RELEASE_SIGNOFF): "listed_in_signoff_matrix",
}

#: Condition name -> ``(user, resource) -> bool``. Epics register theirs
#: (E08: ``owns_failure``); an unregistered condition is never satisfied.
_CONDITIONS: dict[str, Callable[[Any, Any], bool]] = {}

#: ``frozenset()`` = unconditional; otherwise every named condition must hold.
Conditions = frozenset[str]
Grants = Mapping[Permission, Conditions]


def register_condition(name: str, check: Callable[[Any, Any], bool]) -> None:
    _CONDITIONS[name] = check


def check_condition(conditions: Iterable[str], user: Any, resource: Any) -> bool:
    """True when every named condition holds for ``user`` on ``resource``."""
    for name in conditions:
        check = _CONDITIONS.get(name)
        if check is None or not check(user, resource):
            return False
    return True


# ── Whose roles, which grants ────────────────────────────────────────────────


def _job_roles(values: Any) -> tuple[UserRole, ...]:
    """The valid extra job roles in a stored ``additional_roles`` value."""
    if not values:
        return ()
    if isinstance(values, str):
        values = [values]
    out: list[UserRole] = []
    for value in values:
        try:
            role = normalize_role(value)
        except ValueError:
            continue
        if role in JOB_ROLES and role not in out:
            out.append(role)
    return tuple(out)


def job_roles_in(values: Any) -> tuple[UserRole, ...]:
    """The valid extra job roles in a stored ``additional_roles`` value, in enum order."""
    found = set(_job_roles(values))
    return tuple(role for role in EXTRA_ROLE_CHOICES if role in found)


def clean_additional_roles(values: Any, *, primary: UserRole | str | None = None) -> list[str]:
    """Extra roles as stored: job roles only, in enum order, no repeats, never the primary role.

    ``ValueError`` naming the first value that is not a job role (a ladder role,
    AGENT or an unknown string) -- the API answers it with a 400. ``None`` or an
    empty value is ``[]``.
    """
    if not values:
        return []
    if isinstance(values, str):
        values = [values]
    wanted: set[UserRole] = set()
    for value in values:
        try:
            role: Optional[UserRole] = normalize_role(value)
        except ValueError:
            role = None
        if role is None or role not in JOB_ROLES:
            allowed = ", ".join(r.value for r in EXTRA_ROLE_CHOICES)
            raise ValueError(f"'{getattr(value, 'value', value)}' cannot be an extra role (allowed: {allowed})")
        wanted.add(role)
    skip = normalize_role(primary) if primary is not None else None
    return [role.value for role in EXTRA_ROLE_CHOICES if role in wanted and role != skip]


def assigned_roles(primary: UserRole | str, additional: Any = None) -> tuple[UserRole, ...]:
    """Primary role first, then valid extra job roles. ``ValueError`` for an unknown primary.

    An AGENT carries no extra roles; neither does a stored value that is a
    ladder role, an unknown string, or AGENT (defensive: the API refuses them).
    """
    role = normalize_role(primary)
    if role == UserRole.AGENT:
        return (role,)
    return (role, *(r for r in _job_roles(additional) if r != role))


def user_roles(user: Any) -> tuple[UserRole, ...]:
    """A user's instance roles (``users.role`` + ``users.additional_roles``)."""
    return assigned_roles(user.role, getattr(user, "additional_roles", None))


def grants_for(roles: Iterable[UserRole]) -> dict[Permission, Conditions]:
    """Permission -> conditions for a set of roles held together.

    Any role granting a permission unconditionally makes it unconditional;
    otherwise the first condition found applies.
    """
    out: dict[Permission, Conditions] = {}
    for role in roles:
        for perm in ROLE_PERMISSIONS.get(role, frozenset()):
            cond = ROLE_PERMISSION_CONDITIONS.get((role, perm))
            conditions: Conditions = frozenset({cond}) if cond else frozenset()
            if perm not in out:
                out[perm] = conditions
            elif not conditions:
                out[perm] = frozenset()  # another role grants it outright
    return out


def agent_grants(base: Iterable[UserRole], registered: Iterable[Permission] = ()) -> dict[Permission, Conditions]:
    """An agent's grants: its role's base plus registration grants, minus AGENT_FORBIDDEN."""
    out = grants_for(base)
    for perm in registered:
        out.setdefault(perm, frozenset())
    return {p: c for p, c in out.items() if p not in AGENT_FORBIDDEN}


def intersect(a: Grants, b: Grants) -> dict[Permission, Conditions]:
    """Permissions held on both sides; a permission conditional on either side keeps those conditions."""
    return {p: a[p] | b[p] for p in a.keys() & b.keys()}


def effective_grants(
    instance_roles: Iterable[UserRole],
    project_roles: Optional[Iterable[UserRole]],
    *,
    is_write: bool,
) -> dict[Permission, Conditions]:
    """What a caller may do in one project.

    No membership row (``project_roles is None``): the instance roles (the
    membership guard refuses a non-member separately). A read: the project
    roles. A write: the intersection of both -- a project role can lower what
    someone may do there, never raise it.
    """
    instance = _for(instance_roles)
    if project_roles is None:
        return instance
    project = _for(project_roles)
    return intersect(instance, project) if is_write else project


def _for(roles: Iterable[UserRole]) -> dict[Permission, Conditions]:
    roles = tuple(roles)
    if UserRole.AGENT in roles:
        return agent_grants(roles)
    return grants_for(roles)


def has_permission(
    roles: Iterable[UserRole], permission: Permission, *, allow_conditional: bool = False
) -> bool:
    """Whether ``roles`` (held together) grant ``permission``.

    A conditional grant counts only with ``allow_conditional=True`` -- the caller
    then owes a ``check_condition`` on the resource.
    """
    grants = _for(roles)
    if permission not in grants:
        return False
    return allow_conditional or not grants[permission]


def granted(grants: Grants, permission: Permission, *, allow_conditional: bool = False) -> bool:
    if permission not in grants:
        return False
    return allow_conditional or not grants[permission]


def roles_holding(permission: Permission) -> list[UserRole]:
    """Roles that hold ``permission`` (outright or conditionally), ladder first, for messages."""
    return [role for role in UserRole if permission in ROLE_PERMISSIONS.get(role, frozenset())]


def permission_refusal_detail(permission: Permission, *, in_project: bool = False) -> str:
    holders = ", ".join(role.value for role in roles_holding(permission) if role != UserRole.AGENT)
    where = " in this project" if in_project else ""
    return f"Requires the '{permission.value}' permission{where} (held by: {holders})"


# ── Granting roles ───────────────────────────────────────────────────────────


#: Roles only an administrator gives (owner decision D-17, 2026-11-26): the release, project and product
#: managers own sign-offs, schedules and acceptance, so who holds them is not a project lead's call even where
#: every permission of the role is one the lead holds. (AGENT is never given here at all.)
ADMIN_ONLY_GRANTS: frozenset[UserRole] = frozenset({
    UserRole.RELEASE_MANAGER,
    UserRole.PROJECT_MANAGER,
    UserRole.PRODUCT_MANAGER,
})


def can_grant(
    granter_roles: Iterable[UserRole], role: UserRole | str, *, grants: Optional[Grants] = None
) -> bool:
    """Whether someone holding ``granter_roles`` may give ``role`` to another person.

    Every permission of ``role`` (conditional ones included) must be a
    permission the granter holds. For two ladder roles this is "not above your
    own rank", the rule the members router has always applied. A role in
    ``ADMIN_ONLY_GRANTS`` also needs ``instance.admin`` (D-17). AGENT is
    granted only through agent registration (epic E01), never here; ADMIN
    callers are handled by the router before this is asked.

    ``grants`` (when given) replaces the granter's own grants: the members
    routes pass what the granter may do in THAT project (``effective_grants``),
    so a project role that caps them caps what they hand out there too.
    """
    target = normalize_role(role)
    if target == UserRole.AGENT:
        return False
    held = grants if grants is not None else grants_for(tuple(granter_roles))
    if target in ADMIN_ONLY_GRANTS and Permission.INSTANCE_ADMIN not in held:
        return False
    return ROLE_PERMISSIONS.get(target, frozenset()) <= held.keys()


def missing_for_grant(
    granter_roles: Iterable[UserRole], role: UserRole | str, *, grants: Optional[Grants] = None
) -> list[str]:
    """The permissions ``role`` has that the granter lacks, sorted, for refusal messages."""
    held = grants if grants is not None else grants_for(tuple(granter_roles))
    return sorted(p.value for p in ROLE_PERMISSIONS.get(normalize_role(role), frozenset()) - held.keys())
