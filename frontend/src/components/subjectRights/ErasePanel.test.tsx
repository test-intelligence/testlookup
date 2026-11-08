/**
 * W31 STRAT-12: the in-row Erase panel (contract §2.3, §4.1, §4.4) through the REAL SWR hooks, the service mocked
 * (JOB_POLL_MS 40 ms): preview first; blockers listed and the button disabled; the button enables ONLY on the exact
 * username; `measured: false` reads "not counted (took too long)", never 0; erase → ONE POST → the phase line →
 * `erase_done`; `confirm_mismatch` under the input; "Resume erasure" for an unfinished job. Never a dialog.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  ADMIN_DISPLAY,
  blockerFixture,
  erasurePreviewFixture,
  incompleteErasureFixture,
  SUBJECT_DISPLAY,
  SUBJECT_PSEUDONYM,
  SUBJECT_USER_ID,
  SUBJECT_USERNAME,
  subjectErrorBody,
  subjectJobFixture,
  subjectRefusal,
} from '@/test/subjectRightsMocks'
import { ERROR_SENTENCES, UI_SENTENCES } from '@/types/subjectRights'
import { formatDay } from '@/utils/formatters'

vi.mock('@/types/subjectRights', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/types/subjectRights')>()),
  JOB_POLL_MS: 40,
}))
const service = vi.hoisted(() => ({ getErasurePreview: vi.fn(), eraseUser: vi.fn(), getJob: vi.fn() }))
vi.mock('@/services/subjectRightsService', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/services/subjectRightsService')>()),
  subjectRightsService: service,
}))

import ErasePanel from './ErasePanel'
import { confirmLabel, eraseBody, erasedLine, NOT_COUNTED, phaseLine, requestedByLine, RESUME_BUTTON } from './erasureModel'

const USER = { id: SUBJECT_USER_ID, username: SUBJECT_USERNAME, full_name: SUBJECT_DISPLAY }
const onChanged = vi.fn()
const onUsersChanged = vi.fn()

function renderPanel() {
  return render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0, shouldRetryOnError: false }}>
      <ErasePanel user={USER} latestErasure={null} onChanged={onChanged} onUsersChanged={onUsersChanged} />
    </SWRConfig>,
  )
}

const eraseButton = () => screen.getByRole('button', { name: UI_SENTENCES.erase_button })
const confirmInput = () => screen.getByLabelText(confirmLabel(SUBJECT_USERNAME))
const type = (value: string) => fireEvent.change(confirmInput(), { target: { value } })
const list = (id: string) => document.querySelector(`[data-erase-list="${id}"]`) as HTMLElement

beforeEach(() => {
  for (const fn of Object.values(service)) fn.mockReset()
  onChanged.mockReset()
  onUsersChanged.mockReset()
})

describe('ErasePanel — the preview and the typed confirm', () => {
  it('preview first: the pseudonym, the summary, changes / kept / open items; the button waits for the EXACT username', async () => {
    service.getErasurePreview.mockResolvedValue(erasurePreviewFixture())
    renderPanel()
    expect(await screen.findByText(eraseBody(SUBJECT_PSEUDONYM))).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 3, name: UI_SENTENCES.erase_title })).toBeInTheDocument()
    expect(screen.getByText('6 changes · 4 kept · 2 open items')).toBeInTheDocument()
    expect(within(list('changes')).getAllByRole('listitem')).toHaveLength(6)
    expect(within(list('kept')).getByRole('heading', { name: UI_SENTENCES.erase_kept_title })).toBeInTheDocument()
    expect(within(list('kept')).getAllByRole('listitem')).toHaveLength(4)
    expect(within(list('open')).getAllByRole('listitem')).toHaveLength(2)
    // The uncounted legal-holds row says so and shows no number at all — never 0.
    const holds = document.querySelector('[data-erase-location="retention_holds.placed_by_id"]') as HTMLElement
    expect(holds).toHaveTextContent(NOT_COUNTED)
    expect((holds.firstElementChild as HTMLElement).textContent).toBe('Legal holds they placed')

    expect(eraseButton()).toBeDisabled()
    for (const wrong of ['quinn', 'Quinn.Tester', ` ${SUBJECT_USERNAME}`, `${SUBJECT_USERNAME} `]) {
      type(wrong)
      expect(eraseButton(), JSON.stringify(wrong)).toBeDisabled()
    }
    type(SUBJECT_USERNAME)
    expect(eraseButton()).toBeEnabled()
    expect(document.querySelector('[role="dialog"], [aria-modal]')).toBeNull()
  })

  it('blockers: each sentence is listed and the button stays disabled even on the exact username', async () => {
    service.getErasurePreview.mockResolvedValue(
      erasurePreviewFixture({ blockers: [blockerFixture('last_admin'), blockerFixture('export_blocks_erasure')] }),
    )
    renderPanel()
    expect(await screen.findByText(ERROR_SENTENCES.last_admin)).toBeInTheDocument()
    expect(screen.getByText(ERROR_SENTENCES.export_blocks_erasure)).toBeInTheDocument()
    type(SUBJECT_USERNAME)
    expect(eraseButton()).toBeDisabled()
  })

  it("an export that finishes beside the panel clears its own blocker (the preview is re-read on the export's change)", async () => {
    service.getErasurePreview
      .mockResolvedValueOnce(erasurePreviewFixture({ blockers: [blockerFixture('export_blocks_erasure')] }))
      .mockResolvedValue(erasurePreviewFixture())
    const panel = (exportState: string) => (
      <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0, shouldRetryOnError: false }}>
        <ErasePanel user={USER} latestErasure={null} onChanged={onChanged} onUsersChanged={onUsersChanged} exportState={exportState} />
      </SWRConfig>
    )
    const { rerender } = render(panel('export-1:running'))
    expect(await screen.findByText(ERROR_SENTENCES.export_blocks_erasure)).toBeInTheDocument()
    type(SUBJECT_USERNAME)
    expect(eraseButton()).toBeDisabled()

    rerender(panel('export-1:succeeded'))
    await waitFor(() => expect(screen.queryByText(ERROR_SENTENCES.export_blocks_erasure)).not.toBeInTheDocument())
    expect(service.getErasurePreview).toHaveBeenCalledTimes(2)
    expect(eraseButton()).toBeEnabled()
  })

  it('confirm_mismatch (422) sits under the input; any other refusal is an alert with its sentence', async () => {
    service.getErasurePreview.mockResolvedValue(erasurePreviewFixture())
    service.eraseUser
      .mockRejectedValueOnce(subjectRefusal(422, subjectErrorBody('confirm_mismatch')))
      .mockRejectedValueOnce(subjectRefusal(409, subjectErrorBody('export_blocks_erasure')))
    renderPanel()
    await screen.findByText(eraseBody(SUBJECT_PSEUDONYM))
    type(SUBJECT_USERNAME)
    fireEvent.click(eraseButton())
    const mismatch = await screen.findByText(ERROR_SENTENCES.confirm_mismatch)
    expect(mismatch).toHaveAttribute('data-erase-mismatch')
    expect(screen.queryByRole('alert')).toBeNull()
    type(SUBJECT_USERNAME)
    fireEvent.click(eraseButton())
    expect(await screen.findByRole('alert')).toHaveTextContent(ERROR_SENTENCES.export_blocks_erasure)
  })
})

describe('ErasePanel — the erasure', () => {
  it('erase → ONE POST {username} → "Erasure in progress" per step (never "Erased" at lockout) → erase_done', async () => {
    service.getErasurePreview.mockResolvedValue(erasurePreviewFixture())
    const queued = subjectJobFixture({ kind: 'erasure', status: 'queued' })
    const at = (phase: string, status = 'running') => ({ ...queued, status, phase })
    service.eraseUser.mockResolvedValue(queued)
    service.getJob
      .mockResolvedValueOnce(at('lockout', 'queued')).mockResolvedValueOnce(at('lockout', 'queued')).mockResolvedValueOnce(at('lockout', 'queued'))
      .mockResolvedValueOnce(at('postgres')).mockResolvedValueOnce(at('postgres')).mockResolvedValueOnce(at('postgres'))
      .mockResolvedValue(subjectJobFixture({ id: queued.id, kind: 'erasure', status: 'succeeded' }))
    renderPanel()
    await screen.findByText(eraseBody(SUBJECT_PSEUDONYM))
    type(SUBJECT_USERNAME)
    fireEvent.click(eraseButton())
    const lockout = await screen.findByText(phaseLine({ phase: 'lockout' }))
    expect(lockout.closest('[data-erase-panel]')?.textContent).not.toMatch(/Erased\./)
    expect(await screen.findByText(phaseLine({ phase: 'postgres' }))).toBeInTheDocument()
    expect(await screen.findByText(erasedLine(SUBJECT_PSEUDONYM))).toBeInTheDocument()
    expect(service.eraseUser).toHaveBeenCalledTimes(1)
    expect(service.eraseUser).toHaveBeenCalledWith(SUBJECT_USER_ID, SUBJECT_USERNAME)
    // After the lock-out and again at done: the job list and the users list re-read.
    await waitFor(() => expect(onUsersChanged).toHaveBeenCalledTimes(2))
    expect(onChanged).toHaveBeenCalledTimes(2)
    expect(screen.queryByRole('button', { name: UI_SENTENCES.erase_button })).toBeNull()
  })

  it('an unfinished erasure: its step, the server sentence, and "Resume erasure" behind the same exact confirm', async () => {
    const stopped = incompleteErasureFixture('objects')
    service.getErasurePreview.mockResolvedValue(
      erasurePreviewFixture({ erasure_job: stopped, blockers: [blockerFixture('erasure_running')] }),
    )
    service.getJob.mockResolvedValue(stopped)
    service.eraseUser.mockResolvedValue({ ...stopped, status: 'queued', error: null })
    renderPanel()
    expect(await screen.findByText(phaseLine({ phase: 'objects' }))).toBeInTheDocument()
    expect(screen.getByText(stopped.error?.message ?? '')).toBeInTheDocument()
    const resume = screen.getByRole('button', { name: RESUME_BUTTON })
    expect(resume).toBeDisabled()
    type(SUBJECT_USERNAME.toUpperCase())
    expect(resume).toBeDisabled()
    type(SUBJECT_USERNAME)
    expect(resume).toBeEnabled()
    fireEvent.click(resume)
    await waitFor(() => expect(service.eraseUser).toHaveBeenCalledWith(SUBJECT_USER_ID, SUBJECT_USERNAME))
  })
})

describe('ErasePanel — W38: the pending request and who started the erasure', () => {
  it('a pending request reads "This person asked for erasure on <date>." above the preview; none without one', async () => {
    const requestedAt = '2026-09-18T10:00:00Z'
    service.getErasurePreview.mockResolvedValueOnce(
      erasurePreviewFixture({ pending_request: { id: '00000000-0000-4000-8000-0000000e5a01', requested_at: requestedAt } }),
    )
    const { unmount } = renderPanel()
    const line = await screen.findByText(`This person asked for erasure on ${formatDay(requestedAt)}.`)
    expect(line).toHaveAttribute('data-erase-pending-request')
    expect(line.nextElementSibling).toHaveTextContent(eraseBody(SUBJECT_PSEUDONYM))
    unmount()
    service.getErasurePreview.mockResolvedValueOnce(erasurePreviewFixture({ pending_request: null }))
    renderPanel()
    await screen.findByText(eraseBody(SUBJECT_PSEUDONYM))
    expect(document.querySelector('[data-erase-pending-request]')).toBeNull()
  })

  it('an erasure job names its requester; a SCIM one reads "Your identity provider (SCIM)" (D-11)', async () => {
    const scim = subjectJobFixture({ kind: 'erasure', status: 'running', phase: 'postgres', source: 'scim', requested_by_display: null })
    service.getErasurePreview.mockResolvedValue(erasurePreviewFixture({ erasure_job: scim }))
    service.getJob.mockResolvedValue(scim)
    const { unmount } = renderPanel()
    expect(await screen.findByText(phaseLine({ phase: 'postgres' }))).toBeInTheDocument()
    expect(screen.getByText(requestedByLine(UI_SENTENCES.requester_scim))).toHaveAttribute('data-erase-requester')
    expect(screen.getByText('Requested by Your identity provider (SCIM).')).toBeInTheDocument()
    unmount()
    const admin = subjectJobFixture({ kind: 'erasure', status: 'running', phase: 'postgres' })
    service.getErasurePreview.mockResolvedValue(erasurePreviewFixture({ erasure_job: admin }))
    service.getJob.mockResolvedValue(admin)
    renderPanel()
    expect(await screen.findByText(requestedByLine(ADMIN_DISPLAY))).toBeInTheDocument()
    expect(screen.queryByText(requestedByLine(UI_SENTENCES.requester_scim))).toBeNull()
  })
})
