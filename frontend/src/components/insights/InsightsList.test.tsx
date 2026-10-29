/**
 * W23a FAIL-11 (contract §4.4 G-D): the insights rows shared by the Overview
 * card and Inbox › Insights, and their pure model (`insightsModel`).
 */
import { act, fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { NOT_MEASURED, type InsightDetector } from '@/types/insights'
import { detector, insight, insightList, SENTINEL_SUITE } from '@/test/insightCadenceMocks'

const perms = vi.hoisted(() => ({ isQaEngineer: true }))
vi.mock('@/hooks/usePermissions', () => ({ usePermissions: () => ({ isQaEngineer: perms.isQaEngineer }) }))

const service = vi.hoisted(() => ({ dismissInsight: vi.fn() }))
vi.mock('@/services/insightsService', () => ({ dismissInsight: service.dismissInsight, listInsights: vi.fn() }))

const hooks = vi.hoisted(() => ({ refreshInsights: vi.fn(async () => undefined) }))
vi.mock('@/hooks/useInsights', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/hooks/useInsights')>()),
  refreshInsights: hooks.refreshInsights,
}))

import InsightsList from './InsightsList'
import {
  KIND_LABEL,
  agoText,
  detectorLines,
  dismissQuestion,
  emptyChecksLine,
  emptySentence,
  metaLine,
  normaliseDetectors,
  orderInsights,
  severityWord,
  showingLine,
} from './insightsModel'

const NOW = Date.parse('2026-10-28T12:00:00Z')
const at = (hoursBefore: number) => new Date(NOW - hoursBefore * 3_600_000).toISOString()
const KINDS = ['perf_drift', 'flake_rising', 'coverage_drop', 'cost_budget'] as const

describe('insightsModel', () => {
  it('names the four detectors and the severities', () => {
    expect(KINDS.map((k) => KIND_LABEL[k])).toEqual(['Perf drift', 'Flake rising', 'Coverage drop', 'AI budget'])
    expect(['high', 'medium', 'low'].map(severityWord)).toEqual(['High', 'Medium', 'Low'])
    expect(showingLine(5, 12)).toBe('Showing 5 of 12')
    expect(dismissQuestion('Checkout')).toBe('Dismiss for everyone in Checkout?')
    expect(emptySentence('7d')).toBe('No insights in the last 7 days.')
    expect(emptySentence('24h')).toBe('No insights in the last 24 hours.')
  })

  it('reads ages against the given clock', () => {
    expect(agoText(at(0.005), NOW)).toBe('just now')
    expect(agoText(at(0.2), NOW)).toBe('12 min ago')
    expect(agoText(at(3), NOW)).toBe('3 h ago')
    expect(agoText(at(30), NOW)).toBe('1 day ago')
    expect(agoText(at(72), NOW)).toBe('3 days ago')
    expect(agoText('not a date', NOW)).toBe('')
  })

  it('meta lines: open, cleared, stale, dismissed', () => {
    const open = insight({ first_seen_at: at(3), last_seen_at: at(0.2) })
    expect(metaLine(open, NOW)).toBe('Perf drift · first seen 3 h ago · confirmed 12 min ago')
    const cleared = insight({ status: 'resolved', resolved_reason: 'cleared', resolved_at: at(2) })
    expect(metaLine(cleared, NOW)).toBe('Cleared 2 h ago')
    const stale = insight({ status: 'resolved', resolved_reason: 'stale', resolved_at: at(1), last_seen_at: at(73) })
    expect(metaLine(stale, NOW)).toBe('Not seen for 3 days')
    const dismissed = insight({ dismissed_at: at(1), dismissed_by_name: 'Alice Lead' })
    expect(metaLine(dismissed, NOW)).toBe('Dismissed by Alice Lead · 1 h ago')
    expect(metaLine(insight({ dismissed_at: at(1), dismissed_by_name: null }), NOW)).toBe('Dismissed · 1 h ago')
  })

  it('orders open before resolved, then high → low, then the newest confirmation', () => {
    const rows = [
      insight({ id: 'r1', severity: 'high', status: 'resolved', resolved_at: at(1), last_seen_at: at(1) }),
      insight({ id: 'm1', severity: 'medium', last_seen_at: at(1) }),
      insight({ id: 'h2', severity: 'high', last_seen_at: at(3) }),
      insight({ id: 'h1', severity: 'high', last_seen_at: at(1) }),
    ]
    expect(orderInsights(rows).map((r) => r.id)).toEqual(['h1', 'h2', 'm1', 'r1'])
  })

  it('detector lines: not measured with its sentence, a server note, and "checked N h ago" when old', () => {
    const detectors: InsightDetector[] = [
      detector('perf_drift', { reason: NOT_MEASURED.perf_tests_flag_off, evaluated_at: at(0.5) }),
      detector('flake_rising', { evaluated_at: at(5) }),
      detector('coverage_drop', { measured: false, reason: NOT_MEASURED.coverage_no_snapshot, evaluated_at: at(0.5) }),
      detector('cost_budget', { measured: false, reason: NOT_MEASURED.cost_no_budget, evaluated_at: at(6) }),
    ]
    expect(detectorLines(detectors, NOW)).toEqual([
      `Perf drift — ${NOT_MEASURED.perf_tests_flag_off}`,
      'Flake rising · checked 5 h ago',
      `Coverage drop — not measured: ${NOT_MEASURED.coverage_no_snapshot}`,
      `AI budget — not measured: ${NOT_MEASURED.cost_no_budget} · checked 6 h ago`,
    ])
    expect(detectorLines(KINDS.map((k) => detector(k, { evaluated_at: at(1) })), NOW)).toEqual([])
  })

  it('a kind the server omitted is "Not evaluated yet", never a ✓', () => {
    const three = KINDS.slice(0, 3).map((k) => detector(k, { evaluated_at: at(1) }))
    expect(normaliseDetectors(three).map((d) => d.kind)).toEqual([...KINDS])
    expect(detectorLines(three, NOW)).toEqual([`AI budget — not measured: ${NOT_MEASURED.not_evaluated}`])
    expect(emptyChecksLine(three)).toBe('Perf drift ✓ · Flake rising ✓ · Coverage drop ✓ · AI budget not measured')
    expect(emptyChecksLine(KINDS.map((k) => detector(k)))).toBe('Perf drift ✓ · Flake rising ✓ · Coverage drop ✓ · AI budget ✓')
  })
})

