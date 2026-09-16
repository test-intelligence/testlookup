import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useAuditLog, useTestCases } from '@/hooks/useTestManagement'
import type { ManagedTestCase, PaginatedResponse } from '@/types/test-management'
import { TestCasesTab } from './TestManagementPage'

vi.mock('@/hooks/useTestManagement', async (importOriginal) => ({
  ...await importOriginal<typeof import('@/hooks/useTestManagement')>(),
  useAuditLog: vi.fn(),
  useTestCases: vi.fn(),
}))

vi.mock('@/hooks/useNow', () => ({
  useNow: () => Date.parse('2026-09-16T00:00:00Z'),
}))

vi.mock('@/hooks/useDataFreshness', () => ({
  useDataFreshness: () => Date.parse('2026-09-16T00:00:00Z'),
}))

vi.mock('react-hot-toast', () => ({
  default: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }),
}))

function testCase(): ManagedTestCase {
  return {
    id: 'case-1',
    project_id: 'project-1',
    title: 'Checkout preserves cart',
    test_type: 'functional',
    priority: 'high',
    severity: 'major',
    test_suite_id: null,
    status: 'active',
    version: 1,
    is_automated: false,
    automation_status: 'manual',
    ai_generated: false,
    source: 'manual',
    allowed_actions: ['deprecate'],
    created_at: '2026-09-01T00:00:00Z',
    updated_at: '2026-09-15T00:00:00Z',
  }
}

function page(items: ManagedTestCase[]): PaginatedResponse<ManagedTestCase> {
  return { items, total: items.length, page: 1, size: 25, pages: items.length ? 1 : 0 }
}

function renderTab() {
  return render(
    <MemoryRouter>
      <TestCasesTab projectId="project-1" lifecycleV2 />
    </MemoryRouter>,
  )
}

describe('TestCasesTab request failures and retries', () => {
  const mutateCases = vi.fn().mockResolvedValue(undefined)
  const mutateHealth = vi.fn().mockResolvedValue(undefined)

  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    mutateCases.mockResolvedValue(undefined)
    mutateHealth.mockResolvedValue(undefined)
    vi.mocked(useAuditLog).mockReturnValue({ data: undefined } as ReturnType<typeof useAuditLog>)
    vi.mocked(useTestCases).mockImplementation((params) => {
      const isHealth = params?.size === 200
      return {
        data: page([testCase()]),
        error: isHealth ? undefined : new Error('refresh failed'),
        isLoading: false,
        isValidating: false,
        mutate: isHealth ? mutateHealth : mutateCases,
      } as unknown as ReturnType<typeof useTestCases>
    })
  })

  it('keeps stale rows visible and retries the list and health roll together', async () => {
    renderTab()

    expect(screen.getByRole('alert')).toHaveTextContent(
      'Test cases could not be refreshed. Previously loaded results remain visible.',
    )
    expect(screen.getByText('Checkout preserves cart')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Retry case data' }))

    await waitFor(() => {
      expect(mutateCases).toHaveBeenCalledTimes(1)
      expect(mutateHealth).toHaveBeenCalledTimes(1)
    })
  })

  it('does not render a successful empty state when the initial request failed', () => {
    vi.mocked(useTestCases).mockImplementation((params) => ({
      data: params?.size === 200 ? page([]) : undefined,
      error: params?.size === 200 ? undefined : new Error('load failed'),
      isLoading: false,
      isValidating: false,
      mutate: params?.size === 200 ? mutateHealth : mutateCases,
    } as unknown as ReturnType<typeof useTestCases>))

    renderTab()

    expect(screen.getByRole('alert')).toHaveTextContent('Test cases could not be loaded.')
    expect(screen.getByText('No test-case data is available until the request succeeds.')).toBeInTheDocument()
    expect(screen.queryByText(/Create your first test case/i)).not.toBeInTheDocument()
  })

  it('reports a failed retry while retaining the stale row', async () => {
    mutateCases.mockRejectedValueOnce(new Error('still offline'))
    renderTab()

    fireEvent.click(screen.getByRole('button', { name: 'Retry case data' }))

    expect(await screen.findByRole('status')).toHaveTextContent(
      'Test-case data is still unavailable. Previously loaded results remain visible.',
    )
    expect(screen.getByText('Checkout preserves cart')).toBeInTheDocument()
  })

  it('allows only one retry while a refresh is in flight', async () => {
    let resolveRetry: (() => void) | undefined
    mutateCases.mockReturnValueOnce(new Promise<void>((resolve) => { resolveRetry = resolve }))
    renderTab()

    const retry = screen.getByRole('button', { name: 'Retry case data' })
    fireEvent.click(retry)
    expect(screen.getByRole('button', { name: 'Retrying…' })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: 'Retrying…' }))

    expect(mutateCases).toHaveBeenCalledTimes(1)
    expect(mutateHealth).toHaveBeenCalledTimes(1)
    resolveRetry?.()
    await waitFor(() => expect(screen.getByRole('button', { name: 'Retry case data' })).toBeEnabled())
  })
})
