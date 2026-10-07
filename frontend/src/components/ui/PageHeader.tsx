import { ReactNode, useContext } from 'react'
import { HelpCircle } from 'lucide-react'
import { clsx } from 'clsx'
import { DocumentTitleRouteKeyContext, useDocumentTitle } from '@/hooks/useDocumentTitle'
import { openHelp } from '@/store/helpStore'
import OverflowMenu, { type OverflowItem } from './OverflowMenu'

interface Props {
  title: string
  subtitle?: string
  /** At most one primary and one secondary button; the rest go in `overflow`. */
  actions?: ReactNode
  className?: string
  /**
   * A documentation topic: renders a **?** beside the title that opens it in
   * the help drawer. `topic#anchor` opens it on that section.
   */
  helpTopic?: string
  /** Header actions beyond `actions`, in a **⋯** menu. */
  overflow?: readonly OverflowItem[]
  /** A tab bar under the title row (`Tabs` or `RouteTabs`). */
  tabs?: ReactNode
  /**
   * The redesign's compact header: `text-xl`, a tighter bottom margin, and the
   * subtitle on one line (its full text in `title`). Off by default in P0 so
   * no existing page moves; pages switch as they adopt the page template (P3).
   */
  compact?: boolean
}

export default function PageHeader({ title, subtitle, actions, className, helpTopic, overflow, tabs, compact = false }: Props) {
  const routeKey = useContext(DocumentTitleRouteKeyContext)
  // Drive the browser-tab title off the page heading. PageHeader is rendered by
  // essentially every routed page, so wiring the title here gives every route a
  // distinct tab title with no per-page duplication (see useDocumentTitle).
  useDocumentTitle(title, routeKey)
  const hasActions = Boolean(actions) || (overflow?.length ?? 0) > 0
  const heading = <h1 className={clsx('font-bold text-[var(--color-text)]', compact ? 'text-xl' : 'text-2xl')}>{title}</h1>
  return (
    <div data-page-header="" data-compact={compact ? 'true' : 'false'} className={clsx(compact ? 'mb-3' : 'mb-5', className)}>
      <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
        <div className="max-w-3xl">
          {helpTopic ? (
            <div className="flex items-center gap-2">
              {heading}
              <button
                type="button"
                aria-label={`Help: ${title}`}
                data-help-topic={helpTopic.split('#')[0]}
                data-help-anchor={helpTopic.split('#')[1] || undefined}
                onClick={() => {
                  const [topic, anchor] = helpTopic.split('#')
                  openHelp(topic, anchor || null)
                }}
                className="inline-flex h-6 w-6 items-center justify-center rounded-full text-[var(--color-text-muted)] hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text)]"
              >
                <HelpCircle aria-hidden="true" className="h-4 w-4" />
              </button>
            </div>
          ) : (
            heading
          )}
          {subtitle && (
            <p
              className={clsx('text-sm text-[var(--color-text-muted)]', compact ? 'mt-0.5 line-clamp-1' : 'mt-1')}
              title={compact ? subtitle : undefined}
            >
              {subtitle}
            </p>
          )}
        </div>
        {hasActions && (
          <div className="flex flex-wrap items-center gap-2">
            {actions}
            {overflow && <OverflowMenu items={overflow} />}
          </div>
        )}
      </div>
      {tabs && <div className="mt-3">{tabs}</div>}
    </div>
  )
}
