/**
 * W10 FAIL-09 (contract §2.6 test 5): the run Changes tab's commit list says
 * why it is empty, links the commits it lists (http(s) only), and renders
 * exactly the old markup for a server without `commit_range_status`.
 *
 * The real `useRegressionDiff` runs (its read is mocked at `services/http`), so
 * the tab asks for exactly one regression diff.
 */
import { render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { CommitRangeStatus, RegressionDiff } from '@/types/regressionDiff'
import { formatDay } from '@/utils/formatters'

const getData = vi.fn()
vi.mock('@/services/http', () => ({ getData: (...a: unknown[]) => getData(...a) }))
vi.mock('@/components/runs/DurationRegressionsCard', () => ({ default: () => null }))
const perms = vi.hoisted(() => ({ isAdmin: false }))
vi.mock('@/hooks/usePermissions', () => ({ usePermissions: () => ({ isAdmin: perms.isAdmin }) }))

import RunChangesTab from './RunChangesTab'

const BASE: RegressionDiff = {
  baseline_available: true,
  baseline_run_id: 'run-0',
  baseline_build_number: '41',
  pass_rate_delta: -12.5,
  new_failing_tests: [],
  new_failing_count: 0,
  resolved_count: 0,
  commit_range: [],
}

function status(overrides: Partial<CommitRangeStatus> = {}): CommitRangeStatus {
  return {
    available: false,
    source: 'unavailable',
    base_source: 'unavailable',
    base_anchor_is_strong: false,
    base_run_id: null,
    base_build_number: null,
    reason: 'offline_mode',
    commit_count: 0,
    resolved_at: null,
    ...overrides,
  }
}

async function renderWith(diff: RegressionDiff) {
  getData.mockResolvedValue(diff)
  const out = render(
    <SWRConfig value={{ provider: () => new Map() }}>
      <MemoryRouter>
        <RunChangesTab runId="run-1" run={undefined} onCompareWithPrevious={() => {}} />
      </MemoryRouter>
    </SWRConfig>,
  )
  await screen.findByText('Pass rate delta')
  return out
}

const reasonEl = () => document.querySelector('[data-commit-range-reason]') as HTMLElement | null

describe('RunChangesTab — why the commit list is empty (W10)', () => {
  beforeEach(() => {
    getData.mockReset()
    perms.isAdmin = false
  })

  it('reads the diff once, through the run\'s regression-diff route', async () => {
    await renderWith({ ...BASE, commit_range_status: status() })
    expect(getData).toHaveBeenCalledTimes(1)
    expect(getData).toHaveBeenCalledWith('/api/v1/runs/run-1/regression-diff')
  })

  it('offline: one line, no link', async () => {
    await renderWith({ ...BASE, commit_range_status: status({ reason: 'offline_mode' }) })
    const line = reasonEl() as HTMLElement
    expect(line.textContent).toBe('Commit list unavailable: offline mode (AI_OFFLINE_MODE=true).')
    expect(line.getAttribute('data-commit-range-reason')).toBe('offline_mode')
    expect(line.className).toContain('text-sm')
    expect(within(line).queryByRole('link')).toBeNull()
    expect(document.querySelectorAll('[data-commit-range-reason]')).toHaveLength(1)
  })

  it('no integration: the line and a ScopedLink to /settings/github', async () => {
    await renderWith({ ...BASE, commit_range_status: status({ reason: 'no_integration' }) })
    const line = reasonEl() as HTMLElement
    expect(line.textContent).toContain('Commit list unavailable: this project has no GitHub integration.')
    const link = within(line).getByRole('link', { name: 'Connect GitHub in Settings › GitHub' })
    expect(link.getAttribute('href')).toBe('/settings/github')
    // A ScopedLink: with All Projects active it says a project is asked for first.
    expect(link.getAttribute('title')).toBe('This page shows one project at a time — you will be asked to choose one.')
  })

  it('GitHub checks off: the Feature flags link for an admin only', async () => {
    perms.isAdmin = true
    const { unmount } = await renderWith({ ...BASE, commit_range_status: status({ reason: 'github_checks_off' }) })
    const link = within(reasonEl() as HTMLElement).getByRole('link', { name: 'Feature flags' })
    expect(link.getAttribute('href')).toBe('/settings/feature-flags')
    unmount()

    perms.isAdmin = false
    await renderWith({ ...BASE, commit_range_status: status({ reason: 'github_checks_off' }) })
    expect((reasonEl() as HTMLElement).textContent).toBe('Commit list unavailable: GitHub checks are turned off for this project.')
    expect(within(reasonEl() as HTMLElement).queryByRole('link')).toBeNull()
  })

  it('an unknown reason reads "Commit list unavailable."', async () => {
    await renderWith({ ...BASE, commit_range_status: status({ reason: 'a_future_reason' }) })
    expect((reasonEl() as HTMLElement).textContent).toBe('Commit list unavailable.')
  })
})

describe('RunChangesTab — the listed commits (W10)', () => {
  beforeEach(() => {
    getData.mockReset()
    perms.isAdmin = false
  })

  const COMMITS = [
    { sha: 'aaaa1111', message: 'Pool size from config', author: 'ana', timestamp: '2026-10-20T09:00:00Z', full_sha: 'a'.repeat(40), commit_url: 'https://ghe.fake.test/acme/payments/commit/' + 'a'.repeat(40) },
    { sha: 'bbbb2222', message: 'Retry the gateway', author: null, timestamp: null, full_sha: 'b'.repeat(40), commit_url: 'https://ghe.fake.test/acme/payments/commit/' + 'b'.repeat(40) },
    { sha: 'cccc3333', message: '<img src=x onerror=alert(1)>', author: 'eve', timestamp: null, full_sha: 'c'.repeat(40), commit_url: 'javascript:alert(1)' },
  ]

  it('heading with its own base build, the weak-anchor line, and each sha linking to its commit_url', async () => {
    await renderWith({
      ...BASE,
      commit_range: COMMITS,
      commit_range_status: status({ available: true, reason: null, source: 'connector', base_source: 'last_completed_run', base_anchor_is_strong: false, base_build_number: '40', commit_count: 3 }),
    })
    expect(screen.getByText('Commits since build #40 (3)')).toBeInTheDocument()
    expect(reasonEl()).toBeNull()
    expect((document.querySelector('[data-commit-range-weak-anchor]') as HTMLElement).textContent).toBe(
      'Counted from the last completed run, not the last green one: some of these may predate the failure.',
    )
    expect(document.querySelector('[data-commit-range-supplied]')).toBeNull()

    const first = screen.getByRole('link', { name: 'aaaa1111' })
    expect(first.getAttribute('href')).toBe('https://ghe.fake.test/acme/payments/commit/' + 'a'.repeat(40))
    expect(first.getAttribute('target')).toBe('_blank')
    expect(first.getAttribute('rel')).toBe('noopener noreferrer')
    expect(screen.getByRole('link', { name: 'bbbb2222' }).getAttribute('href')).toBe('https://ghe.fake.test/acme/payments/commit/' + 'b'.repeat(40))
    // No author: no dangling dash.
    const second = screen.getByRole('link', { name: 'bbbb2222' }).closest('li') as HTMLElement
    expect(second.textContent).toBe('bbbb2222Retry the gateway')
  })

  it('a javascript: commit_url renders no link, and a commit message renders as text', async () => {
    await renderWith({
      ...BASE,
      commit_range: COMMITS,
      commit_range_status: status({ available: true, reason: null, source: 'connector', base_anchor_is_strong: true, base_build_number: '40', commit_count: 3 }),
    })
    expect(screen.queryByRole('link', { name: 'cccc3333' })).toBeNull()
    expect(screen.getByText('cccc3333').tagName).toBe('SPAN')
    expect(document.querySelector('a[href^="javascript"]')).toBeNull()
    expect(screen.getByText('<img src=x onerror=alert(1)>')).toBeInTheDocument()
    expect(document.querySelector('img')).toBeNull()
    // A strong anchor says nothing more.
    expect(document.querySelector('[data-commit-range-weak-anchor]')).toBeNull()
  })

  it('commits supplied with the run say so', async () => {
    await renderWith({
      ...BASE,
      commit_range: COMMITS.slice(0, 1),
      commit_range_status: status({ available: true, reason: null, source: 'supplied', base_source: 'supplied', base_anchor_is_strong: true, base_build_number: '40', commit_count: 1 }),
    })
    expect((document.querySelector('[data-commit-range-supplied]') as HTMLElement).textContent).toBe('Commits supplied with the run (no GitHub call).')
  })

  it('no base build number: today\'s heading', async () => {
    await renderWith({
      ...BASE,
      commit_range: COMMITS.slice(0, 2),
      commit_range_status: status({ available: true, reason: null, source: 'connector', base_anchor_is_strong: true, base_build_number: null, commit_count: 2 }),
    })
    expect(screen.getByText('Commits since baseline (2)')).toBeInTheDocument()
  })
})

describe('RunChangesTab — an older server (no commit_range_status)', () => {
  beforeEach(() => getData.mockReset())

  it('renders exactly the markup from before: today\'s heading, the sha as text, the author line', async () => {
    const { container } = await renderWith({
      ...BASE,
      commit_range: [{ sha: 'abc1234', message: 'Change the gateway timeout', author: 'dev', timestamp: '2026-03-31T09:00:00Z' }],
    })
    await waitFor(() => expect(screen.getByText('Commits since baseline (1)')).toBeInTheDocument())
    expect(reasonEl()).toBeNull()
    expect(document.querySelector('[data-commit-range-weak-anchor]')).toBeNull()
    expect(container.querySelector('a[data-commit-link]')).toBeNull()
    const li = screen.getByText('abc1234').closest('li') as HTMLElement
    expect(li.innerHTML).toContain('<span class="font-mono text-[var(--color-text)] mr-1">abc1234</span><span>Change the gateway timeout</span><span class="text-[var(--color-text-faint)]">— dev</span>')
  })
})

describe('RunChangesTab — the purged baseline (W22a ACT-04)', () => {
  beforeEach(() => getData.mockReset())

  const purgedEl = () => document.querySelector('[data-baseline-purged]') as HTMLElement | null
  // Built inside each test; the date is shown with the app's `formatDay`.
  const purgedAt = () => '2026-03-02T10:00:00Z'
  const expectedDate = (iso: string) => formatDay(iso)

  async function renderNoBaseline(diff: RegressionDiff) {
    getData.mockResolvedValue(diff)
    render(
      <SWRConfig value={{ provider: () => new Map() }}>
        <MemoryRouter>
          <RunChangesTab runId="run-1" run={undefined} onCompareWithPrevious={() => {}} />
        </MemoryRouter>
      </SWRConfig>,
    )
    await screen.findByText('No recent passing baseline run found for comparison.')
  }

  it('says the baseline was purged under the baseline line when one was found', async () => {
    const at = purgedAt()
    await renderWith({ ...BASE, baseline_purged: { build_number: '4471', purged_at: at } })
    const line = purgedEl() as HTMLElement
    expect(line.textContent).toBe(`Baseline build 4471 was purged on ${expectedDate(at)}.`)
    const baselineLine = screen.getByText('Baseline: Build #41')
    expect(baselineLine.nextElementSibling).toBe(line)
  })

  it('says it under "No recent passing baseline…" when none was found', async () => {
    const at = purgedAt()
    await renderNoBaseline({ baseline_available: false, baseline_purged: { build_number: '4471', purged_at: at } } as RegressionDiff)
    const line = purgedEl() as HTMLElement
    expect(line.textContent).toBe(`Baseline build 4471 was purged on ${expectedDate(at)}.`)
    expect(screen.getByText('No recent passing baseline run found for comparison.').nextElementSibling).toBe(line)
  })

  it('reads without a build number', async () => {
    const at = purgedAt()
    await renderNoBaseline({ baseline_available: false, baseline_purged: { build_number: null, purged_at: at } } as RegressionDiff)
    expect((purgedEl() as HTMLElement).textContent).toBe(`The baseline run was purged on ${expectedDate(at)}.`)
  })

  it('shows nothing when baseline_purged is null or absent (both branches)', async () => {
    await renderWith({ ...BASE, baseline_purged: null })
    expect(purgedEl()).toBeNull()
    await renderWith({ ...BASE })
    expect(purgedEl()).toBeNull()
    await renderNoBaseline({ baseline_available: false, baseline_purged: null } as RegressionDiff)
    expect(purgedEl()).toBeNull()
  })
})
