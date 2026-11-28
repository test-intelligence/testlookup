import { describe, expect, it } from 'vitest'
import { jobRolesIn, normalizeRole, roleAtLeast, roleRank } from './roles'
import { canWith } from '@/hooks/useMyPermissions'
import { ROLE_INFO, rolesSummary } from '@/content/roles'
import { ALL_ROLES, ASSIGNABLE_ROLES } from '@/types/permissions'
import type { MyPermissions } from '@/types/permissions'

describe('role ranks (enterprise roles E00)', () => {
  it('the five new roles normalise to themselves and rank as VIEWER', () => {
    for (const role of ['RELEASE_MANAGER', 'PROJECT_MANAGER', 'PRODUCT_MANAGER', 'DEVELOPER', 'AGENT']) {
      expect(normalizeRole(role)).toBe(role)
      expect(normalizeRole(`UserRole.${role}`)).toBe(role)
      expect(roleRank(role)).toBe('VIEWER')
      expect(roleAtLeast(role, 'TESTER')).toBe(false)
      expect(roleAtLeast(role, 'VIEWER')).toBe(true)
    }
  })

  it('ladder roles keep their rank and unknown strings are VIEWER', () => {
    expect(roleRank('QA_LEAD')).toBe('QA_LEAD')
    expect(roleAtLeast('ADMIN', 'QA_LEAD')).toBe(true)
    expect(roleAtLeast('QA_ENGINEER', 'QA_LEAD')).toBe(false)
    expect(normalizeRole('SUPERUSER')).toBe('VIEWER')
    expect(normalizeRole(null)).toBe('VIEWER')
  })

  it('stored extra roles keep only job roles, in catalogue order', () => {
    expect(jobRolesIn(['DEVELOPER', 'ADMIN', 'AGENT', 'RELEASE_MANAGER', 'junk'])).toEqual(['RELEASE_MANAGER', 'DEVELOPER'])
    expect(jobRolesIn(undefined)).toEqual([])
  })

  it('every role has a label and a description; a person is never offered AGENT', () => {
    for (const role of ALL_ROLES) {
      expect(ROLE_INFO[role].label).toBeTruthy()
      expect(ROLE_INFO[role].description).toBeTruthy()
    }
    expect(ASSIGNABLE_ROLES).not.toContain('AGENT')
    expect(rolesSummary('QA_ENGINEER', ['RELEASE_MANAGER'])).toBe('QA Engineer + Release Manager')
  })
})

describe('can() from /me/permissions', () => {
  const perms: MyPermissions = {
    role: 'VIEWER', additional_roles: [], project_id: 'p1', project_role: 'RELEASE_MANAGER',
    project_additional_roles: [], read_permissions: ['project.read', 'release.write'],
    write_permissions: ['project.read'], conditional: {},
  }

  it('is false while loading', () => {
    expect(canWith(undefined, 'project.read')).toBe(false)
  })

  it('asks about writes by default and reads on request (the lower of the two decides a write)', () => {
    expect(canWith(perms, 'release.write')).toBe(false)
    expect(canWith(perms, 'release.write', { write: false })).toBe(true)
    expect(canWith(perms, 'project.read')).toBe(true)
  })
})
