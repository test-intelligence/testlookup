/**
 * Roles and permissions (enterprise roles, epic E00).
 *
 * `Permission` mirrors `backend/app/core/permissions.py::Permission`;
 * `backend/tests/test_spa_permission_parity.py` keeps the two lists equal.
 */

/** The five ranks, lowest first. Every `require_role(X)` on the server compares these. */
export type RankRole = 'VIEWER' | 'TESTER' | 'QA_ENGINEER' | 'QA_LEAD' | 'ADMIN'

/** Job roles: a person may hold them as their role or as extra roles. Their rank is VIEWER. */
export type JobRole = 'RELEASE_MANAGER' | 'PROJECT_MANAGER' | 'PRODUCT_MANAGER' | 'DEVELOPER'

/** Every role. AGENT belongs to registered AI agents, never to a person. */
export type Role = RankRole | JobRole | 'AGENT'

export const RANK_ROLES: readonly RankRole[] = ['VIEWER', 'TESTER', 'QA_ENGINEER', 'QA_LEAD', 'ADMIN']
export const JOB_ROLES: readonly JobRole[] = ['RELEASE_MANAGER', 'PROJECT_MANAGER', 'PRODUCT_MANAGER', 'DEVELOPER']
export const ALL_ROLES: readonly Role[] = [...RANK_ROLES, ...JOB_ROLES, 'AGENT']
/**
 * Roles only an administrator gives (owner decision D-17); mirrors `permissions.ADMIN_ONLY_GRANTS`. A QA lead
 * managing members is not offered them.
 */
export const ADMIN_ONLY_GRANTS: readonly JobRole[] = ['RELEASE_MANAGER', 'PROJECT_MANAGER', 'PRODUCT_MANAGER']

/** Roles an administrator may give a person (AGENT comes from agent registration). */
export const ASSIGNABLE_ROLES: readonly Role[] = [...RANK_ROLES, ...JOB_ROLES]

export type Permission =
  | 'project.read'
  | 'requirement.read'
  | 'run.ingest'
  | 'run.delete'
  | 'failure.triage.assigned'
  | 'failure.triage.any'
  | 'classification.correct'
  | 'analysis.trigger'
  | 'defect.create'
  | 'quarantine.propose'
  | 'quarantine.decide'
  | 'testcase.write'
  | 'testcase.review'
  | 'testcase.approve'
  | 'testplan.write'
  | 'testplan.schedule'
  | 'testplan.execute'
  | 'ownership.manage'
  | 'taxonomy.manage'
  | 'requirement.write'
  | 'requirement.link'
  | 'uat.manage'
  | 'acceptance.signoff'
  | 'risk.write'
  | 'risk.accept'
  | 'release.write'
  | 'release.schedule'
  | 'release.gate.evaluate'
  | 'release.checklist.manage'
  | 'release.signoff'
  | 'release.decide'
  | 'release.override.request'
  | 'release.override.approve'
  | 'release.calendar.manage'
  | 'release.delete'
  | 'gate_policy.write'
  | 'gate_policy.publish'
  | 'report.publish'
  | 'audit.read.project'
  | 'ai.chat'
  | 'agent.proposal.approve'
  | 'agent.manage'
  | 'apikey.create.own'
  | 'project.settings'
  | 'member.manage'
  | 'instance.admin'

/** `GET /api/v1/me/permissions` */
export interface MyPermissions {
  role: Role
  additional_roles: JobRole[]
  project_id: string | null
  project_role: Role | null
  project_additional_roles: JobRole[]
  /** Reads: the project roles decide. */
  read_permissions: Permission[]
  /** Writes: instance roles and project roles intersect ("the lower of the two decides"). */
  write_permissions: Permission[]
  /** A permission granted only when the server's check on the resource passes (e.g. `owns_failure`). */
  conditional: Partial<Record<Permission, string[]>>
}
