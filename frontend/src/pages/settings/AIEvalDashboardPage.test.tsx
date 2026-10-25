/**
 * AIEvalDashboardPage — human/AI agreement, label health, drift.
 *
 * Zero coverage before this (backlog: "zero-coverage surfaces"), and covering
 * it found a defect: **the agreement tiles could not add up.**
 *
 * `compute_agreement_rate` returns THREE verdict buckets and derives the
 * headline rate from all of them:
 *
 *     correct, partially_correct, incorrect
 *     agreement_rate = (correct + partially_correct * 0.5) / total
 *
 * The page rendered four tiles — rate, total, correct, incorrect — and dropped
 * `partially_correct`. With 60 correct / 20 partial / 20 incorrect it showed
 *
 *     70.0%   |   100 total   |   60 correct   |   20 incorrect
 *
 * where 60 + 20 ≠ 100, and the 70% matched neither 60/100 nor 60/80. A reader
 * reconciling the panel has to invent the missing 20 — and this is the page
 * people consult to decide whether the AI's verdicts can be trusted.
 *
 * The bucket was in the API response and in `AIQualityDashboard` the whole
 * time; only the tile was missing. That is the "value published that no code
 * path consumes" class, and its consequence here is the "figures that cannot
 * all be true on one screen" class.
 *
 * The reconciliation is asserted as ARITHMETIC over the rendered tiles rather
 * than as "a Partially Correct label exists", so a future tile that renders
 * the wrong field still fails.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { SWRConfig } from 'swr'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import AIEvalDashboardPage from './AIEvalDashboardPage'
import { useShadowPairs } from '@/hooks/useAiEval'
import { ALL_PROJECTS_ID, useProjectStore } from '@/store/projectStore'
import { expectTemplateHeader } from '@/test/expectTemplateHeader'
import type { AIQualityDashboard } from '../../services/aiEvalService'

const mockGetDashboard = vi.fn<() => Promise<AIQualityDashboard>>()

vi.mock('react-hot-toast', () => ({ default: { success: vi.fn(), error: vi.fn() } }))

vi.mock('../../services/aiEvalService', async () => {
  const actual =
    await vi.importActual<typeof import('../../services/aiEvalService')>(
      '../../services/aiEvalService',
    )
  return {
    ...actual,
    getDashboard: () => mockGetDashboard(),
    listDatasets: () => Promise.resolve([]),
    // The service's names: the old listReportCycles / getReportReadiness keys mocked
    // nothing once the functions were renamed, and the page made a real request.
    listReportEvalCycles: () => Promise.resolve([]),
    getReportEvalReadiness: () => Promise.resolve(null),
    listBaselines: () => Promise.resolve([]),
    generateDataset: vi.fn(),
  }
})

// W16: the page reads the shadow-pair badge through the AI-eval hooks and mounts G-D's Tiers tab; neither is this
// file's subject (each has its own tests), so both answer nothing here.
vi.mock('@/hooks/useAiEval', async () => (await import('@/test/aiEvalMocks')).aiEvalHookMocks())
vi.mock('@/components/aiEval/TierComparisonTab', () => ({ default: () => null }))

function dashboard(
  agreement: AIQualityDashboard['agreement'],
): AIQualityDashboard {
  return {
    agreement,
    drift: null,
    recent_eval_runs: [],
    model_versions: [],
    feedback_summary: null,
    label_health: null,
    eval_provenance: null,
  } as AIQualityDashboard
}

function renderPage() {
  return render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <MemoryRouter>
        <AIEvalDashboardPage />
      </MemoryRouter>
    </SWRConfig>,
  )
}

/** Read a tile's number by its caption. */
function tileValue(caption: RegExp): number {
  const label = screen.getByText(caption)
  const value = label.parentElement?.querySelector('div')?.textContent ?? ''
  return Number(value.replace(/[^0-9.]/g, ''))
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('the agreement panel reconciles', () => {
  const MIXED = {
    total_feedback: 100,
    correct: 60,
    partially_correct: 20,
    incorrect: 20,
    agreement_rate: 0.7,
    period_days: 30,
  }

  it('shows every verdict bucket the rate is computed from', async () => {
    mockGetDashboard.mockResolvedValue(dashboard(MIXED))
    renderPage()

    await screen.findByText(/human-ai agreement/i)

    // Arithmetic, not the presence of a label: the visible counts must
    // account for the visible total.
    await waitFor(() => {
      const correct = tileValue(/^correct$/i)
      const partial = tileValue(/partially correct/i)
      const incorrect = tileValue(/^incorrect$/i)
      const total = tileValue(/total feedback/i)
      expect(correct + partial + incorrect).toBe(total)
    })
  })

  it('shows a headline rate the visible buckets can explain', async () => {
    mockGetDashboard.mockResolvedValue(dashboard(MIXED))
    renderPage()

    await screen.findByText(/human-ai agreement/i)

    // agreement_rate = (correct + partial * 0.5) / total. Without the partial
    // tile this 70% matched neither 60/100 nor 60/80, so the number on the
    // largest tile was unexplainable from the panel beneath it.
    await waitFor(() => {
      const correct = tileValue(/^correct$/i)
      const partial = tileValue(/partially correct/i)
      const total = tileValue(/total feedback/i)
      expect((correct + partial * 0.5) / total).toBeCloseTo(0.7, 3)
    })
    expect(screen.getByText('70.0%')).toBeInTheDocument()
  })

  it('still reconciles when nothing is partially correct', async () => {
    // The bug was invisible in this shape, which is why it survived: with no
    // partial verdicts the four original tiles added up correctly.
    mockGetDashboard.mockResolvedValue(
      dashboard({
        total_feedback: 50,
        correct: 40,
        partially_correct: 0,
        incorrect: 10,
        agreement_rate: 0.8,
        period_days: 30,
      }),
    )
    renderPage()

    await screen.findByText(/human-ai agreement/i)

    await waitFor(() => {
      expect(
        tileValue(/^correct$/i) + tileValue(/partially correct/i) + tileValue(/^incorrect$/i),
      ).toBe(tileValue(/total feedback/i))
    })
  })
})

describe('an unrated period does not fabricate a rate', () => {
  it('renders N/A rather than 0% when there is no feedback', async () => {
    // agreement_rate is None when total is 0. "0%" would read as total
    // disagreement rather than "nobody has rated anything".
    mockGetDashboard.mockResolvedValue(
      dashboard({
        total_feedback: 0,
        correct: 0,
        partially_correct: 0,
        incorrect: 0,
        agreement_rate: null,
        period_days: 30,
      }),
    )
    renderPage()

    await screen.findByText(/human-ai agreement/i)
    expect(await screen.findByText('N/A')).toBeInTheDocument()
    expect(screen.queryByText('0.0%')).not.toBeInTheDocument()
  })
})

describe('runtime eval provenance', () => {
  it('flags recent runs whose manifest checksum cannot be resolved', async () => {
    const value = dashboard(null)
    value.eval_provenance = {
      window_days: 7,
      window_start: '2026-09-07T00:00:00Z',
      window_end: '2026-09-14T00:00:00Z',
      total_runs: 12,
      stamped_runs: 11,
      resolved_runs: 9,
      missing_checksum_count: 1,
      unresolvable_run_count: 3,
      unresolvable_checksums: ['f'.repeat(64)],
      has_unresolvable_checksums: true,
    }
    mockGetDashboard.mockResolvedValue(value)
    renderPage()

    expect(await screen.findByText('UNRESOLVED')).toBeInTheDocument()
    expect(screen.getByText(/3 of 12 recent runs/)).toBeInTheDocument()
    expect(screen.getByText(/Unknown: f{64}/)).toBeInTheDocument()
  })
})

// P2 item 3: the evaluation workflow timeline is a collapsed "Pipeline"
// disclosure at the bottom of the page; it renders nothing until opened.
describe('the evaluation workflow timeline is collapsed at the bottom', () => {
  it('starts closed below the tabs and opens on click', async () => {
    mockGetDashboard.mockResolvedValue(dashboard(null as unknown as AIQualityDashboard['agreement']))
    renderPage()
    const pipeline = await screen.findByRole('button', { name: 'Pipeline' })
    expect(pipeline).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByText('Evaluation workflow')).toBeNull()
    const tab = screen.getByRole('tab', { name: 'Gate' })
    expect(tab.compareDocumentPosition(pipeline) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    fireEvent.click(pipeline)
    expect(screen.getByText('Evaluation workflow')).toBeInTheDocument()
  })
})

// W16 AGT-09: the hand-rolled three-button row became the Tabs primitive with six tabs kept in `?tab=`.
describe('the six tabs', () => {
  function renderAt(url: string) {
    function Probe() {
      const location = useLocation()
      return <output data-testid="location">{location.search}</output>
    }
    return render(
      <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
        <MemoryRouter initialEntries={[url]}>
          <AIEvalDashboardPage />
          <Probe />
        </MemoryRouter>
      </SWRConfig>,
    )
  }

  afterEach(() => {
    useProjectStore.setState({ activeProjectId: ALL_PROJECTS_ID, activeProject: null })
  })

  it('shows the six short labels on one tab bar, Overview first', async () => {
    mockGetDashboard.mockResolvedValue(dashboard(null))
    renderAt('/settings/ai-eval')
    const bar = screen.getByRole('tablist', { name: 'AI evaluation' })
    expect(within(bar).getAllByRole('tab').map((t) => t.textContent)).toEqual(
      ['Overview', 'Datasets', 'Gate', 'Agent stack', 'Tiers', 'Reviewer'],
    )
    expect(within(bar).getByRole('tab', { name: 'Overview' })).toHaveAttribute('aria-selected', 'true')
  })

  it('restores a tab from ?tab= and writes the choice back', async () => {
    mockGetDashboard.mockResolvedValue(dashboard(null))
    renderAt('/settings/ai-eval?tab=reviewer')
    expect(screen.getByRole('tab', { name: 'Reviewer' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByText('Select a project')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('tab', { name: 'Agent stack' }))
    expect(screen.getByTestId('location')).toHaveTextContent('?tab=stack')
    fireEvent.click(screen.getByRole('tab', { name: 'Overview' }))
    expect(screen.getByTestId('location')).toHaveTextContent(/^$/)
  })

  it('counts pending shadow pairs on Tiers only with one project pinned', async () => {
    mockGetDashboard.mockResolvedValue(dashboard(null))
    vi.mocked(useShadowPairs).mockReturnValue({
      data: { items: [], total: 0, pending_total: 3, live_tier_pairs_seen: true },
      error: undefined, isLoading: false, mutate: vi.fn(),
    } as unknown as ReturnType<typeof useShadowPairs>)
    const { unmount } = renderAt('/settings/ai-eval')
    expect(vi.mocked(useShadowPairs)).toHaveBeenLastCalledWith(null, undefined, 'pending')
    expect(screen.getByRole('tab', { name: 'Tiers' }).querySelector('[data-tab-count]')).toBeNull()
    unmount()

    useProjectStore.setState({ activeProjectId: 'prA1b2C3d4' })
    renderAt('/settings/ai-eval')
    expect(vi.mocked(useShadowPairs)).toHaveBeenLastCalledWith('prA1b2C3d4', undefined, 'pending')
    expect(screen.getByRole('tab', { name: /Tiers/ }).querySelector('[data-tab-count]')).toHaveTextContent('3')
  })

  it('labels each recent run with its metric kind (legacy unless replay)', async () => {
    const value = dashboard(null)
    value.recent_eval_runs = [
      { id: 'rnA1b2C3d4', dataset_id: 'dsA1b2C3d4', model_name: 'AnalysisAgent', task_type: 'classification', precision: null, recall: null, f1_score: 0.7, accuracy: 0.75, agreement_rate: null, total_items: 12, correct_items: 9, fallback_used: false, evaluated_at: '2026-10-20T09:00:00+00:00', duration_ms: 10 },
      { id: 'rnB2c3D4e5', dataset_id: 'dsA1b2C3d4', model_name: 'rules', task_type: 'classification', precision: null, recall: null, f1_score: 0.8, accuracy: 0.82, agreement_rate: null, total_items: 12, correct_items: 9, fallback_used: false, evaluated_at: '2026-10-25T09:00:00+00:00', duration_ms: 10, metric_kind: 'replay', candidate: { tier: 'rules', offline_mode: false, requested: { tier: 'rules' } } },
    ]
    mockGetDashboard.mockResolvedValue(value)
    renderAt('/settings/ai-eval')
    await screen.findByText('Recent Evaluation Runs')
    expect(document.querySelector('[data-eval-run-row="rnA1b2C3d4"] [data-metric-kind]')).toHaveTextContent('Legacy label agreement')
    expect(document.querySelector('[data-eval-run-row="rnB2c3D4e5"] [data-metric-kind]')).toHaveTextContent('Replay · Rules')
  })
})

describe('the settings page template (UX redesign P5)', () => {
  it('has the compact header with the route help topic', async () => {
    mockGetDashboard.mockResolvedValue(dashboard(null))
    renderPage()
    expectTemplateHeader('AI Evaluation Dashboard', '/settings/ai-eval')
  })
})
