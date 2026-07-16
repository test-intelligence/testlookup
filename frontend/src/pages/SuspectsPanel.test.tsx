import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { SuspectsPanel } from './FailureAnalysisPage'

// Drive the panel via a mocked useSuspects so the tests are hermetic (no SWR
// fetch / axios). Each case sets the hook's return before rendering.
vi.mock('@/hooks/useCommitAttribution', () => ({
  useSuspects: vi.fn(),
}))

const CAVEAT =
  'Suspects, not culprits: these commits changed files near the failing test. ' +
  'In a monorepo the file owner is often not the commit author — treat each as a lead to inspect, not blame.'

async function mockSuspects(value: Record<string, unknown>) {
  const { useSuspects } = await import('@/hooks/useCommitAttribution')
  ;(useSuspects as ReturnType<typeof vi.fn>).mockReturnValue({
    ranking: value,
    suspects: (value.suspects as unknown[]) ?? [],
    available: value.available ?? false,
    caveat: value.caveat ?? CAVEAT,
    hasLocationSignal: value.has_location_signal ?? false,
    isLoading: false,
    isError: false,
    refresh: vi.fn(),
  })
}

describe('SuspectsPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders nothing when there is no run to attribute against', async () => {
    await mockSuspects({ available: false })
    const { container } = render(<SuspectsPanel runId={null} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('shows an honest empty state when no commit range is available', async () => {
    await mockSuspects({ available: false, source: 'unavailable' })
    render(<SuspectsPanel runId="run-1" panelId="suspects-panel" />)
    expect(screen.getByText(/No commit range available/i)).toBeInTheDocument()
    expect(screen.getByText(/no GitHub connector is configured/i)).toBeInTheDocument()
  })

  it('renders ranked suspects with the score, deep link and expandable rationale', async () => {
    await mockSuspects({
      available: true,
      source: 'connector',
      has_location_signal: true,
      caveat: CAVEAT,
      suspects: [
        {
          sha: 'a'.repeat(40),
          author: 'Al',
          message: 'fix charge rounding',
          score: 92.5,
          commit_url: 'https://github.com/acme/webapp/commit/' + 'a'.repeat(40),
          rationale: {
            overlapping_files: ['services/payments/charge.py'],
            overlap_score: 1.0,
            recency_rank: 1,
            author_touched_module_before: false,
            changed_file_count: 2,
          },
        },
      ],
    })
    render(<SuspectsPanel runId="run-1" fingerprint="fp1" panelId="suspects-panel" />)

    // Monorepo "suspects, not culprits" caveat is surfaced verbatim.
    expect(screen.getByText(/Suspects, not culprits/i)).toBeInTheDocument()
    // Short SHA + score + deep link.
    const link = screen.getByRole('link', { name: /aaaaaaaa/i })
    expect(link).toHaveAttribute('href', 'https://github.com/acme/webapp/commit/' + 'a'.repeat(40))
    expect(screen.getByText('93')).toBeInTheDocument()
    // Rationale is inspectable — the overlapping file is listed.
    expect(screen.getByText('services/payments/charge.py')).toBeInTheDocument()
    expect(screen.getByText(/Recency rank 1/i)).toBeInTheDocument()
  })

  it('warns when there is no source-location signal (recency-only ordering)', async () => {
    await mockSuspects({
      available: true,
      has_location_signal: false,
      suspects: [
        {
          sha: 'b'.repeat(40),
          author: 'Bo',
          message: 'unrelated',
          score: 20,
          commit_url: null,
          rationale: {
            overlapping_files: [],
            overlap_score: 0,
            recency_rank: 1,
            author_touched_module_before: false,
            changed_file_count: 1,
          },
        },
      ],
    })
    render(<SuspectsPanel runId="run-1" panelId="suspects-panel" />)
    expect(screen.getByText(/ordered by recency, not path overlap/i)).toBeInTheDocument()
    // No-overlap commits still render, honestly labelled.
    expect(screen.getByText(/No file overlapped/i)).toBeInTheDocument()
  })
})
