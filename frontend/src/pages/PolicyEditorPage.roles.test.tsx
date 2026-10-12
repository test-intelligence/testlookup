/**
 * Release-gate policies: every write is ADMIN on the API.
 *
 * `routers/release_gate_policies.py` guards create, patch, publish, deactivate
 * and simulate with `require_role(UserRole.ADMIN)`. The page is a management
 * route, so a QA lead reaches it -- and was offered New Policy, Save Draft,
 * Publish, Deactivate and Simulate, each of which answered 403. A QA lead now
 * reads the policies; the controls are disabled with the role they need, or
 * not drawn where there is nothing to explain (Deactivate on a row).
 */
import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import PolicyEditorPage from './PolicyEditorPage'
import { useAuthStore } from '@/store/authStore'
import { DEFAULT_HARD_CAPS, DEFAULT_KIND_RULES, DEFAULT_PASS_RATE_BANDS, DEFAULT_THRESHOLDS, DEFAULT_WEIGHTS } from '../services/policyService'

const ACTIVE = {
  id: 'p-active', name: 'Checkout gate', version: 2, is_active: true, is_draft: false,
  project_id: null, created_at: '2026-10-01T00:00:00Z', description: '',
  rules: {},
}
const DRAFT = {
  id: 'p-draft', name: 'Draft gate', version: 3, is_active: false, is_draft: true,
  project_id: null, created_at: '2026-10-02T00:00:00Z', description: '',
  rules: {
    schema_version: 1,
    thresholds: { ...DEFAULT_THRESHOLDS },
    dimension_weights: { ...DEFAULT_WEIGHTS },
    rules: [],
    pass_rate_bands: { ...DEFAULT_PASS_RATE_BANDS },
    hard_caps: { ...DEFAULT_HARD_CAPS },
    kind_rules: { ...DEFAULT_KIND_RULES },
  },
}

vi.mock('@/hooks/usePolicyEditor', () => ({
  usePolicies: () => ({ policies: [ACTIVE, DRAFT], isLoading: false, isError: false, mutate: vi.fn() }),
  usePolicy: (id: string | undefined) => ({
    policy: id === DRAFT.id ? DRAFT : null, isLoading: false, isError: false,
  }),
}))

vi.mock('../services/policyService', async () => {
  const actual = await vi.importActual<typeof import('../services/policyService')>(
    '../services/policyService',
  )
  return {
    ...actual,
    createPolicy: vi.fn(),
    updatePolicy: vi.fn(),
    publishPolicy: vi.fn(),
    deactivatePolicy: vi.fn(),
    simulatePolicy: vi.fn(),
  }
})

vi.mock('react-hot-toast', () => ({ default: { error: vi.fn(), success: vi.fn() } }))

function renderAt(path: string) {
  return render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="policies" element={<PolicyEditorPage />} />
          <Route path="policies/:policyId" element={<PolicyEditorPage />} />
        </Routes>
      </MemoryRouter>
    </SWRConfig>,
  )
}

const signInAs = (role: string) => useAuthStore.setState({ user: { id: role, role } as never })

describe('a QA lead reads release-gate policies', () => {
  beforeEach(() => signInAs('QA_LEAD'))

  it('disables New Policy with the role it needs and offers no Deactivate', () => {
    renderAt('/policies')
    const create = screen.getByRole('button', { name: /new policy/i })
    expect(create).toBeDisabled()
    expect(create).toHaveAttribute('title', 'Admin role required')
    expect(screen.queryByRole('button', { name: /deactivate/i })).not.toBeInTheDocument()
    // A draft opens for reading, not editing.
    expect(screen.queryByRole('button', { name: /^edit$/i })).not.toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: /^view$/i })).toHaveLength(2)
  })

  it('shows a draft read-only: no Save Draft or Publish, fields disabled, Simulate needs an admin', () => {
    renderAt(`/policies/${DRAFT.id}`)
    expect(screen.getByRole('heading', { name: /view: draft gate/i })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /save draft/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /publish/i })).not.toBeInTheDocument()
    expect(screen.getByPlaceholderText(/policy name/i)).toBeDisabled()
    const simulate = screen.getByRole('button', { name: /^simulate$/i })
    expect(simulate).toBeDisabled()
    expect(simulate).toHaveAttribute('title', 'Admin role required')
    expect(screen.getByText(/an administrator creates, edits, publishes and simulates/i)).toBeInTheDocument()
  })
})

describe('an admin authors them (the control)', () => {
  beforeEach(() => signInAs('ADMIN'))

  it('offers New Policy, Edit and Deactivate on the list', () => {
    renderAt('/policies')
    expect(screen.getByRole('button', { name: /new policy/i })).toBeEnabled()
    expect(screen.getByRole('button', { name: /deactivate/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^edit$/i })).toBeInTheDocument()
  })

  it('edits a draft: Save Draft, Publish and Simulate are live', () => {
    renderAt(`/policies/${DRAFT.id}`)
    expect(screen.getByRole('button', { name: /save draft/i })).toBeEnabled()
    expect(screen.getByRole('button', { name: /publish/i })).toBeEnabled()
    expect(screen.getByRole('button', { name: /^simulate$/i })).toBeEnabled()
    expect(screen.getByPlaceholderText(/policy name/i)).toBeEnabled()
    expect(screen.queryByText(/an administrator creates/i)).not.toBeInTheDocument()
  })
})
