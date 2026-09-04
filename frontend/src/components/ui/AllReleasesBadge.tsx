import { Layers } from 'lucide-react'
import { useReleaseScope } from '@/hooks/useReleaseScope'

/**
 * Marks a panel that does NOT honour the global release filter.
 *
 * Why mark the exception rather than the rule
 * -------------------------------------------
 * The release picker sits in the TopBar, so it is present on every route, but
 * only some data is release-scoped. That mixed state is worse than no scoping
 * at all: on a page where one card visibly changes when you pick a release,
 * the change teaches the reader that the filter works *here*, so they read the
 * numbers beside it as the same release's story. A page where nothing changes
 * at least invites suspicion.
 *
 * Early on, most surfaces were unscoped and the honest marker would have been
 * on the few that were. That has inverted — the analytics endpoints, the KPI
 * cards, the trend charts and the run lists are all release-aware now — so
 * marking the remaining exceptions is both quieter and self-limiting: as more
 * surfaces are scoped, these disappear.
 *
 * Renders nothing when no release is selected
 * -------------------------------------------
 * With no release chosen there is no discrepancy to explain, and every page
 * looks exactly as it did before the release axis existed. That mirrors what
 * the request layer does — omit the parameter entirely rather than send an
 * empty one — and keeps the badge from becoming furniture people stop reading.
 *
 * `reason` should say why this panel cannot answer per release, when there is a
 * real answer. For value metrics it is not an oversight: the headline is an
 * explicit 30-day figure, so scoping it to a three-day hotfix would produce a
 * number whose own label contradicts it.
 */
export default function AllReleasesBadge({ reason }: { reason?: string }) {
  const releaseId = useReleaseScope()
  // No release selected → nothing to explain.
  if (!releaseId) return null

  return (
    <span
      className="inline-flex items-center gap-1 text-[10px] px-2 py-0.5 rounded-full border border-[var(--color-border)] text-[var(--color-text-muted)] bg-[var(--color-bg-secondary)]"
      title={
        reason
          ? `Not filtered by the selected release. ${reason}`
          : 'Not filtered by the selected release — this panel covers all releases.'
      }
    >
      <Layers className="h-3 w-3" />
      All releases
    </span>
  )
}
