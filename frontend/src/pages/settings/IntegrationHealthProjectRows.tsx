/**
 * Integration Health, under the deployment-wide cards (W10 INT-04): one section
 * per provider with a card per project integration (GitHub / GHE, GitLab), then
 * the webhooks the circuit breaker paused.
 *
 * Every name, host and error is React text. A settings link is a ScopedLink;
 * clicking it first makes the row's project the active one (when the reader has
 * that project), so the settings page opens on the integration the row is about.
 * A row whose project is not in the reader's list offers no link.
 */
import { clsx } from 'clsx'
import ScopedLink from '@/components/ui/ScopedLink'
import { useProjectStore } from '@/store/projectStore'
import type { IntegrationStatus } from '@/services/integrationHealthService'
import type { Project } from '@/types/projects'
import { formatCompactDateTime } from '@/utils/formatters'
import {
  OPEN_WEBHOOKS,
  PAUSED_WEBHOOKS_HEADING,
  PROBE_NOW,
  groupHealthRows,
  pausedWebhookLine,
  projectSectionHeading,
  rowKey,
  rowTitle,
  settingsLinkLabel,
  settingsPathFor,
  statusLabel,
  statusTone,
} from './integrationHealthModel'

// The chip's tokens (UI meanings, never a test status): "Not probed yet" and a
// skipped probe are neutral; a paused webhook is danger.
const CHIP: Record<string, string> = {
  healthy: 'bg-[var(--ui-success-bg)]/40 text-[var(--ui-success-ink)]',
  degraded: 'bg-[var(--ui-warning-bg)]/40 text-[var(--ui-warning-ink)]',
  timeout: 'bg-[var(--ui-warning-bg)]/40 text-[var(--ui-warning-ink)]',
  down: 'bg-[var(--ui-danger-bg)]/40 text-[var(--ui-danger-ink)]',
  auth_error: 'bg-[var(--ui-danger-bg)]/40 text-[var(--ui-danger-ink)]',
  paused: 'bg-[var(--ui-danger-bg)] text-[var(--ui-danger-ink)]',
  not_probed: 'bg-[var(--color-bg-secondary)] text-[var(--color-text-muted)]',
}
const NEUTRAL_CHIP = 'bg-[var(--color-bg-secondary)] text-[var(--color-text-muted)]'

const GRID = 'grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3'
const LINK = 'text-xs text-[var(--color-accent)] hover:underline'

function StatusChip({ row }: { row: IntegrationStatus }) {
  return (
    <span data-health-status={statusTone(row)} className={clsx('shrink-0 px-2 py-0.5 rounded text-xs font-medium', CHIP[statusTone(row)] ?? NEUTRAL_CHIP)}>
      {statusLabel(row)}
    </span>
  )
}

/**
 * The provider · project (or webhook · project) name is what the card exists to
 * show, so it WRAPS — never truncates (≈ 310 px cards cut it at 1280 on Windows
 * fonts, and CI's DejaVu is wider). The status chip sits on its own line under it.
 */
function CardTitle({ row }: { row: IntegrationStatus }) {
  const title = rowTitle(row)
  return (
    <div className="space-y-1">
      <p data-health-title="" className="min-w-0 break-words text-sm font-medium text-[var(--color-text)]" title={title}>{title}</p>
      <div className="flex flex-wrap items-center gap-2">
        <StatusChip row={row} />
      </div>
    </div>
  )
}

/** A settings link that first selects the row's project, or nothing when the reader does not have that project. */
function ProjectSettingsLink({ to, label, project, onSelect }: {
  to: string; label: string; project: Project | undefined; onSelect: (p: Project) => void
}) {
  if (!project) return null
  return (
    // Capture: the project is active before the link navigates.
    <span onClickCapture={() => onSelect(project)}>
      <ScopedLink to={to} hintInTitleOnly className={LINK}>{label}</ScopedLink>
    </span>
  )
}

