/**
 * W11 GATE-04: Live's pass-rate chip and colour follow the selected project's
 * EFFECTIVE policy bands (`useEffectivePassRateBands`, mocked here: its one
 * read is the hook's own business). At or above the GO line: no chip; between
 * the lines: `below the GO line (95%)`; under `orange_min`:
 * `in the No-Go zone (< 90%)`; the chip's title names the bands.
 */
import { render } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { readinessBands } from '@/utils/passRateBands'

const live = vi.hoisted(() => ({ useLiveExecution: vi.fn() }))
const bands = vi.hoisted(() => ({ policy: null as unknown }))
vi.mock('@/hooks/useLiveExecution', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/hooks/useLiveExecution')>()
  return { ...actual, useLiveExecution: live.useLiveExecution }
})
vi.mock('@/hooks/useSuiteOptions', () => ({ useSuiteOptions: vi.fn(() => ({ options: [], isLoading: false })) }))
vi.mock('@/hooks/useEffectivePassRateBands', async () => {
  const { readinessBands: toBands } = await import('@/utils/passRateBands')
  return { useEffectivePassRateBands: () => toBands(bands.policy as never) }
})

import LiveExecutionPage from './LiveExecutionPage'
import { useProjectStore } from '@/store/projectStore'
import { useTimeWindowStore } from '@/store/timeWindowStore'

const PROJECT = 'aaaaaaaa-0000-4000-8000-000000000001'
const minutesAgo = (m: number) => new Date(Date.now() - m * 60_000).toISOString()

/** One completed session: `passed` of 100 evaluated, so the overall rate is `passed` %. */
function session(passed: number) {
  return {
    run_id: `build-${passed}`, test_run_id: `r-${passed}`, project_id: PROJECT, build_number: `build-${passed}`,
    status: 'completed', total: 100, passed, failed: 100 - passed, skipped: 0, broken: 0, pass_rate: passed,
    started_at: minutesAgo(30), completed_at: minutesAgo(20), last_event_at: minutesAgo(20),
  }
}

function renderAt(passed: number) {
  const sessions = [session(passed)]
  live.useLiveExecution.mockReturnValue({
    sessions,
    activeSessions: [],
    completedSessions: sessions,
    recentEvents: [],
    wsStatus: 'open',
    isLoading: false,
  })
  return render(<MemoryRouter><LiveExecutionPage /></MemoryRouter>)
}

const chip = () => document.querySelector('[data-live-pass-rate-chip]')

beforeEach(() => {
  localStorage.clear()
  live.useLiveExecution.mockReset()
  bands.policy = null
  useProjectStore.setState({ activeProjectId: PROJECT, activeProject: { id: PROJECT, name: 'Checkout' } as never })
  useTimeWindowStore.setState({ days: 7 })
})

describe('LiveExecutionPage — the pass-rate chip follows the policy bands (W11 GATE-04)', () => {
  it('96 %: at or above the GO line, no chip', () => {
    renderAt(96)
    expect(chip()).toBeNull()
  })

  it('93 %: between the lines, below the GO line, titled with the default bands', () => {
    renderAt(93)
    expect(chip()).toHaveTextContent('below the GO line (95%)')
    expect(chip()).toHaveAttribute('data-live-pass-rate-chip', 'warning')
    expect(chip()).toHaveAttribute('title', 'Bands: default')
  })

  it('85 %: in the No-Go zone', () => {
    renderAt(85)
    expect(chip()).toHaveTextContent('in the No-Go zone (< 90%)')
    expect(chip()).toHaveAttribute('data-live-pass-rate-chip', 'danger')
  })

  it('a project policy moves the lines and names itself in the title', () => {
    bands.policy = {
      id: 'p-1', project_id: PROJECT, version: 4, name: 'Payments gate', description: null, is_active: true,
      is_draft: false, created_by: 'u', activated_by: 'u', activated_at: null, created_at: '', updated_at: null,
      rules: { pass_rate_bands: { orange_min: 92, yellow_min: 97, green_min: 99 } },
    }
    expect(readinessBands(bands.policy as never).goMin).toBe(97)
    renderAt(96)
    expect(chip()).toHaveTextContent('below the GO line (97%)')
    expect(chip()).toHaveAttribute('title', 'Bands: Payments gate v4')
  })

  it('the old fixed 90 % chip is gone', () => {
    renderAt(85)
    expect(document.body).not.toHaveTextContent('below 90% target')
  })
})
