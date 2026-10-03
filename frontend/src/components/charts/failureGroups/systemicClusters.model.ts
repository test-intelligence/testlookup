/**
 * The systemic flake clusters tab's model (VIZ-504 / VIZ-207): what
 * `GET /api/v1/analytics/systemic-clusters` says, checked and read. Pure.
 *
 * The first UI for this endpoint (plan F5). Its body is not a C3 series but
 * its own list (`items`, `total`, `empty_is_normal`, and `scope` when a release
 * or suite filter selected the members), beside a C2 `meta`. So it has its own
 * validator, held to the same rule as every chart: a body that does not check
 * out is an error frame naming the request, never a half-drawn list.
 *
 * IDENTITY. A cluster is keyed by `membership_key` (migration 0193): the hash
 * of its EXACT member set, so the same cluster keeps its key across nightly
 * sweeps while no test joins or leaves it — and gets a new one when one does
 * (plan F4, OD-4: the UI says so). `cluster_key` is the sweep's rank and
 * changes owners between sweeps; it is used only when a cluster has no member
 * row (the one case BE3 leaves the key null), and never as identity otherwise.
 *
 * Every string is untrusted (test names come from ingested CI files): React
 * text only. Ids are Map keys, never object keys.
 */
import { validateContract, type EnvelopeMeta, type ValidationResult } from '@/lib/viz/contracts'
import { formatNumber, formatPercent } from '@/utils/formatters'
import { NO_VALUE } from '../chartText'
import { tipContent, type TooltipContent } from '../tooltip'

export const CLUSTERS_URL = '/api/v1/analytics/systemic-clusters'

/** The window the nightly sweep looks back over, when the clusters do not say. */
export const DEFAULT_CLUSTER_WINDOW_DAYS = 60

export interface SystemicClusterMember {
  fingerprint: string
  name: string
  failureRuns: number | null
}

export interface SystemicCluster {
  /** What the UI keys the cluster by: `membership_key`, else `rank:<cluster_key>` (a member-less cluster). */
  key: string
  membershipKey: string | null
  clusterKey: string | null
  label: string
  /** `unknown` is a real answer (no cause could be named), shown as such. */
  causeFamily: string | null
  size: number
  /** 0..1, or `null` when not sent. */
  cohesion: number | null
  coFailureRuns: number | null
  windowDays: number | null
  computedAt: string | null
  members: SystemicClusterMember[]
}

export interface SystemicClustersResponse {
  meta: EnvelopeMeta | null
  /** Largest first (the server's order). */
  clusters: SystemicCluster[]
  /** The server's own sentence for an empty list (an empty list is normal). */
  emptyIsNormal: string | null
  /** The membership filter note ("…computed project-wide and are NOT recomputed per suite…"), when filtered. */
  scopeNote: string | null
}

type Dict = Record<string, unknown>
const isDict = (value: unknown): value is Dict => typeof value === 'object' && value !== null && !Array.isArray(value)
const own = (record: Dict, key: string): unknown => (Object.prototype.hasOwnProperty.call(record, key) ? record[key] : undefined)
const count = (value: unknown): number | null =>
  typeof value === 'number' && Number.isInteger(value) && value >= 0 ? value : null
const text = (value: unknown): string | null => (typeof value === 'string' ? value : null)
const unit = (value: unknown): number | null =>
  typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 1 ? value : null

/** At most this many errors are listed for one body (a hostile body cannot grow the list). */
const MAX_ERRORS = 10

/**
 * The body as a `SystemicClustersResponse`, or the reasons it is not one. A
 * cluster needs a label and a whole, non-negative size; anything else it
 * carries is read defensively (`null` when missing or malformed, never 0).
 */