export default function IntegrationHealthProjectRows({ rows, probing, onProbe }: {
  rows: readonly IntegrationStatus[]
  probing: boolean
  onProbe: (provider: string, projectId?: string) => void
}) {
  const projects = useProjectStore(s => s.projects)
  const setActiveProject = useProjectStore(s => s.setActiveProject)
  const { projects: groups, webhooks } = groupHealthRows(rows)
  const projectOf = (row: IntegrationStatus) => (row.project_id ? projects.find(p => p.id === row.project_id) : undefined)

  if (groups.length === 0 && webhooks.length === 0) return null

  return (
    <div className="space-y-4" data-health-project-rows="">
      {groups.map(group => (
        <section key={group.provider} aria-label={projectSectionHeading(group.provider)} data-health-provider={group.provider} className="space-y-2">
          <h2 className="text-sm font-semibold text-[var(--color-text)]">{projectSectionHeading(group.provider)}</h2>
          <div className={GRID}>
            {group.rows.map(row => {
              const settings = settingsPathFor(row.provider)
              return (
                <div key={rowKey(row)} className="card min-w-0 space-y-2" data-health-row={row.scope_key ?? ''} data-health-kind="project">
                  <CardTitle row={row} />
                  {row.target && (
                    <p data-health-target="" className="text-xs font-mono text-[var(--color-text-muted)] [overflow-wrap:anywhere]">{row.target}</p>
                  )}
                  <div className="space-y-1 text-xs text-[var(--color-text-muted)]">
                    {row.response_ms != null && <p>Latency: <span className="text-[var(--color-text-secondary)]">{row.response_ms}ms</span></p>}
                    {row.message && <p className="[overflow-wrap:anywhere]">{row.message}</p>}
                    {row.consecutive_failures > 0 && (
                      <p className="text-[var(--ui-danger-ink)]">Consecutive failures: {row.consecutive_failures}</p>
                    )}
                    {row.last_checked_at && <p>Last checked: {formatCompactDateTime(row.last_checked_at)}</p>}
                  </div>
                  <div className="flex flex-wrap items-center gap-2">
                    <button type="button" className="btn-secondary btn-sm" disabled={probing || !row.project_id}
                      onClick={() => onProbe(row.provider, row.project_id ?? undefined)}>
                      {PROBE_NOW}
                    </button>
                    {settings && (
                      <ProjectSettingsLink to={settings} label={settingsLinkLabel(row.provider)} project={projectOf(row)} onSelect={setActiveProject} />
                    )}
                  </div>
                </div>
              )
            })}
          </div>
        </section>
      ))}

      {webhooks.length > 0 && (
        <section aria-label={PAUSED_WEBHOOKS_HEADING} data-health-paused-webhooks="" className="space-y-2">
          <h2 className="text-sm font-semibold text-[var(--color-text)]">{PAUSED_WEBHOOKS_HEADING}</h2>
          <div className={GRID}>
            {webhooks.map(row => (
              <div key={rowKey(row)} className="card min-w-0 space-y-2" data-health-row={row.scope_key ?? ''} data-health-kind="webhook">
                <CardTitle row={row} />
                {row.target && (
                  <p data-health-target="" className="text-xs font-mono text-[var(--color-text-muted)] [overflow-wrap:anywhere]">{row.target}</p>
                )}
                <div className="space-y-1 text-xs text-[var(--color-text-muted)]">
                  <p data-health-paused-line="" className="text-[var(--ui-danger-ink)]">{pausedWebhookLine(row)}</p>
                  {row.message && <p className="[overflow-wrap:anywhere]">Last error: {row.message}</p>}
                  {row.last_checked_at && <p>Paused: {formatCompactDateTime(row.last_checked_at)}</p>}
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <ProjectSettingsLink to="/settings/webhooks" label={OPEN_WEBHOOKS} project={projectOf(row)} onSelect={setActiveProject} />
                </div>
              </div>
            ))}
          </div>
        </section>
      )}
    </div>
  )
}
