import { useAuthStore } from '@/store/authStore'

type Role = 'VIEWER' | 'TESTER' | 'QA_ENGINEER' | 'QA_LEAD' | 'ADMIN'

const ROLE_ORDER: Role[] = ['VIEWER', 'TESTER', 'QA_ENGINEER', 'QA_LEAD', 'ADMIN']

function normalizeRole(role: string | null | undefined): Role {
  const rawRole = String(role ?? 'VIEWER').trim()
  const normalized = rawRole.startsWith('UserRole.') ? rawRole.slice('UserRole.'.length) : rawRole
  return ROLE_ORDER.includes(normalized as Role) ? (normalized as Role) : 'VIEWER'
}

export function usePermissions() {
  const user = useAuthStore((s) => s.user)
  const role = normalizeRole(user?.role)

  function hasRole(minRole: Role): boolean {
    return ROLE_ORDER.indexOf(role) >= ROLE_ORDER.indexOf(minRole)
  }

  return {
    role,
    isAdmin: role === 'ADMIN',
    isQaLead: hasRole('QA_LEAD'),
    isQaEngineer: hasRole('QA_ENGINEER'),
    canManageUsers: role === 'ADMIN',
    canManageProjectMembers: hasRole('QA_LEAD'),
    canGenerateApiKeys: hasRole('QA_ENGINEER'),
    canTriggerLlm: hasRole('QA_ENGINEER'),
    canAccessManagement: hasRole('QA_LEAD'),
    canViewSettings: hasRole('QA_LEAD'),
    canEditSettings: role === 'ADMIN',
    hasRole,
  }
}
