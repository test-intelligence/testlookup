/**
 * W22a — the purge preview's kept / floor / reference lines.
 */
import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import PreviewKeptLines, { ReferenceWarnings } from './PreviewKeptLines'
import { NOT_HELD_NOTE, PROJECT_HELD_LINE, REFERENCES_UNMEASURED } from './holdsModel'

const RELEASE_ID = '0b5f2c1e-7d1a-4c55-9a51-3f1e2d4c5b6a'

function hold(overrides: Record<string, unknown> = {}) {
  return {
    hold_id: 'hold-a', entity_type: 'release', entity_id: RELEASE_ID, entity_label: 'release 2026-Q4',
    reason: 'Audit Q4 sentinel', expires_at: null, runs_kept: 5, packs_kept: 0, ...overrides,
  }
}

const NO_HOLDS = { project_held: false, runs_kept: 0, packs_kept: 0, active: [] }

function kinds(container: HTMLElement): string[] {
  return Array.from(container.querySelectorAll('[data-kept-line]')).map((el) => el.getAttribute('data-kept-line') ?? '')
}

describe('PreviewKeptLines', () => {
  it('shows the project line first while the whole project is held', () => {
    const { container } = render(
      <PreviewKeptLines preview={{ holds: { project_held: true, runs_kept: 0, packs_kept: 0, active: [hold({ entity_type: 'project', entity_id: null, entity_label: 'the whole project', runs_kept: 0 })] } }} />,
    )
    expect(kinds(container)[0]).toBe('project')
    expect(screen.getByText(PROJECT_HELD_LINE)).toBeInTheDocument()
  })

  it('lists one line per live hold with its entity label muted beside it', () => {
    render(
      <PreviewKeptLines
        preview={{
          holds: {
            project_held: false, runs_kept: 6, packs_kept: 1,
            active: [hold(), hold({ hold_id: 'hold-b', reason: 'Litigation sentinel', entity_label: 'pack of 2026-10-02', entity_type: 'compliance_pack', runs_kept: 1, packs_kept: 1 })],
          },
        }}
      />,
    )
    const first = screen.getByText('Kept by hold: Audit Q4 sentinel — 5 runs, 0 packs')
    expect(first).toHaveAttribute('title', 'Kept by hold: Audit Q4 sentinel — 5 runs, 0 packs')
    expect(screen.getByText('release 2026-Q4')).toBeInTheDocument()
    expect(screen.getByText('Kept by hold: Litigation sentinel — 1 run, 1 pack')).toBeInTheDocument()
    expect(screen.getByText('pack of 2026-10-02')).toBeInTheDocument()
  })

  it('names each floor part that applies', () => {
    render(
      <PreviewKeptLines
        preview={{ holds: NO_HOLDS, floor: { keep_last_runs: 10, runs_kept: 12, latest_passed_kept: true, suites_kept: 2 } }}
      />,
    )
    expect(
      screen.getByText('Kept by the floor: 12 runs (the newest 10, the latest passing run, the latest passing run of 2 suites)'),
    ).toBeInTheDocument()
  })

  it('shows no floor line when the floor keeps nothing', () => {
    const { container } = render(
      <PreviewKeptLines preview={{ holds: NO_HOLDS, floor: { keep_last_runs: 10, runs_kept: 0, latest_passed_kept: false, suites_kept: 0 } }} />,
    )
    expect(container.querySelector('[data-retention-preview-kept]')).toBeNull()
  })

  it('lists measured reference warnings under their heading', () => {
    render(
      <PreviewKeptLines
        preview={{
          holds: NO_HOLDS,
          references_broken: [
            { table: 'run_baselines', column: 'baseline_run_id', count: 3, label: '3 runs lose their baseline link' },
            { table: 'run_diffs', column: 'baseline_run_id', count: 2, label: '2 run diffs lose their baseline run' },
          ],
          references_measured: true,
        }}
      />,
    )
    expect(screen.getByText('Reference warnings:')).toBeInTheDocument()
    expect(screen.getByText('3 runs lose their baseline link')).toBeInTheDocument()
    expect(screen.getByText('2 run diffs lose their baseline run')).toBeInTheDocument()
  })

  it('says the warnings were not measured — never that there are none', () => {
    render(<PreviewKeptLines preview={{ holds: NO_HOLDS, references_broken: [], references_measured: false }} />)
    expect(screen.getByText(REFERENCES_UNMEASURED)).toBeInTheDocument()
    expect(screen.queryByText(/none/i)).toBeNull()
  })

  it('shows the not-held note only while a hold is active', () => {
    const { container, rerender } = render(
      <PreviewKeptLines preview={{ holds: NO_HOLDS, floor: { keep_last_runs: 10, runs_kept: 3, latest_passed_kept: false, suites_kept: 0 } }} />,
    )
    expect(screen.queryByText(NOT_HELD_NOTE)).toBeNull()
    rerender(<PreviewKeptLines preview={{ holds: { ...NO_HOLDS, active: [hold()] } }} />)
    expect(screen.getByText(NOT_HELD_NOTE)).toBeInTheDocument()
    const all = kinds(container)
    expect(all[all.length - 1]).toBe('not-held-note')
  })

  it('renders nothing for an older server without the fields', () => {
    const { container } = render(<PreviewKeptLines preview={{}} />)
    expect(container.innerHTML).toBe('')
  })

  it('renders a reason as text, never markup', () => {
    const { container } = render(
      <PreviewKeptLines preview={{ holds: { ...NO_HOLDS, active: [hold({ reason: '<img src=x onerror=alert(1)>' })] } }} />,
    )
    expect(container.querySelector('img')).toBeNull()
    expect(screen.getByText('Kept by hold: <img src=x onerror=alert(1)> — 5 runs, 0 packs')).toBeInTheDocument()
  })
})

describe('ReferenceWarnings', () => {
  it('renders nothing when measured and nothing breaks', () => {
    const { container } = render(<ReferenceWarnings refs={[]} measured />)
    expect(container.innerHTML).toBe('')
  })

  it('renders the not-measured line', () => {
    render(<ReferenceWarnings refs={undefined} measured={false} />)
    expect(screen.getByText(REFERENCES_UNMEASURED)).toBeInTheDocument()
  })
})
