/**
 * UX redesign P4: the old URLs of merged pages are never deleted. Each one
 * lands on the page and tab that now holds its content, keeping the old URL's
 * query string (a shared link's filters survive the move).
 */
import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  AgentRunRedirect,
  DeepInvestigationRunRedirect,
  FlakyCoachRedirect,
  IntelligenceRedirect,
  QuarantineRedirect,
  ReviewsRedirect,
  RunIntelligenceRedirect,
  SuiteByNameRedirect,
} from './legacyRedirects'
import { withParams } from './withParams'

const suites = vi.hoisted(() => ({ value: { data: undefined as unknown, error: undefined as unknown, isLoading: false } }))
vi.mock('@/hooks/useSuites', () => ({ useSuites: () => suites.value }))

function Where() {
  const { pathname, search, hash } = useLocation()
  return <div data-testid="where">{`${pathname}${search}${hash}`}</div>
}

function landAt(from: string, pattern: string, element: React.ReactElement): string {
  render(
    <MemoryRouter initialEntries={[from]}>
      <Routes>
        <Route path={pattern} element={element} />
        <Route path="*" element={<Where />} />
      </Routes>
    </MemoryRouter>,
  )
  return screen.getByTestId('where').textContent ?? ''
}

beforeEach(() => {
  suites.value = { data: undefined, error: undefined, isLoading: false }
})

describe('legacy redirects (UX redesign P4)', () => {
  it.each([
    ['/runs/r1/intelligence?persona=dev', '/runs/:runId/intelligence', <RunIntelligenceRedirect />, '/runs/r1?persona=dev&tab=analysis'],
    ['/deep-investigate/r2', '/deep-investigate/:runId', <DeepInvestigationRunRedirect />, '/runs/r2?tab=analysis'],
    ['/agents/run/r3?stage=triage#x', '/agents/run/:runId', <AgentRunRedirect />, '/runs/r3?stage=triage&tab=evidence#x'],
    ['/intelligence?range=7d&status=failed', '/intelligence', <IntelligenceRedirect />, '/runs?range=7d&status=failed'],
    ['/flaky-coach?suite=Auth', '/flaky-coach', <FlakyCoachRedirect />, '/flaky?suite=Auth'],
    ['/quarantine', '/quarantine', <QuarantineRedirect />, '/flaky?tab=quarantined'],
    ['/reviews?status=pending', '/reviews', <ReviewsRedirect />, '/my-failures?status=pending&tab=approvals'],
  ])('%s → %s', (from, pattern, element, to) => {
    expect(landAt(from, pattern, element)).toBe(to)
  })

  it("an old link's own tab is replaced by the tab the content moved to", () => {
    expect(landAt('/runs/r1/intelligence?tab=old', '/runs/:runId/intelligence', <RunIntelligenceRedirect />)).toBe('/runs/r1?tab=analysis')
  })

  it('/coverage/suite?name= resolves the suite by name to its page, Charts tab, keeping other params', () => {
    suites.value = { data: { items: [{ id: 's-9', name: 'Payments' }], total: 1 }, error: undefined, isLoading: false }
    expect(landAt('/coverage/suite?name=Payments&days=7', '/coverage/suite', <SuiteByNameRedirect />)).toBe('/suites/s-9?days=7&tab=charts')
  })

  it('an unknown suite name, or none, goes to the suites list, not a blank page', () => {
    suites.value = { data: { items: [{ id: 's-9', name: 'Payments' }], total: 1 }, error: undefined, isLoading: false }
    expect(landAt('/coverage/suite?name=Gone', '/coverage/suite', <SuiteByNameRedirect />)).toBe('/suites')
  })

  it('waits for the suites list instead of guessing', () => {
    suites.value = { data: undefined, error: undefined, isLoading: true }
    render(
      <MemoryRouter initialEntries={['/coverage/suite?name=Payments']}>
        <Routes>
          <Route path="/coverage/suite" element={<SuiteByNameRedirect />} />
          <Route path="*" element={<Where />} />
        </Routes>
      </MemoryRouter>,
    )
    expect(screen.queryByTestId('where')).toBeNull()
  })

  it('withParams sets extra keys over the old ones and keeps the rest', () => {
    expect(withParams('?a=1&tab=x', { tab: 'y' })).toBe('?a=1&tab=y')
    expect(withParams('', {})).toBe('')
  })
})
