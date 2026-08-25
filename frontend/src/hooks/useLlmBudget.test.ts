/**
 * Regression: a project with no LLM budget is an unset state, not an error.
 *
 * ``GET /api/v1/projects/{id}/llm-quota`` answers **404** when no budget is
 * configured — that is the documented contract, not a failure:
 *
 *     raise HTTPException(404, "No LLM cost budget configured for this project")
 *
 * ``useProjectQuota`` maps that single status to ``null`` so the deep
 * investigation and intelligence pages can render "no cap set" instead of an
 * error state. Nothing pinned that mapping, and it is one `catch` edit away
 * from turning every unbudgeted project's page into a failure.
 *
 * Found by a live sweep of the homelab, which flagged the 404 on
 * ``/deep-investigate/:runId``. The 404 turned out to be correct on both
 * sides — but correct-and-untested, which is how it stops being correct.
 *
 * The negative case matters as much: a 500 must still surface as an error.
 * Swallowing every status would make a broken billing service look like an
 * unconfigured one, which is the "absence reads as health" shape this codebase
 * has been bitten by before.
 */
import { createElement, type ReactNode } from 'react'
import { renderHook, waitFor } from '@testing-library/react'
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { SWRConfig } from 'swr'

vi.mock('@/services/llmBudgetService', () => ({
  llmBudgetService: {
    getQuota: vi.fn(),
    getUsage: vi.fn(),
    overview: vi.fn(),
    putQuota: vi.fn(),
    getHistory: vi.fn(),
  },
}))

import { llmBudgetService } from '@/services/llmBudgetService'
import { useProjectQuota } from './useLlmBudget'

// Each test gets its own SWR cache so a previous resolution cannot bleed over.
function wrapper({ children }: { children: ReactNode }) {
  return createElement(SWRConfig, { value: { provider: () => new Map() } }, children)
}

const PROJECT = '440a4346-4efa-4aea-96b4-cde04009e267'

function axiosStatus(status: number) {
  return Object.assign(new Error(`Request failed with status code ${status}`), {
    response: { status },
  })
}

describe('useProjectQuota', () => {
  beforeEach(() => {
    vi.mocked(llmBudgetService.getQuota).mockReset()
  })

  it('treats a 404 as "no budget configured" rather than an error', async () => {
    vi.mocked(llmBudgetService.getQuota).mockRejectedValue(axiosStatus(404))

    const { result } = renderHook(() => useProjectQuota(PROJECT), { wrapper })

    await waitFor(() => expect(result.current.isLoading).toBe(false))
    expect(result.current.quota).toBeNull()
    expect(
      result.current.isError,
      'an unconfigured budget must not render the page as failed',
    ).toBe(false)
  })

  it('still reports a real server failure as an error', async () => {
    vi.mocked(llmBudgetService.getQuota).mockRejectedValue(axiosStatus(500))

    const { result } = renderHook(() => useProjectQuota(PROJECT), { wrapper })

    await waitFor(() => expect(result.current.isError).toBe(true))
    expect(
      result.current.quota,
      'a broken billing service must not look like an unset budget',
    ).toBeNull()
  })

  it('returns the quota when one is configured', async () => {
    const quota = {
      project_id: PROJECT,
      monthly_cost_cap_usd: 25,
      hard_stop: true,
    }
    vi.mocked(llmBudgetService.getQuota).mockResolvedValue(quota as never)

    const { result } = renderHook(() => useProjectQuota(PROJECT), { wrapper })

    await waitFor(() => expect(result.current.quota).not.toBeNull())
    expect(result.current.quota).toMatchObject({ monthly_cost_cap_usd: 25 })
    expect(result.current.isError).toBe(false)
  })

  it('does not fetch at all without a project id', async () => {
    const { result } = renderHook(() => useProjectQuota(null), { wrapper })

    await waitFor(() => expect(result.current.isLoading).toBe(false))
    expect(llmBudgetService.getQuota).not.toHaveBeenCalled()
    expect(result.current.quota).toBeNull()
  })
})
