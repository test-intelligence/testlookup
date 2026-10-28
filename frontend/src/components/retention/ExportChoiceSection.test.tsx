/**
 * W22b ACT-03 — the export step inside the criteria preview.
 *
 * Pins (§4.4): "Delete without an archive" is the default; an over-cap tier is
 * disabled with its limit; the full warning shows only for `full`; the
 * not-a-backup sentence is ALWAYS there; a set that cannot be archived
 * disables the two archive targets and shows the server's refusal.
 */
import { useState } from 'react'
import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { exportEstimate } from '@/test/retentionExportMocks'
import { ARCHIVE_NOT_BACKUP, EXPORT_FULL_WARNING, EXPORT_ERROR_SENTENCES } from '@/types/retentionExport'
import type { ExportEstimate } from '@/types/retentionExport'
import ExportChoiceSection from './ExportChoiceSection'
import type { TargetChoice, TierKey } from './exportModel'

/** The section with its own state, as the panel holds it. */
function Harness({ estimate, onChange }: { estimate: ExportEstimate; onChange?: (v: unknown) => void }) {
  const [target, setTarget] = useState<TargetChoice>('none')
  const [tier, setTier] = useState<TierKey>('summary')
  return (
    <ExportChoiceSection
      estimate={estimate}
      target={target}
      tier={tier}
      onChange={(next) => {
        setTarget(next.target)
        setTier(next.tier)
        onChange?.(next)
      }}
    />
  )
}

function radio(name: RegExp | string) {
  return screen.getByRole('radio', { name }) as HTMLInputElement
}

describe('ExportChoiceSection', () => {
  it('heads the tiers with "Archive contents", so they read as either archive choice, not the last radio', () => {
    // Found reading the DejaVu render: the indented tier list sat directly under "Keep an archive in storage".
    render(<Harness estimate={exportEstimate()} />)
    const group = screen.getByRole('radiogroup', { name: 'Archive contents' })
    expect(group.querySelectorAll('input[name="criteria-export-tier"]')).toHaveLength(3)
  })

  it('defaults to deleting without an archive, with the estimates and the not-a-backup sentence', () => {
    render(<Harness estimate={exportEstimate()} />)
    expect(radio('Delete without an archive').checked).toBe(true)
    expect(radio('Download an archive first').checked).toBe(false)
    expect(screen.getByText(/^Summary — archive ≈ /)).toBeInTheDocument()
    expect(screen.getByText('Reclaims ≈ 180 MB')).toBeInTheDocument()
    expect(screen.getByText(ARCHIVE_NOT_BACKUP)).toBeInTheDocument()
    // No archive chosen: the tiers are not choosable yet, and no full warning.
    expect(radio(/^Summary — archive/).disabled).toBe(true)
    expect(screen.queryByText(EXPORT_FULL_WARNING)).toBeNull()
  })

  it('disables an over-cap tier with the limit as its title', () => {
    render(<Harness estimate={exportEstimate()} />)
    fireEvent.click(radio('Download an archive first'))
    const full = radio(/^Full — archive/)
    expect(full.disabled).toBe(true)
    expect(full.closest('label')?.getAttribute('title')).toBe('Over the 512 MB limit')
    expect(radio(/^Evidence — archive/).disabled).toBe(false)
  })

  it('reports the choice and shows the target note', () => {
    const onChange = vi.fn()
    render(<Harness estimate={exportEstimate()} onChange={onChange} />)
    fireEvent.click(radio('Download an archive first'))
    fireEvent.click(radio(/^Evidence — archive/))
    expect(onChange).toHaveBeenLastCalledWith({ target: 'collect', tier: 'evidence' })
    expect(radio(/^Evidence — archive/).checked).toBe(true)
    expect(screen.getByText(/nothing is deleted before you confirm/)).toBeInTheDocument()
    fireEvent.click(radio('Keep an archive in storage'))
    expect(onChange).toHaveBeenLastCalledWith({ target: 'retain', tier: 'evidence' })
    expect(screen.getByText(/only the difference is reclaimed/)).toBeInTheDocument()
  })

  it('shows the full warning only for full', () => {
    const estimate = exportEstimate({
      tiers: [
        { tier: 'summary', archive_bytes: 2048, measured: true, over_cap: false },
        { tier: 'evidence', archive_bytes: 4096, measured: true, over_cap: false },
        { tier: 'full', archive_bytes: 8192, measured: true, over_cap: false },
      ],
    })
    render(<Harness estimate={estimate} />)
    fireEvent.click(radio('Keep an archive in storage'))
    expect(screen.queryByText(EXPORT_FULL_WARNING)).toBeNull()
    fireEvent.click(radio(/^Full — archive/))
    expect(screen.getByText(EXPORT_FULL_WARNING)).toBeInTheDocument()
    fireEvent.click(radio(/^Summary — archive/))
    expect(screen.queryByText(EXPORT_FULL_WARNING)).toBeNull()
  })

  it('reads unmeasured sizes as "not measured", never 0', () => {
    const estimate = exportEstimate({
      reclaim_bytes: null,
      reclaim_measured: false,
      tiers: [
        { tier: 'summary', archive_bytes: 2048, measured: true, over_cap: false },
        { tier: 'evidence', archive_bytes: null, measured: false, over_cap: false },
        { tier: 'full', archive_bytes: null, measured: false, over_cap: false },
      ],
    })
    render(<Harness estimate={estimate} />)
    expect(screen.getByText('Reclaims not measured')).toBeInTheDocument()
    expect(screen.getByText('Evidence — archive not measured')).toBeInTheDocument()
    expect(screen.queryByText(/≈ 0 B/)).toBeNull()
  })

  it('disables both archive targets and shows the refusal when the set cannot be archived', () => {
    const refusal = EXPORT_ERROR_SENTENCES.too_many_runs
    render(<Harness estimate={exportEstimate({ run_count: 2400, exportable: false, refusal })} />)
    expect(radio('Download an archive first').disabled).toBe(true)
    expect(radio('Keep an archive in storage').disabled).toBe(true)
    expect(radio('Delete without an archive').disabled).toBe(false)
    expect(screen.getByText(refusal)).toBeInTheDocument()
    expect(screen.getByText(ARCHIVE_NOT_BACKUP)).toBeInTheDocument()
  })

  it('shows an execute refusal inline', () => {
    render(
      <ExportChoiceSection
        estimate={exportEstimate()}
        target="collect"
        tier="summary"
        onChange={() => undefined}
        error={EXPORT_ERROR_SENTENCES.over_cap}
      />,
    )
    expect(screen.getByRole('alert')).toHaveTextContent(EXPORT_ERROR_SENTENCES.over_cap)
  })
})
