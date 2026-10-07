import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { clsx } from 'clsx'

export type BannerState = 'go' | 'conditional' | 'no_go' | 'pending' | 'ok' | 'warn' | 'fail'

export interface BannerFact {
  label: string
  value: ReactNode
}

/** The pill's words and hue per state: the word carries the meaning, the hue repeats it. */
export const BANNER_STATES: Record<BannerState, { word: string; hue: string }> = {
  go: { word: 'GO', hue: 'var(--status-passed)' },
  ok: { word: 'OK', hue: 'var(--status-passed)' },
  conditional: { word: 'CONDITIONAL', hue: 'var(--status-broken)' },
  warn: { word: 'WARNING', hue: 'var(--status-broken)' },
  no_go: { word: 'NO-GO', hue: 'var(--status-failed)' },
  fail: { word: 'FAILING', hue: 'var(--status-failed)' },
  pending: { word: 'PENDING', hue: 'var(--color-text-muted)' },
}

const MAX_FACTS = 4

/**
 * A one-line verdict (UX redesign P0): a state pill, two to four facts, and one
 * link. About 44 px tall; it replaces the page-local verdict cards above a
 * page's primary content. A page has a StatusBanner OR a KpiStrip there, not
 * both.
 */
export default function StatusBanner({
  state,
  title,
  facts,
  action,
  className,
}: {
  state: BannerState
  /** Optional words after the pill, e.g. the release name. */
  title?: ReactNode
  facts: readonly BannerFact[]
  action?: { label: string; href?: string; onClick?: () => void }
  className?: string
}) {
  const { word, hue } = BANNER_STATES[state]
  if (import.meta.env.DEV && facts.length > MAX_FACTS) {
    console.warn(`StatusBanner: ${facts.length} facts given; it shows at most ${MAX_FACTS}.`)
  }
  return (
    <div
      data-status-banner={state}
      className={clsx(
        'flex min-h-11 flex-wrap items-center gap-x-4 gap-y-1 rounded-lg border px-4 py-2 text-[13px]',
        className,
      )}
      style={{ borderColor: `color-mix(in srgb, ${hue} 40%, var(--color-border))`, background: `color-mix(in srgb, ${hue} 8%, var(--color-bg-card))` }}
    >
      <span
        data-banner-pill=""
        className="inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[11.5px] font-semibold tracking-wide"
        style={{ color: hue, background: `color-mix(in srgb, ${hue} 14%, transparent)` }}
      >
        <span aria-hidden="true" className="h-1.5 w-1.5 rounded-full" style={{ background: hue }} />
        {word}
      </span>
      {title && <span className="font-medium text-[var(--color-text)]">{title}</span>}
      {facts.slice(0, MAX_FACTS).map((fact) => (
        <span key={fact.label} data-banner-fact="" className="text-[var(--color-text-secondary)]">
          {fact.label} <span className="font-semibold tabular-nums text-[var(--color-text)]">{fact.value}</span>
        </span>
      ))}
      {action && (
        <span className="ml-auto">
          {action.href ? (
            <Link to={action.href} className="font-medium text-[var(--color-accent)] hover:underline">
              {action.label} →
            </Link>
          ) : (
            <button type="button" onClick={action.onClick} className="font-medium text-[var(--color-accent)] hover:underline">
              {action.label} →
            </button>
          )}
        </span>
      )}
    </div>
  )
}
