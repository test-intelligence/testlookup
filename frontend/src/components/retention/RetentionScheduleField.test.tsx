/**
 * W22b ACT-06 — the purge window, the pause, and the inherited markers.
 *
 * Pins (§4.4): Pause sends `{pause_days: 7}` and Resume `{resume: true}` as
 * their own PUT; both wait while the form has unsaved changes; the status
 * line per state; `InheritedMarker` per source (instance / default / project)
 * and nothing without sources. Timestamps are built inside each test.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { EXPORT_PROJECT_ID, policyWithSources } from '@/test/retentionExportMocks'
import type { RetentionPolicy } from '@/types/retention'

const hooks = vi.hoisted(() => ({ updateRetentionPolicy: vi.fn() }))
vi.mock('@/hooks/useRetentionPolicy', () => hooks)
vi.mock('react-hot-toast', () => ({ default: { success: vi.fn(), error: vi.fn() } }))

import InheritedMarker from './InheritedMarker'
import RetentionScheduleField from './RetentionScheduleField'

function renderField(policy: RetentionPolicy, extra: Partial<Parameters<typeof RetentionScheduleField>[0]> = {}) {
  const props = {
    projectId: EXPORT_PROJECT_ID,
    policy,
    value: String(policy.purge_window_start_utc ?? 2),
    mode: undefined,
    dirty: false,
    onChange: vi.fn(),
    onOverride: vi.fn(),
    onReset: vi.fn(),
    onSaved: vi.fn(),
    ...extra,
  }
  render(<RetentionScheduleField {...props} />)
  return props
}

beforeEach(() => {
  hooks.updateRetentionPolicy.mockReset()
})

describe('RetentionScheduleField', () => {
  it('pauses for 7 days with its own PUT and hands the saved policy back', async () => {
    const paused = policyWithSources({ purge_paused_until: new Date(Date.now() + 7 * 86_400_000).toISOString() })
    hooks.updateRetentionPolicy.mockResolvedValue(paused)
    const props = renderField(policyWithSources({ enabled: true }))
    fireEvent.click(screen.getByRole('button', { name: 'Pause 7 days' }))
    await waitFor(() => expect(props.onSaved).toHaveBeenCalledWith(paused))
    expect(hooks.updateRetentionPolicy).toHaveBeenCalledWith(EXPORT_PROJECT_ID, { pause_days: 7 })
  })

  it('reads a pause and resumes with {resume: true}', async () => {
    const until = new Date(2026, 10, 4, 14, 0, 0).toISOString()
    hooks.updateRetentionPolicy.mockResolvedValue(policyWithSources())
    const props = renderField(policyWithSources({ enabled: true, purge_paused_until: until }))
    expect(screen.getByText('Paused until Nov 4, 2026, 14:00')).toBeInTheDocument()
    expect(screen.getByText('Paused')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Resume' }))
    await waitFor(() => expect(props.onSaved).toHaveBeenCalled())
    expect(hooks.updateRetentionPolicy).toHaveBeenCalledWith(EXPORT_PROJECT_ID, { resume: true })
  })

  it('waits for Save while the form has unsaved changes', () => {
    renderField(policyWithSources({ enabled: true }), { dirty: true })
    const pause = screen.getByRole('button', { name: 'Pause 7 days' })
    expect(pause).toBeDisabled()
    expect(pause).toHaveAttribute('title', 'Save or discard your changes first')
    fireEvent.click(pause)
    expect(hooks.updateRetentionPolicy).not.toHaveBeenCalled()
  })

  it('reads the next scheduled purge in UTC, and says when purging is off', () => {
    const next = new Date(Date.UTC(2026, 9, 29, 3, 0, 0)).toISOString()
    renderField(policyWithSources({ enabled: true, purge_window_start_utc: 3, next_scheduled_purge_at: next }))
    expect(screen.getByText('Next scheduled purge Oct 29, 2026, 03:00 UTC')).toBeInTheDocument()
    expect((screen.getByLabelText('Purge window (UTC)') as HTMLSelectElement).value).toBe('3')
  })

  it('says purging is off for a disabled project', () => {
    renderField(policyWithSources({ enabled: false }))
    expect(screen.getByText('Scheduled purging is off for this project.')).toBeInTheDocument()
  })

  it('reports a window change and disables the select while inherited', () => {
    const props = renderField(policyWithSources(), { mode: 'inherit' })
    const select = screen.getByLabelText('Purge window (UTC)') as HTMLSelectElement
    expect(select).toBeDisabled()
    expect(select.options).toHaveLength(24)
    expect(screen.getByText('02:00 UTC (default)')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Override' }))
    expect(props.onOverride).toHaveBeenCalled()
  })
})

describe('InheritedMarker', () => {
  const policy = policyWithSources()
  const noop = () => undefined

  it('reads an instance value with Override', () => {
    const onOverride = vi.fn()
    render(<InheritedMarker field="runs_days" policy={policy} onOverride={onOverride} onReset={noop} />)
    expect(screen.getByText('180 days (instance default)')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Override' }))
    expect(onOverride).toHaveBeenCalled()
  })

  it('reads a code default with Override', () => {
    render(<InheritedMarker field="audit_days" policy={policy} onOverride={noop} onReset={noop} />)
    expect(screen.getByText('2555 days (default)')).toBeInTheDocument()
  })

  it('reads a project value with Reset to inherited', () => {
    const onReset = vi.fn()
    render(<InheritedMarker field="raw_events_days" policy={policy} onOverride={noop} onReset={onReset} />)
    expect(screen.getByText('Project value')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Reset to inherited' }))
    expect(onReset).toHaveBeenCalled()
  })

  it('says what Save will do in the two unsaved states', () => {
    const { rerender } = render(
      <InheritedMarker field="raw_events_days" policy={policy} mode="inherit" onOverride={noop} onReset={noop} />,
    )
    expect(screen.getByText('Inherited value on save')).toBeInTheDocument()
    rerender(<InheritedMarker field="runs_days" policy={policy} mode="own" onOverride={noop} onReset={noop} />)
    expect(screen.getByText('Project value on save')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Reset to inherited' })).toBeInTheDocument()
  })

  it('renders nothing without sources (an older server)', () => {
    const { field_sources: _omit, ...legacy } = policy
    const { container } = render(
      <InheritedMarker field="runs_days" policy={legacy} onOverride={noop} onReset={noop} />,
    )
    expect(container.querySelector('[data-retention-inherited]')).toBeNull()
  })
})
