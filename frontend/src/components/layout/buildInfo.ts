import type { BuildProvenance } from '@/hooks/useSystemHealth'

// The build env injects the literal "unknown" when a field is unset (local/dev
// runs), so an empty string and "unknown" both mean "no provenance".
export function known(value: string | undefined): string | undefined {
  if (!value) return undefined
  const trimmed = value.trim()
  if (!trimmed || trimmed.toLowerCase() === 'unknown') return undefined
  return trimmed
}

// Git revisions arrive as full 40-char SHAs; the badge shows the conventional
// 7-char short form. Non-SHA revisions (tags, "unknown") pass through untouched.
export function shortRevision(revision: string): string {
  return /^[0-9a-f]{40}$/i.test(revision) ? revision.slice(0, 7) : revision
}

/**
 * Hover text spelling out the full build identity, assembled from whatever
 * provenance is actually present. The visible badge is deliberately terse, so
 * the tooltip is where an operator confirms the exact commit and build date.
 */
export function buildTitle(
  version: string | undefined,
  build: BuildProvenance | undefined,
  env: string | undefined,
): string {
  const parts: string[] = []
  const v = known(version)
  if (v) parts.push(`Version ${v}`)
  const rev = known(build?.revision)
  if (rev) parts.push(`Revision ${rev}`)
  const builtAt = known(build?.built_at)
  if (builtAt) parts.push(`Built ${builtAt}`)
  const e = known(env)
  if (e) parts.push(`Env ${e}`)
  return parts.join('\n')
}
