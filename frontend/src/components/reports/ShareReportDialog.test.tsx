/**
 * GATE-06: the release gate's Share report dialog. The API has always taken a
 * layout and an expiry (1-30 days), listed a run's links and revoked one; the
 * page used none of it (always executive, always 7 days, no list, no revoke).
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ShareLink } from '@/services/reportExportService'
import ShareReportDialog from './ShareReportDialog'

const { service, clipboard, toast } = vi.hoisted(() => ({
  service: {
    createShareLink: vi.fn(),
    listShareLinks: vi.fn(),
    revokeShareLink: vi.fn(),
  },
  clipboard: { copyTextToClipboard: vi.fn() },
  toast: { success: vi.fn(), error: vi.fn() },
}))

vi.mock('@/services/reportExportService', async (importOriginal) => ({
  ...await importOriginal<typeof import('@/services/reportExportService')>(),
  createShareLink: service.createShareLink,
  listShareLinks: service.listShareLinks,
  revokeShareLink: service.revokeShareLink,
}))
vi.mock('@/utils/clipboard', () => clipboard)
vi.mock('react-hot-toast', () => ({ default: toast }))

const DAY = 86_400_000

function link(overrides: Partial<ShareLink> = {}): ShareLink {
  return {
    id: 'link-1',
    token: null,
    share_url: null,
    report_layout: 'engineering',
    // A minute short of 14 days, so "in 14 days" whatever the test's speed.
    expires_at: new Date(Date.now() + 14 * DAY - 60_000).toISOString(),
    created_by_name: 'priya',
    access_count: 0,
    is_revoked: false,
    created_at: new Date(Date.now() - 60_000).toISOString(),
    ...overrides,
  }
}

function renderDialog() {
  return render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <ShareReportDialog runId="run-1" open onClose={() => {}} />
    </SWRConfig>,
  )
}

const dialog = () => screen.getByRole('dialog', { name: 'Share report' })

describe('ShareReportDialog', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    service.listShareLinks.mockResolvedValue([])
    clipboard.copyTextToClipboard.mockResolvedValue(true)
  })

  it('creates the link with the chosen layout and expiry, shows its URL once, and copies it', async () => {
    const created = link({ id: 'link-new', token: 'tok', share_url: 'https://tl.example/api/v1/shared/reports/tok', snapshot_ready: true, warning: null })
    service.createShareLink.mockResolvedValue(created)
    renderDialog()

    fireEvent.click(within(dialog()).getByRole('radio', { name: 'Engineering' }))
    fireEvent.change(within(dialog()).getByRole('combobox', { name: 'Link expires after' }), { target: { value: '14' } })
    service.listShareLinks.mockResolvedValue([{ ...created, token: null, share_url: null }])
    fireEvent.click(within(dialog()).getByRole('button', { name: 'Create link' }))

    await waitFor(() => expect(service.createShareLink).toHaveBeenCalledWith('run-1', 'engineering', 14))
    expect(await within(dialog()).findByRole('textbox', { name: 'Share URL' })).toHaveValue(created.share_url)
    // The list re-reads and shows the new link with its expiry and views.
    const row = await waitFor(() => {
      const el = dialog().querySelector('[data-share-link="link-new"]')
      if (!el) throw new Error('row not listed yet')
      return el as HTMLElement
    })
    expect(row).toHaveTextContent('expires in 14 days, 0 views')
    expect(row).toHaveTextContent('Engineering')

    fireEvent.click(within(dialog()).getByRole('button', { name: 'Copy' }))
    await waitFor(() => expect(clipboard.copyTextToClipboard).toHaveBeenCalledWith(created.share_url))
    expect(toast.success).toHaveBeenCalledWith('Share link copied to clipboard')
  })

  it('says why the link will not work yet when the run has no snapshot', async () => {
    service.createShareLink.mockResolvedValue(link({
      share_url: 'https://tl.example/api/v1/shared/reports/tok', snapshot_ready: false,
      warning: 'This run has no intelligence snapshot yet, so anyone opening the link right now gets a 404.',
    }))
    renderDialog()
    fireEvent.click(within(dialog()).getByRole('button', { name: 'Create link' }))
    expect(await within(dialog()).findByRole('status')).toHaveTextContent(/no intelligence snapshot yet/)
  })

  it("lists the run's links and revokes one: DELETE by id, then the row reads Revoked", async () => {
    service.listShareLinks.mockResolvedValue([link({ access_count: 1 })])
    service.revokeShareLink.mockResolvedValue(undefined)
    renderDialog()

    const row = await waitFor(() => {
      const el = dialog().querySelector('[data-share-link="link-1"]')
      if (!el) throw new Error('row not listed yet')
      return el as HTMLElement
    })
    expect(row).toHaveTextContent('1 view')
    expect(row).toHaveAttribute('data-share-status', 'active')

    service.listShareLinks.mockResolvedValue([link({ access_count: 1, is_revoked: true })])
    fireEvent.click(within(row).getByRole('button', { name: 'Revoke' }))

    await waitFor(() => expect(service.revokeShareLink).toHaveBeenCalledWith('link-1'))
    await waitFor(() => expect(dialog().querySelector('[data-share-link="link-1"]')).toHaveAttribute('data-share-status', 'revoked'))
    const revoked = dialog().querySelector('[data-share-link="link-1"]') as HTMLElement
    expect(within(revoked).getByText('Revoked')).toBeInTheDocument()
    expect(within(revoked).queryByRole('button', { name: 'Revoke' })).toBeNull()
  })

  it('an expired link is labelled expired and offers no revoke', async () => {
    service.listShareLinks.mockResolvedValue([link({ expires_at: new Date(Date.now() - DAY).toISOString() })])
    renderDialog()
    const row = await waitFor(() => {
      const el = dialog().querySelector('[data-share-link="link-1"]')
      if (!el) throw new Error('row not listed yet')
      return el as HTMLElement
    })
    expect(row).toHaveAttribute('data-share-status', 'expired')
    expect(within(row).queryByRole('button', { name: 'Revoke' })).toBeNull()
  })

  it("shows a refusal in the API's own words", async () => {
    service.createShareLink.mockRejectedValue({
      response: { status: 409, data: { detail: { code: 'report_pending_review', message: 'This report has not been reviewed yet.' } } },
    })
    renderDialog()
    fireEvent.click(within(dialog()).getByRole('button', { name: 'Create link' }))
    expect(await within(dialog()).findByRole('alert')).toHaveTextContent('This report has not been reviewed yet.')
  })
})
