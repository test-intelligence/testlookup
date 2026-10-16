/**
 * W08 TRND-08: `/ownership?team=<name>` (the link from Value metrics › By
 * team) keeps only that team's rules — equal ignoring case and surrounding
 * space — with a `Team: {team} · Show all` chip that clears the filter.
 */
import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import OwnershipEditorPage, { sameTeam } from './OwnershipEditorPage'

const rule = (id: string, team: string, pattern: string) => ({
  id,
  project_id: 'proj-1',
  match_type: 'suite_name',
  match_pattern: pattern,
  service_name: `${id}-service`,
  team_name: team,
  team_contact: null,
  priority: 1,
  is_active: true,
  created_by: null,
  created_at: '2026-10-01T00:00:00Z',
  updated_at: null,
})

vi.mock('@/hooks/useOwnershipRules', () => ({
  useOwnershipRules: () => ({
    rules: [rule('r1', 'Payments', 'pay-x*'), rule('r2', '  payments ', 'chk-x*'), rule('r3', 'Identity', 'idt-x*')],
    isLoading: false,
    isError: false,
    refresh: vi.fn(),
  }),
  useCodeownersCoverage: () => ({ coverage: null, refresh: vi.fn() }),
}))
vi.mock('@/hooks/useSourceRoots', () => ({
  useSourceRoots: () => ({
    roots: [], maxRoots: 20, isLoading: false, isError: false,
    refresh: vi.fn(), save: vi.fn(), preview: vi.fn(),
  }),
}))
vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: '__ALL__',
  useProjectStore: vi.fn((selector: (s: { activeProjectId: string; projects: unknown[]; setActiveProject: () => void }) => unknown) =>
    selector({ activeProjectId: 'proj-1', projects: [], setActiveProject: () => {} })),
}))
vi.mock('react-hot-toast', () => ({ default: { success: vi.fn(), error: vi.fn() } }))

function Location() {
  const location = useLocation()
  return <output data-testid="location">{`${location.pathname}${location.search}`}</output>
}

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <OwnershipEditorPage />
      <Location />
    </MemoryRouter>,
  )
}

const patterns = () => ['pay-x*', 'chk-x*', 'idt-x*'].filter((p) => screen.queryByText(p) !== null)

describe('OwnershipEditorPage ?team= filter (W08 TRND-08)', () => {
  it('?team=Payments keeps only that team’s rules (case and surrounding space ignored), with the chip', () => {
    renderAt('/ownership?team=Payments')
    expect(patterns()).toEqual(['pay-x*', 'chk-x*'])
    const chip = document.querySelector('[data-ownership-team-filter]') as HTMLElement
    expect(chip).toHaveTextContent('Team: Payments · Show all')
  })

  it('Show all clears the filter and every rule is listed again', () => {
    renderAt('/ownership?team=Payments')
    fireEvent.click(screen.getByRole('button', { name: 'Show all' }))
    expect(screen.getByTestId('location')).toHaveTextContent(/^\/ownership$/)
    expect(patterns()).toEqual(['pay-x*', 'chk-x*', 'idt-x*'])
    expect(document.querySelector('[data-ownership-team-filter]')).toBeNull()
  })

  it('no ?team= → every rule and no chip', () => {
    renderAt('/ownership')
    expect(patterns()).toEqual(['pay-x*', 'chk-x*', 'idt-x*'])
    expect(document.querySelector('[data-ownership-team-filter]')).toBeNull()
  })

  it('a team no rule names says so (and the chip still clears it); a hostile name is text', () => {
    renderAt(`/ownership?team=${encodeURIComponent('<img src=x onerror=alert(1)>')}`)
    expect(patterns()).toEqual([])
    expect(screen.getByText('No ownership rule names this team.')).toBeInTheDocument()
    expect(document.querySelector('[data-ownership-team-filter] img')).toBeNull()
    expect(document.querySelector('[data-ownership-team-filter]')).toHaveTextContent('Team: <img src=x onerror=alert(1)>')
  })

  it('sameTeam compares ignoring case and surrounding space', () => {
    expect(sameTeam('  Payments ', 'payments')).toBe(true)
    expect(sameTeam('Payments', 'Pay ments')).toBe(false)
    expect(sameTeam(null, '')).toBe(true)
  })
})
