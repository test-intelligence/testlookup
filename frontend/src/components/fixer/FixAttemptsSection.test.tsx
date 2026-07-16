/**
 * Fix Attempts section (AI-2): status chips (incl. the honest rejected_globs /
 * skipped_budget outcomes with tooltips), row expansion fetching the
 * single-attempt endpoint, empty states, and pagination. Hermetic — hooks
 * mocked; the 5 s polling semantics are covered in hooks/useFixer.test.ts.
 */
import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import FixAttemptsSection from './FixAttemptsSection'
import type { FixAttempt, FixAttemptStatus, FixerConfig } from '@/types/fixer'

vi.mock('@/hooks/useFixer', () => ({
  useFixAttempts: vi.fn(),
  useFixerConfig: vi.fn(),
  useFixAttempt: vi.fn(),
}))

function attempt(id: string, status: FixAttemptStatus, extra: Partial<FixAttempt> = {}): FixAttempt {
  return {
    id,
    fixer_run_id: 'fr-1',
    test_fingerprint: `fp-${id}`,
    test_name: `tests/test_checkout.py::test_${id}`,
    status,
    attempt_no: 1,
    patch_summary: null,
    validation: null,
    pr_url: null,
    reason: null,
    created_at: '2026-07-15T10:00:00Z',
    completed_at: null,
    ...extra,
  }
}

const ENABLED_CONFIG = { enabled: true } as FixerConfig
const DISABLED_CONFIG = { enabled: false } as FixerConfig

async function mockHooks({
  items = [] as FixAttempt[],
  total = 0,
  config = ENABLED_CONFIG,
}: { items?: FixAttempt[]; total?: number; config?: FixerConfig } = {}) {
  const { useFixAttempts, useFixerConfig, useFixAttempt } = await import('@/hooks/useFixer')
  ;(useFixAttempts as ReturnType<typeof vi.fn>).mockReturnValue({
    data: { items, total: total || items.length },
    isLoading: false,
    error: undefined,
    mutate: vi.fn(),
  })
  ;(useFixerConfig as ReturnType<typeof vi.fn>).mockReturnValue({
    data: config,
    isLoading: false,
    error: undefined,
    mutate: vi.fn(),
  })
  ;(useFixAttempt as ReturnType<typeof vi.fn>).mockImplementation((attemptId: string | null) => ({
    data: attemptId
      ? {
          ...attempt(attemptId, 'validated', {
            reason: 'Assertion drift after fixture rename',
            pr_url: 'https://github.com/acme/app/pull/42',
          }),
          patch: '--- a/tests/test_checkout.py\n+++ b/tests/test_checkout.py\n-  assert total == 9\n+  assert total == 10',
          runner_log_digest: 'sha256:1f2e3d4c',
          ledger_run_id: 'ar-99887766',
        }
      : undefined,
    isLoading: false,
    error: undefined,
  }))
  return { useFixAttempts: useFixAttempts as ReturnType<typeof vi.fn>, useFixAttempt: useFixAttempt as ReturnType<typeof vi.fn> }
}

function renderSection() {
  return render(
    <MemoryRouter>
      <FixAttemptsSection projectId="proj-1" />
    </MemoryRouter>,
  )
}

