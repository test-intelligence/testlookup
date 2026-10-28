/**
 * S5 UI — the criteria builder.
 *
 * The tests that matter here are the ones about the FREEZE: that a stale
 * preview can never be executed, and that execute is handed a job id rather
 * than criteria. Everything else is form plumbing.
 */
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import CriteriaDeletionPanel from './CriteriaDeletionPanel'
import { buildCriteria, EMPTY_CRITERIA_FORM, narrowsNothing } from './criteriaForm'
import { exportEstimate } from '@/test/retentionExportMocks'
import { ARCHIVE_NOT_BACKUP, EXPORT_ERROR_SENTENCES } from '@/types/retentionExport'

const preview = vi.fn()
const execute = vi.fn()

vi.mock('@/services/retentionService', () => ({
  criteriaDeletionService: {
    preview: (...args: unknown[]) => preview(...args),
    execute: (...args: unknown[]) => execute(...args),
  },
}))

vi.mock('react-hot-toast', () => ({
  default: { success: vi.fn(), error: vi.fn() },
}))

function frozen(overrides = {}) {
  return {
    job_id: 'job-1',
    project_id: 'p1',
    run_count: 3,
    run_ids: ['r1', 'r2', 'r3'],
    candidate_hash: 'abc123',
    truncated: false,
    refused_prefixes: [],
    blocked: [],
    ...overrides,
  }
}

function renderPanel() {
  return render(
    <CriteriaDeletionPanel projectId="p1" projectName="Checkout" />,
  )
}

beforeEach(() => {
  preview.mockReset()
  execute.mockReset()
  preview.mockResolvedValue(frozen())
})

// ── the empty-criteria guard ────────────────────────────────────────────────

describe('criteria that narrow nothing', () => {
  it('leaves Preview disabled until a criterion is set', () => {
    renderPanel()

    const button = screen.getByRole('button', { name: /preview/i })
    expect(button).toBeDisabled()

    fireEvent.change(screen.getByLabelText(/older than days/i), {
      target: { value: '90' },
    })
    expect(button).toBeEnabled()
  })

  it('explains why, rather than only greying the button out', () => {
    renderPanel()
    expect(screen.getByText(/would purge the entire project/i)).toBeInTheDocument()
  })

  it('is a pure function that agrees with the backend rule', () => {
    expect(narrowsNothing(buildCriteria(EMPTY_CRITERIA_FORM))).toBe(true)
    expect(
      narrowsNothing(buildCriteria({ ...EMPTY_CRITERIA_FORM, statuses: ['FAILED'] })),
    ).toBe(false)
  })

  it('does not count an EMPTY list as a criterion', () => {
    // Going through buildCriteria cannot reach this: it omits empty lists, so
    // the array branch was never exercised and a mutation making every array
    // count as narrowing survived. narrowsNothing is exported and must be
    // right for any input — and the backend agrees, since an empty list is
    // falsy in its own `any(...)` check.
    expect(narrowsNothing({ statuses: [] })).toBe(true)
    expect(narrowsNothing({ run_ids: [], branches: [] })).toBe(true)
    expect(narrowsNothing({ statuses: ['FAILED'] })).toBe(false)
  })
})

// ── the freeze ──────────────────────────────────────────────────────────────

