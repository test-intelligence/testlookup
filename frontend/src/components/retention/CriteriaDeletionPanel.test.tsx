/**
 * S5 UI — the criteria builder.
 *
 * The tests that matter here are the ones about the FREEZE: that a stale
 * preview can never be executed, and that execute is handed a job id rather
 * than criteria. Everything else is form plumbing.
 */
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import CriteriaDeletionPanel from './CriteriaDeletionPanel'
import { buildCriteria, EMPTY_CRITERIA_FORM, narrowsNothing } from './criteriaForm'

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
