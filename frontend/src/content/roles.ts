/**
 * Role names and one-line descriptions shown wherever a role is picked or shown (enterprise roles E00-S05).
 * Wording follows docs/enterprise-roles-2026-11-26/03-role-model.md §4.
 */
import { ASSIGNABLE_ROLES, JOB_ROLES, RANK_ROLES, type JobRole, type Role } from '@/types/permissions'

export interface RoleInfo {
  label: string
  description: string
}

export const ROLE_INFO: Record<Role, RoleInfo> = {
  ADMIN: { label: 'Admin', description: 'Runs the instance: users, SSO, flags, retention, integrations.' },
  QA_LEAD: { label: 'QA Lead', description: 'Owns quality for a project: quarantine, test-case approval, QA sign-off.' },
  QA_ENGINEER: { label: 'QA Engineer', description: 'Writes, runs and triages tests; files defects.' },
  TESTER: { label: 'Tester', description: 'Runs manual and acceptance tests assigned to them.' },
  VIEWER: { label: 'Viewer', description: 'Reads everything in their projects; changes nothing.' },
  RELEASE_MANAGER: {
    label: 'Release Manager',
    description: 'Owns each release end to end: readiness, sign-offs, the go / no-go call.',
  },
  PROJECT_MANAGER: {
    label: 'Project Manager',
    description: 'Owns the schedule: phase dates, plan assignees, risks, status reports.',
  },
  PRODUCT_MANAGER: {
    label: 'Product Manager',
    description: 'Owns requirements and acceptance: acceptance criteria, UAT, acceptance sign-off.',
  },
  DEVELOPER: {
    label: 'Developer',
    description: 'Fixes what breaks: triages their own failures, proposes quarantine, links fixes.',
  },
  AGENT: { label: 'AI Agent', description: 'A registered AI agent: reads, analyses and files proposals a person approves.' },
}

export function roleLabel(role: string | null | undefined): string {
  return ROLE_INFO[role as Role]?.label ?? String(role ?? '')
}

/** Primary-role options for a person (never AGENT), ranks first. */
export const PERSON_ROLE_OPTIONS = ASSIGNABLE_ROLES.map((value) => ({ value, ...ROLE_INFO[value] }))

/** Extra-role options: the four job roles. */
export const EXTRA_ROLE_OPTIONS = JOB_ROLES.map((value) => ({ value, ...ROLE_INFO[value] }))

/** Short text for a role plus its extra roles, e.g. "QA Engineer + Release Manager". */
export function rolesSummary(role: string | null | undefined, extras: readonly string[] | null | undefined): string {
  const names = [roleLabel(role), ...(extras ?? []).map(roleLabel)]
  return names.filter(Boolean).join(' + ')
}

export function isJobRole(role: string | null | undefined): role is JobRole {
  return (JOB_ROLES as readonly string[]).includes(String(role))
}

export { RANK_ROLES }
