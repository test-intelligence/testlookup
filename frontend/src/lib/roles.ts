/**
 * The SPA's one role order and rank helpers (enterprise roles E00). Pure: no React, no request.
 *
 * Mirrors `backend/app/core/permissions.py`: five ranks; a job role (RELEASE_MANAGER, …) or AGENT ranks as
 * VIEWER on anything that names no permission. A quality-gate guard refuses `ROLE_ORDER.indexOf(` anywhere
 * but here, `types/permissions.ts` and `hooks/usePermissions.ts`. That shell hook keeps its own five-rank copy
 * (and reads a job role as VIEWER) because it is in the eager bundle, which has a few bytes of headroom: load
 * this module from lazily loaded pages only.
 */
import { ALL_ROLES, JOB_ROLES, RANK_ROLES, type JobRole, type RankRole, type Role } from '@/types/permissions'

/** The ladder, lowest first. */
const ROLE_ORDER: readonly RankRole[] = RANK_ROLES

/** A stored role string as a `Role`; anything unknown is VIEWER (fail closed). */
export function normalizeRole(role: string | null | undefined): Role {
  const rawRole = String(role ?? 'VIEWER').trim()
  const normalized = rawRole.startsWith('UserRole.') ? rawRole.slice('UserRole.'.length) : rawRole
  return (ALL_ROLES as readonly string[]).includes(normalized) ? (normalized as Role) : 'VIEWER'
}

/** The rank a role counts as: itself for the five ranks, VIEWER for job roles and AGENT. */
export function roleRank(role: string | null | undefined): RankRole {
  const normalized = normalizeRole(role)
  return (ROLE_ORDER as readonly string[]).includes(normalized) ? (normalized as RankRole) : 'VIEWER'
}

/** `role`'s rank is at least `minRole`. */
export function roleAtLeast(role: string | null | undefined, minRole: RankRole): boolean {
  return ROLE_ORDER.indexOf(roleRank(role)) >= ROLE_ORDER.indexOf(minRole)
}

/** The extra (job) roles a stored value holds, in catalogue order; anything else is dropped. */
export function jobRolesIn(values: readonly string[] | null | undefined): JobRole[] {
  const held = new Set(values ?? [])
  return JOB_ROLES.filter((r) => held.has(r))
}