describe('the frozen candidate set', () => {
  it('sends the job id to execute, never the criteria', async () => {
    execute.mockResolvedValue({ job_id: 'job-1', run_count: 3, status: 'accepted' })
    renderPanel()

    fireEvent.change(screen.getByLabelText(/older than days/i), {
      target: { value: '90' },
    })
    fireEvent.click(screen.getByRole('button', { name: /preview/i }))
    await screen.findByTestId('criteria-preview')

    fireEvent.click(screen.getByRole('button', { name: /delete 3 runs/i }))
    fireEvent.change(screen.getByLabelText(/type.*to confirm/i), {
      target: { value: 'Checkout' },
    })
    fireEvent.click(screen.getByRole('button', { name: /delete permanently/i }))

    await waitFor(() => expect(execute).toHaveBeenCalledTimes(1))
    // (projectId, jobId, confirmationName) — no criteria anywhere.
    expect(execute).toHaveBeenCalledWith('p1', 'job-1', 'Checkout')
  })

  it('discards the preview when a criterion changes', async () => {
    renderPanel()

    fireEvent.change(screen.getByLabelText(/older than days/i), {
      target: { value: '90' },
    })
    fireEvent.click(screen.getByRole('button', { name: /preview/i }))
    await screen.findByTestId('criteria-preview')

    // Editing after previewing: the counts on screen describe the OLD set, so
    // leaving them visible would let an operator authorise a number that is no
    // longer what would run.
    fireEvent.change(screen.getByLabelText(/branches/i), { target: { value: 'main' } })

    expect(screen.queryByTestId('criteria-preview')).not.toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: /delete 3 runs/i }),
    ).not.toBeInTheDocument()
  })

  it('requires the typed project name before deleting', async () => {
    renderPanel()

    fireEvent.change(screen.getByLabelText(/older than days/i), {
      target: { value: '90' },
    })
    fireEvent.click(screen.getByRole('button', { name: /preview/i }))
    await screen.findByTestId('criteria-preview')
    fireEvent.click(screen.getByRole('button', { name: /delete 3 runs/i }))

    const confirm = screen.getByRole('button', { name: /delete permanently/i })
    expect(confirm).toBeDisabled()

    fireEvent.change(screen.getByLabelText(/type.*to confirm/i), {
      target: { value: 'Checkou' },
    })
    expect(confirm).toBeDisabled()

    fireEvent.change(screen.getByLabelText(/type.*to confirm/i), {
      target: { value: 'Checkout' },
    })
    expect(confirm).toBeEnabled()
  })

  // W29a UIX-01 (L2): "Delete permanently" is the shared .btn-danger (white on
  // the 15 % failed tint was 1.26:1 on Lab).
  it('"Delete permanently" is the shared destructive button, with no hand-written colours', async () => {
    renderPanel()
    fireEvent.change(screen.getByLabelText(/older than days/i), { target: { value: '90' } })
    fireEvent.click(screen.getByRole('button', { name: /preview/i }))
    await screen.findByTestId('criteria-preview')
    fireEvent.click(screen.getByRole('button', { name: /delete 3 runs/i }))

    const confirm = screen.getByRole('button', { name: /delete permanently/i })
    expect(confirm.classList.contains('btn-danger')).toBe(true)
    expect(confirm.className).not.toMatch(/text-white|status-failed|rounded\b|px-3/)
  })
})

// W29a UIX-07 (§0.27): the selected status criterion read --color-primary,
// which no theme defines (it silently inherited); it is the accent now.
describe('the status criteria', () => {
  it('a selected status is outlined and tinted with the accent', () => {
    renderPanel()
    const failed = screen.getByRole('button', { name: 'FAILED' })
    expect(failed).toHaveAttribute('aria-pressed', 'false')
    fireEvent.click(failed)
    expect(failed).toHaveAttribute('aria-pressed', 'true')
    expect(failed.className).toContain('border-[var(--color-accent)]')
    expect(failed.className).toContain('bg-[var(--color-accent)]/15')
    expect(failed.className).not.toContain('--color-primary')
  })
})

// ── what the preview must surface ───────────────────────────────────────────

describe('what the preview reports', () => {
  it('shows the count that will actually go', async () => {
    renderPanel()
    fireEvent.change(screen.getByLabelText(/older than days/i), {
      target: { value: '90' },
    })
    fireEvent.click(screen.getByRole('button', { name: /preview/i }))

    expect(await screen.findByTestId('preview-run-count')).toHaveTextContent('3')
  })

  it('lists blocked runs and their reasons', async () => {
    preview.mockResolvedValue(
      frozen({
        run_count: 1,
        blocked: [{ run_id: 'aaaaaaaa-1111', reasons: ['cited by 1 compliance pack(s)'] }],
      }),
    )
    renderPanel()
    fireEvent.change(screen.getByLabelText(/older than days/i), {
      target: { value: '90' },
    })
    fireEvent.click(screen.getByRole('button', { name: /preview/i }))

    const blocked = await screen.findByTestId('preview-blocked')
    expect(blocked).toHaveTextContent(/compliance pack/i)
    expect(blocked).toHaveTextContent('aaaaaaaa')
  })

  it('warns that object storage outside the project will NOT be deleted', async () => {
    preview.mockResolvedValue(frozen({ refused_prefixes: ['uploads/shared/'] }))
    renderPanel()
    fireEvent.change(screen.getByLabelText(/older than days/i), {
      target: { value: '90' },
    })
    fireEvent.click(screen.getByRole('button', { name: /preview/i }))

    const refused = await screen.findByTestId('preview-refused-prefixes')
    expect(refused).toHaveTextContent('uploads/shared/')
    expect(refused).toHaveTextContent(/will NOT be deleted/i)
  })

  it('says the set was truncated rather than quietly deleting a slice', async () => {
    preview.mockResolvedValue(frozen({ truncated: true }))
    renderPanel()
    fireEvent.change(screen.getByLabelText(/older than days/i), {
      target: { value: '90' },
    })
    fireEvent.click(screen.getByRole('button', { name: /preview/i }))

    expect(await screen.findByText(/more runs matched than can be reviewed/i)).toBeInTheDocument()
  })

  it('will not open the confirm dialog for an empty selection', async () => {
    preview.mockResolvedValue(frozen({ run_count: 0, run_ids: [] }))
    renderPanel()
    fireEvent.change(screen.getByLabelText(/older than days/i), {
      target: { value: '90' },
    })
    fireEvent.click(screen.getByRole('button', { name: /preview/i }))
    await screen.findByTestId('criteria-preview')

    expect(screen.getByRole('button', { name: /delete 0 runs/i })).toBeDisabled()
  })
})

