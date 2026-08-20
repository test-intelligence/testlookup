/**
 * SeedDataPage — demo-data controls for development environments.
 *
 * Zero coverage before this (backlog: "zero-coverage surfaces"). **No defect
 * found**, and the reason is worth recording so the missing confirmation on
 * the destructive button is not re-filed later:
 *
 * `DELETE /api/v1/dev/seed` wipes the demo data with no `confirm()`, which on
 * any other page in this app would be the same finding as the SSO enforcement
 * toggle. Here it is not, because the backend is guarded twice over —
 * `_require_dev()` returns **404** outside a development environment, and every
 * mutating route carries `Depends(require_role(UserRole.ADMIN))`. An admin
 * wiping demo data in a dev environment is the button working as intended.
 *
 * What these tests do pin is that each of the three buttons calls the endpoint
 * it claims to, and that a failure reports the server's reason instead of a
 * generic message — this page reads `response.data.detail`, which is the
 * pattern StoragePage was missing.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import SeedDataPage from './SeedDataPage'

const mockPost = vi.fn()
const mockDelete = vi.fn()
const mockToastError = vi.fn()
const mockToastSuccess = vi.fn()
const mockRefresh = vi.fn()

vi.mock('react-hot-toast', () => ({
  default: {
    error: (m: string) => mockToastError(m),
    success: (m: string) => mockToastSuccess(m),
  },
}))

vi.mock('@/services/api', () => ({
  api: {
    post: (...a: unknown[]) => mockPost(...a),
    delete: (...a: unknown[]) => mockDelete(...a),
  },
}))

vi.mock('@/hooks/useSeedStatus', () => ({
  useSeedStatus: () => ({
    status: { seeded: true, projects: 3, runs: 12 },
    isLoading: false,
    refresh: mockRefresh,
  }),
}))

function renderPage() {
  return render(
    <MemoryRouter>
      <SeedDataPage />
    </MemoryRouter>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  mockPost.mockResolvedValue({ data: { message: 'ok', output: null } })
  mockDelete.mockResolvedValue({ data: { message: 'ok', output: null } })
})

describe('each control calls the endpoint it advertises', () => {
  it('loads seed data with POST /dev/seed', async () => {
    renderPage()

    fireEvent.click(screen.getByRole('button', { name: /load/i }))

    await waitFor(() => expect(mockPost).toHaveBeenCalledWith('/api/v1/dev/seed'))
    expect(mockDelete).not.toHaveBeenCalled()
  })

  it('resets with POST /dev/seed/reset, not the plain seed route', async () => {
    renderPage()

    fireEvent.click(screen.getByRole('button', { name: /reset/i }))

    // Reset and load differ only by a path suffix; swapping them would
    // silently re-seed on top of existing data instead of replacing it.
    await waitFor(() => expect(mockPost).toHaveBeenCalledWith('/api/v1/dev/seed/reset'))
  })

  it('deletes with DELETE /dev/seed', async () => {
    renderPage()

    fireEvent.click(screen.getByRole('button', { name: /delete/i }))

    await waitFor(() => expect(mockDelete).toHaveBeenCalledWith('/api/v1/dev/seed'))
    expect(mockPost).not.toHaveBeenCalled()
  })
})

describe('failures are reported with the server reason', () => {
  it('shows the API detail rather than a generic message', async () => {
    mockDelete.mockRejectedValue({
      response: { data: { detail: 'seed script exited 1' } },
    })
    renderPage()

    fireEvent.click(screen.getByRole('button', { name: /delete/i }))

    await waitFor(() => expect(mockToastError).toHaveBeenCalledWith('seed script exited 1'))
  })

  it('falls back to a generic message when there is no detail', async () => {
    mockDelete.mockRejectedValue(new Error('Network Error'))
    renderPage()

    fireEvent.click(screen.getByRole('button', { name: /delete/i }))

    await waitFor(() => expect(mockToastError).toHaveBeenCalledWith('Operation failed'))
  })

  it('refreshes status after a successful action', async () => {
    renderPage()

    fireEvent.click(screen.getByRole('button', { name: /load/i }))

    // Without the refresh the page keeps showing the pre-action counts, which
    // reads as "the button did nothing".
    await waitFor(() => expect(mockRefresh).toHaveBeenCalled())
    expect(mockToastSuccess).toHaveBeenCalled()
  })
})
