import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { useState } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import ApiKeysPage, { CreatedKeyModal } from './ApiKeysPage'

const create = vi.fn()
const refresh = vi.fn()
const retry = vi.fn()
let hookState: { data?: unknown[]; isLoading: boolean; error?: unknown; mutate: typeof retry }
let projectState = {
  activeProjectId: 'project-a',
  activeProject: { id: 'project-a', name: 'Project A' },
}
let permissionsState = { isAdmin: true, role: 'ADMIN' }
let authState = { sessionGeneration: 1, user: { id: 'admin-1' } }

vi.mock('@/services/apiKeyService', () => ({
  apiKeyService: { create: (...args: unknown[]) => create(...args), revoke: vi.fn() },
}))
vi.mock('@/hooks/useApiKeys', () => ({
  useApiKeys: () => hookState,
  refreshApiKeys: () => refresh(),
}))
vi.mock('@/hooks/usePermissions', () => ({ usePermissions: () => permissionsState }))
vi.mock('@/store/authStore', () => ({
  useAuthStore: (selector: (state: typeof authState) => unknown) => selector(authState),
}))
vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: '__ALL__',
  useProjectStore: (selector: (state: typeof projectState) => unknown) => selector(projectState),
}))
vi.mock('react-hot-toast', () => ({ default: { error: vi.fn(), success: vi.fn() } }))

const created = {
  id: 'key-1', name: 'CI key', key_hint: 'qai_test...', raw_key: 'qai_secret_once',
  scopes: ['stream:write'], project_id: 'project-a', is_active: true,
  expires_at: null, last_used_at: null, created_at: '2026-09-18T00:00:00Z',
}

beforeEach(() => {
  create.mockReset()
  refresh.mockReset()
  retry.mockReset()
  hookState = { data: [], isLoading: false, mutate: retry }
  projectState = {
    activeProjectId: 'project-a',
    activeProject: { id: 'project-a', name: 'Project A' },
  }
  permissionsState = { isAdmin: true, role: 'ADMIN' }
  authState = { sessionGeneration: 1, user: { id: 'admin-1' } }
})

describe('ApiKeysPage identity safety', () => {
  it('renders a retryable outage instead of claiming the key list is empty', () => {
    hookState = { isLoading: false, error: Object.assign(new Error('down'), { response: { status: 503 } }), mutate: retry }
    render(<ApiKeysPage />)

    expect(screen.getByTestId('api-keys-unavailable')).toHaveAttribute('role', 'alert')
    expect(screen.queryByText('No API keys yet')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(retry).toHaveBeenCalledTimes(1)
  })

  it('removes a one-time key secret when the active project changes', async () => {
    create.mockResolvedValue(created)
    const view = render(<ApiKeysPage />)
    fireEvent.click(screen.getByRole('button', { name: 'Generate streaming key' }))
    fireEvent.change(screen.getByPlaceholderText('ci-runner-prod'), { target: { value: 'CI key' } })
    fireEvent.click(screen.getByRole('button', { name: 'Generate' }))
    await screen.findByText('qai_secret_once')
    expect(screen.getByText(/testlookup\.project=Project A/)).toBeInTheDocument()

    projectState = {
      activeProjectId: 'project-b',
      activeProject: { id: 'project-b', name: 'Project B' },
    }
    view.rerender(<ApiKeysPage />)

    await waitFor(() => expect(screen.queryByText('qai_secret_once')).not.toBeInTheDocument())
  })

  it('drops key UI and ignores in-flight creation when authority is downgraded', async () => {
    let resolveCreate!: (value: typeof created) => void
    create.mockImplementation(() => new Promise(resolve => { resolveCreate = resolve }))
    const view = render(<ApiKeysPage />)
    fireEvent.click(screen.getByRole('button', { name: 'Generate streaming key' }))
    fireEvent.change(screen.getByPlaceholderText('ci-runner-prod'), { target: { value: 'CI key' } })
    fireEvent.click(screen.getByRole('button', { name: 'Generate' }))

    permissionsState = { isAdmin: false, role: 'VIEWER' }
    view.rerender(<ApiKeysPage />)
    expect(screen.queryByRole('button', { name: 'Generate streaming key' })).not.toBeInTheDocument()
    expect(screen.queryByPlaceholderText('ci-runner-prod')).not.toBeInTheDocument()

    resolveCreate(created)
    await waitFor(() => expect(screen.queryByText('qai_secret_once')).not.toBeInTheDocument())
  })

  it('removes a one-time key secret when the authenticated session changes', async () => {
    create.mockResolvedValue(created)
    const view = render(<ApiKeysPage />)
    fireEvent.click(screen.getByRole('button', { name: 'Generate streaming key' }))
    fireEvent.change(screen.getByPlaceholderText('ci-runner-prod'), { target: { value: 'CI key' } })
    fireEvent.click(screen.getByRole('button', { name: 'Generate' }))
    await screen.findByText('qai_secret_once')

    authState = { sessionGeneration: 2, user: { id: 'admin-1' } }
    view.rerender(<ApiKeysPage />)

    await waitFor(() => expect(screen.queryByText('qai_secret_once')).not.toBeInTheDocument())
  })
})

describe('CreatedKeyModal keyboard contract', () => {
  it('contains keyboard focus, closes on Escape, and restores the trigger', () => {
    function Harness() {
      const [open, setOpen] = useState(false)
      return <>
        <button type="button" onClick={() => setOpen(true)}>Show generated key</button>
        {open && <CreatedKeyModal created={created} baseUrl="https://example.test" projectLabel="Project A" onClose={() => setOpen(false)} />}
      </>
    }
    render(<Harness />)
    const trigger = screen.getByRole('button', { name: 'Show generated key' })
    trigger.focus()
    fireEvent.click(trigger)
    const dialog = screen.getByRole('dialog')
    const focusable = Array.from(dialog.querySelectorAll<HTMLElement>('button:not([disabled])'))
    expect(dialog.contains(document.activeElement)).toBe(true)
    focusable[0].focus()
    fireEvent.keyDown(focusable[0], { key: 'Tab', shiftKey: true })
    expect(focusable[focusable.length - 1]).toHaveFocus()
    fireEvent.keyDown(document.activeElement ?? document.body, { key: 'Tab' })
    expect(focusable[0]).toHaveFocus()
    fireEvent.keyDown(document.activeElement ?? document.body, { key: 'Escape' })
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(trigger).toHaveFocus()
  })
})
