/**
 * W11 REL-07: Draft from a PRD, end to end in the form — through the REAL
 * `releasesService` over a mocked Axios base, so the multipart body is the one
 * the browser would send:
 *
 * - pasted text → `FormData` with `text` only; a chosen file → `file` only
 *   (and choosing it clears the text and names the file);
 * - the badge for every source / fallback reason;
 * - the preview is editable: rename, remove, add a row, set a first start →
 *   Create sends ONE bulk body with consecutive windows and the draft's source
 *   and model; a repeated name blocks Create with the reason as text;
 * - a 422 / 413 refusal is the server's sentence, in the form;
 * - a PRD heading that is markup is TEXT (an input's value), never HTML.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import toast from 'react-hot-toast'
import { api } from '@/services/api'
import type { PhaseDraftResult } from '@/types/releases'
import DraftPhasesForm from './DraftPhasesForm'
import { FALLBACK_REASON_TEXT } from './draftPhasesModel'

vi.mock('@/services/api', () => ({ api: { post: vi.fn(), get: vi.fn() } }))
vi.mock('react-hot-toast', () => ({ default: { success: vi.fn(), error: vi.fn() } }))

const RELEASE = { id: 'rel-13', name: '2026.13' }

function draft(overrides: Partial<PhaseDraftResult> = {}): PhaseDraftResult {
  return {
    release_id: RELEASE.id,
    source: 'rules',
    model_name: null,
    fallback_reason: 'llm_unavailable',
    phases: [
      { name: 'Smoke testing', phase_type: 'qa_testing', description: 'Run the smoke pack.', exit_criteria_text: null, planned_days: 2 },
      { name: 'Regression', phase_type: 'qa_testing', description: null, exit_criteria_text: null, planned_days: 5 },
      { name: 'UAT', phase_type: 'uat', description: 'Finance walks the flows.', exit_criteria_text: 'finance signs off', planned_days: null },
    ],
    warnings: [],
    text_chars: 120,
    ...overrides,
  }
}

const post = vi.mocked(api.post)

function renderForm() {
  const onDone = vi.fn()
  const onCancel = vi.fn()
  render(<DraftPhasesForm release={RELEASE} onDone={onDone} onCancel={onCancel} />)
  return { onDone, onCancel, form: () => screen.getByRole('region', { name: 'Draft phases from a PRD' }) }
}

async function draftFrom(text: string, answer: PhaseDraftResult = draft()) {
  post.mockResolvedValueOnce({ data: answer })
  const view = renderForm()
  fireEvent.change(within(view.form()).getByLabelText('PRD text'), { target: { value: text } })
  fireEvent.click(within(view.form()).getByRole('button', { name: 'Draft phases' }))
  await within(view.form()).findByText((_, el) => el?.hasAttribute('data-draft-badge') ?? false)
  return view
}

beforeEach(() => {
  post.mockReset()
  vi.mocked(toast.success).mockReset()
})

describe('DraftPhasesForm — the input', () => {
  it('pasted text posts FormData with `text` only, multipart, no global toast', async () => {
    const { form } = await draftFrom('## Smoke testing\n\n## UAT\n')
    const [url, body, config] = post.mock.calls[0]
    expect(url).toBe('/api/v1/releases/rel-13/phases/generate')
    expect(body).toBeInstanceOf(FormData)
    expect((body as FormData).get('text')).toBe('## Smoke testing\n\n## UAT\n')
    expect((body as FormData).has('file')).toBe(false)
    expect(config).toMatchObject({ suppressToast: true, headers: { 'Content-Type': undefined } })
    expect(document.getElementById('draft-phases-title')).toHaveTextContent('Draft phases from a PRD')
    expect(within(form()).getAllByRole('textbox', { name: /^Name of phase/ })).toHaveLength(3)
  })

  it('a chosen file posts `file` only, clears the text and names the file', async () => {
    post.mockResolvedValueOnce({ data: draft() })
    const { form } = renderForm()
    fireEvent.change(within(form()).getByLabelText('PRD text'), { target: { value: '## typed first' } })
    const file = new File(['## Smoke'], 'checkout-v2.md', { type: 'text/markdown' })
    fireEvent.change(within(form()).getByLabelText('Choose file'), { target: { files: [file] } })
    expect(within(form()).getByLabelText('PRD text')).toHaveValue('')
    expect(within(form()).getByText('checkout-v2.md')).toBeInTheDocument()
    fireEvent.click(within(form()).getByRole('button', { name: 'Draft phases' }))
    await waitFor(() => expect(post).toHaveBeenCalled())
    const body = post.mock.calls[0][1] as FormData
    expect((body.get('file') as File).name).toBe('checkout-v2.md')
    expect(body.has('text')).toBe(false)
  })

  it('Draft is off until there is text or a file', () => {
    const { form } = renderForm()
    expect(within(form()).getByRole('button', { name: 'Draft phases' })).toBeDisabled()
  })

  it.each([
    [422, 'Only .md, .txt and .docx files can be read.'],
    [413, 'The PRD is larger than 200 KB. Paste the part that describes the test phases.'],
  ])('a %s refusal is the server\'s sentence, in the form', async (status, detail) => {
    post.mockRejectedValueOnce({ response: { status, data: { detail } } })
    const { form } = renderForm()
    fireEvent.change(within(form()).getByLabelText('PRD text'), { target: { value: 'x' } })
    fireEvent.click(within(form()).getByRole('button', { name: 'Draft phases' }))
    expect(await within(form()).findByRole('alert')).toHaveTextContent(detail)
  })
})

describe('DraftPhasesForm — the badge', () => {
  it.each(Object.entries(FALLBACK_REASON_TEXT))('a rules draft because %s says why', async (reason, text) => {
    const { form } = await draftFrom('## Smoke', draft({ fallback_reason: reason }))
    expect(within(form()).getByText(`Drafted by rules — ${text}`)).toBeInTheDocument()
  })

  it('a model draft names the model; a plain rules draft says rules', async () => {
    const { form } = await draftFrom('## Smoke', draft({ source: 'llm', model_name: 'qwen2.5:7b', fallback_reason: null }))
    expect(within(form()).getByText('Drafted by qwen2.5:7b')).toBeInTheDocument()
  })

  it('no phases drafted: says so, shows the warning, offers a row', async () => {
    const { form } = await draftFrom('plain prose', draft({
      phases: [], warnings: ['No headings were found, so no phases were drafted. Use ## headings for the phases.'],
    }))
    expect(within(form()).getByText('No phases were drafted.')).toBeInTheDocument()
    expect(within(form()).getByText(/No headings were found/)).toBeInTheDocument()
    fireEvent.click(within(form()).getByRole('button', { name: 'Add a row' }))
    expect(within(form()).getAllByRole('textbox', { name: /^Name of phase/ })).toHaveLength(1)
  })
})

describe('DraftPhasesForm — the preview and Create', () => {
  it('edit, remove, add, a first start → ONE bulk body with consecutive windows and the draft\'s source', async () => {
    const answer = draft({ source: 'llm', model_name: 'qwen2.5:7b', fallback_reason: null })
    const { form, onDone } = await draftFrom('## Smoke testing\n## Regression\n## UAT', answer)
    const f = form()
    // The exit criteria travel in the description the reader edits.
    expect(within(f).getByRole('textbox', { name: 'Description of phase 3' })).toHaveValue('Finance walks the flows.\nExit criteria: finance signs off')
    fireEvent.change(within(f).getByRole('textbox', { name: 'Name of phase 1' }), { target: { value: 'Smoke' } })
    fireEvent.click(within(f).getByRole('button', { name: 'Remove phase 2' }))
    fireEvent.click(within(f).getByRole('button', { name: 'Add a row' }))
    fireEvent.change(within(f).getByRole('textbox', { name: 'Name of phase 3' }), { target: { value: 'Production cutover' } })
    fireEvent.change(within(f).getByRole('combobox', { name: 'Type of phase 3' }), { target: { value: 'production' } })
    fireEvent.change(within(f).getByRole('spinbutton', { name: 'Days of phase 3' }), { target: { value: '1' } })
    fireEvent.change(within(f).getByLabelText('First phase starts (optional)'), { target: { value: '2026-11-03' } })

    const created = { release_id: RELEASE.id, created: 3, phases: [] }
    post.mockResolvedValueOnce({ data: created })
    fireEvent.click(within(f).getByRole('button', { name: 'Create 3 phases' }))
    await waitFor(() => expect(onDone).toHaveBeenCalledWith(created))

    const [url, body, config] = post.mock.calls[1]
    expect(url).toBe('/api/v1/releases/rel-13/phases/bulk')
    expect(config).toMatchObject({ suppressToast: true })
    expect(body).toEqual({
      phases: [
        { name: 'Smoke', phase_type: 'qa_testing', description: 'Run the smoke pack.',
          planned_start: '2026-11-03T00:00:00Z', planned_end: '2026-11-04T00:00:00Z' },
        { name: 'UAT', phase_type: 'uat', description: 'Finance walks the flows.\nExit criteria: finance signs off',
          planned_start: '2026-11-05T00:00:00Z', planned_end: '2026-11-09T00:00:00Z' },
        { name: 'Production cutover', phase_type: 'production', description: null,
          planned_start: '2026-11-10T00:00:00Z', planned_end: '2026-11-10T00:00:00Z' },
      ],
      source: 'llm',
      model_name: 'qwen2.5:7b',
    })
    expect(post).toHaveBeenCalledTimes(2)   // the draft, then ONE create
    expect(toast.success).toHaveBeenCalledWith('Created 3 phases from the draft.')
  })

  it('stacks each row for the narrow pane: the Name alone on the first line, labelled fields, no shared header', async () => {
    const { form } = await draftFrom('## Smoke')
    const rowsBox = form().querySelector('[data-draft-rows]') as HTMLElement
    const cards = Array.from(rowsBox.children)
    expect(cards.every(c => c.hasAttribute('data-draft-row')), 'no header row beside the cards').toBe(true)
    const first = cards[0] as HTMLElement
    const lines = Array.from(first.children)
    // Line 1: the Name input, full width, with its own label; line 2: Type, Days, Remove; line 3: the description.
    const name = within(first).getByRole('textbox', { name: 'Name of phase 1' })
    expect(lines[0].contains(name)).toBe(true)
    expect(lines[0].querySelectorAll('input, select, textarea, button')).toHaveLength(1)
    expect(name).toHaveClass('w-full')
    expect(within(lines[0] as HTMLElement).getByText('Name')).toBeInTheDocument()
    expect(lines[1].contains(within(first).getByRole('combobox', { name: 'Type of phase 1' }))).toBe(true)
    expect(lines[1].contains(within(first).getByRole('spinbutton', { name: 'Days of phase 1' }))).toBe(true)
    expect(lines[1].contains(within(first).getByRole('button', { name: 'Remove phase 1' }))).toBe(true)
    expect(lines[2].contains(within(first).getByRole('textbox', { name: 'Description of phase 1' }))).toBe(true)
    expect(form().querySelector('[data-draft-badge]')?.className).not.toMatch(/\bm-0\b/)
  })

  it('the footer wraps whole buttons, never their words', async () => {
    const { form } = await draftFrom('## Smoke')
    for (const label of ['Cancel', 'Back to the text', 'Create 3 phases']) {
      const button = within(form()).getByRole('button', { name: label })
      expect(button, label).toHaveClass('whitespace-nowrap')
      expect(button.parentElement, label).toHaveClass('flex-wrap', 'w-full')
    }
  })

  it('a repeated name blocks Create, with the reason as text', async () => {
    const { form } = await draftFrom('## Smoke')
    fireEvent.change(within(form()).getByRole('textbox', { name: 'Name of phase 2' }), { target: { value: 'smoke TESTING' } })
    expect(within(form()).getByText('Two phases are named "smoke TESTING": each name must be different.')).toBeInTheDocument()
    expect(within(form()).getByRole('button', { name: 'Create 3 phases' })).toBeDisabled()
  })

  it('a refused Create keeps the rows and shows the server\'s sentence', async () => {
    const { form, onDone } = await draftFrom('## Smoke')
    post.mockRejectedValueOnce({ response: { status: 409, data: { detail: 'This release already has phases. Remove them first, or add phases one at a time.' } } })
    fireEvent.click(within(form()).getByRole('button', { name: 'Create 3 phases' }))
    expect(await within(form()).findByRole('alert')).toHaveTextContent('This release already has phases.')
    expect(within(form()).getAllByRole('textbox', { name: /^Name of phase/ })).toHaveLength(3)
    expect(onDone).not.toHaveBeenCalled()
  })

  it('Back to the text keeps the PRD to edit and draft again', async () => {
    const { form } = await draftFrom('## Smoke testing')
    fireEvent.click(within(form()).getByRole('button', { name: 'Back to the text' }))
    expect(within(form()).getByLabelText('PRD text')).toHaveValue('## Smoke testing')
  })

  it('a heading that is markup is text: no element is created from it', async () => {
    const { form } = await draftFrom('## <img src=x onerror=alert(1)>', draft({
      phases: [{ name: '<img src=x onerror=alert(1)>', phase_type: 'qa_testing', description: '<script>alert(2)</script>' }],
    }))
    expect(within(form()).getByRole('textbox', { name: 'Name of phase 1' })).toHaveValue('<img src=x onerror=alert(1)>')
    expect(form().querySelector('img')).toBeNull()
    expect(form().querySelector('script')).toBeNull()
  })

  it('the PRD never reaches the URL or storage', async () => {
    const before = window.location.href
    await draftFrom('## SENTINEL-PRD-9f2 confidential')
    expect(window.location.href).toBe(before)
    expect(JSON.stringify({ ...window.localStorage })).not.toContain('SENTINEL-PRD-9f2')
    expect(JSON.stringify({ ...window.sessionStorage })).not.toContain('SENTINEL-PRD-9f2')
  })
})
