/**
 * FeatureFlagsPage — ADMIN flag management (Settings › System).
 *
 * No coverage before UX redesign P5. What P5 changed is the header: compact,
 * with the route's help topic, "New Flag" its one primary action. The page is a
 * table, so its layout did not move; the tests also pin the table rows and the
 * create form (already two fields per row) opening from that action.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import FeatureFlagsPage from './FeatureFlagsPage'
import type { FeatureFlag } from '@/services/featureFlagService'
import { expectTemplateHeader } from '@/test/expectTemplateHeader'

const FLAG: FeatureFlag = {
  id: 'flag-1',
  key: 'manual_upload',
  description: 'Upload report files by hand',
  enabled_global: true,
  enabled_projects: null,
  enabled_roles: null,
  rollout_percent: 100,
  created_at: '2026-09-01T00:00:00Z',
  updated_at: '2026-09-01T00:00:00Z',
  updated_by_user_id: null,
}

const permissions = { isAdmin: true }
vi.mock('@/hooks/usePermissions', () => ({ usePermissions: () => permissions }))

const flagsEnabled: unknown[] = []
let flagList: FeatureFlag[] = [FLAG]
vi.mock('@/hooks/useFeatureFlags', () => ({
  useFeatureFlags: (enabled?: boolean) => {
    flagsEnabled.push(enabled)
    return { flags: flagList, isLoading: false, isError: false, refresh: vi.fn() }
  },
}))

const { update } = vi.hoisted(() => ({ update: vi.fn() }))
vi.mock('@/services/featureFlagService', () => ({
  featureFlagService: { update, remove: vi.fn(), create: vi.fn() },
}))

vi.mock('react-hot-toast', () => ({ default: { success: vi.fn(), error: vi.fn() } }))

beforeEach(() => {
  permissions.isAdmin = true
  flagsEnabled.length = 0
  flagList = [FLAG]
  update.mockReset()
  update.mockResolvedValue({})
})

describe('FeatureFlagsPage', () => {
  it('has the compact header with the route help topic; New Flag is its one action', () => {
    render(<FeatureFlagsPage />)
    expectTemplateHeader('Feature Flags', '/settings/feature-flags')
    expect(screen.getByRole('button', { name: /new flag/i }).closest('[data-page-header]')).not.toBeNull()
  })

  it('each flag\'s delete bin is named after the flag, with a tooltip (browser E2E pass: it had no name)', () => {
    render(<FeatureFlagsPage />)
    const del = screen.getByRole('button', { name: 'Delete flag manual_upload' })
    expect(del).toHaveAttribute('title', 'Delete flag manual_upload')
  })

  it('lists the flags in the table and opens the create form from the header action', () => {
    render(<FeatureFlagsPage />)
    expect(screen.getByRole('table')).toHaveTextContent('manual_upload')
    expect(screen.getByRole('button', { name: 'Toggle manual_upload' })).toHaveTextContent('ON')

    fireEvent.click(screen.getByRole('button', { name: /new flag/i }))
    expect(screen.getByText('New feature flag')).toBeInTheDocument()
    expect(screen.getByPlaceholderText('cypress_ingest')).toBeInTheDocument()
  })

  it('a non-admin: the header and the admin-required state, no action, and the ADMIN-only list never asked for', () => {
    permissions.isAdmin = false
    render(<FeatureFlagsPage />)
    expect(screen.getByRole('heading', { level: 1, name: 'Feature Flags' })).toBeInTheDocument()
    expect(screen.getByText('Admin access required')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /new flag/i })).toBeNull()
    // Asked, it answered 403 and toasted "Requires at least ADMIN role" over
    // the page's own message (browser E2E pass).
    expect(flagsEnabled).toEqual([false])
  })
})

// ── W04 AIC-07: the agent stage flags, grouped ─────────────────────────────

const flag = (key: string, enabled_global: boolean, description: string | null = `${key} description`): FeatureFlag => ({
  ...FLAG,
  id: `id-${key}`,
  key,
  enabled_global,
  description,
})

/** What migrations 0208/0209 seed on a fresh install, in a scrambled server order, around two other flags. */
const SEEDED = [
  flag('defect_commander', false, 'MUTATING deep-pipeline stage: promotes high-confidence failure clusters to defects. Off until Act mode routes its effects through approvals.'),
  flag('llm_cost_budget', true),
  flag('change_ownership', true),
  flag('aiq_gap_refinement', true),
  flag('log_intelligence', true),
  flag('async_decision_report_supersession', false),
  flag('contract_validation', true),
  flag('regression_watchman', true),
  FLAG,
]

