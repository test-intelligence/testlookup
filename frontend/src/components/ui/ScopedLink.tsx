/**
 * A `Link` that knows whether its destination can show anything.
 *
 * `OverviewPage` offers "Open flaky coach" from a card whose number is a
 * CROSS-project count — "Flaky tests: 22" — pointing at a page that cannot
 * display any of them while All Projects is active. The link was not wrong to
 * exist; it was wrong to look identical to the six beside it that do work.
 *
 * The fix is not to hide it. An admin who follows it now lands on a prompt with
 * a project picker on it (`ProjectRequiredEmptyState`), which is a working
 * route to the data — hiding the link would remove the only path there. What
 * the reader needs is to know, before clicking, that one more choice is coming.
 *
 * So: same link, plus a qualifier, driven by `config/routeScope.ts` rather than
 * by each call site remembering which destinations are project-scoped. That is
 * the whole point of the registry — the knowledge lived inside the destination,
 * where no link could see it.
 */
import { Link } from 'react-router-dom'
import { clsx } from 'clsx'
import { isRouteReachable } from '@/config/routeScope'
import { useProjectStore } from '@/store/projectStore'

interface Props {
  to: string
  children: React.ReactNode
  /** Classes for the anchor itself. */
  className?: string
  /** Classes for the wrapper. The wrapper is what a flex parent lays out, so
   *  positioning that used to sit on the anchor belongs here. */
  containerClassName?: string
  /** Appended when the destination needs a project the reader has not picked.
   *  Overridable so a dense surface can shorten it. */
  scopeHint?: string
  /**
   * Suppress the VISIBLE qualifier, keeping the title.
   *
   * For a link inside a sentence. "Generate a project key under Settings → API
   * Keys · pick a project and pass it as X-API-Key" is worse than no warning at
   * all — and that sentence already says "a project key", so the scoping is in
   * the prose. Use it only where the surrounding text carries the same meaning.
   */
  hintInTitleOnly?: boolean
}

export default function ScopedLink({
  to,
  children,
  className,
  containerClassName,
  scopeHint = 'pick a project',
  hintInTitleOnly = false,
}: Props) {
  const activeProjectId = useProjectStore(s => s.activeProjectId)
  const reachable = isRouteReachable(to, activeProjectId)

  return (
    <span className={clsx('inline-flex items-baseline gap-1 flex-wrap', containerClassName)}>
      <Link
        to={to}
        className={className}
        title={
          reachable
            ? undefined
            : 'This page shows one project at a time — you will be asked to choose one.'
        }
      >
        {children}
      </Link>
      {!reachable && !hintInTitleOnly && (
        <span
          className={clsx('text-[10px] text-[var(--color-text-faint)]')}
          // Not aria-hidden: "one more step before you see data" is exactly the
          // kind of thing a screen-reader user needs BEFORE following a link,
          // and it is the reason this component exists.
        >
          · {scopeHint}
        </span>
      )}
    </span>
  )
}
