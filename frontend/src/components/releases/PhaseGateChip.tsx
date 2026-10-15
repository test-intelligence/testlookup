/**
 * A phase's gate, as a chip in its row on the release detail (REL-01, D-2).
 *
 * It is a LIVE preview (`GET /releases/{id}/phases/gate` evaluates with
 * `record=False`), unlike the release verdict, which is a recorded snapshot;
 * the two can differ, so the chip's tooltip says so when there is no blocking
 * reason to show instead (R-11).
 */
import type { PhaseGateResult } from '@/types/releases'
import { mapVerdict } from './mapping'
import { verdictWord } from './gateCopy'

export const LIVE_PREVIEW = 'Live preview — nothing is recorded'

const TONE: Record<string, { fg: string; bd: string }> = {
  go: { fg: 'var(--status-passed)', bd: 'color-mix(in srgb, var(--status-passed) 40%, transparent)' },
  no_go: { fg: 'var(--status-failed)', bd: 'color-mix(in srgb, var(--status-failed) 40%, transparent)' },
  conditional: { fg: 'var(--status-broken)', bd: 'color-mix(in srgb, var(--status-skipped) 40%, transparent)' },
}
const MUTED = { fg: 'var(--color-text-muted)', bd: 'var(--color-border)' }

export default function PhaseGateChip({ result }: { result: PhaseGateResult }) {
  const tone = TONE[mapVerdict(result.verdict)] ?? MUTED
  return (
    <span
      data-testid="phase-gate-chip"
      data-verdict={result.verdict}
      title={result.blocking_reasons?.[0] || LIVE_PREVIEW}
      className="text-[11px] px-1.5 py-0.5 rounded border font-medium bg-[var(--color-bg-card)]"
      style={{ color: tone.fg, borderColor: tone.bd }}
    >
      Gate: {verdictWord(result.verdict)}
    </span>
  )
}