export function validateClustersResponse(input: unknown): ValidationResult<SystemicClustersResponse> {
  if (!isDict(input)) return { ok: false, errors: ['invalid_type: a clusters response must be an object'] }
  const errors: string[] = []
  let meta: EnvelopeMeta | null = null
  const rawMeta = own(input, 'meta')
  if (rawMeta !== undefined && rawMeta !== null) {
    const checked = validateContract('envelope', rawMeta)
    if (checked.ok) meta = checked.value
    else errors.push(...checked.errors.slice(0, MAX_ERRORS).map((e) => `meta: ${e}`))
  }
  const items = own(input, 'items')
  if (!Array.isArray(items)) {
    errors.push('items: required_field: a list of clusters')
    return { ok: false, errors }
  }
  const clusters: SystemicCluster[] = []
  const seen = new Set<string>()
  items.forEach((item, index) => {
    if (errors.length >= MAX_ERRORS) return
    if (!isDict(item)) {
      errors.push(`items[${index}]: invalid_type: a cluster must be an object`)
      return
    }
    const label = text(own(item, 'label'))
    const size = count(own(item, 'size'))
    if (label === null) errors.push(`items[${index}].label: required_field`)
    if (size === null) errors.push(`items[${index}].size: invalid_type: a whole number of tests`)
    if (label === null || size === null) return
    const membershipKey = text(own(item, 'membership_key'))
    const clusterKey = text(own(item, 'cluster_key'))
    const key = membershipKey ?? `rank:${clusterKey ?? index}`
    // The key's index is non-unique on purpose (BE3: a duplicate must not abort
    // the nightly sweep), so a duplicate is possible: the same member set is
    // the same cluster, listed once.
    if (seen.has(key)) return
    seen.add(key)
    const members: SystemicClusterMember[] = []
    const rawMembers = own(item, 'members')
    if (Array.isArray(rawMembers)) {
      for (const member of rawMembers) {
        if (!isDict(member)) continue
        const fingerprint = text(own(member, 'test_fingerprint'))
        if (fingerprint === null) continue
        members.push({
          fingerprint,
          name: text(own(member, 'test_name')) ?? fingerprint,
          failureRuns: count(own(member, 'failure_runs')),
        })
      }
    }
    clusters.push({
      key,
      membershipKey,
      clusterKey,
      label,
      causeFamily: text(own(item, 'cause_family')),
      size,
      cohesion: unit(own(item, 'cohesion')),
      coFailureRuns: count(own(item, 'co_failure_runs')),
      windowDays: count(own(item, 'window_days')),
      computedAt: text(own(item, 'computed_at')),
      members,
    })
  })
  if (errors.length > 0) return { ok: false, errors }
  const scope = own(input, 'scope')
  return {
    ok: true,
    value: {
      meta,
      clusters,
      emptyIsNormal: text(own(input, 'empty_is_normal')),
      scopeNote: isDict(scope) ? text(own(scope, 'note')) : null,
    },
  }
}

/** The window the clusters were computed over, in days: theirs when they agree, else the sweep's default. */
export function clusterWindowDays(clusters: readonly SystemicCluster[]): number {
  const windows = new Set(clusters.map((c) => c.windowDays).filter((d): d is number => d !== null && d > 0))
  return windows.size === 1 ? [...windows][0] : DEFAULT_CLUSTER_WINDOW_DAYS
}

/** The tab's title (plan 3.3.3): never "AI clusters" — they come from co-failure counts, not a model. */
export function clustersTitle(clusters: readonly SystemicCluster[]): string {
  return `Tests that fail together (last ${clusterWindowDays(clusters)} days, whole project)`
}

/** What a key promises, in words (plan F4, OD-4). */
export const IDENTITY_NOTE =
  'Identity follows the exact member set: a cluster keeps its place across nightly runs while its tests stay the same, and is a new cluster when one joins or leaves.'

/** The server's empty sentence, or ours when it sent none. */
export const EMPTY_CLUSTERS_FALLBACK =
  'Most projects have no systemic clusters. An empty list means no group of tests met the co-failure bar, not that clustering failed.'

/** A cause family in words; `unknown` (or nothing) is said as "Unknown cause", never guessed. */
export function causeLabel(cause: string | null): string {
  if (cause === null || cause.trim() === '' || cause.trim().toLowerCase() === 'unknown') return 'Unknown cause'
  return cause.replace(/_/g, ' ')
}

export const formatCohesion = (value: number | null): string =>
  value === null ? NO_VALUE : formatPercent(value, { from: 'ratio' })

/** The readout and announcement for one cluster. */
export function clusterTipContent(cluster: SystemicCluster, rank: number): TooltipContent {
  return tipContent(`#${rank} ${cluster.label}`, [
    { kind: 'dimension', key: 'cause', label: 'Cause', value: causeLabel(cluster.causeFamily) },
    { kind: 'value', key: 'tests', label: 'Tests', value: formatNumber(cluster.size) },
    { kind: 'value', key: 'cohesion', label: 'Cohesion', value: formatCohesion(cluster.cohesion) },
    { kind: 'value', key: 'runs', label: 'Runs failing together', value: formatNumber(cluster.coFailureRuns) },
  ])
}