// ── the form → request translation ──────────────────────────────────────────

describe('buildCriteria', () => {
  it('omits empty fields rather than sending empty lists', () => {
    // A present-but-empty list is a criterion that matches nothing, which the
    // backend would treat as a real narrowing selecting zero runs.
    expect(buildCriteria(EMPTY_CRITERIA_FORM)).toEqual({})
  })

  it('splits comma and newline separated lists', () => {
    const out = buildCriteria({
      ...EMPTY_CRITERIA_FORM,
      branches: 'main, release/2.0\n develop ',
    })
    expect(out.branches).toEqual(['main', 'release/2.0', 'develop'])
  })

  it('sends suite_match only alongside suite_names', () => {
    expect(buildCriteria({ ...EMPTY_CRITERIA_FORM, suiteMatch: 'any' }).suite_match)
      .toBeUndefined()

    const withSuites = buildCriteria({
      ...EMPTY_CRITERIA_FORM,
      suiteNames: 'Auth',
      suiteMatch: 'any',
    })
    expect(withSuites.suite_names).toEqual(['Auth'])
    expect(withSuites.suite_match).toBe('any')
  })

  it('ignores a non-numeric or zero day count', () => {
    expect(buildCriteria({ ...EMPTY_CRITERIA_FORM, olderThanDays: 'abc' }).older_than_days)
      .toBeUndefined()
    expect(buildCriteria({ ...EMPTY_CRITERIA_FORM, olderThanDays: '0' }).older_than_days)
      .toBeUndefined()
    expect(buildCriteria({ ...EMPTY_CRITERIA_FORM, olderThanDays: '90' }).older_than_days)
      .toBe(90)
  })

  it('warns before the destructive suite reading', () => {
    // `any` deletes multi-suite runs where only one suite matched; `only` does
    // not. The default must be the conservative one.
    expect(EMPTY_CRITERIA_FORM.suiteMatch).toBe('only')
  })
})

// ── the typed confirmation, in the page (W27b PAN-06, R2) ───────────────────

