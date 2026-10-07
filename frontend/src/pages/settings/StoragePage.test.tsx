/**
 * StoragePage — storage backend configuration.
 *
 * Zero coverage before this (backlog: "zero-coverage surfaces"). One real
 * shortcoming found: the save handler was a bare `catch {}` that reported
 * "Failed to save storage configuration" and threw away the server's reason,
 * so a rejected bucket name or a 422 on a specific field gave the operator
 * nothing to act on. `SeedDataPage` already read `response.data.detail`; this
 * page now does too.
 *
 * The other guard here is the non-admin read-only state. Storage config points
 * the deployment at its object store — a viewer must not be able to repoint it,
 * and the failure mode of getting that wrong is silent (the field simply
 * accepts input and the save 403s later).
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import StoragePage from './StoragePage'
import type { StorageConfigRead } from '@/services/appSettingsService'
import { expectTemplateHeader } from '@/test/expectTemplateHeader'

const mockGet = vi.fn<() => Promise<StorageConfigRead>>()
const mockUpdate = vi.fn()
const mockToastError = vi.fn()
const mockToastSuccess = vi.fn()

let isAdmin = true

vi.mock('react-hot-toast', () => ({
  default: {
    error: (m: string) => mockToastError(m),
    success: (m: string) => mockToastSuccess(m),
  },
}))

vi.mock('@/services/appSettingsService', () => ({
  appSettingsService: {
    getStorageConfig: () => mockGet(),
    updateStorageConfig: (p: unknown) => mockUpdate(p),
  },
}))

vi.mock('@/hooks/usePermissions', () => ({ usePermissions: () => ({ isAdmin }) }))

const CONFIG = {
  storage_backend: 'minio',
  chroma_collection: 'test_case_search',
  minio_endpoint: 'minio:9000',
  minio_bucket_name: 'testlookup',
  minio_use_ssl: false,
} as unknown as StorageConfigRead

function renderPage() {
  return render(
    <MemoryRouter>
      <StoragePage />
    </MemoryRouter>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  isAdmin = true
  mockGet.mockResolvedValue(CONFIG)
})

describe('a failed save tells the operator why', () => {
  it('includes the server detail in the toast', async () => {
    mockUpdate.mockRejectedValue({
      response: { data: { detail: 'bucket name must be lowercase' } },
    })
    renderPage()
    await screen.findByDisplayValue('minio:9000')

    fireEvent.click(screen.getByRole('button', { name: /save/i }))

    await waitFor(() =>
      expect(mockToastError).toHaveBeenCalledWith(
        expect.stringContaining('bucket name must be lowercase'),
      ),
    )
  })

  it('falls back to the generic message when the server sends no detail', async () => {
    // A network error has no response body; the page must still say something
    // rather than render "undefined" into the toast.
    mockUpdate.mockRejectedValue(new Error('Network Error'))
    renderPage()
    await screen.findByDisplayValue('minio:9000')

    fireEvent.click(screen.getByRole('button', { name: /save/i }))

    await waitFor(() =>
      expect(mockToastError).toHaveBeenCalledWith('Failed to save storage configuration'),
    )
  })

  it('reports a successful save', async () => {
    mockUpdate.mockResolvedValue(CONFIG)
    renderPage()
    await screen.findByDisplayValue('minio:9000')

    fireEvent.click(screen.getByRole('button', { name: /save/i }))

    await waitFor(() => expect(mockToastSuccess).toHaveBeenCalled())
    expect(mockToastError).not.toHaveBeenCalled()
  })
})

describe('a non-admin cannot repoint the deployment at another store', () => {
  it('disables the configuration fields', async () => {
    isAdmin = false
    renderPage()

    const endpoint = await screen.findByDisplayValue('minio:9000')
    expect(endpoint).toBeDisabled()
  })

  it('leaves them editable for an admin', async () => {
    renderPage()

    const endpoint = await screen.findByDisplayValue('minio:9000')
    expect(endpoint).not.toBeDisabled()
  })
})

describe('the settings page template (UX redesign P5)', () => {
  it('has the compact header with the route help topic', async () => {
    renderPage()
    await screen.findByDisplayValue('minio:9000')

    expectTemplateHeader('Data & Storage', '/settings/storage')
  })

  it('lays the form out in two columns at >= 1280 px instead of a max-w-2xl column', async () => {
    const { container } = renderPage()
    await screen.findByDisplayValue('minio:9000')

    expect(container.querySelector('.max-w-2xl')).toBeNull()
    const grid = container.querySelector('[data-settings-form-grid]') as HTMLElement
    expect(grid).toHaveClass('grid', 'grid-cols-1', 'xl:grid-cols-2')

    // In order: the status strip across both columns, then the two stores side by side.
    const cards = Array.from(grid.children)
    expect(cards.map((c) => c.querySelector('h3')?.textContent)).toEqual([
      'Infrastructure',
      'Object Storage (MinIO / S3)',
      'ChromaDB (Vector Store)',
    ])
    expect(cards[0]).toHaveClass('xl:col-span-2')
    expect(cards[1]).not.toHaveClass('xl:col-span-2')
    expect(cards[2]).not.toHaveClass('xl:col-span-2')
    // ChromaDB's three fields wrap to two per row in a half-width column.
    expect(cards[2].querySelector('.grid')).toHaveClass('grid-cols-3', 'xl:grid-cols-2')

    // Save stays under the grid, inside the same form.
    const save = screen.getByRole('button', { name: /save configuration/i })
    expect(grid.contains(save)).toBe(false)
    expect(save.closest('form')).toBe(grid.closest('form'))
  })
})

describe('a failed load is surfaced', () => {
  it('toasts rather than rendering an empty form silently', async () => {
    mockGet.mockRejectedValue(new Error('boom'))
    renderPage()

    await waitFor(() =>
      expect(mockToastError).toHaveBeenCalledWith(
        expect.stringMatching(/failed to load storage configuration/i),
      ),
    )
  })
})
