/**
 * W28 TCM-04: the History tab — the cards, who and what, Compare with
 * current, and Restore asked in the card (one request; three answers).
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ManagedTestCase, TestCaseVersion } from '@/types/test-management'

const restoreVersion = vi.fn()
const toastSuccess = vi.fn()

vi.mock('@/services/testCaseEditingService', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/services/testCaseEditingService')>()
  return { ...actual, testCaseEditingService: { restoreVersion: (...args: unknown[]) => restoreVersion(...args) } }
})
vi.mock('react-hot-toast', () => ({ default: Object.assign(vi.fn(), { success: (...a: unknown[]) => toastSuccess(...a), error: vi.fn() }) }))

import CaseHistoryPanel from './CaseHistoryPanel'

const STEP = (n: number, action: string, expected_result?: string) => ({ step_number: n, action, expected_result })

function version(n: number, over: Partial<TestCaseVersion> = {}): TestCaseVersion {
  return {
    id: `ver-${n}`, test_case_id: 'tc-1', version: n, title: 'Login works', description: 'Signs in', preconditions: '',
    steps: [STEP(1, 'Open /login', 'The form shows')], parameters: [], expected_result: 'Signed in', test_type: 'functional',
    priority: 'medium', severity: 'major', suite_name: 'Smoke', tags: ['smoke'], is_automated: false,
    automation_status: 'not_automated', status: 'active', change_type: 'updated', created_at: '2026-10-10T10:00:00Z',
    source: 'person', assignee_id: null, changed_by_id: 'u-priya', changed_by_name: 'Priya Shah', executions: [],
    ...over,
  }
}

const V1 = version(1, {
  change_type: 'created', source: 'ai', changed_by_id: undefined, changed_by_name: null, status: 'draft',
  title: 'Login', steps: [STEP(1, 'Open /login', 'Form')], changed_fields: ['title', 'steps'],
  executions: [{ plan_id: 'pl-1', plan_name: 'Release 4.2', item_id: 'it-1', execution_status: 'passed', executed_at: '2026-10-11T09:00:00Z' }],
})
const V2 = version(2, {
  change_type: 'status_changed', source: 'api', changed_by_name: 'CI key', status: 'active',
  title: 'Login', steps: [STEP(1, 'Open /login', 'Form')], change_summary: 'Activated by the pipeline',
})
const V3 = version(3, {
  title: 'Login works', change_summary: 'Clearer steps',
  steps: [STEP(1, 'Open /sign-in', 'The form shows'), STEP(2, 'Submit valid credentials', 'The dashboard shows')],
  parameters: [{ name: 'token', value: null, masked: true }], changed_fields: ['title', 'steps', 'parameters'],
})
const VERSIONS = [V3, V1, V2] // any order: the panel sorts newest first

const CASE: ManagedTestCase = {
  id: 'tc-1', project_id: 'p-1', title: 'Login works', description: 'Signs in', preconditions: '',
  steps: V3.steps, parameters: [{ name: 'token', value: null, masked: true }], expected_result: 'Signed in',
  test_type: 'functional', priority: 'medium', severity: 'major', suite_name: 'Smoke', tags: ['smoke'],
  test_suite_id: null, status: 'active', version: 3, is_automated: false, automation_status: 'not_automated',
  ai_generated: true, created_at: '2026-10-10T10:00:00Z', updated_at: '2026-10-12T10:00:00Z', source: 'managed',
}

function renderPanel(over: Partial<React.ComponentProps<typeof CaseHistoryPanel>> = {}) {
  const props: React.ComponentProps<typeof CaseHistoryPanel> = {
    caseItem: CASE, versions: VERSIONS, isLoading: false, canRestore: true, onRestored: vi.fn(), onStale: vi.fn(), ...over,
  }
  render(<CaseHistoryPanel {...props} />)
  return props
}

const card = (n: number) => document.querySelector(`[data-history-version="${n}"]`) as HTMLElement
const lines = (el: Element | null) => Array.from(el?.querySelectorAll('li') ?? []).map((li) => li.textContent)

describe('CaseHistoryPanel', () => {
  beforeEach(() => {
    restoreVersion.mockReset()
    toastSuccess.mockReset()
  })

  it('loading shows the spinner; no versions shows the empty state', () => {
    const { unmount } = render(<CaseHistoryPanel caseItem={CASE} versions={undefined} isLoading canRestore onRestored={vi.fn()} onStale={vi.fn()} />)
    expect(document.querySelector('[data-history-version]')).toBeNull()
    expect(screen.queryByText('No history yet')).toBeNull()
    unmount()
    renderPanel({ versions: [] })
    expect(screen.getByText('No history yet')).toBeInTheDocument()
  })

  it('one card per version, newest first, with the change type, who, and the AI / API chips', () => {
    renderPanel()
    const cards = Array.from(document.querySelectorAll('[data-history-version]')).map((el) => el.getAttribute('data-history-version'))
    expect(cards).toEqual(['3', '2', '1'])
    expect(within(card(3)).getByText('v3 — updated')).toBeInTheDocument()
    expect(within(card(3)).getByText('by Priya Shah')).toBeInTheDocument()
    expect(card(3).querySelector('[data-history-source]')).toBeNull()
    expect(within(card(2)).getByText('v2 — status changed')).toBeInTheDocument()
    expect(card(2).querySelector('[data-history-source]')?.textContent).toBe('API')
    expect(within(card(2)).getByText('Activated by the pipeline')).toBeInTheDocument()
    expect(card(1).querySelector('[data-history-source]')?.textContent).toBe('AI')
    expect(within(card(1)).queryByText(/^by /)).toBeNull()
  })

  it('What changed: field before → after, steps by position, a masked parameter without its value', () => {
    renderPanel()
    expect(lines(card(3).querySelector('[data-history-changes]'))).toEqual([
      'Title: Login → Login works',
      'Step 1 action: Open /login → Open /sign-in',
      'Step 1 expected: Form → The form shows',
      'Step 2 added: Submit valid credentials',
      'Parameter token: masked value changed',
    ])
    const before = card(3).querySelector('[data-diff-before]') as HTMLElement
    expect(before.className).toContain('line-through')
    expect(before.className).toContain('bg-[var(--status-failed-bg)]')
    expect((card(3).querySelector('[data-diff-after]') as HTMLElement).className).toContain('bg-[var(--status-passed-bg)]')
    expect(lines(card(2).querySelector('[data-history-changes]'))).toEqual(['Status: Draft → Active'])
  })

  it('the oldest card lists its changed fields only, and the executions recorded against it', () => {
    renderPanel()
    expect(card(1).querySelector('[data-history-changes]')).toBeNull()
    expect(within(card(1)).getByText('Changed: Title, Steps')).toBeInTheDocument()
    const runs = card(1).querySelector('[data-history-executions]') as HTMLElement
    expect(runs.textContent).toMatch(/^Ran in Release 4\.2 · passed · /)
    expect(card(3).querySelector('[data-history-executions]')).toBeNull()
  })

  it('Compare with current toggles a block under the card; the current version has none', () => {
    renderPanel()
    expect(within(card(3)).queryByRole('button', { name: 'Compare with current' })).toBeNull()
    const compare = within(card(1)).getByRole('button', { name: 'Compare with current' })
    fireEvent.click(compare)
    const block = card(1).querySelector('[data-history-compare]') as HTMLElement
    expect(block).toHaveTextContent('v1 → current (v3)')
    expect(lines(block)).toEqual([
      'Title: Login → Login works',
      'Status: Draft → Active',
      'Step 1 action: Open /login → Open /sign-in',
      'Step 1 expected: Form → The form shows',
      'Step 2 added: Submit valid credentials',
      'Parameter token: masked value changed',
    ])
    fireEvent.click(compare)
    expect(card(1).querySelector('[data-history-compare]')).toBeNull()
  })

  it('Compare says so when a version equals the current one', () => {
    const same = version(2, { title: 'Login works', steps: CASE.steps, parameters: [], status: 'active' })
    renderPanel({ versions: [version(3), same], caseItem: { ...CASE, parameters: [] } })
    fireEvent.click(within(card(2)).getByRole('button', { name: 'Compare with current' }))
    expect(card(2).querySelector('[data-history-compare]')).toHaveTextContent('Same as the current version')
  })

  it('no masked value reaches the DOM', () => {
    const leaky = { ...CASE, steps: version(2).steps, parameters: [{ name: 'token', value: 's3cr3t-value', masked: true }] }
    renderPanel({
      caseItem: leaky,
      versions: [
        version(3, { parameters: [{ name: 'token', value: 'other-value', masked: true }] }),
        version(2, { parameters: [{ name: 'token', value: 'older-value', mode: 'hidden' }] }),
      ],
    })
    fireEvent.click(within(card(2)).getByRole('button', { name: 'Compare with current' }))
    expect(lines(card(3).querySelector('[data-history-changes]'))).toEqual(['Parameter token: masked value changed'])
    expect(lines(card(2).querySelector('[data-history-compare]'))).toEqual(['Parameter token: masked value changed'])
    for (const value of ['s3cr3t-value', 'other-value', 'older-value']) expect(document.body.innerHTML).not.toContain(value)
  })

  it('Restore asks in the card; Cancel sends nothing; Restore sends the exact request and reports the new version', async () => {
    const restored = { ...CASE, version: 4, title: 'Login' }
    restoreVersion.mockResolvedValue(restored)
    const props = renderPanel()
    expect(within(card(3)).queryByRole('button', { name: 'Restore this version' })).toBeNull()
    fireEvent.click(within(card(1)).getByRole('button', { name: 'Restore this version' }))
    expect(restoreVersion).not.toHaveBeenCalled()
    const strip = card(1).querySelector('[data-inline-confirm]') as HTMLElement
    expect(strip.querySelector('p')?.textContent).toBe('Restore v1 as a new version? The status stays Active.')
    expect(document.querySelector('[role="dialog"], [aria-modal]')).toBeNull()

    fireEvent.click(within(strip).getByRole('button', { name: 'Cancel' }))
    expect(card(1).querySelector('[data-inline-confirm]')).toBeNull()
    expect(restoreVersion).not.toHaveBeenCalled()

    fireEvent.click(within(card(1)).getByRole('button', { name: 'Restore this version' }))
    fireEvent.click(within(card(1).querySelector('[data-inline-confirm]') as HTMLElement).getByRole('button', { name: 'Restore' }))
    await waitFor(() => expect(props.onRestored).toHaveBeenCalledWith(restored))
    expect(restoreVersion).toHaveBeenCalledTimes(1)
    expect(restoreVersion).toHaveBeenCalledWith('tc-1', { version: 1, expected_version: 3 })
    expect(toastSuccess).toHaveBeenCalledWith('Restored v1 as v4')
    expect(card(1).querySelector('[data-inline-confirm]')).toBeNull()
  })

  it('the same version back: nothing to restore, said in the card', async () => {
    restoreVersion.mockResolvedValue({ ...CASE })
    const props = renderPanel()
    fireEvent.click(within(card(2)).getByRole('button', { name: 'Restore this version' }))
    fireEvent.click(within(card(2).querySelector('[data-inline-confirm]') as HTMLElement).getByRole('button', { name: 'Restore' }))
    expect(await within(card(2)).findByText('v2 matches the current version: nothing to restore.')).toBeInTheDocument()
    expect(props.onRestored).not.toHaveBeenCalled()
    expect(toastSuccess).not.toHaveBeenCalled()
  })

  it('a 409: changed since you opened it, with Reload (onStale)', async () => {
    restoreVersion.mockRejectedValue({ response: { status: 409, data: { detail: { current_version: 5, expected_version: 3, message: 'stale' } } } })
    const props = renderPanel()
    fireEvent.click(within(card(1)).getByRole('button', { name: 'Restore this version' }))
    fireEvent.click(within(card(1).querySelector('[data-inline-confirm]') as HTMLElement).getByRole('button', { name: 'Restore' }))
    const notice = await within(card(1)).findByRole('alert')
    expect(notice).toHaveTextContent('This test case changed since you opened it.')
    expect(props.onRestored).not.toHaveBeenCalled()
    fireEvent.click(within(notice).getByRole('button', { name: 'Reload' }))
    expect(props.onStale).toHaveBeenCalledTimes(1)
    expect(within(card(1)).queryByRole('alert')).toBeNull()
  })

  it('another failure shows the server sentence, no Reload', async () => {
    restoreVersion.mockRejectedValue({ response: { status: 422, data: { detail: 'v1 cannot be restored: steps.0.action: blank' } } })
    renderPanel()
    fireEvent.click(within(card(1)).getByRole('button', { name: 'Restore this version' }))
    fireEvent.click(within(card(1).querySelector('[data-inline-confirm]') as HTMLElement).getByRole('button', { name: 'Restore' }))
    expect(await within(card(1)).findByText('v1 cannot be restored: steps.0.action: blank')).toBeInTheDocument()
    expect(within(card(1)).queryByRole('button', { name: 'Reload' })).toBeNull()
  })

  it('no Restore when canRestore is false', () => {
    renderPanel({ canRestore: false })
    expect(screen.queryByRole('button', { name: 'Restore this version' })).toBeNull()
    expect(within(card(1)).getByRole('button', { name: 'Compare with current' })).toBeInTheDocument()
  })

  it('renders names, summaries and step text as text, never markup', () => {
    const evil = '<img src=x onerror=alert(1)>'
    renderPanel({ versions: [version(3, { changed_by_name: evil, change_summary: evil, steps: [STEP(1, evil)] }), version(2)] })
    expect(within(card(3)).getByText(`by ${evil}`)).toBeInTheDocument()
    expect(document.querySelector('[data-case-history] img')).toBeNull()
  })
})
