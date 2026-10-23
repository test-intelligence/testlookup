/**
 * Integration Health, the rows' model (W10 INT-04). Pure: the page, its
 * project-rows section and their tests import the same strings and grouping.
 *
 * `GET /integration-health/status?scope=all` answers the deployment-wide rows,
 * then one row per enabled GitHub / GitLab integration of the reader's projects
 * (`kind: 'project'`; `probed: false` until a probe ran — never "healthy"), then
 * one row per paused webhook (`kind: 'webhook'`). Missing fields read as the
 * pre-W10 answer: a missing `kind` is `global`, a missing `probed` is true.
 */
import type { IntegrationStatus } from '@/services/integrationHealthService'

export type HealthRowKind = 'global' | 'project' | 'webhook'

/** The chip of a row that has never been probed. */
export const NOT_PROBED_LABEL = 'Not probed yet'
/** The chip of a paused webhook row. */
export const PAUSED_LABEL = 'paused'
/** The page below QA_LEAD: it asks nothing (the routes answer 403). */
export const ROLE_LINE = 'Integration health needs the QA lead role or above.'
export const PAUSED_WEBHOOKS_HEADING = 'Paused webhooks'
export const PROBE_NOW = 'Probe now'
export const OPEN_WEBHOOKS = 'Open Outbound webhooks'

/** The row's kind; anything but `project` / `webhook` (or nothing) is a deployment-wide row. */
export function rowKind(row: IntegrationStatus): HealthRowKind {
  return row.kind === 'project' || row.kind === 'webhook' ? row.kind : 'global'
}

/** False only when the server says no probe has run for this row. */
export function isProbed(row: IntegrationStatus): boolean {
  return row.probed !== false
}

/** The deployment-wide rows: the global cards, the workflow timeline and the trends' providers. */
export function globalRows(rows: readonly IntegrationStatus[]): IntegrationStatus[] {
  return rows.filter(row => rowKind(row) === 'global')
}

const PROVIDER_NAMES: Record<string, string> = { github: 'GitHub', gitlab: 'GitLab', webhook: 'Webhook' }

/** `github` → `GitHub`; an unknown provider is capitalised. */
export function providerLabel(provider: string): string {
  return PROVIDER_NAMES[provider] ?? (provider ? provider[0].toUpperCase() + provider.slice(1) : provider)
}

/** A provider's section of project rows: "GitHub projects" / "GitLab projects". */
export function projectSectionHeading(provider: string): string {
  return `${providerLabel(provider)} projects`
}

/** The settings page that configures a project row's integration. */
export function settingsPathFor(provider: string): '/settings/github' | '/settings/gitlab' | null {
  if (provider === 'github') return '/settings/github'
  if (provider === 'gitlab') return '/settings/gitlab'
  return null
}

/** "Open GitHub settings" / "Open GitLab settings". */
export function settingsLinkLabel(provider: string): string {
  return `Open ${providerLabel(provider)} settings`
}

function projectName(row: IntegrationStatus): string {
  return row.label || row.project_name || row.project_id || 'project'
}

/**
 * The card's title: `GitHub · Payment Service` for a project row,
 * `Webhook Release bot · Payments` for a paused webhook, the provider for a global row.
 */
export function rowTitle(row: IntegrationStatus): string {
  const kind = rowKind(row)
  if (kind === 'project') return `${providerLabel(row.provider)} · ${projectName(row)}`
  if (kind === 'webhook') {
    const name = row.label || 'webhook'
    return row.project_name ? `Webhook ${name} · ${row.project_name}` : `Webhook ${name}`
  }
  return row.provider
}

/** A paused webhook's line: `paused after N consecutive failures (threshold T)`. */
export function pausedWebhookLine(row: IntegrationStatus): string {
  const threshold = row.breaker_threshold != null ? ` (threshold ${row.breaker_threshold})` : ''
  return `Paused after ${row.consecutive_failures ?? 0} consecutive failures${threshold}`
}

/** The chip's word: `Not probed yet`, `paused`, or the probe status. */
export function statusLabel(row: IntegrationStatus): string {
  if (!isProbed(row)) return NOT_PROBED_LABEL
  if (rowKind(row) === 'webhook') return PAUSED_LABEL
  return row.status
}

/** The chip's tone key: `not_probed`, `paused` or the status (the page maps each to tokens). */
export function statusTone(row: IntegrationStatus): string {
  if (!isProbed(row)) return 'not_probed'
  if (rowKind(row) === 'webhook') return 'paused'
  return row.status
}

export interface ProjectProviderGroup {
  provider: string
  rows: IntegrationStatus[]
}

export interface GroupedHealthRows {
  global: IntegrationStatus[]
  /** One group per provider, GitHub then GitLab then any other, rows in the server's order. */
  projects: ProjectProviderGroup[]
  webhooks: IntegrationStatus[]
}

const PROVIDER_ORDER = ['github', 'gitlab']

export function groupHealthRows(rows: readonly IntegrationStatus[]): GroupedHealthRows {
  const global: IntegrationStatus[] = []
  const webhooks: IntegrationStatus[] = []
  const byProvider = new Map<string, IntegrationStatus[]>()
  for (const row of rows) {
    const kind = rowKind(row)
    if (kind === 'global') global.push(row)
    else if (kind === 'webhook') webhooks.push(row)
    else {
      const list = byProvider.get(row.provider) ?? []
      list.push(row)
      byProvider.set(row.provider, list)
    }
  }
  const providers = [...byProvider.keys()].sort((a, b) => {
    const ia = PROVIDER_ORDER.indexOf(a)
    const ib = PROVIDER_ORDER.indexOf(b)
    if (ia !== -1 || ib !== -1) return (ia === -1 ? PROVIDER_ORDER.length : ia) - (ib === -1 ? PROVIDER_ORDER.length : ib)
    return 0
  })
  return { global, projects: providers.map(provider => ({ provider, rows: byProvider.get(provider) ?? [] })), webhooks }
}

/** A stable React key for any row. */
export function rowKey(row: IntegrationStatus): string {
  return `${rowKind(row)}:${row.provider}:${row.scope_key ?? ''}`
}

/** The History select's value: the provider for a global row, `provider:projectId` for a project row. */
export function historyOptionValue(row: IntegrationStatus): string {
  return rowKind(row) === 'project' && row.project_id ? `${row.provider}:${row.project_id}` : row.provider
}

/** The History select's label. */
export function historyOptionLabel(row: IntegrationStatus): string {
  return rowKind(row) === 'project' ? rowTitle(row) : row.provider
}

/** The rows the History select offers: global and project rows (a paused webhook has no probe history). */
export function historyRows(rows: readonly IntegrationStatus[]): IntegrationStatus[] {
  return rows.filter(row => rowKind(row) !== 'webhook' && (rowKind(row) === 'global' || Boolean(row.project_id)))
}

/** Back from a select value: `{ provider, projectId }` (no project for a global row). */
export function parseHistoryOption(value: string): { provider: string; projectId?: string } {
  const at = value.indexOf(':')
  if (at === -1) return { provider: value }
  return { provider: value.slice(0, at), projectId: value.slice(at + 1) || undefined }
}
