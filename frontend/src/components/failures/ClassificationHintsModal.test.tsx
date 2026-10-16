/**
 * W07 FAIL-10: the Classification hints dialog (Failures › Categories, QA lead).
 *
 * Pins: the list reads as the ledger's sentence (`error message contains "…" →
 * Infrastructure`) with its note and author; Add sends ONE body and refreshes
 * the list; the preview asks only after the 400 ms debounce and renders the
 * count; a refusal shows the server's text in one toast; and every string from
 * a person or a CI file renders as text, never markup.
 */
import { act, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ClassificationHint, ClassificationHintList, ClassificationHintPreview } from '@/types/classificationHints'

const hooks = vi.hoisted(() => ({
  list: vi.fn(),
  preview: vi.fn(),
  mutate: vi.fn(async () => undefined),
}))
vi.mock('@/hooks/useClassificationHints', () => ({
  PREVIEW_MIN_CHARS: 3,
  useClassificationHints: (projectId: string) => hooks.list(projectId),
  useClassificationHintPreview: (projectId: string, field: string, pattern: string) =>
    hooks.preview(projectId, field, pattern),
}))
const service = vi.hoisted(() => ({
  list: vi.fn(), preview: vi.fn(), create: vi.fn(), update: vi.fn(), remove: vi.fn(),
}))
vi.mock('@/services/classificationHintService', () => ({ classificationHintService: service }))
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }))
vi.mock('react-hot-toast', () => ({ default: toast }))

import ClassificationHintsModal from './ClassificationHintsModal'
import { PREVIEW_DEBOUNCE_MS, hintSentence } from './classificationHintWords'

const HINT: ClassificationHint = {
  id: 'h1', project_id: 'proj-1', match_field: 'error_message', pattern: 'ECONNREFUSED',
  category: 'INFRASTRUCTURE', note: 'The staging DB restarts nightly', enabled: true,
  created_by_name: 'Priya Nair', created_at: '2026-10-15T09:00:00Z', updated_at: null,
}
const LIST: ClassificationHintList = { items: [HINT], total: 1, max_hints: 50 }
const PREVIEW: ClassificationHintPreview = {
  matched: 7, sampled: 200, lookback_days: 30,
  examples: [{ test_name: 'test_checkout_pays', value: 'connect ECONNREFUSED 10.0.0.4:5432' }],
}

function renderModal() {
  const onClose = vi.fn()
  render(<ClassificationHintsModal projectId="proj-1" onClose={onClose} />)
  return { onClose }
}

const addForm = () => screen.getByRole('form', { name: 'Add a hint' })
const containsInput = () => within(addForm()).getByLabelText('Contains')

beforeEach(() => {
  vi.clearAllMocks()
  hooks.list.mockReturnValue({ data: LIST, error: undefined, isLoading: false, mutate: hooks.mutate })
  hooks.preview.mockImplementation((_p: string, _f: string, pattern: string) => ({
    data: pattern.trim().length >= 3 ? PREVIEW : undefined, error: undefined,
  }))
  service.create.mockResolvedValue({ ...HINT, id: 'h2' })
  service.update.mockResolvedValue({ ...HINT, enabled: false })
  service.remove.mockResolvedValue(undefined)
})
afterEach(() => {
  vi.useRealTimers()
  vi.restoreAllMocks()
})

