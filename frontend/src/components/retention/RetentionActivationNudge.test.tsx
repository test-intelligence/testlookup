/**
 * S1 — the retention activation nudge.
 *
 * The prompt exists because retention shipped inert: present, discoverable,
 * and defaulted off with nothing ever asking an operator to turn it on. These
 * tests pin the two properties that make it safe to ship — it appears only for
 * the population it targets, and it cannot enable a destructive nightly job
 * without showing the numbers first.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import RetentionActivationNudge from './RetentionActivationNudge'
import type { RetentionPolicy } from '@/types/retention'

vi.mock('@/hooks/useRetentionPolicy', () => ({
  previewRetentionPurge: vi.fn(),
  updateRetentionPolicy: vi.fn(),
}))
vi.mock('@/hooks/useUIDismissals', () => ({
  useUIDismissals: vi.fn(),
  dismissUIPrompt: vi.fn(),
}))
vi.mock('react-hot-toast', () => ({
  default: { success: vi.fn(), error: vi.fn() },
}))

import { previewRetentionPurge, updateRetentionPolicy } from '@/hooks/useRetentionPolicy'
import { dismissUIPrompt, useUIDismissals } from '@/hooks/useUIDismissals'

const POLICY = (enabled: boolean): RetentionPolicy =>
  ({
    enabled,
    raw_events_days: 90,
    runs_days: 365,
    artifacts_days: 180,
    audit_days: 2555,
    source: 'default',
  }) as unknown as RetentionPolicy

const PREVIEW = {
  cutoffs: {},
  candidates: {
    runs: 42,
    test_cases: 900,
    minio_objects: 17,
    audit_rows: 5,
    event_archive_rows: 0,
    provenance_rows: 0,
    compliance_packs_expired: 0,
    mongo_docs: {},
  },
}

function mockDismissals(dismissed: string[] | undefined) {
  vi.mocked(useUIDismissals).mockReturnValue({
    data: dismissed === undefined ? undefined : { dismissed },
    // Faithful enough to SWR's `mutate`: it AWAITS the updater and returns a
    // promise. A bare `vi.fn()` returns undefined, which both swallows the
    // updater (so `dismissUIPrompt` never runs and the assertion below passes
    // vacuously) and blows up the component's `.catch()`.
    mutate: vi.fn(async (updater: unknown) =>
      typeof updater === 'function' ? await (updater as () => unknown)() : updater,
    ),
  } as unknown as ReturnType<typeof useUIDismissals>)
}

function renderNudge(policy: RetentionPolicy, onEnabled = vi.fn()) {
  return render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <RetentionActivationNudge
        projectId="p1"
        projectName="Checkout"
        policy={policy}
        onEnabled={onEnabled}
      />
    </SWRConfig>,
  )
}

describe('RetentionActivationNudge', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(previewRetentionPurge).mockResolvedValue(PREVIEW as never)
    vi.mocked(updateRetentionPolicy).mockResolvedValue(POLICY(true) as never)
    vi.mocked(dismissUIPrompt).mockResolvedValue({
      dismissed: ['retention_activation_nudge'],
    })
  })

  it('prompts when retention is configured but switched off', () => {
    mockDismissals([])
    renderNudge(POLICY(false))
    expect(screen.getByTestId('retention-activation-nudge')).toBeInTheDocument()
  })

  it('stays silent once retention is enabled', () => {
    mockDismissals([])
    renderNudge(POLICY(true))
    expect(screen.queryByTestId('retention-activation-nudge')).not.toBeInTheDocument()
  })

  it('stays silent for a user who already dismissed it', () => {
    mockDismissals(['retention_activation_nudge'])
    renderNudge(POLICY(false))
    expect(screen.queryByTestId('retention-activation-nudge')).not.toBeInTheDocument()
  })

  it('does not flash the prompt while dismissals are still loading', () => {
    // Rendering on `undefined` would show the prompt for one frame to someone
    // who dismissed it months ago — the exact annoyance the store prevents.
    mockDismissals(undefined)
    renderNudge(POLICY(false))
    expect(screen.queryByTestId('retention-activation-nudge')).not.toBeInTheDocument()
  })

  it('will not enable retention without showing the numbers first', async () => {
    mockDismissals([])
    renderNudge(POLICY(false))

    // The enable action is not reachable until a preview has been run — this
    // is the guard against switching on an irreversible nightly delete blind.
    expect(screen.queryByRole('button', { name: /enable retention/i })).not.toBeInTheDocument()
    expect(updateRetentionPolicy).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: /review what would be deleted/i }))

    await waitFor(() =>
      expect(screen.getByTestId('retention-nudge-preview')).toBeInTheDocument(),
    )
    expect(screen.getByText(/42 test runs/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /enable retention/i })).toBeInTheDocument()
  })

  it('enables only after the operator confirms the preview', async () => {
    mockDismissals([])
    const onEnabled = vi.fn()
    renderNudge(POLICY(false), onEnabled)

    fireEvent.click(screen.getByRole('button', { name: /review what would be deleted/i }))
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /enable retention/i })).toBeInTheDocument(),
    )
    fireEvent.click(screen.getByRole('button', { name: /enable retention/i }))

    await waitFor(() =>
      expect(updateRetentionPolicy).toHaveBeenCalledWith('p1', { enabled: true }),
    )
    expect(onEnabled).toHaveBeenCalledWith(POLICY(true))
  })

  it('records the dismissal server-side, not just in this tab', async () => {
    mockDismissals([])
    renderNudge(POLICY(false))

    fireEvent.click(screen.getByRole('button', { name: /not now/i }))

    // The point of the store: a second machine must not re-prompt.
    await waitFor(() =>
      expect(dismissUIPrompt).toHaveBeenCalledWith('retention_activation_nudge'),
    )
    expect(updateRetentionPolicy).not.toHaveBeenCalled()
  })
})
