/**
 * W28 TCM-05: a plan item's "Ran against" cell (four states) and the steps of
 * the version it ran (from the case history; two fallbacks).
 */
import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { TestCaseVersion, TestPlanItem } from '@/types/test-management'

const useTestCaseHistory = vi.fn()
vi.mock('@/hooks/useTestManagement', () => ({
  useTestCaseHistory: (...args: unknown[]) => useTestCaseHistory(...args),
}))

import { PlanItemVersionCell, PlanItemVersionSteps } from './PlanItemVersion'

function item(over: Partial<TestPlanItem> = {}): TestPlanItem {
  return {
    id: 'it-1', plan_id: 'pl-1', test_case_id: 'tc-1', order_index: 0, execution_status: 'not_run', created_at: 't', ...over,
  }
}

function version(n: number, over: Partial<TestCaseVersion> = {}): TestCaseVersion {
  return {
    id: `ver-${n}`, test_case_id: 'tc-1', version: n, title: `Login v${n}`, status: 'active', change_type: 'updated',
    created_at: 't', steps: [], expected_result: '', ...over,
  }
}

describe('PlanItemVersionCell', () => {
  it('not run: —', () => {
    render(<PlanItemVersionCell item={item()} open={false} onToggle={vi.fn()} />)
    expect(document.querySelector('[data-plan-item-version]')?.textContent).toBe('—')
    expect(screen.queryByRole('button')).toBeNull()
  })

  it('run before versions were recorded: not recorded (never v0)', () => {
    render(<PlanItemVersionCell item={item({ execution_status: 'passed', executed_case_version: null })} open={false} onToggle={vi.fn()} />)
    expect(document.querySelector('[data-plan-item-version]')?.textContent).toBe('not recorded')
    expect(screen.queryByRole('button')).toBeNull()
    expect(document.body.textContent).not.toContain('v0')
  })

  it('run against the current version: a v3 button only', () => {
    const onToggle = vi.fn()
    render(<PlanItemVersionCell item={item({ execution_status: 'passed', executed_case_version: 3, case_version: 3 })} open={false} onToggle={onToggle} />)
    const cell = document.querySelector('[data-plan-item-version]') as HTMLElement
    expect(cell.textContent).toBe('v3')
    expect(cell.className).toContain('whitespace-nowrap')
    const button = screen.getByRole('button', { name: 'v3' })
    expect(button).toHaveAttribute('aria-expanded', 'false')
    fireEvent.click(button)
    expect(onToggle).toHaveBeenCalledTimes(1)
  })

  it('the case moved on: v3 · case now v5; open is reflected', () => {
    render(<PlanItemVersionCell item={item({ execution_status: 'failed', executed_case_version: 3, case_version: 5 })} open onToggle={vi.fn()} />)
    expect(document.querySelector('[data-plan-item-version]')?.textContent).toBe('v3 · case now v5')
    expect(screen.getByRole('button', { name: 'v3' })).toHaveAttribute('aria-expanded', 'true')
  })

  it('a create / execute answer without case_version shows only the version it ran', () => {
    render(<PlanItemVersionCell item={item({ execution_status: 'passed', executed_case_version: 2, case_version: null })} open={false} onToggle={vi.fn()} />)
    expect(document.querySelector('[data-plan-item-version]')?.textContent).toBe('v2')
  })
})

describe('PlanItemVersionSteps', () => {
  beforeEach(() => useTestCaseHistory.mockReset())

  it('shows the steps and expected result of the version it ran (not the current one)', () => {
    useTestCaseHistory.mockReturnValue({
      data: [
        version(2, { steps: [{ step_number: 1, action: 'Open /sign-in', expected_result: 'The new form' }], expected_result: 'Signed in (v2)' }),
        version(1, {
          steps: [
            { step_number: 1, action: 'Open /login', expected_result: 'The form shows' },
            { step_number: 2, action: 'Submit' },
          ],
          expected_result: 'Signed in',
        }),
      ],
      error: undefined,
      isLoading: false,
    })
    render(<PlanItemVersionSteps item={item({ execution_status: 'passed', executed_case_version: 1, case_version: 2 })} />)
    expect(useTestCaseHistory).toHaveBeenCalledWith('tc-1')
    const box = document.querySelector('[data-plan-item-steps="1"]') as HTMLElement
    expect(box).toHaveTextContent('Steps it ran against (v1)')
    expect(box).toHaveTextContent('v1 · Login v1')
    expect(Array.from(box.querySelectorAll('[data-plan-item-step]')).map((li) => li.textContent)).toEqual([
      '#1 Open /loginExpected: The form shows',
      '#2 Submit',
    ])
    expect(box).toHaveTextContent('Expected result: Signed in')
    expect(box).not.toHaveTextContent('Open /sign-in')
    expect(box).not.toHaveTextContent('Signed in (v2)')
  })

  it('a version older than the history keeps: says so', () => {
    useTestCaseHistory.mockReturnValue({ data: [version(104), version(103)], error: undefined, isLoading: false })
    render(<PlanItemVersionSteps item={item({ execution_status: 'passed', executed_case_version: 3 })} />)
    expect(screen.getByText('v3 is older than the last 100 versions')).toBeInTheDocument()
  })

  it('a failed history read: says so', () => {
    useTestCaseHistory.mockReturnValue({ data: undefined, error: new Error('boom'), isLoading: false })
    render(<PlanItemVersionSteps item={item({ execution_status: 'passed', executed_case_version: 3 })} />)
    expect(screen.getByText("This case's history could not be read")).toBeInTheDocument()
  })

  it('loading shows a spinner, not a fallback', () => {
    useTestCaseHistory.mockReturnValue({ data: undefined, error: undefined, isLoading: true })
    render(<PlanItemVersionSteps item={item({ execution_status: 'passed', executed_case_version: 3 })} />)
    expect(screen.queryByText(/could not be read|older than/)).toBeNull()
    expect(document.querySelector('[data-plan-item-steps="3"]')).not.toBeNull()
  })

  it('reads nothing for an item without a recorded version', () => {
    useTestCaseHistory.mockReturnValue({ data: undefined, error: undefined, isLoading: false })
    const { container } = render(<PlanItemVersionSteps item={item({ execution_status: 'passed', executed_case_version: null })} />)
    expect(useTestCaseHistory).toHaveBeenCalledWith(undefined)
    expect(container.textContent).toBe('')
  })

  it('renders step text as text, never markup', () => {
    useTestCaseHistory.mockReturnValue({
      data: [version(1, { title: '<b>x</b>', steps: [{ step_number: 1, action: '<img src=x onerror=alert(1)>' }] })],
      error: undefined,
      isLoading: false,
    })
    render(<PlanItemVersionSteps item={item({ execution_status: 'passed', executed_case_version: 1 })} />)
    expect(document.querySelector('[data-plan-item-steps] img, [data-plan-item-steps] b')).toBeNull()
    expect(screen.getByText('<img src=x onerror=alert(1)>')).toBeInTheDocument()
  })
})
