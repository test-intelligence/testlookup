/**
 * W38 (W31 F-2, contract §4, D-8): "When your identity provider deletes a user" through the REAL SWR hook and the
 * REAL ssoService (only `@/services/api` mocked, so the GET / PUT and the body asserted are the wire ones):
 * deactivate is checked by default; choosing erase shows the warning and the acknowledgement, which alone enables
 * Save; Save sends ONE PUT `{action}`; a 403 `instance_interactive_only` reads inline (no toast); switching back to
 * the saved value needs no acknowledgement and offers no Save; a saved erase shows the warning without the checkbox.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { api } from '@/services/api'
import { subjectRefusal } from '@/test/subjectRightsMocks'
import { formatCompactDateTime } from '@/utils/formatters'
import ScimDeleteActionSection from './ScimDeleteActionSection'
import { SCIM_DELETE_LOAD_FAILED, SCIM_ERASE_ACK, SCIM_ERASE_WARNING } from './scimDeleteActionModel'

vi.mock('@/services/api', () => ({ api: { get: vi.fn(), put: vi.fn() } }))

const INTERACTIVE_ONLY = 'The instance default is changed by a signed-in administrator; API keys can only read it.'
const setting = (action: string, extra: Record<string, unknown> = {}) => ({ action, updated_at: null, updated_by_display: null, ...extra })

function renderSection() {
  return render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0, shouldRetryOnError: false }}>
      <ScimDeleteActionSection />
    </SWRConfig>,
  )
}

const section = () => screen.getByRole('region', { name: 'When your identity provider deletes a user' })
const deactivate = () => within(section()).getByRole('radio', { name: /Deactivate the account \(default\)/ })
const erase = () => within(section()).getByRole('radio', { name: /Erase the person's data — cannot be undone/ })
const save = () => within(section()).getByRole('button', { name: 'Save' })
const ack = () => within(section()).queryByRole('checkbox', { name: SCIM_ERASE_ACK })

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(api.get).mockResolvedValue({ data: setting('deactivate') })
})

describe('ScimDeleteActionSection', () => {
  it('reads the setting: deactivate checked, no warning, Save disabled until something changes', async () => {
    renderSection()
    await waitFor(() => expect(deactivate()).toBeChecked())
    expect(api.get).toHaveBeenCalledWith('/api/v1/settings/scim-delete-action')
    expect(erase()).not.toBeChecked()
    expect(save()).toBeDisabled()
    expect(within(section()).queryByText(SCIM_ERASE_WARNING)).toBeNull()
    expect(section()).toHaveAttribute('data-scim-delete-saved', 'deactivate')
  })

  it('erase → the warning + the acknowledgement; Save enables ONLY once ticked → ONE PUT {action: "erase"}', async () => {
    vi.mocked(api.put).mockResolvedValueOnce({ data: setting('erase', { updated_at: '2026-11-07T10:00:00Z', updated_by_display: 'Morgan Admin' }) })
    renderSection()
    await waitFor(() => expect(deactivate()).toBeChecked())
    fireEvent.click(erase())
    expect(within(section()).getByText(SCIM_ERASE_WARNING)).toBeInTheDocument()
    expect(save()).toBeDisabled()
    fireEvent.click(ack() as HTMLElement)
    expect(save()).toBeEnabled()
    fireEvent.click(save())
    await waitFor(() => expect(api.put).toHaveBeenCalledTimes(1))
    expect(api.put).toHaveBeenCalledWith('/api/v1/settings/scim-delete-action', { action: 'erase' }, { suppressToast: true })
    await waitFor(() => expect(section()).toHaveAttribute('data-scim-delete-saved', 'erase'))
    expect(within(section()).getByText(`Last changed ${formatCompactDateTime('2026-11-07T10:00:00Z')} by Morgan Admin.`)).toBeInTheDocument()
    // Saved: the warning stays (erase is on), the acknowledgement and Save do not.
    expect(within(section()).getByText(SCIM_ERASE_WARNING)).toBeInTheDocument()
    expect(ack()).toBeNull()
    expect(save()).toBeDisabled()
  })

  it('unticking the acknowledgement disables Save again; picking deactivate drops the warning', async () => {
    renderSection()
    await waitFor(() => expect(deactivate()).toBeChecked())
    fireEvent.click(erase())
    fireEvent.click(ack() as HTMLElement)
    fireEvent.click(ack() as HTMLElement)
    expect(save()).toBeDisabled()
    fireEvent.click(deactivate())
    expect(within(section()).queryByText(SCIM_ERASE_WARNING)).toBeNull()
    expect(save()).toBeDisabled()
    expect(api.put).not.toHaveBeenCalled()
  })

  it('a 403 instance_interactive_only reads inline as the server sentence, never the Axios words', async () => {
    vi.mocked(api.put).mockRejectedValueOnce(
      subjectRefusal(403, { detail: { code: 'instance_interactive_only', message: INTERACTIVE_ONLY } }),
    )
    renderSection()
    await waitFor(() => expect(deactivate()).toBeChecked())
    fireEvent.click(erase())
    fireEvent.click(ack() as HTMLElement)
    fireEvent.click(save())
    expect(await within(section()).findByRole('alert')).toHaveTextContent(INTERACTIVE_ONLY)
    expect(screen.queryByText(/Request failed with status code/)).toBeNull()
    expect(section()).toHaveAttribute('data-scim-delete-saved', 'deactivate')
  })

  it('erase already saved: switching to deactivate needs no acknowledgement → ONE PUT {action: "deactivate"}', async () => {
    vi.mocked(api.get).mockResolvedValue({ data: setting('erase') })
    vi.mocked(api.put).mockResolvedValueOnce({ data: setting('deactivate') })
    renderSection()
    await waitFor(() => expect(erase()).toBeChecked())
    expect(within(section()).getByText(SCIM_ERASE_WARNING)).toBeInTheDocument()
    expect(ack()).toBeNull()
    fireEvent.click(deactivate())
    expect(save()).toBeEnabled()
    fireEvent.click(save())
    await waitFor(() => expect(api.put).toHaveBeenCalledWith('/api/v1/settings/scim-delete-action', { action: 'deactivate' }, { suppressToast: true }))
  })

  it('an unknown stored value reads as the default; a failed read says so', async () => {
    vi.mocked(api.get).mockResolvedValueOnce({ data: setting('shred') })
    const { unmount } = renderSection()
    await waitFor(() => expect(deactivate()).toBeChecked())
    unmount()
    vi.mocked(api.get).mockRejectedValueOnce(new Error('boom'))
    renderSection()
    expect(await within(section()).findByText(SCIM_DELETE_LOAD_FAILED)).toBeInTheDocument()
  })
})
