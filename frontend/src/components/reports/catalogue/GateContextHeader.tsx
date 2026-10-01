/**
 * The Release gate "Context" group's heading and note, and the placeholder
 * that holds the group's place while its chunk loads (R1-7).
 *
 * In a file of its own, outside the lazy `GateCatalogue` chunk, because the
 * page needs it BEFORE that chunk arrives: the Suspense fallback draws the
 * same heading and note the group will, over a box of the group's height, so
 * "Linked Failure Clusters" below does not jump down when the charts land.
 * Pure text and one box. Its value imports are React and the dependency-free
 * `gateContextWords` only (`ClusterRow` is a type, erased): NOT
 * `GateCatalogue.model`, which reaches the request-state resolver and would
 * put it in every flag-off gate visit (`sectionOnlyModules.test.ts`).
 */
import { useId } from 'react'
import type { ClusterRow } from './catalogueAdapters'
import { CONTEXT_HEADING, contextBodyHeight, contextNote, hasClusterShare } from './gateContextWords'

export interface GateContextHeaderProps {
  headingId: string
  build: string
}

/**
 * An h3, like every other section of the gate (Policy Rules Evaluated,
 * Blocking Issues, Linked Failure Clusters ...): as an h2 it put every
 * section after it under "Context" in a screen reader's outline.
 */
export function GateContextHeader({ headingId, build }: GateContextHeaderProps) {
  return (
    <div>
      <h3 id={headingId} className="text-sm font-semibold text-[var(--color-text)]">
        {CONTEXT_HEADING}
      </h3>
      <p data-gate-note="context" className="text-xs text-[var(--color-text-secondary)]">
        {contextNote(build)}
      </p>
    </div>
  )
}

export interface GateContextPendingProps {
  build: string
  clusters: readonly ClusterRow[]
}

/** The Suspense fallback: the group's heading and note, and an empty box of its body's height. */
export function GateContextPending({ build, clusters }: GateContextPendingProps) {
  const headingId = useId()
  return (
    <section data-gate-context-pending="" aria-labelledby={headingId} aria-busy="true" className="space-y-3">
      <GateContextHeader headingId={headingId} build={build} />
      <div data-gate-context-box="" aria-hidden="true" style={{ minHeight: contextBodyHeight(hasClusterShare(clusters)) }} />
    </section>
  )
}
