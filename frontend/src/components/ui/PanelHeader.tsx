/**
 * The ONE panel header and panel (W29b UIX-09), replacing the page-local `CardShell` copies.
 *
 * - The title is 14 px / 600 in a tight line (`PANEL_TITLE_CLASS`, which ChartFrame's title reads too): one step
 *   above the 13 px body, in a line SHORTER than the CardShells' (13 px in an inherited 19.5 px line), so no panel
 *   grows. Sentence case; a real heading (`h3` by default: every CardShell was an h3).
 * - `subtitle` is one 13 px line in --color-text-secondary (muted is 4.15:1 on Lab's card).
 * - `actions` sits on the right: counts, links, a `btn-secondary btn-sm`; 12 px --color-text-muted, its words never
 *   wrap (when the title and the actions do not fit one line, the actions take a second one).
 * - `Panel`'s root carries the class `card` (12 px corners, the card surface and border) and is a `<div>`, never a
 *   `<section>` or `<header>`: the visual specs find a card as the nearest `section` / `.card` / `.rounded-xl`
 *   ancestor of a real heading (`tests/lib/production-pages.ts` `cardByHeading` / `cardAround`).
 */
import type { ReactNode } from 'react'
import { clsx } from 'clsx'

/** The ONE panel title look (UIX-09): 14 px / 600, sentence case, a tight line. ChartFrame's title reads it too. */
export const PANEL_TITLE_CLASS = 'm-0 text-base font-semibold leading-tight text-[var(--color-text)]'

export interface PanelHeaderProps {
  title: ReactNode
  /** Default 3: every CardShell copy was an h3. */
  headingLevel?: 2 | 3 | 4
  /** The heading's `title` (how to read the panel, as a tooltip). */
  titleHint?: string
  /** One 13 px line under the title, --color-text-secondary. */
  subtitle?: ReactNode
  /** The right side: counts, links, a `btn-secondary btn-sm`. */
  actions?: ReactNode
  /** Default true: a 1 px --color-border line under the header (the body is a table or a list). */
  divider?: boolean
  /** The title never wraps (Defects: a tab read "In / progress" on CI's fonts). */
  nowrapTitle?: boolean
  /** The heading's id (for an `aria-labelledby`). */
  id?: string
}

const HEADINGS = { 2: 'h2', 3: 'h3', 4: 'h4' } as const

export default function PanelHeader({
  title, headingLevel = 3, titleHint, subtitle, actions, divider = true, nowrapTitle = false, id,
}: PanelHeaderProps) {
  const Heading = HEADINGS[headingLevel]
  return (
    <div
      data-panel-header=""
      className={clsx(
        'flex flex-wrap items-center justify-between gap-x-2.5 gap-y-2 px-4 py-3',
        divider && 'border-b border-[var(--color-border)]',
      )}
    >
      <div className="min-w-0">
        <Heading id={id} title={titleHint} className={clsx(PANEL_TITLE_CLASS, nowrapTitle && 'whitespace-nowrap')}>
          {title}
        </Heading>
        {subtitle && <p className="mt-0.5 text-sm text-[var(--color-text-secondary)]">{subtitle}</p>}
      </div>
      {actions && (
        <div data-panel-actions="" className="flex items-center gap-2.5 whitespace-nowrap text-xs text-[var(--color-text-muted)]">
          {actions}
        </div>
      )}
    </div>
  )
}

export interface PanelProps extends PanelHeaderProps {
  children?: ReactNode
  /** Layout only (a margin, `h-full`); the surface, border and radius are `.card`'s. */
  className?: string
  'data-testid'?: string
}

/** A panel: the `.card` surface with no padding of its own, the header, then the body (a table, a list, a chart). */
export function Panel({ children, className, 'data-testid': testId, ...header }: PanelProps) {
  return (
    <div data-panel="" data-testid={testId} className={clsx('card !p-0 overflow-hidden', className)}>
      <PanelHeader {...header} />
      {children}
    </div>
  )
}