describe('ClassificationHintsModal — the list', () => {
  it("reads the project's hints as the ledger sentence, with the note and who set it", () => {
    renderModal()
    expect(hooks.list).toHaveBeenCalledWith('proj-1')
    expect(screen.getByRole('dialog', { name: 'Classification hints' })).toBeInTheDocument()
    expect(screen.getByText(/Hints classify new failures before the AI does\. A failure that matches several uses the oldest hint\. Existing analyses are not changed\./)).toBeInTheDocument()
    const row = document.querySelector('[data-hint-row="h1"]') as HTMLElement
    expect(within(row).getByText('error message contains "ECONNREFUSED" → Infrastructure')).toBeInTheDocument()
    expect(within(row).getByText('The staging DB restarts nightly')).toBeInTheDocument()
    expect(within(row).getByText('set by Priya Nair')).toBeInTheDocument()
    expect(within(row).getByRole('checkbox')).toBeChecked()
  })

  it('names every field and category in the sentence; an unknown value reads as itself', () => {
    expect(hintSentence({ match_field: 'test_name', pattern: 'login', category: 'FLAKY' })).toBe('test name contains "login" → Flaky')
    expect(hintSentence({ match_field: 'suite_name', pattern: 'perf', category: 'TEST_DATA' })).toBe('suite contains "perf" → Test data')
    expect(hintSentence({ match_field: 'class_name', pattern: 'Pay', category: 'AUTOMATION_DEFECT' })).toBe('class contains "Pay" → Automation defect')
    expect(hintSentence({ match_field: 'error_message', pattern: 'x1x', category: 'PRODUCT_BUG' })).toBe('error message contains "x1x" → Product bug')
    expect(hintSentence({ match_field: 'other', pattern: 'abc', category: 'NEW' })).toBe('other contains "abc" → NEW')
  })

  it('renders a hostile pattern and note as text, not markup', () => {
    hooks.list.mockReturnValue({
      data: { ...LIST, items: [{ ...HINT, pattern: '<b>x</b>', note: '<img src=x onerror=alert(1)>' }] },
      error: undefined, isLoading: false, mutate: hooks.mutate,
    })
    renderModal()
    const row = document.querySelector('[data-hint-row="h1"]') as HTMLElement
    expect(within(row).getByText('error message contains "<b>x</b>" → Infrastructure')).toBeInTheDocument()
    expect(within(row).getByText('<img src=x onerror=alert(1)>')).toBeInTheDocument()
    expect(row.querySelector('b')).toBeNull()
    expect(row.querySelector('img')).toBeNull()
  })

  it('the Enabled box sends only `enabled` and says so', async () => {
    renderModal()
    await act(async () => {
      fireEvent.click(screen.getByRole('checkbox', { name: /^Enabled: / }))
    })
    expect(service.update).toHaveBeenCalledTimes(1)
    expect(service.update).toHaveBeenCalledWith('proj-1', 'h1', { enabled: false })
    expect(toast.success).toHaveBeenCalledWith('Hint updated')
    expect(hooks.mutate).toHaveBeenCalled()
  })

  it('Delete asks first, and deletes only on yes', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
    renderModal()
    fireEvent.click(screen.getByRole('button', { name: /^Delete hint: / }))
    expect(confirm).toHaveBeenCalledWith('Delete this hint?')
    expect(service.remove).not.toHaveBeenCalled()

    confirm.mockReturnValue(true)
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /^Delete hint: / }))
    })
    expect(service.remove).toHaveBeenCalledTimes(1)
    expect(service.remove).toHaveBeenCalledWith('proj-1', 'h1')
    expect(toast.success).toHaveBeenCalledWith('Hint deleted')
  })
})

describe('ClassificationHintsModal — adding a hint', () => {
  it('Add hint is disabled until the text has 3 characters', () => {
    renderModal()
    const add = screen.getByRole('button', { name: 'Add hint' })
    expect(add).toBeDisabled()
    fireEvent.change(containsInput(), { target: { value: ' EC ' } })
    expect(add).toBeDisabled()
    fireEvent.change(containsInput(), { target: { value: 'ECO' } })
    expect(add).toBeEnabled()
    expect(containsInput()).toHaveAttribute('maxLength', '200')
    expect(within(addForm()).getByLabelText('Note (optional)')).toHaveAttribute('maxLength', '500')
  })

  it('offers the four fields and the five categories', () => {
    renderModal()
    const options = (label: string) =>
      Array.from((within(addForm()).getByLabelText(label) as HTMLSelectElement).options).map(o => o.textContent)
    expect(options('Field')).toEqual(['Error message', 'Test name', 'Suite', 'Class'])
    expect(options('Category')).toEqual(['Product bug', 'Infrastructure', 'Test data', 'Automation defect', 'Flaky'])
  })

  it('sends the body once (trimmed, the note when given) and refreshes the list', async () => {
    renderModal()
    fireEvent.change(within(addForm()).getByLabelText('Field'), { target: { value: 'test_name' } })
    fireEvent.change(within(addForm()).getByLabelText('Category'), { target: { value: 'FLAKY' } })
    fireEvent.change(containsInput(), { target: { value: '  login times out  ' } })
    fireEvent.change(within(addForm()).getByLabelText('Note (optional)'), { target: { value: ' known race ' } })
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Add hint' }))
    })
    expect(service.create).toHaveBeenCalledTimes(1)
    expect(service.create).toHaveBeenCalledWith('proj-1', {
      match_field: 'test_name', pattern: 'login times out', category: 'FLAKY', note: 'known race',
    })
    expect(toast.success).toHaveBeenCalledWith('Hint added')
    expect(hooks.mutate).toHaveBeenCalledTimes(1)
    expect(containsInput()).toHaveValue('')
  })

  it('sends no note when none is given', async () => {
    renderModal()
    fireEvent.change(containsInput(), { target: { value: 'ECONNREFUSED' } })
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Add hint' }))
    })
    expect(service.create).toHaveBeenCalledWith('proj-1', {
      match_field: 'error_message', pattern: 'ECONNREFUSED', category: 'INFRASTRUCTURE',
    })
  })

  it("a refused write shows the server's detail in ONE toast and keeps the form", async () => {
    service.create.mockRejectedValue({ response: { status: 409, data: { detail: 'A hint with this field and text already exists.' } } })
    renderModal()
    fireEvent.change(containsInput(), { target: { value: 'ECONNREFUSED' } })
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Add hint' }))
    })
    expect(toast.error).toHaveBeenCalledTimes(1)
    expect(toast.error).toHaveBeenCalledWith('A hint with this field and text already exists.')
    expect(toast.success).not.toHaveBeenCalled()
    expect(containsInput()).toHaveValue('ECONNREFUSED')
  })
})

