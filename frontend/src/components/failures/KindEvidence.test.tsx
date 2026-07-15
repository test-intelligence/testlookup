/**
 * Kind badge + evidence popover (AI-4) — hermetic rendering tests.
 *
 * Pins:
 *  - lazy fetch: evidence is NOT requested until the badge is clicked;
 *  - the popover renders one row per check with its verdict + detail and
 *    keeps the "AI-classified" provenance copy;
 *  - the confidence-basis chip maps human_corrected → "human-corrected"
 *    and heuristic_estimate → "estimated";
 *  - no lookup key → plain badge, no button;
 *  - empty evidence → honest fallback copy instead of a fake checklist.
 */
import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { KindBadgeWithEvidence, KindEvidencePanel } from './KindEvidence'
import type { KindEvidence } from '@/types/analytics'

vi.mock('@/hooks/useMetrics', () => ({
  useKindEvidence: vi.fn(),
}))

const EVIDENCE: KindEvidence = {
  schema_version: 1,
  kind: 'infrastructure',
  confidence: 82,
  confidence_basis: 'heuristic_estimate',
  pinned_by_human_correction: false,
  classifier_confidence: 72,
  checks: [
    { check: 'memory_recall', verdict: 'unavailable', detail: 'No prior corrections or analyses recalled.' },
    { check: 'infra_shape', verdict: 'supports', detail: 'Error text matches pattern.connection_refused (INFRASTRUCTURE).' },
    { check: 'history_pattern', verdict: 'neutral', detail: 'History: 3 passes / 1 failures — no strong pattern.' },
    { check: 'status_signal', verdict: 'supports', detail: 'Status BROKEN — unexpected error outside an assertion.' },
    { check: 'classifier', verdict: 'supports', detail: 'rules_engine classified INFRASTRUCTURE (confidence 72).' },
  ],
}

async function mockedHook() {
  const { useKindEvidence } = await import('@/hooks/useMetrics')
  return useKindEvidence as unknown as ReturnType<typeof vi.fn>
}

beforeEach(async () => {
  ;(await mockedHook()).mockReturnValue({ data: undefined, isLoading: false })
})

describe('KindEvidencePanel', () => {
  it('renders every check row with verdict and detail, plus provenance + basis', () => {
    render(<KindEvidencePanel evidence={EVIDENCE} />)
    for (const row of EVIDENCE.checks) {
      const el = screen.getByTestId(`kind-check-${row.check}`)
      expect(el).toHaveTextContent(row.detail)
      expect(el).toHaveTextContent(row.verdict)
    }
    expect(screen.getByText('AI-classified')).toBeInTheDocument()
    expect(screen.getByText('82%')).toBeInTheDocument()
    expect(screen.getByText('estimated')).toBeInTheDocument()
  })

  it('maps the human_corrected basis to the human-corrected chip', () => {
    render(
      <KindEvidencePanel
        evidence={{ ...EVIDENCE, confidence: 95, confidence_basis: 'human_corrected', pinned_by_human_correction: true }}
      />,
    )
    expect(screen.getByText('human-corrected')).toBeInTheDocument()
    expect(screen.getByText('95%')).toBeInTheDocument()
  })
})

describe('KindBadgeWithEvidence', () => {
  it('does not fetch until opened, then shows the checklist', async () => {
    const hook = await mockedHook()
    hook.mockImplementation((_lookup: unknown, enabled: boolean) => ({
      data: enabled ? { found: true, test_case_id: 'tc-1', kind_evidence: EVIDENCE } : undefined,
      isLoading: false,
    }))

    const lastEnabledArg = () => hook.mock.calls[hook.mock.calls.length - 1]?.[1]

    render(<KindBadgeWithEvidence kind="infrastructure" testFingerprint="fp-1" />)
    // Lazy: last call before opening carries enabled=false.
    expect(lastEnabledArg()).toBe(false)
    expect(screen.queryByTestId('kind-evidence-panel')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /failure-kind evidence/i }))
    expect(lastEnabledArg()).toBe(true)
    expect(await screen.findByTestId('kind-evidence-panel')).toBeInTheDocument()
    expect(screen.getByTestId('kind-check-classifier')).toBeInTheDocument()
  })

  it('renders honest fallback copy when no evidence exists', async () => {
    const hook = await mockedHook()
    hook.mockReturnValue({
      data: { found: false, test_case_id: null, kind_evidence: null },
      isLoading: false,
    })
    render(<KindBadgeWithEvidence kind="product" testCaseId="tc-9" />)
    fireEvent.click(screen.getByRole('button', { name: /failure-kind evidence/i }))
    expect(await screen.findByText(/No analysis evidence recorded/i)).toBeInTheDocument()
    expect(screen.queryByTestId('kind-evidence-panel')).not.toBeInTheDocument()
  })

  it('renders a plain badge (no button) without a lookup key', () => {
    render(<KindBadgeWithEvidence kind="product" />)
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
    expect(screen.getByTitle(/AI-classified failure kind: Product/i)).toBeInTheDocument()
  })
})
