import { useSystemHealth } from '@/hooks/useSystemHealth'
import { buildTitle, known, shortRevision } from '@/components/layout/buildInfo'

/**
 * Compact "which build am I running" line for the sidebar footer.
 *
 * The backend already reports its version and build provenance (git revision +
 * build date) on every `/health/details` response — the poll {@link
 * useSystemHealth} already makes for the degraded banner — but nothing surfaced
 * it, so a self-host operator had no in-app way to confirm which image a
 * container is running or match a pinned digest back to its commit. This reads
 * the shared health payload (no extra request) and renders nothing until it has
 * a version, so it stays invisible when the backend is unreachable.
 */
export default function AppVersionBadge() {
  const { data } = useSystemHealth()

  const version = known(data?.version)
  if (!version) return null

  const rev = known(data?.build?.revision)
  const title = buildTitle(data?.version, data?.build, data?.env)

  return (
    <p
      className="px-3 pt-1 text-[10.5px] text-[var(--color-text-faint)] tabular-nums truncate"
      title={title}
      data-testid="app-version-badge"
    >
      v{version}
      {rev ? <span className="text-[var(--color-text-muted)]"> · {shortRevision(rev)}</span> : null}
    </p>
  )
}