describe('ClassificationHintsModal — the preview', () => {
  it('asks only after the 400 ms debounce, then renders the count and the examples as text', () => {
    vi.useFakeTimers()
    renderModal()
    fireEvent.change(containsInput(), { target: { value: 'ECONNREFUSED' } })
    // Before the debounce: the hook still holds the settled (empty) pattern; nothing is shown.
    expect(hooks.preview).toHaveBeenLastCalledWith('proj-1', 'error_message', '')
    expect(document.querySelector('[data-hint-preview]')).toBeNull()
    act(() => { vi.advanceTimersByTime(PREVIEW_DEBOUNCE_MS - 1) })
    expect(hooks.preview).not.toHaveBeenCalledWith('proj-1', 'error_message', 'ECONNREFUSED')
    act(() => { vi.advanceTimersByTime(1) })
    expect(hooks.preview).toHaveBeenLastCalledWith('proj-1', 'error_message', 'ECONNREFUSED')
    const box = document.querySelector('[data-hint-preview]') as HTMLElement
    expect(box).toHaveTextContent('7 of the last 200 failures match')
    expect(within(box).getByText('test_checkout_pays: connect ECONNREFUSED 10.0.0.4:5432')).toBeInTheDocument()
    expect(PREVIEW_DEBOUNCE_MS).toBe(400)
  })

  it('shows nothing below 3 characters', () => {
    vi.useFakeTimers()
    renderModal()
    fireEvent.change(containsInput(), { target: { value: 'EC' } })
    act(() => { vi.advanceTimersByTime(PREVIEW_DEBOUNCE_MS) })
    expect(document.querySelector('[data-hint-preview]')).toBeNull()
  })

  it('says when there is no recent failure to test against', () => {
    vi.useFakeTimers()
    hooks.preview.mockReturnValue({ data: { matched: 0, sampled: 0, lookback_days: 30, examples: [] }, error: undefined })
    renderModal()
    fireEvent.change(containsInput(), { target: { value: 'ECONNREFUSED' } })
    act(() => { vi.advanceTimersByTime(PREVIEW_DEBOUNCE_MS) })
    expect(document.querySelector('[data-hint-preview]')).toHaveTextContent('No failure in the last 30 days to test against.')
  })

  it('renders a hostile matched value as text', () => {
    vi.useFakeTimers()
    hooks.preview.mockReturnValue({
      data: { ...PREVIEW, examples: [{ test_name: '<b>t</b>', value: '<img src=x onerror=alert(1)>' }] }, error: undefined,
    })
    renderModal()
    fireEvent.change(containsInput(), { target: { value: '<b>x</b>' } })
    act(() => { vi.advanceTimersByTime(PREVIEW_DEBOUNCE_MS) })
    const box = document.querySelector('[data-hint-preview]') as HTMLElement
    expect(within(box).getByText('<b>t</b>: <img src=x onerror=alert(1)>')).toBeInTheDocument()
    expect(box.querySelector('img, b')).toBeNull()
  })
})
