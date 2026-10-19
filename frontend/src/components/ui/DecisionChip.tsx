import { clsx } from 'clsx'
import { DECISION_TOKEN, decisionKey, decisionLabel } from './statusVocabulary'

interface Props {
  /** Any spelling of a gate decision: GO, CONDITIONAL_GO, CONDITIONAL, NO_GO, NO-GO, … */
  decision: string
  className?: string
  title?: string
}

const PILL = 'inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-semibold'

/**
 * A release-gate decision as a chip (W29a UIX-03): the one vocabulary's word
 * (Go / Conditional / No-Go, sentence case, never `uppercase`), the decision's
 * tint and border, and its legible `-ink` text. Conditional is the WARNING hue.
 * An unknown value prints its own words (sentence case) in the muted neutral.
 */
export default function DecisionChip({ decision, className, title }: Props) {
  const key = decisionKey(decision)
  if (!key) {
    return (
      <span
        data-decision=""
        title={title}
        className={clsx(PILL, 'border-[var(--color-border-light)] bg-[var(--color-bg-secondary)] text-[var(--color-text-muted)]', className)}
      >
        {decisionLabel(decision)}
      </span>
    )
  }
  const token = DECISION_TOKEN[key]
  return (
    <span
      data-decision={key}
      title={title}
      className={clsx(PILL, className)}
      style={{ background: token.tint, borderColor: token.border, color: token.ink }}
    >
      {decisionLabel(decision)}
    </span>
  )
}
