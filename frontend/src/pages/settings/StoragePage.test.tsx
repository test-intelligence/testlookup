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
import { parityHistory } from '@/test/suiteParityMocks'

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
  // W20 ADM-10: the Secrets card (Vault not configured here).
  getSecretProviders: () => Promise.resolve({ providers: [{ name: 'vault', configured: false, address_host: null, auth_method: 'token', healthy: false, message: 'Not configured: set SECRET_PROVIDER_VAULT_ADDR on the deployment.', references: 0 }], reference_syntax: 'vault:<path>#<key>' }),
}))

vi.mock('@/hooks/usePermissions', () => ({ usePermissions: () => ({ isAdmin }) }))

// W25a HYG-04: the cutover card's history read. Mocked (the network guard), except where a test sets
// `parity.real` to prove the page never mounts the REAL read for a non-admin (a request would fail the guard).
const parity = vi.hoisted(() => ({ real: false, history: vi.fn() }))
vi.mock('@/hooks/useSuiteParity', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/hooks/useSuiteParity')>()
  return {
    ...actual,
    useSuiteParityHistory: (...args: Parameters<typeof actual.useSuiteParityHistory>) =>
      parity.real ? actual.useSuiteParityHistory(...args) : parity.history(...args),
    useSuiteParityRun: () => ({ data: undefined, error: undefined }),
  }
})

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
  parity.real = false
  parity.history.mockReturnValue({ data: undefined, error: undefined, mutate: vi.fn() })
})

describe('the suite membership cutover card (W25a HYG-04)', () => {
  it('an admin sees it as the LAST block of the page, below the Secrets card', async () => {
    parity.history.mockReturnValue({ data: parityHistory({ streak: 4 }), error: undefined, mutate: vi.fn() })
    const { container } = renderPage()
    await screen.findByDisplayValue('minio:9000')

    expect(screen.getByRole('heading', { name: 'Suite membership cutover' })).toBeInTheDocument()
    expect(screen.getByText('Day 4 of 7 with no differences.')).toBeInTheDocument()
    expect(parity.history).toHaveBeenCalled()
    expect(parity.history.mock.calls.every(([enabled]) => enabled === true)).toBe(true)
    const blocks = Array.from((container.firstElementChild as HTMLElement).children)
    expect(blocks[blocks.length - 1]).toHaveAttribute('data-suite-parity')
    expect(blocks[blocks.length - 2]).toHaveAttribute('data-testid', 'secret-providers')
  })

  it('a QA lead renders no card and requests nothing on the parity path (the REAL read would trip the network guard)', async () => {
    isAdmin = false
    parity.real = true
    renderPage()
    await screen.findByDisplayValue('minio:9000')

    expect(screen.queryByRole('heading', { name: 'Suite membership cutover' })).toBeNull()
    expect(document.querySelector('[data-suite-parity]')).toBeNull()
    expect(parity.history).not.toHaveBeenCalled()
  })
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
        expect.stringMatching(/could not load storage configuration/i),
      ),
    )
  })
})

/**
 * W12 SRCH-02 (contract §2.4): `chroma_collection` is the Collection prefix of every vector collection. The
 * field shows the prefix the stored value MEANS (the legacy default and an invalid value read as none, with
 * the server's note), the card says which prefix is in use, an untouched field sends the stored value back,
 * and an edited one refused with a 422 shows the sentence under the field.
 */
