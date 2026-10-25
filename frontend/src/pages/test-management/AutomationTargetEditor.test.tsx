/**
 * W13 TCL-03: the automation target editor — QA lead only; Save sends
 * `{ automation_target_pct: 75 }` through the project PUT; a 422 renders its
 * sentence inline; success revalidates the library health.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const update = vi.fn()
const refreshLibraryHealth = vi.fn()
let role = 'QA_LEAD'
const ORDER = ['VIEWER', 'TESTER', 'QA_ENGINEER', 'QA_LEAD', 'ADMIN']

vi.mock('@/hooks/usePermissions', () => ({
  usePermissions: () => ({ role, hasRole: (min: string) => ORDER.indexOf(role) >= ORDER.indexOf(min) }),
}))
vi.mock('@/hooks/useTestLibrary', () => ({ refreshLibraryHealth: () => refreshLibraryHealth() }))
vi.mock('@/services/projectsService', () => ({ projectsService: { update: (...args: unknown[]) => update(...args) } }))
vi.mock('react-hot-toast', () => ({ default: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }))

import AutomationTargetEditor from './AutomationTargetEditor'

const TARGET_RANGE_TEXT = 'Enter a whole number from 0 to 100.'

const onSaved = vi.fn()

function renderEditor() {
  return render(<AutomationTargetEditor projectId="p-1" target={60} onSaved={onSaved} />)
}

function httpError(status: number, detail: unknown) {
  return Object.assign(new Error(`HTTP ${status}`), { response: { status, data: { detail } } })
}

beforeEach(() => {
  update.mockReset()
  refreshLibraryHealth.mockReset()
  onSaved.mockReset()
  role = 'QA_LEAD'
})

describe('AutomationTargetEditor', () => {
  it.each(['VIEWER', 'TESTER', 'QA_ENGINEER'])('renders nothing for %s', (r) => {
    role = r
    const { container } = renderEditor()
    expect(container).toBeEmptyDOMElement()
  })

  it.each(['QA_LEAD', 'ADMIN'])('offers Change target to %s, prefilled with the current target', (r) => {
    role = r
    renderEditor()
    fireEvent.click(screen.getByRole('button', { name: 'Change target' }))
    expect(screen.getByLabelText('Automation target (%)')).toHaveValue(60)
  })

  it('saves the new target through the project update, then refreshes health', async () => {
    update.mockResolvedValueOnce({ id: 'p-1', automation_target_pct: 75 })
    renderEditor()
    fireEvent.click(screen.getByRole('button', { name: 'Change target' }))
    fireEvent.change(screen.getByLabelText('Automation target (%)'), { target: { value: '75' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(update).toHaveBeenCalledWith('p-1', { automation_target_pct: 75 }, { suppressToast: true }))
    await waitFor(() => expect(onSaved).toHaveBeenCalledTimes(1))
    expect(refreshLibraryHealth).toHaveBeenCalledTimes(1)
    expect(screen.queryByLabelText('Automation target (%)')).toBeNull()
  })

  it('renders the server 422 sentence inline and keeps the editor open', async () => {
    update.mockRejectedValueOnce(httpError(422, [{ loc: ['body', 'automation_target_pct'], msg: 'Input should be less than or equal to 100', type: 'less_than_equal' }]))
    renderEditor()
    fireEvent.click(screen.getByRole('button', { name: 'Change target' }))
    fireEvent.change(screen.getByLabelText('Automation target (%)'), { target: { value: '99' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Input should be less than or equal to 100')
    expect(screen.getByLabelText('Automation target (%)')).toBeInTheDocument()
    expect(refreshLibraryHealth).not.toHaveBeenCalled()
    expect(onSaved).not.toHaveBeenCalled()
  })

  it('refuses a value outside 0..100 before sending, and Cancel / Escape close it', () => {
    renderEditor()
    fireEvent.click(screen.getByRole('button', { name: 'Change target' }))
    fireEvent.change(screen.getByLabelText('Automation target (%)'), { target: { value: '101' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect(screen.getByRole('alert')).toHaveTextContent(TARGET_RANGE_TEXT)
    expect(update).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(screen.queryByLabelText('Automation target (%)')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Change target' }))
    fireEvent.keyDown(screen.getByLabelText('Automation target (%)'), { key: 'Escape' })
    expect(screen.queryByLabelText('Automation target (%)')).toBeNull()
  })

  it.each(['-1', '7.5', ''])('refuses %j as a target', (value) => {
    renderEditor()
    fireEvent.click(screen.getByRole('button', { name: 'Change target' }))
    fireEvent.change(screen.getByLabelText('Automation target (%)'), { target: { value } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect(screen.getByRole('alert')).toHaveTextContent(TARGET_RANGE_TEXT)
    expect(update).not.toHaveBeenCalled()
  })

  it('accepts the bounds 0 and 100', async () => {
    update.mockResolvedValue({ id: 'p-1' })
    renderEditor()
    fireEvent.click(screen.getByRole('button', { name: 'Change target' }))
    fireEvent.change(screen.getByLabelText('Automation target (%)'), { target: { value: '0' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(update).toHaveBeenLastCalledWith('p-1', { automation_target_pct: 0 }, { suppressToast: true }))
    fireEvent.click(await screen.findByRole('button', { name: 'Change target' }))
    fireEvent.change(screen.getByLabelText('Automation target (%)'), { target: { value: '100' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(update).toHaveBeenLastCalledWith('p-1', { automation_target_pct: 100 }, { suppressToast: true }))
  })
})
