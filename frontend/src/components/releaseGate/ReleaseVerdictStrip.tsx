/**
 * W11 GATE-08a: the top bar's RELEASE and its recorded release-level verdict,
 * one line under `/release-gate`'s header, so the two verdict vocabularies sit
 * side by side with their scopes named — this strip is the release's gate
 * (`GET /releases/{id}/gate`, read by the page through `useReleaseGate`), the
 * card below stays the RUN's decision.
 *
 * Rendered only while a release is selected. The release's name comes from the
 * top bar's own cached list (`useReleases(undefined, { cached: true })`, the
 * picker's SWR entry: no new request). Every name is React text.
 */
import { Link } from 'react-router-dom'
import { Shield } from 'lucide-react'

import { useReleases } from '@/hooks/useReleases'
import { decisionLabel } from '@/components/ui/statusVocabulary'
import { decidedAgo } from '@/services/digestService'
import type { ReleaseGateDetail } from '@/types/releases'

export const NEVER_EVALUATED = 'not evaluated yet'
export const GATE_LOAD_ERROR = 'Could not load the release gate.'

export interface ReleaseVerdictStripProps {
  releaseId: string
  /** `useReleaseGate`'s answer: `null` = never evaluated (the 404), `undefined` = not read yet. */
  gate: ReleaseGateDetail | null | undefined
  error?: unknown
}

/** `No-Go · 2 blockers · decided 2 h ago`, or `not evaluated yet`. */
export function gateWords(gate: ReleaseGateDetail | null): string {
  if (!gate) return NEVER_EVALUATED
  const parts = [decisionLabel(gate.verdict)]
  const blockers = gate.blocking_total ?? gate.blocking_reasons?.length ?? 0
  if (blockers > 0) parts.push(`${blockers} blocker${blockers === 1 ? '' : 's'}`)
  if (gate.decided_at) parts.push(`decided ${decidedAgo(gate.decided_at)}`)
  return parts.join(' · ')
}

export default function ReleaseVerdictStrip({ releaseId, gate, error }: ReleaseVerdictStripProps) {
  const { data: releases } = useReleases(undefined, { cached: true })
  const name = releases?.items?.find((release) => release.id === releaseId)?.name ?? null
  const scope = name ? `Release ${name} (top bar)` : 'The top-bar release'
  const verdict = error ? GATE_LOAD_ERROR : gate === undefined ? null : `release gate: ${gateWords(gate)}`
  return (
    <div data-release-verdict-strip="" className="flex flex-wrap items-center gap-2 text-sm text-[var(--color-text-secondary)]">
      <Shield className="h-4 w-4 shrink-0 text-[var(--color-text-muted)]" aria-hidden />
      <span>
        <span className="font-medium text-[var(--color-text)]">{scope}</span>
        {verdict && <> · {verdict}</>}
      </span>
      <Link to={`/releases/${releaseId}#gate`} className="font-medium text-[var(--color-accent)] hover:underline">
        Open release →
      </Link>
    </div>
  )
}
