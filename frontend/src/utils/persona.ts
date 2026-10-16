/**
 * "My role first" in a run's Recommended actions (FAIL-07, owner decision D03:
 * no persona tabs, no segmented control, nothing stored — the reader's own
 * role's row simply comes first).
 *
 * The roles are TestLookup's (`VIEWER, TESTER, QA_ENGINEER, QA_LEAD, ADMIN`);
 * the rows are the analysis's audiences. A tester, QA engineer or QA lead
 * acts on the QA row; an admin decides the release (Release Manager). A
 * viewer, or a role this map does not know, keeps the default order
 * (Developer → QA → Release Manager → SRE). There is no developer role, so
 * nobody is moved onto the Developer row (it is first already).
 */

export type ActionRowId = 'DEVELOPER' | 'QA' | 'RELEASE_MANAGER' | 'SRE'

export const ROLE_ACTION_ROW: Readonly<Record<string, ActionRowId>> = {
  TESTER: 'QA',
  QA_ENGINEER: 'QA',
  QA_LEAD: 'QA',
  ADMIN: 'RELEASE_MANAGER',
}

/** The row a role acts on; `null` for a viewer or an unknown role. `UserRole.X` spellings read as `X`. */
export function actionRowForRole(role: string | null | undefined): ActionRowId | null {
  if (!role) return null
  const raw = String(role).trim()
  const normalized = raw.startsWith('UserRole.') ? raw.slice('UserRole.'.length) : raw
  return Object.prototype.hasOwnProperty.call(ROLE_ACTION_ROW, normalized) ? ROLE_ACTION_ROW[normalized] : null
}

/**
 * `rows` with the role's row moved to the front, every other row in its
 * relative order. `movedId` is the row that moved — `null` when the role maps
 * to no row, or its row is already first (nothing to point out).
 */
export function orderActionRows<T extends { id: string }>(
  rows: readonly T[],
  role: string | null | undefined,
): { rows: T[]; movedId: ActionRowId | null } {
  const target = actionRowForRole(role)
  const index = target === null ? -1 : rows.findIndex((r) => r.id === target)
  if (target === null || index <= 0) return { rows: [...rows], movedId: null }
  return { rows: [rows[index], ...rows.slice(0, index), ...rows.slice(index + 1)], movedId: target }
}
