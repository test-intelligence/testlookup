/**
 * NTF-04: under the preview's header, what the digest covers when a saved
 * view was asked for — the same lines a delivered digest carries:
 *
 *   Filtered by view: Checkout regressions — release 2026.10 · suite checkout · last 7 days
 *   2 releases in this view no longer exist in this project and were left out.
 *   Project-wide sections (flaky debt by team, hours saved) are left out of a filtered digest.
 *
 * Reads `scope` through `parseDigestScope`: a missing, project-wide or
 * malformed scope renders nothing (the preview stands without it). Names are
 * React text, clipped by CSS.
 */
import { parseDigestScope } from '@/services/digestService'
import { FILTERED_NOTE, droppedNote, isFilteredScope, scopeHeadline } from './digestScopeModel'

export default function DigestScopeBlock({ scope: raw }: { scope: unknown }) {
  const scope = parseDigestScope(raw)
  if (!scope) return null
  const headline = scopeHeadline(scope)
  if (!headline) return null
  const filtered = isFilteredScope(scope)
  const dropped = scope.source === 'saved_view' ? droppedNote(scope.dropped_release_count) : null
  return (
    <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-bg)]/50 px-3 py-2 space-y-1" data-digest-scope={scope.source}>
      <p className="text-sm text-[var(--color-text)] truncate" title={headline}>
        {headline}
      </p>
      {dropped && <p className="text-xs text-[var(--status-broken)]">{dropped}</p>}
      {filtered && <p className="text-xs text-[var(--color-text-muted)]">{FILTERED_NOTE}</p>}
    </div>
  )
}
