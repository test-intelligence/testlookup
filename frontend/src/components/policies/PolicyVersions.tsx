/**
 * W11 GATE-02: every version of a policy's scope, in the editor page — a
 * CLOSED `Disclosure` (no side panel, no `persistKey`), whose content mounts
 * only when opened and only then reads the history
 * (`GET /release-gate-policies/history/{project_id}`, or `/system-history`).
 * So the editor's load requests are exactly what they were.
 *
 * One row per version, newest first: `v4 · Active`, the name, who created it
 * and when, when it was activated; a Compare checkbox (at most two) and Open.
 * Two compared: `Compare v3 → v4` and the changed settings, oldest to newest.
 * Read-only for every role that can open the page.
 */
import { useMemo, useState } from 'react'

import Disclosure from '@/components/ui/Disclosure'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import { usePolicyHistory } from '@/hooks/usePolicyHistory'
import type { PolicyDocument, PolicySummary } from '@/services/policyService'
import { diffPolicyDocuments } from './policyDiff'
import PolicyDiffList from './PolicyDiffList'

export const VERSIONS_TITLE = 'Versions'
export const VERSIONS_SUMMARY = "Every version of this policy's scope, and what changed"
export const VERSIONS_LOAD_ERROR = 'Could not load policy versions.'
const MAX_COMPARED = 2

/** `Active` / `Published` / `Draft`. */
export function versionState(policy: Pick<PolicySummary, 'is_active' | 'is_draft'>): string {
  if (policy.is_active) return 'Active'
  return policy.is_draft ? 'Draft' : 'Published'
}

/** The UTC day of an ISO time (`2026-10-01`), or '' when it cannot be read. */
function day(iso: string | null | undefined): string {
  if (!iso) return ''
  const when = new Date(iso)
  return Number.isNaN(when.getTime()) ? '' : when.toISOString().slice(0, 10)
}

function VersionList({ policy, onOpen }: { policy: PolicySummary; onOpen: (id: string) => void }) {
  const { data, error, isLoading } = usePolicyHistory(policy.project_id, true)
  const [compared, setCompared] = useState<string[]>([])
  // The pair whose diff is shown (Compare pressed); another pair hides it again.
  const [shownPair, setShownPair] = useState<string | null>(null)
  const versions = useMemo(() => [...(data ?? [])].sort((a, b) => b.version - a.version), [data])

  if (isLoading && !data) return <LoadingSpinner size="sm" />
  if (error && !data) return <p className="m-0 text-sm text-[var(--color-text-muted)]">{VERSIONS_LOAD_ERROR}</p>
  if (versions.length === 0) return <p className="m-0 text-sm text-[var(--color-text-muted)]">No versions yet.</p>

  const toggle = (id: string, on: boolean) =>
    setCompared((current) => (on ? [...current.filter((x) => x !== id), id].slice(-MAX_COMPARED) : current.filter((x) => x !== id)))
  const pair = versions.filter((version) => compared.includes(version.id)).sort((a, b) => a.version - b.version)
  const pairKey = pair.length === MAX_COMPARED ? `${pair[0].id}:${pair[1].id}` : null

  return (
    <div className="space-y-3">
      <ul aria-label="Policy versions" className="m-0 space-y-2 pl-0">
        {versions.map((version) => {
          const checked = compared.includes(version.id)
          const full = !checked && compared.length >= MAX_COMPARED
          return (
            <li key={version.id} data-policy-version={version.version} className="flex list-none flex-wrap items-center gap-2 text-sm">
              <label className="inline-flex items-center gap-1 text-xs text-[var(--color-text-muted)]">
                <input
                  type="checkbox"
                  aria-label={`Compare v${version.version}`}
                  checked={checked}
                  disabled={full}
                  title={full ? 'Two versions are compared at a time' : undefined}
                  onChange={(event) => toggle(version.id, event.target.checked)}
                />
                Compare
              </label>
              <span className="font-medium text-[var(--color-text)]">v{version.version} · {versionState(version)}</span>
              <span className="min-w-0 truncate text-[var(--color-text-secondary)]">{version.name}</span>
              <span className="text-xs text-[var(--color-text-muted)]">
                created by {version.created_by_name ?? 'a removed user'}
                {day(version.created_at) && ` · ${day(version.created_at)}`}
              </span>
              {version.activated_at && (
                <span className="text-xs text-[var(--color-text-muted)]">activated {day(version.activated_at)}</span>
              )}
              {version.id === policy.id ? (
                <span data-this-version="" className="text-xs font-medium text-[var(--color-text)]">This version</span>
              ) : (
                <button type="button" className="btn-secondary btn-sm" onClick={() => onOpen(version.id)}>
                  Open
                </button>
              )}
            </li>
          )
        })}
      </ul>
      {pairKey !== null && (
        <div data-policy-compare="" className="space-y-2 border-t border-[var(--color-border)] pt-3">
          <button type="button" className="btn-secondary btn-sm" onClick={() => setShownPair(pairKey)}>
            Compare v{pair[0].version} → v{pair[1].version}
          </button>
          {shownPair === pairKey && (
            <PolicyDiffList changes={diffPolicyDocuments(pair[0].rules as PolicyDocument, pair[1].rules as PolicyDocument)} />
          )}
        </div>
      )}
    </div>
  )
}

export default function PolicyVersions({ policy, onOpen }: { policy: PolicySummary; onOpen: (id: string) => void }) {
  return (
    <Disclosure title={VERSIONS_TITLE} summary={VERSIONS_SUMMARY}>
      <VersionList policy={policy} onOpen={onOpen} />
    </Disclosure>
  )
}