const rowKeys = () =>
  Array.from(document.querySelectorAll('tbody tr')).map((tr) =>
    tr.getAttribute('data-flag-group') ? `# ${tr.getAttribute('data-flag-group')}` : tr.getAttribute('data-flag-key'),
  )

describe('W04 AIC-07: agent stage flags are grouped', () => {
  it('the group header, then its flags in the group\'s order, then every other flag ungrouped in the server\'s order', () => {
    flagList = SEEDED
    render(<FeatureFlagsPage />)
    expect(rowKeys()).toEqual([
      '# agent-stages',
      'contract_validation',
      'log_intelligence',
      'regression_watchman',
      'change_ownership',
      'defect_commander',
      'aiq_gap_refinement',
      'async_decision_report_supersession',
      'llm_cost_budget',
      'manual_upload',
    ])
    const header = document.querySelector('[data-flag-group="agent-stages"]') as HTMLElement
    expect(header).toHaveTextContent('Agent stages (deep pipeline)')
    expect(header).toHaveTextContent('Turn a stage on for one project by switching it on globally and listing the project under Edit.')
    expect(header.querySelector('th')).toHaveAttribute('colspan', '6')
  })

  it('the four read-only stages read ON, defect_commander OFF with a MUTATING pill; nothing else is marked', () => {
    flagList = SEEDED
    render(<FeatureFlagsPage />)
    for (const key of ['contract_validation', 'log_intelligence', 'regression_watchman', 'change_ownership', 'aiq_gap_refinement']) {
      expect(screen.getByRole('button', { name: `Toggle ${key}` })).toHaveTextContent('ON')
    }
    expect(screen.getByRole('button', { name: 'Toggle defect_commander' })).toHaveTextContent('OFF')
    const pills = document.querySelectorAll('[data-flag-mutating]')
    expect(pills).toHaveLength(1)
    expect(pills[0].closest('tr')).toHaveAttribute('data-flag-key', 'defect_commander')
    expect(pills[0]).toHaveTextContent('MUTATING')
  })

  it('a truncated description keeps its full text in the tooltip', () => {
    flagList = SEEDED
    render(<FeatureFlagsPage />)
    const row = document.querySelector('[data-flag-key="defect_commander"]') as HTMLElement
    const description = within(row).getByText(/^MUTATING deep-pipeline stage/)
    expect(description).toHaveClass('truncate')
    expect(description).toHaveAttribute('title', SEEDED[0].description)
  })

  it('a group with no flag present renders nothing; a missing key is simply absent', () => {
    flagList = [FLAG, flag('llm_cost_budget', true)]
    const { unmount } = render(<FeatureFlagsPage />)
    expect(document.querySelector('[data-flag-group]')).toBeNull()
    expect(rowKeys()).toEqual(['manual_upload', 'llm_cost_budget'])
    unmount()

    flagList = [flag('defect_commander', false), FLAG]
    render(<FeatureFlagsPage />)
    expect(rowKeys()).toEqual(['# agent-stages', 'defect_commander', 'manual_upload'])
  })

  it('a grouped flag toggles like any other (the same PATCH)', async () => {
    flagList = SEEDED
    render(<FeatureFlagsPage />)
    fireEvent.click(screen.getByRole('button', { name: 'Toggle defect_commander' }))
    await waitFor(() => expect(update).toHaveBeenCalledWith('defect_commander', { enabled_global: true }))
  })
})
