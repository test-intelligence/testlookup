/**
 * W03 AIH-04: a stored AI version, read-only. It shows the version's own verdict, the summary written by the same
 * pipeline (through ChatMarkdown), its categories, clusters, per-test conclusions (each test opens) and how it was
 * produced; it links the decision report it cited; "Back to current" clears the version and its report; a version
 * that is gone says so; AI text renders as text.
 */
import { fireEvent, render, screen, within } from '@testing-library/react'
import { useEffect } from 'react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import AIVersionSnapshot from './AIVersionSnapshot'
import { digest, HOSTILE, historyList, storedVersion, versionSummary } from './aiHistory.fixtures'

const state = vi.hoisted(() => ({
  digestList: undefined as unknown,
  summaryList: undefined as unknown,
  byId: {} as Record<string, unknown>,
  errorIds: new Set<string>(),
  calls: [] as unknown[][],
}))

vi.mock('@/hooks/useAIHistory', () => ({
  useAIHistory: (runId: string | null, kind = 'analysis_digest') => {
    state.calls.push(['useAIHistory', runId, kind])
    if (!runId) return { data: undefined, error: undefined, isLoading: false, mutate: vi.fn() }
    return { data: kind === 'run_summary' ? state.summaryList : state.digestList, error: undefined, isLoading: false, mutate: vi.fn() }
  },
  useAIVersion: (runId: string | null, id: string | null) => {
    state.calls.push(['useAIVersion', runId, id])
    if (!runId || !id) return { data: undefined, error: undefined, isLoading: false, mutate: vi.fn() }
    if (state.errorIds.has(id)) return { data: undefined, error: new Error('404'), isLoading: false, mutate: vi.fn() }
    return { data: state.byId[id], error: undefined, isLoading: false, mutate: vi.fn() }
  },
}))

const V1 = versionSummary(1)
const V2 = versionSummary(2)
const SUMMARY_V1 = { ...versionSummary(1, { kind: 'run_summary', version_id: 'summary-1' }) }

beforeEach(() => {
  state.digestList = historyList([V1, V2])
  state.summaryList = historyList([SUMMARY_V1], { kind: 'run_summary' })
  state.byId = {
    [V1.version_id]: storedVersion(1, digest()),
    'summary-1': { ...SUMMARY_V1, payload: { executive_summary: '**Session store** timeouts broke login.' } },
  }
  state.errorIds = new Set()
  state.calls = []
})

let location = ''
function LocationProbe() {
  const { pathname, search } = useLocation()
  useEffect(() => {
    location = `${pathname}${search}`
  })
  return null
}

function renderSnapshot(version = 1, search = '?tab=analysis&ai_version=1&report_version=1&status=FAILED') {
  return render(
    <MemoryRouter initialEntries={[`/runs/run-1${search}`]}>
      <Routes>
        <Route path="/runs/:runId" element={<><AIVersionSnapshot runId="run-1" version={version} /><LocationProbe /></>} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('AIVersionSnapshot', () => {
  it('shows the stored version: banner, verdict, its summary, categories, clusters, tests, provenance — in that order', () => {
    const { container } = renderSnapshot()
    const root = container.querySelector('[data-ai-snapshot]') as HTMLElement
    expect(root).toHaveAttribute('data-ai-snapshot-version', '1')
    expect(root.querySelector('[data-ai-snapshot-banner]')).toHaveTextContent(/Viewing v1 \(rules · .+\) — this is a stored snapshot\./)
    expect(Array.from(root.querySelectorAll('[data-ai-snapshot-section]')).map((s) => s.getAttribute('data-ai-snapshot-section')))
      .toEqual(['verdict', 'summary', 'categories', 'clusters', 'tests', 'provenance'])
    const verdict = root.querySelector('[data-ai-snapshot-section="verdict"]') as HTMLElement
    expect(verdict).toHaveTextContent('CONDITIONAL_GO')
    expect(verdict).toHaveTextContent('risk 55/100')
    // The paired run summary (the same pipeline's), rendered as markdown without raw HTML.
    const summary = root.querySelector('[data-ai-snapshot-section="summary"]') as HTMLElement
    expect(within(summary).getByTestId('chat-markdown')).toHaveTextContent('Session store timeouts broke login.')
    expect(summary.querySelector('strong')).toHaveTextContent('Session store')
    expect(state.calls).toContainEqual(['useAIHistory', 'run-1', 'run_summary'])
    expect(state.calls).toContainEqual(['useAIVersion', 'run-1', 'summary-1'])
    // Each analysed test opens on this run.
    expect(within(root).getByRole('link', { name: 'test_login' })).toHaveAttribute('href', '/runs/run-1/tests/tc-a')
    // Provenance: engine, and the tests this pipeline did not re-analyse.
    expect(root.querySelector('[data-ai-snapshot-carried-over]')).toHaveTextContent('1 test was not re-analysed by this pipeline')
  })

  it('links the decision report the version cited, with the version', () => {
    renderSnapshot()
    expect(screen.getByRole('link', { name: 'Open decision report v1' }))
      .toHaveAttribute('href', '/runs/run-1?tab=evidence&report_version=1&ai_version=1')
  })

  it('"Back to current" clears the version, the comparison and its report, keeping the rest', () => {
    renderSnapshot(1, '?tab=analysis&ai_version=1&report_version=1&ai_compare=2&status=FAILED')
    fireEvent.click(screen.getByRole('button', { name: /Back to current/ }))
    const params = new URLSearchParams(location.split('?')[1])
    expect(params.get('ai_version')).toBeNull()
    expect(params.get('report_version')).toBeNull()
    expect(params.get('ai_compare')).toBeNull()
    expect(params.get('tab')).toBe('analysis')
    expect(params.get('status')).toBe('FAILED')
  })

  it('a version that is no longer listed (pruned) or 404s says so', () => {
    const { container, unmount } = renderSnapshot(7, '?tab=analysis&ai_version=7')
    expect(screen.getByText('That version no longer exists')).toBeInTheDocument()
    expect(container.querySelector('[data-ai-snapshot-section]')).toBeNull()
    unmount()
    state.errorIds.add(V1.version_id)
    renderSnapshot()
    expect(screen.getByText('That version no longer exists')).toBeInTheDocument()
  })

  it('no paired summary: says none was stored', () => {
    state.digestList = historyList([versionSummary(1, { summary_version: null }), V2])
    renderSnapshot()
    expect(screen.getByText('No summary was stored for this version.')).toBeInTheDocument()
  })

  it('XSS: a hostile test name, excerpt and cluster label render as text', () => {
    const { container } = renderSnapshot()
    expect(container.querySelector('img')).toBeNull()
    expect(container.querySelector('[onerror]')).toBeNull()
    expect(screen.getByRole('link', { name: HOSTILE })).toHaveAttribute('href', '/runs/run-1/tests/tc-c')
    expect(screen.getAllByText(HOSTILE).length).toBeGreaterThanOrEqual(3)
  })
})