function renderList(data = insightList([insight()]), props: Partial<Parameters<typeof InsightsList>[0]> = {}) {
  return render(
    <MemoryRouter>
      <InsightsList projectId="p1" projectName="Checkout" data={data} range="7d" {...props} />
    </MemoryRouter>,
  )
}
const rows = () => Array.from(document.querySelectorAll('[data-insight-row]'))

describe('InsightsList', () => {
  beforeEach(() => {
    perms.isQaEngineer = true
    service.dismissInsight.mockReset()
    hooks.refreshInsights.mockClear()
  })

  it('rows: the severity word, the server title as TEXT, the link only for an allowed path', () => {
    const hostile = '<script>window.__xss=1</script> sentinel'
    renderList(insightList([
      insight({ id: 'a', severity: 'high', title: `Suite ${hostile} is 2.4× slower than its previous 20 runs` }),
      insight({ id: 'b', kind: 'flake_rising', severity: 'medium', title: 'Flaky tests rising', run_path: null,
        link: { path: 'javascript:alert(1)', label: 'Flaky tests' } }),
    ]))
    expect(rows().map((r) => r.querySelector('[data-insight-pill]')?.textContent)).toEqual(['High', 'Medium'])
    expect(screen.getByText(`Suite ${hostile} is 2.4× slower than its previous 20 runs`)).toBeInTheDocument()
    expect(document.querySelector('script')).toBeNull()
    const [perf, flake] = rows()
    expect(within(perf as HTMLElement).getByRole('link', { name: 'Trends › Execution time' })).toHaveAttribute('href', '/trends?tab=durations')
    expect(within(perf as HTMLElement).getByRole('link', { name: 'Latest run' }).getAttribute('href')).toMatch(/^\/runs\/[0-9a-f-]{36}\?tab=changes$/)
    expect(within(flake as HTMLElement).queryByRole('link')).toBeNull()
    expect(within(flake as HTMLElement).getByText('Flaky tests')).toBeInTheDocument()
  })

  it('Dismiss is hidden below QA engineer, and on a row already dismissed', () => {
    perms.isQaEngineer = false
    const { unmount } = renderList()
    expect(screen.queryByRole('button', { name: 'Dismiss' })).toBeNull()
    unmount()
    perms.isQaEngineer = true
    renderList(insightList([insight({ dismissed_at: at(1), dismissed_by_name: 'Alice Lead' })]), { includeDismissed: true })
    expect(screen.queryByRole('button', { name: 'Dismiss' })).toBeNull()
    expect(screen.getByText(/^Dismissed by Alice Lead · /)).toBeInTheDocument()
  })

  it('Dismiss asks inline, then POSTs, then the row leaves and every insights read revalidates', async () => {
    const row = insight({ id: 'i-1', subject_label: SENTINEL_SUITE })
    service.dismissInsight.mockResolvedValue({ ...row, dismissed_at: new Date().toISOString(), dismissed_by_name: 'Quinn Engineer' })
    renderList(insightList([row, insight({ id: 'i-2', severity: 'low' })]))
    fireEvent.click(within(rows()[0] as HTMLElement).getByRole('button', { name: 'Dismiss' }))
    expect(service.dismissInsight).not.toHaveBeenCalled()
    const confirm = screen.getByRole('group', { name: 'Dismiss for everyone in Checkout?' })
    await act(async () => {
      fireEvent.click(within(confirm).getByRole('button', { name: 'Dismiss' }))
    })
    expect(service.dismissInsight).toHaveBeenCalledWith('p1', 'i-1')
    expect(rows().map((r) => r.getAttribute('data-insight-row'))).toEqual(['i-2'])
    expect(hooks.refreshInsights).toHaveBeenCalledWith('p1')
  })

  it('a refusal reads inline under its row (the server sentence), and the row stays', async () => {
    service.dismissInsight.mockRejectedValue({
      response: { status: 404, data: { detail: { code: 'insight_not_found', message: 'Insight not found.' } } },
    })
    renderList(insightList([insight({ id: 'gone' })]))
    fireEvent.click(screen.getByRole('button', { name: 'Dismiss' }))
    await act(async () => {
      fireEvent.click(within(screen.getByRole('group')).getByRole('button', { name: 'Dismiss' }))
    })
    expect(within(rows()[0] as HTMLElement).getByRole('alert')).toHaveTextContent('Insight not found.')
    expect(hooks.refreshInsights).not.toHaveBeenCalled()
  })

  it('not-measured detectors read their sentence under the rows', () => {
    renderList(insightList([insight()], [
      ...KINDS.slice(0, 2).map((k) => detector(k)),
      detector('coverage_drop', { measured: false, reason: NOT_MEASURED.coverage_no_snapshot }),
      detector('cost_budget'),
    ]))
    expect(document.querySelector('[data-insights-detectors]')).toHaveTextContent(
      `Coverage drop — not measured: ${NOT_MEASURED.coverage_no_snapshot}`)
  })

  it('no row: the empty sentence and a ✓ only for the checks that ran', () => {
    renderList(insightList([], [
      ...KINDS.slice(0, 3).map((k) => detector(k)),
      detector('cost_budget', { measured: false, reason: NOT_MEASURED.cost_flag_off }),
    ]), { range: '24h' })
    expect(screen.getByText('No insights in the last 24 hours.')).toBeInTheDocument()
    expect(document.querySelector('[data-insights-checks]')).toHaveTextContent(
      'Perf drift ✓ · Flake rising ✓ · Coverage drop ✓ · AI budget not measured')
    expect(screen.getByText(`AI budget — not measured: ${NOT_MEASURED.cost_flag_off}`)).toBeInTheDocument()
  })

  it('resolved rows say so; the flag off replaces the whole body', () => {
    const { unmount } = renderList(insightList([
      insight({ id: 'c', kind: 'coverage_drop', status: 'resolved', resolved_reason: 'cleared', resolved_at: new Date(Date.now() - 2 * 3_600_000).toISOString() }),
    ]))
    expect(screen.getByText('Cleared 2 h ago')).toBeInTheDocument()
    unmount()
    renderList({ ...insightList([insight()]), enabled: false })
    expect(screen.getByText('Insights are off for this project (feature flag cross_run_insights).')).toBeInTheDocument()
    expect(rows()).toHaveLength(0)
  })

  it('caps the rows at maxRows', () => {
    renderList(insightList(Array.from({ length: 7 }, (_, i) => insight({ id: `r${i}` }))), { maxRows: 5 })
    expect(rows()).toHaveLength(5)
  })
})