describe('the typed confirmation opens in the preview box', () => {
  async function openConfirmation() {
    renderPanel()
    fireEvent.change(screen.getByLabelText(/older than days/i), { target: { value: '90' } })
    fireEvent.click(screen.getByRole('button', { name: /preview/i }))
    await screen.findByTestId('criteria-preview')
    const trigger = screen.getByRole('button', { name: /delete 3 runs/i })
    trigger.focus()
    fireEvent.click(trigger)
    return { trigger, form: screen.getByRole('region', { name: 'Delete 3 runs' }) }
  }

  it('renders inside the preview, right under its Delete button, with no overlay; the field is focused', async () => {
    const { trigger, form } = await openConfirmation()
    const box = screen.getByTestId('criteria-preview')
    expect(box.contains(form)).toBe(true)
    expect(trigger.nextElementSibling).toBe(form)
    expect(document.querySelector('[aria-modal]')).toBeNull()
    expect(document.querySelector('[role="dialog"]')).toBeNull()
    expect(document.activeElement).toBe(screen.getByLabelText(/type.*to confirm/i))
    expect(within(form).getByRole('button', { name: /delete permanently/i })).toHaveAttribute('aria-disabled', 'true')
  })

  it('Escape closes it and focus goes back to Delete n runs; nothing is sent', async () => {
    const { trigger } = await openConfirmation()
    fireEvent.keyDown(screen.getByLabelText(/type.*to confirm/i), { key: 'Escape' })
    expect(screen.queryByRole('region', { name: 'Delete 3 runs' })).toBeNull()
    expect(document.activeElement).toBe(trigger)
    expect(execute).not.toHaveBeenCalled()
  })

  it('while the delete is being sent, Escape is swallowed and the X is disabled', async () => {
    let answer: () => void = () => undefined
    execute.mockReturnValue(new Promise((resolve) => { answer = () => resolve({ job_id: 'job-1', run_count: 3, status: 'accepted' }) }))
    const { form } = await openConfirmation()
    fireEvent.change(screen.getByLabelText(/type.*to confirm/i), { target: { value: 'Checkout' } })
    fireEvent.click(within(form).getByRole('button', { name: /delete permanently/i }))
    await waitFor(() => expect(execute).toHaveBeenCalledTimes(1))

    fireEvent.keyDown(form, { key: 'Escape' })
    expect(screen.getByRole('region', { name: 'Delete 3 runs' })).toBe(form)
    expect(within(form).getByRole('button', { name: 'Close' })).toBeDisabled()

    answer()
    await waitFor(() => expect(screen.queryByTestId('criteria-preview')).not.toBeInTheDocument())
    expect(execute).toHaveBeenCalledTimes(1)
    expect(execute).toHaveBeenCalledWith('p1', 'job-1', 'Checkout')
  })

  it('editing a criterion, or previewing again, closes it with the set it described', async () => {
    await openConfirmation()
    fireEvent.change(screen.getByLabelText(/branches/i), { target: { value: 'main' } })
    expect(screen.queryByRole('region', { name: 'Delete 3 runs' })).toBeNull()

    // A fresh preview shows its own Delete button, not the old confirmation.
    fireEvent.click(screen.getByRole('button', { name: /preview/i }))
    await screen.findByTestId('criteria-preview')
    expect(screen.queryByRole('region', { name: 'Delete 3 runs' })).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: /delete 3 runs/i }))
    expect(screen.getByRole('region', { name: 'Delete 3 runs' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /preview/i }))
    expect(screen.queryByRole('region', { name: 'Delete 3 runs' })).toBeNull()
    // The preview answered (its button reads Preview again, not Resolving…).
    await screen.findByRole('button', { name: /preview/i })
    expect(screen.queryByRole('region', { name: 'Delete 3 runs' })).toBeNull()
    expect(execute).not.toHaveBeenCalled()
  })
})

// ── W22a: holds, the floor note and the reference warnings ─────────────────

describe('W22a — holds, floor note, reference warnings', () => {
  const FLOOR_NOTE =
    'Explicit deletions are not limited by the keep-last floor; it applies to the scheduled purge only.'

  async function previewWith(overrides: Record<string, unknown>) {
    preview.mockResolvedValue(frozen(overrides))
    renderPanel()
    fireEvent.change(screen.getByLabelText(/older than days/i), { target: { value: '90' } })
    fireEvent.click(screen.getByRole('button', { name: /preview/i }))
    return screen.findByTestId('criteria-preview')
  }

  it('lists a held run with its Held: reason first', async () => {
    const panel = await previewWith({
      run_count: 2,
      blocked: [{ run_id: 'held-run-0001', reasons: ['Held: Audit Q4 sentinel', 'linked to 1 release(s) — unlink the run first'] }],
    })
    const item = within(within(panel).getByTestId('preview-blocked')).getByRole('listitem')
    expect(item.textContent).toContain('— Held: Audit Q4 sentinel; linked to 1 release(s)')
  })

  it('shows the floor note and the reference warnings when the server sends them', async () => {
    const panel = await previewWith({
      floor_applies: false,
      floor_note: FLOOR_NOTE,
      references_broken: [
        { table: 'run_baselines', column: 'baseline_run_id', count: 3, label: '3 runs lose their baseline link' },
      ],
      references_measured: true,
    })
    expect(within(panel).getByText(FLOOR_NOTE)).toBeInTheDocument()
    expect(within(panel).getByText('Reference warnings:')).toBeInTheDocument()
    expect(within(panel).getByText('3 runs lose their baseline link')).toBeInTheDocument()
  })

  it('says the warnings were not measured when the count failed', async () => {
    const panel = await previewWith({ references_broken: [], references_measured: false, floor_note: FLOOR_NOTE })
    expect(within(panel).getByText('Reference warnings were not measured.')).toBeInTheDocument()
    expect(within(panel).queryByText('Reference warnings:')).toBeNull()
  })

  it('shows nothing new when the fields are absent (an older server)', async () => {
    const panel = await previewWith({})
    expect(panel.querySelector('[data-criteria-floor-note]')).toBeNull()
    expect(panel.querySelector('[data-retention-reference-warnings]')).toBeNull()
    expect(within(panel).queryByText(/reference warnings/i)).toBeNull()
  })

  it('shows no warning lines when measured and nothing breaks', async () => {
    const panel = await previewWith({ references_broken: [], references_measured: true, floor_note: FLOOR_NOTE })
    expect(within(panel).getByText(FLOOR_NOTE)).toBeInTheDocument()
    expect(panel.querySelector('[data-retention-reference-warnings]')).toBeNull()
  })
})

