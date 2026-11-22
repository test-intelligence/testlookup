/**
 * A phase's gate, as a chip in its row on the release detail (REL-01, D-2).
 *
 * It is a LIVE preview (`GET /releases/{id}/phases/gate` evaluates with
 * `record=False`), unlike the release verdict, which is a recorded snapshot;
 * the two can differ, so the chip's tooltip says so when there is no blocking
 * reason to show instead (R-11).
 */
import type { PhaseGateResult } from '@/types/releases'
import { DECISION_TOKEN } from '@/components/ui/statusVocabulary'
import { mapVerdict } from './mapping'
import { verdictWord } from './gateCopy'

export const LIVE_PREVIEW = 'Live preview — nothing is recorded'

// W29a UIX-03: the decision's legible ink as text, its border (40 %) as the
// outline; conditional is the warning hue, never skipped-gold.
const TONE: Record<string, { fg: string; bd: string }> = {
  go: { fg: DECISION_TOKEN.go.ink, bd: DECISION_TOKEN.go.border },
  no_go: { fg: DECISION_TOKEN.no_go.ink, bd: DECISION_TOKEN.no_go.border },
  conditional: { fg: DECISION_TOKEN.conditional.ink, bd: DECISION_TOKEN.conditional.border },
}
const MUTED = { fg: 'var(--color-text-muted)', bd: 'var(--color-border)' }

/** S-B-2: a phase's exit criteria are stored with it but no verdict reads them. */
export const EXIT_CRITERIA_NOTE = 'Exit criteria are notes; the phase gate does not check them yet.'

/** D-113: "1 open release blocker" / "2 open release blockers". */
function releaseBlockerLabel(count: number): string {
  return `${count} open release blocker${count === 1 ? '' : 's'}`
}

export default function PhaseGateChip({ result }: { result: PhaseGateResult }) {
  const tone = TONE[mapVerdict(result.verdict)] ?? MUTED
  const blockers = result.release_blockers ?? []
  const title = [
    result.blocking_reasons?.[0] || LIVE_PREVIEW,
    result.exit_criteria_evaluated === false ? EXIT_CRITERIA_NOTE : null,
  ].filter(Boolean).join('\n')
  return (
    <>
      <span
        data-testid="phase-gate-chip"
        data-verdict={result.verdict}
        title={title}
        className="text-2xs px-1.5 py-0.5 rounded border font-medium bg-[var(--color-bg-card)]"
        style={{ color: tone.fg, borderColor: tone.bd }}
      >
        Gate: {verdictWord(result.verdict)}
      </span>
      {blockers.length > 0 && (
        // D-113: defects are release-scoped, so they do not change this
        // phase's verdict; they sit beside it so a Go chip over an open
        // CRITICAL is not the whole story.
        <span
          data-testid="phase-release-blockers"
          title={blockers.map(b => `${b.severity ?? 'Unrated'} defect open: ${b.title || b.id}`).join('\n')}
          className="text-2xs px-1.5 py-0.5 rounded border font-medium"
          style={{
            color: DECISION_TOKEN.no_go.ink,
            borderColor: DECISION_TOKEN.no_go.border,
            background: DECISION_TOKEN.no_go.tint,
          }}
        >
          {releaseBlockerLabel(blockers.length)}
        </span>
      )}
    </>
  )
}
