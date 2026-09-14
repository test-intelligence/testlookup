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
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import AIEvalDashboardPage from './AIEvalDashboardPage'
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
    listReportCycles: () => Promise.resolve([]),
    getReportReadiness: () => Promise.resolve(null),
    listBaselines: () => Promise.resolve([]),
    generateDataset: vi.fn(),
  }
})

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
