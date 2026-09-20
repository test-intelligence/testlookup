/**
 * `/policies` must name the project a policy governs, not its id.
 *
 * TL-2026-09-19-01-010, observed live. The list rendered
 * `project_id.slice(0, 8)` — three policies for three different projects, told
 * apart only by comparing hex fragments, on the one surface whose job is to say
 * which thresholds apply where:
 *
 *     qa-probe-policy-1            Project: d4a8d31f...
 *     Gate Probe Policy            Project: 647c2ee4...
 *     ZZ probe policy - delete me  Project: 9af8eb8f...
 *
 * `/releases` renders the same relationship with real names, so this was an
 * inconsistency rather than a data limitation.
 */
import { render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi, beforeEach } from 'vitest'

// The page reads the list through this hook, not the service directly.
const policiesRef: { current: unknown[] } = { current: [] }

vi.mock('@/hooks/usePolicyEditor', () => ({
  usePolicies: () => ({
    policies: policiesRef.current,
    isLoading: false,
    isError: false,
    mutate: vi.fn(),
  }),
  usePolicy: () => ({ policy: undefined, isLoading: false, isError: false }),
}))

vi.mock('react-router-dom', async () => ({
  ...(await vi.importActual<object>('react-router-dom')),
  useNavigate: () => vi.fn(),
  useParams: () => ({}),
}))

vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: 'all',
  useProjectStore: (sel: (s: { projects: Array<{ id: string; name: string }> }) => unknown) =>
    sel({
      projects: [
        { id: 'd4a8d31f-719b-4d0b-8474-fd9d4da8bee3', name: 'Inventory Service' },
        { id: '647c2ee4-4452-4fad-bdbd-3577c9131d2a', name: 'Checkout Service' },
      ],
    }),
}))

import PolicyEditorPage from './PolicyEditorPage'

const policies = [
  { id: 'p1', name: 'Known project', version: 1, is_active: false, is_draft: true,
    project_id: 'd4a8d31f-719b-4d0b-8474-fd9d4da8bee3', created_at: '2026-09-09T00:00:00Z' },
  { id: 'p2', name: 'Other known project', version: 1, is_active: true, is_draft: false,
    project_id: '647c2ee4-4452-4fad-bdbd-3577c9131d2a', created_at: '2026-08-15T00:00:00Z' },
  { id: 'p3', name: 'Project the user cannot see', version: 1, is_active: false, is_draft: true,
    project_id: '9af8eb8f-064e-4d8d-8d90-98ec991dc62b', created_at: '2026-08-09T00:00:00Z' },
  { id: 'p4', name: 'System wide', version: 1, is_active: true, is_draft: false,
    project_id: null, created_at: '2026-08-01T00:00:00Z' },
]

describe('/policies project labels', () => {
  beforeEach(() => {
    policiesRef.current = policies
  })

  it('names the project instead of a truncated uuid', async () => {
    render(<PolicyEditorPage />)
    await waitFor(() => expect(screen.getByText(/Inventory Service/)).toBeInTheDocument())
    expect(screen.getByText(/Checkout Service/)).toBeInTheDocument()
    expect(screen.queryByText(/d4a8d31f\.\.\./)).not.toBeInTheDocument()
    expect(screen.queryByText(/647c2ee4\.\.\./)).not.toBeInTheDocument()
  })

  it('falls back to the short id for a project the user cannot see', async () => {
    // Better a short id than a blank, or a confidently wrong name.
    render(<PolicyEditorPage />)
    await waitFor(() => expect(screen.getByText(/9af8eb8f\.\.\./)).toBeInTheDocument())
  })

  it('still calls a null project_id the system default', async () => {
    render(<PolicyEditorPage />)
    await waitFor(() => expect(screen.getByText(/System Default/)).toBeInTheDocument())
  })
})
