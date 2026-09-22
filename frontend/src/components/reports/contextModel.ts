/**
 * The report context header's content, as a pure function of `meta` (VIZ-301).
 *
 * The header states what the SERVER applied (`meta.scope`, contract C2), not
 * what the client asked for: a release the API ignored, a suite name it
 * normalised, a project the caller cannot read — the header shows the numbers'
 * real population. The only client input is the All-Projects MODE, which
 * `meta` cannot express (one accessible project looks the same either way).
 *
 * Order is fixed: Project → Release → Test Suite → Window → Basis → Generated.
 * Basis and Generated are omitted when the response does not state them;
 * nothing here is ever a placeholder.
 */
import { UNATTRIBUTED_RELEASE, type EnvelopeMeta } from '@/lib/viz/contracts'
import { passRateBasisLabel } from '@/utils/formatMetric'

export type ContextKey = 'project' | 'release' | 'suite' | 'window' | 'basis' | 'generated'

export const CONTEXT_ORDER: readonly ContextKey[] = ['project', 'release', 'suite', 'window', 'basis', 'generated']

export const CONTEXT_LABELS: Record<ContextKey, string> = {
  project: 'Project',
  release: 'Release',
  suite: 'Test Suite',
  window: 'Window',
  basis: 'Basis',
  generated: 'Generated',
}

/** One value inside an entry: a release, a suite, a project. */
export interface ContextValue {
  text: string
  /** A status tag shown beside the value ("Archived", "Unattributed"). */
  tag?: string
}

export interface ContextEntry {
  key: ContextKey
  label: string
  /** The values; one for Window/Basis/Generated. */
  values: ContextValue[]
  /** Shown instead of `values` when there are none ("All releases"). */
  allText?: string
  /** Secondary line ("2026-08-20 → 2026-09-19 UTC"). */
  detail?: string
  /**
   * Window only: why this window is not the one the page's own control shows
   * ("(max for summary)"), printed beside the value.
   */
  note?: string
  /** The API did not apply this dimension's filter, and why. */
  ignoredReason?: string
  /** Generated only: the ISO instant for `<time dateTime>`. */
  dateTime?: string
}

/** Suites (and releases) shown inline before the rest collapse into "+N". */
export const CONTEXT_INLINE_MAX = 3

const RELEASE_TAGS: Record<string, string> = {
  archived: 'Archived',
  [UNATTRIBUTED_RELEASE]: 'Unattributed',
}

function releaseValue(release: EnvelopeMeta['scope']['releases'][number]): ContextValue {
  if (release.id === UNATTRIBUTED_RELEASE) return { text: 'Unattributed runs', tag: RELEASE_TAGS[UNATTRIBUTED_RELEASE] }
  const name = release.name.trim() === '' ? `Release ${release.id.slice(0, 8)}` : release.name
  const tag = RELEASE_TAGS[release.status.toLowerCase()]
  return tag ? { text: name, tag } : { text: name }
}

export function windowText(days: number): string {
  if (days === 1) return 'Last 24 hours'
  return `Last ${days} days`
}

const pad = (n: number) => String(n).padStart(2, '0')

/** A fixed, unambiguous rendering of a UTC instant: "2026-09-19 10:42 UTC". */
export function formatGeneratedAt(iso: string): string | null {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return null
  return `${date.getUTCFullYear()}-${pad(date.getUTCMonth() + 1)}-${pad(date.getUTCDate())} ${pad(
    date.getUTCHours(),
  )}:${pad(date.getUTCMinutes())} UTC`
}

export interface ContextModelInput {
  meta: EnvelopeMeta
  /** All-Projects mode: `meta` cannot tell it from a single accessible project. */
  allProjects: boolean
  /** The chrome capped the page's window for this endpoint: say so on the Window entry. */
  windowNote?: string
}

export function buildContextEntries({ meta, allProjects, windowNote }: ContextModelInput): ContextEntry[] {
  const ignored = new Map(meta.ignored_filters.map((f) => [f.dimension, f.reason]))
  const { scope } = meta
  const entries: ContextEntry[] = []

  entries.push(
    allProjects
      ? { key: 'project', label: CONTEXT_LABELS.project, values: [], allText: `All projects (${scope.projects.length})` }
      : {
          key: 'project',
          label: CONTEXT_LABELS.project,
          values: scope.projects.map((p) => ({ text: p.name })),
          allText: 'No readable project',
        },
  )

  entries.push({
    key: 'release',
    label: CONTEXT_LABELS.release,
    values: scope.releases.map(releaseValue),
    allText: 'All releases',
    ignoredReason: ignored.get('release'),
  })

  entries.push({
    key: 'suite',
    label: CONTEXT_LABELS.suite,
    values: scope.suites.map((s) => ({ text: s })),
    allText: 'All suites',
    ignoredReason: ignored.get('suite'),
  })

  entries.push({
    key: 'window',
    label: CONTEXT_LABELS.window,
    values: [{ text: windowText(scope.window.days) }],
    detail: `${scope.window.from} → ${scope.window.to} ${scope.window.timezone}`,
    ...(windowNote ? { note: windowNote } : {}),
    ignoredReason: ignored.get('window'),
  })

  const basis = passRateBasisLabel(meta.pass_rate_basis)
  if (basis) {
    entries.push({ key: 'basis', label: CONTEXT_LABELS.basis, values: [{ text: `Pass rate ${basis}` }] })
  }

  const generated = meta.generated_at ? formatGeneratedAt(meta.generated_at) : null
  if (generated) {
    entries.push({
      key: 'generated',
      label: CONTEXT_LABELS.generated,
      values: [{ text: generated }],
      dateTime: meta.generated_at,
    })
  }

  return entries
}