describe('FixAttemptsSection', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders rows with status chips, validation reruns, and the draft-PR link', async () => {
    await mockHooks({
      items: [
        attempt('a1', 'pr_opened', {
          validation: { reruns: 5, passed: 5 },
          pr_url: 'https://github.com/acme/app/pull/42',
          attempt_no: 2,
        }),
        attempt('a2', 'failed_validation', { validation: { reruns: 5, passed: 3 } }),
        attempt('a3', 'diagnosing'),
      ],
    })
    renderSection()

    const row1 = screen.getByTestId('attempt-row-a1')
    expect(row1).toHaveTextContent('tests/test_checkout.py::test_a1')
    expect(row1).toHaveTextContent('5/5 reruns')
    expect(row1).toHaveTextContent('2') // attempt #
    expect(screen.getByTestId('attempt-status-pr_opened')).toHaveTextContent('pr opened')
    expect(screen.getByRole('link', { name: /draft pr/i })).toHaveAttribute(
      'href',
      'https://github.com/acme/app/pull/42',
    )

    expect(screen.getByTestId('attempt-row-a2')).toHaveTextContent('3/5 reruns')
    expect(screen.getByTestId('attempt-status-failed_validation')).toBeInTheDocument()
    expect(screen.getByTestId('attempt-status-diagnosing')).toBeInTheDocument()
  })

  it('gives the honest statuses (rejected_globs, skipped_budget) explanatory tooltips', async () => {
    await mockHooks({
      items: [attempt('a1', 'rejected_globs'), attempt('a2', 'skipped_budget')],
    })
    renderSection()

    expect(screen.getByTestId('attempt-status-rejected_globs')).toHaveAttribute(
      'title',
      expect.stringContaining('outside the allowed test globs'),
    )
    expect(screen.getByTestId('attempt-status-skipped_budget')).toHaveAttribute(
      'title',
      expect.stringContaining('budget was exhausted'),
    )
  })

  it('expands a row to fetch the single attempt: patch, reason, runner log, ledger id', async () => {
    const { useFixAttempt } = await mockHooks({ items: [attempt('a1', 'validated')] })
    renderSection()

    expect(screen.queryByTestId('attempt-detail-a1')).not.toBeInTheDocument()
    fireEvent.click(screen.getByTestId('attempt-row-a1'))

    // The detail hook is invoked with the expanded attempt's id.
    expect(useFixAttempt.mock.calls.some((c) => c[0] === 'a1')).toBe(true)

    const detail = screen.getByTestId('attempt-detail-a1')
    expect(detail).toHaveTextContent('Assertion drift after fixture rename')
    // The patch renders in a <pre> that preserves whitespace; assert on raw
    // textContent since toHaveTextContent collapses the diff's leading spaces.
    expect(detail.textContent).toContain('+  assert total == 10')
    expect(detail).toHaveTextContent('sha256:1f2e3d4c')
    expect(detail).toHaveTextContent('ar-99887') // ledger run id (truncated)

    // Collapses again on a second click.
    fireEvent.click(screen.getByTestId('attempt-row-a1'))
    expect(screen.queryByTestId('attempt-detail-a1')).not.toBeInTheDocument()
  })

  it('paginates with limit/offset', async () => {
    const { useFixAttempts } = await mockHooks({
      items: Array.from({ length: 25 }, (_, i) => attempt(`a${i}`, 'validated')),
      total: 60,
    })
    renderSection()

    expect(screen.getByText('1–25 of 60')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Next' }))

    const lastCall = useFixAttempts.mock.calls[useFixAttempts.mock.calls.length - 1]
    expect(lastCall?.[0]).toBe('proj-1')
    expect(lastCall?.[1]).toEqual({ limit: 25, offset: 25 })
  })

  it('zero attempts + Fixer enabled → "hasn\'t attempted any fixes yet" with a settings pointer', async () => {
    await mockHooks({ items: [], config: ENABLED_CONFIG })
    renderSection()

    expect(screen.getByTestId('fix-attempts-empty')).toBeInTheDocument()
    expect(screen.getByText(/hasn't attempted any fixes yet/i)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /settings → ai agents/i })).toHaveAttribute(
      'href',
      '/settings/ai-agents',
    )
  })

  it('zero attempts + Fixer disabled → explanatory disabled card', async () => {
    await mockHooks({ items: [], config: DISABLED_CONFIG })
    renderSection()

    expect(screen.getByTestId('fixer-disabled-empty')).toBeInTheDocument()
    expect(screen.getByText('The Fixer is disabled')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /settings → ai agents/i })).toHaveAttribute(
      'href',
      '/settings/ai-agents',
    )
  })
})