describe('the Collection prefix (W12 SRCH-02)', () => {
  const HELP =
    "Up to 8 lower-case letters, digits or '-'. Every vector collection's name starts with it, so two deployments " +
    "can share one ChromaDB. Changing it re-embeds every index into new collections; search keeps the current ones " +
    'until that finishes.'
  const INVALID = "A collection prefix is up to 8 lower-case letters, digits or '-', starting with a letter or digit."
  const w12 = (stored: string, prefix: string, active: string | null, note: string | null = null) =>
    ({ ...CONFIG, chroma_collection: stored, collection_prefix: prefix, collection_prefix_note: note, active_collection_prefix: active }) as StorageConfigRead
  const prefixField = () => screen.getByLabelText('Collection prefix') as HTMLInputElement
  const save = () => fireEvent.click(screen.getByRole('button', { name: /save configuration/i }))

  it('labels the field, shows "none" for no prefix, the help and the prefix in use', async () => {
    mockGet.mockResolvedValue(w12('', '', ''))
    renderPage()
    await screen.findByDisplayValue('minio:9000')

    expect(prefixField().value).toBe('')
    expect(prefixField().placeholder).toBe('none')
    expect(screen.getByText(HELP)).toBeInTheDocument()
    expect(screen.getByText('In use: collections carry no prefix.')).toBeInTheDocument()
  })

  it('a stored value that is not a valid prefix reads as none, with the note', async () => {
    const note = "The stored value 'test_failures' is not a valid prefix and is ignored: collections carry no prefix."
    mockGet.mockResolvedValue(w12('test_failures', '', '', note))
    renderPage()
    await screen.findByDisplayValue('minio:9000')

    expect(prefixField().value).toBe('')
    expect(screen.getByText(note)).toBeInTheDocument()
  })

  it('says which prefix is in use, and that it could not tell when the state is unreadable', async () => {
    mockGet.mockResolvedValue(w12('demo', 'demo', 'demo'))
    const { unmount } = renderPage()
    await screen.findByDisplayValue('minio:9000')
    expect(prefixField().value).toBe('demo')
    expect(screen.getByText('In use: collections start with “demo_”.')).toBeInTheDocument()
    unmount()

    mockGet.mockResolvedValue(w12('demo', 'demo', null))
    renderPage()
    await screen.findByDisplayValue('minio:9000')
    expect(screen.getByText('Could not read the embedding index state.')).toBeInTheDocument()
  })

  it('an older server (no prefix fields): the raw value in the field, no "In use" line', async () => {
    renderPage()
    await screen.findByDisplayValue('minio:9000')
    expect(prefixField().value).toBe('test_case_search')
    expect(screen.queryByText(/^In use: collections/)).toBeNull()
  })

  it('an untouched field sends the STORED value back (the legacy default is not rewritten)', async () => {
    mockGet.mockResolvedValue(w12('testlookup_embeddings', '', ''))
    mockUpdate.mockResolvedValue(w12('testlookup_embeddings', '', ''))
    renderPage()
    await screen.findByDisplayValue('minio:9000')
    expect(prefixField().value).toBe('')

    save()
    await waitFor(() => expect(mockUpdate).toHaveBeenCalledTimes(1))
    expect(mockUpdate.mock.calls[0][0]).toMatchObject({ chroma_collection: 'testlookup_embeddings' })
  })

  it('an edited prefix is sent; a 422 shows its sentence under the field, not a toast; typing clears it', async () => {
    mockGet.mockResolvedValue(w12('', '', ''))
    mockUpdate.mockRejectedValueOnce({ response: { status: 422, data: { detail: INVALID } } })
    mockUpdate.mockResolvedValueOnce(w12('demo', 'demo', ''))
    renderPage()
    await screen.findByDisplayValue('minio:9000')

    fireEvent.change(prefixField(), { target: { value: 'Demo_1' } })
    save()
    await waitFor(() => expect(screen.getByText(INVALID)).toBeInTheDocument())
    expect(mockUpdate.mock.calls[0][0]).toMatchObject({ chroma_collection: 'Demo_1' })
    expect(mockToastError).not.toHaveBeenCalled()

    fireEvent.change(prefixField(), { target: { value: 'demo' } })
    expect(screen.queryByText(INVALID)).toBeNull()
    save()
    await waitFor(() => expect(mockToastSuccess).toHaveBeenCalled())
    expect(mockUpdate.mock.calls[1][0]).toMatchObject({ chroma_collection: 'demo' })
    expect(prefixField().value).toBe('demo')
  })
})
