/**
 * The Release gate "Context" group's heading, note and placeholder sizes: a
 * dependency-free leaf on purpose.
 *
 * The page's Suspense fallback (`GateContextHeader.tsx`) draws these BEFORE the
 * group's lazy chunk arrives, so whatever this file imports is in every gate
 * visit's static graph, flag off included (rolldown chunks by static
 * reachability). `GateCatalogue.model.ts` re-exports them; it must not be the
 * one the page imports, since it reaches the request-state resolver, the
 * catalogue scope and the contract validators.
 */
import type { ClusterRow } from './catalogueAdapters'

export const CONTEXT_HEADING = 'Context'

/**
 * The placeholder's height until the lazy comparison is near, px: its
 * section's own as drawn (header, takeaway and scope lines, a 280 px plot,
 * the legend, and the dated caption in the frame's footer). Measured on the
 * hermetic gate at 1280 px: 564 (it was 420, the plot plus a guess, so the
 * cluster list jumped ~140 px when the comparison mounted).
 */
export const RELEASES_PLACEHOLDER_HEIGHT = 560

/** Under the group heading: what the group is for. */
export function contextNote(build: string): string {
  return `These charts explain the evidence; they do not decide. The verdict above is the stored decision for build ${build}.`
}

/**
 * The cluster share's drawn height, px: its frame (header, a 280 px plot,
 * legend) and the stored caption under it. Measured on the hermetic gate at
 * 1280: 372 with three clusters (donut), 380 with seven (ranked bar); a
 * placeholder a little off moves the list below by that little, not by a
 * whole section.
 */
export const CLUSTERS_PLACEHOLDER_HEIGHT = 376

/** The gap between the two charts' rows (`gap-4`), px. */
export const CONTEXT_GRID_GAP = 16

/** A cluster share needs something to share: a stored cluster of positive size. */
export function hasClusterShare(clusters: readonly ClusterRow[]): boolean {
  return clusters.some((cluster) => typeof cluster.size === 'number' && cluster.size > 0)
}

/** The group's body below the heading, px: the release row, then the cluster row when there is one. */
export function contextBodyHeight(both: boolean): number {
  return RELEASES_PLACEHOLDER_HEIGHT + (both ? CONTEXT_GRID_GAP + CLUSTERS_PLACEHOLDER_HEIGHT : 0)
}