// ── W22b ACT-03: the export step ────────────────────────────────────────────

describe('W22b — the export step', () => {
  async function previewWith(overrides: Record<string, unknown>) {
    preview.mockResolvedValue(frozen(overrides))
    renderPanel()
    fireEvent.change(screen.getByLabelText(/older than days/i), { target: { value: '90' } })
    fireEvent.click(screen.getByRole('button', { name: /preview/i }))
    return screen.findByTestId('criteria-preview')
  }

  function typeAndDelete() {
    fireEvent.click(screen.getByRole('button', { name: /delete 3 runs/i }))
    fireEvent.change(screen.getByLabelText(/type.*to confirm/i), { target: { value: 'Checkout' } })
    fireEvent.click(screen.getByRole('button', { name: /delete permanently/i }))
  }

  it('sends the chosen archive in the execute body and says so in the confirmation', async () => {
    execute.mockResolvedValue({ job_id: 'job-1', run_count: 3, status: 'accepted', export_tier: 'evidence', export_target: 'collect' })
    const panel = await previewWith({ export_estimate: exportEstimate({ run_count: 3 }) })
    expect(within(panel).getByText(ARCHIVE_NOT_BACKUP)).toBeInTheDocument()
    fireEvent.click(within(panel).getByRole('radio', { name: 'Download an archive first' }))
    fireEvent.click(within(panel).getByRole('radio', { name: /^Evidence — archive/ }))
    fireEvent.click(screen.getByRole('button', { name: /delete 3 runs/i }))
    expect(
      screen.getByText(
        'An evidence archive is built and verified first. Nothing is deleted until you download it and confirm you have saved it.',
      ),
    ).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText(/type.*to confirm/i), { target: { value: 'Checkout' } })
    fireEvent.click(screen.getByRole('button', { name: /delete permanently/i }))
    await waitFor(() => expect(execute).toHaveBeenCalledTimes(1))
    expect(execute).toHaveBeenCalledWith('p1', 'job-1', 'Checkout', { tier: 'evidence', target: 'collect' })
  })

  it('"Delete without an archive" (the default) keeps today\'s execute call', async () => {
    execute.mockResolvedValue({ job_id: 'job-1', run_count: 3, status: 'accepted' })
    await previewWith({ export_estimate: exportEstimate({ run_count: 3 }) })
    typeAndDelete()
    await waitFor(() => expect(execute).toHaveBeenCalledTimes(1))
    expect(execute).toHaveBeenCalledWith('p1', 'job-1', 'Checkout')
  })

  it('offers no export step without export_estimate (an older server)', async () => {
    const panel = await previewWith({})
    expect(panel.querySelector('[data-criteria-export]')).toBeNull()
    expect(within(panel).queryByText(ARCHIVE_NOT_BACKUP)).toBeNull()
  })

  it('shows an execute refusal inline in the export step; the confirmation stays open', async () => {
    const sentence = EXPORT_ERROR_SENTENCES.over_cap
    execute.mockRejectedValue(
      Object.assign(new Error('request failed'), {
        isAxiosError: true,
        response: { status: 409, data: { detail: { code: 'over_cap', message: sentence } } },
      }),
    )
    const panel = await previewWith({ export_estimate: exportEstimate({ run_count: 3 }) })
    fireEvent.click(within(panel).getByRole('radio', { name: 'Keep an archive in storage' }))
    typeAndDelete()
    const alert = await within(panel).findByRole('alert')
    expect(alert).toHaveTextContent(sentence)
    expect(alert.closest('[data-criteria-export]')).not.toBeNull()
    expect(screen.getByRole('button', { name: /delete permanently/i })).toBeInTheDocument()
  })
})
