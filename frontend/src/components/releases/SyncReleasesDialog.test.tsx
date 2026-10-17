/**
 * REL-08: the sync dialog shows the server's own reason when a sync cannot
 * run (503 while offline / no credential, with the way to Settings; 502 when
 * the source is unreachable), and the counts when it ran.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const sync = vi.fn()
vi.mock('@/services/releasesService', () => ({
  releasesService: { sync: (...args: unknown[]) => sync(...args) },
}))
const toastSuccess = vi.hoisted(() => vi.fn())
vi.mock('react-hot-toast', () => ({ default: { success: toastSuccess, error: vi.fn() } }))

import SyncReleasesDialog from './SyncReleasesDialog'

const httpError = (status: number, detail: string) =>
  Object.assign(new Error(`Request failed with status code ${status}`), { response: { status, data: { detail } } })

function renderDialog() {
  const onClose = vi.fn()
  const onDone = vi.fn()
  const view = render(
    <MemoryRouter>
      <SyncReleasesDialog projectId="proj-1" onClose={onClose} onDone={onDone} />
    </MemoryRouter>,
  )
  return { ...view, onClose, onDone }
}

beforeEach(() => {
  sync.mockReset()
  toastSuccess.mockReset()
})

describe('SyncReleasesDialog', () => {
  it('says what a sync writes and what it leaves alone', () => {
    renderDialog()
    expect(screen.getByRole('region', { name: 'Sync releases' })).toBeInTheDocument()
    expect(screen.getByText('Pulls release names and links from the source. Phases, gate policy and run attribution stay in TestLookup.')).toBeInTheDocument()
    expect(screen.getByLabelText('GitHub milestones')).toBeChecked()
    // The Jira key belongs to Jira only.
    expect(screen.queryByLabelText('Jira project key')).not.toBeInTheDocument()
    fireEvent.click(screen.getByLabelText('Jira fix versions'))
    expect(screen.getByLabelText('Jira project key')).toHaveAttribute('placeholder', 'Uses the default key when empty')
  })

  it('a 503 shows the server sentence verbatim and the way to Settings, and changes nothing', async () => {
    sync.mockRejectedValue(httpError(503, 'AI_OFFLINE_MODE is enabled'))
    const { container, onDone } = renderDialog()
    fireEvent.click(screen.getByRole('button', { name: 'Sync' }))
    await waitFor(() => expect(container.querySelector('[data-sync-error]')).not.toBeNull())
    const error = container.querySelector('[data-sync-error]') as HTMLElement
    expect(error).toHaveTextContent(/^AI_OFFLINE_MODE is enabledOpen Settings › Integrations$/)
    expect(screen.getByRole('link', { name: 'Open Settings › Integrations' })).toHaveAttribute('href', '/settings/integrations')
    expect(sync).toHaveBeenCalledWith({ project_id: 'proj-1', source: 'github' })
    expect(onDone).not.toHaveBeenCalled()
    expect(toastSuccess).not.toHaveBeenCalled()
  })

  it('a 502 (the source unreachable) shows its sentence with no Settings link', async () => {
    sync.mockRejectedValue(httpError(502, 'Could not reach Jira: ConnectError'))
    const { container } = renderDialog()
    fireEvent.click(screen.getByLabelText('Jira fix versions'))
    fireEvent.change(screen.getByLabelText('Jira project key'), { target: { value: ' SHOP ' } })
    fireEvent.click(screen.getByRole('button', { name: 'Sync' }))
    await waitFor(() => expect(container.querySelector('[data-sync-error]')).toHaveTextContent('Could not reach Jira: ConnectError'))
    expect(screen.queryByRole('link', { name: /Open Settings/ })).not.toBeInTheDocument()
    expect(sync).toHaveBeenCalledWith({ project_id: 'proj-1', source: 'jira', jira_project_key: 'SHOP' })
  })

  it('a successful sync toasts the counts and hands back to the page', async () => {
    sync.mockResolvedValue({ source: 'github', created: 2, updated: 1, skipped: 0 })
    const { onDone, container } = renderDialog()
    fireEvent.click(screen.getByRole('button', { name: 'Sync' }))
    await waitFor(() => expect(onDone).toHaveBeenCalledTimes(1))
    expect(toastSuccess).toHaveBeenCalledWith('Synced from GitHub: 2 created, 1 updated, 0 skipped.')
    expect(container.querySelector('[data-sync-error]')).toBeNull()
  })

  it('Cancel closes without a request', () => {
    const { onClose } = renderDialog()
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(onClose).toHaveBeenCalledTimes(1)
    expect(sync).not.toHaveBeenCalled()
  })

  // W27 PAN-02: in the page (under the releases' banner), with the dialog's hooks kept.
  it('renders in the page, keeps its hooks, and Escape inside closes it except mid-sync', async () => {
    let finish: (v: unknown) => void = () => {}
    sync.mockImplementation(() => new Promise((resolve) => { finish = resolve }))
    const { container, onClose } = renderDialog()
    const form = screen.getByRole('region', { name: 'Sync releases' })
    expect(container.contains(form)).toBe(true)
    expect(document.querySelector('[role="dialog"], [aria-modal]')).toBeNull()
    expect(form).toHaveAttribute('data-sync-releases-dialog', '')
    expect(form).toHaveAttribute('aria-labelledby', 'sync-releases-title')
    expect(screen.getByLabelText('GitHub milestones')).toHaveFocus()

    fireEvent.click(screen.getByRole('button', { name: 'Sync' }))
    fireEvent.keyDown(screen.getByLabelText('GitHub milestones'), { key: 'Escape' })
    expect(screen.getByRole('button', { name: 'Close sync releases dialog' })).toBeDisabled()
    expect(onClose).not.toHaveBeenCalled()

    finish({ source: 'github', created: 0, updated: 0, skipped: 0 })
    await waitFor(() => expect(screen.getByRole('button', { name: 'Close sync releases dialog' })).toBeEnabled())
    fireEvent.keyDown(screen.getByLabelText('GitHub milestones'), { key: 'Escape' })
    expect(onClose).toHaveBeenCalledTimes(1)
  })
})
