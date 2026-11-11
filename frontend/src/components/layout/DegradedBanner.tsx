import { AlertTriangle, RefreshCw } from 'lucide-react'
import { useSystemHealth } from '@/hooks/useSystemHealth'
import { useAuthStore } from '@/store/authStore'

// Friendly labels per dependency so the banner reads as "ChromaDB" rather
// than the raw key "chromadb". Unknown keys fall through to a Title-Cased
// version of the key.
const LABELS: Record<string, string> = {
  postgres: 'PostgreSQL',
  mongo: 'MongoDB',
  redis: 'Redis',
  minio: 'MinIO',
  ollama: 'Ollama',
  chromadb: 'ChromaDB',
}

function label(key: string): string {
  return LABELS[key] ?? key.charAt(0).toUpperCase() + key.slice(1)
}

export default function DegradedBanner() {
  const { unavailable, criticalUnavailable, isDegraded, isUnreachable } = useSystemHealth()
  const demoSession = useAuthStore((s) => s.demoSession)

  // On the public demo, an unreachable backend is almost always the nightly
  // rebuild (or maintenance): every container is recreated and the sample
  // data re-seeded, for a minute or two. Say that, calmly, instead of an
  // outage alarm. The rebuild also replaces the demo account, so the visitor
  // may land on the sign-in page afterwards.
  if (isUnreachable && demoSession) {
    return (
      <div
        role="status"
        data-testid="health-demo-refreshing-banner"
        className="flex items-start gap-2 border-b border-[var(--color-border)] bg-[var(--color-bg-secondary)] px-4 py-2 text-sm text-[var(--color-text-secondary)]"
      >
        <RefreshCw className="mt-0.5 h-4 w-4 flex-shrink-0 text-[var(--color-accent)]" aria-hidden="true" />
        <div>
          <strong className="font-semibold text-[var(--color-text)]">The live demo is refreshing.</strong>{' '}
          Its sample data is rebuilt every night at 03:00 UTC and after maintenance, which takes a few
          minutes. Retrying automatically; if you are signed out when it is back, choose Explore the demo
          again.
        </div>
      </div>
    )
  }

  // Checked BEFORE isDegraded: when the health poll itself fails we have no
  // check results, so `unavailable` is empty and the dependency banner below
  // would render nothing at all. "We cannot reach the backend" outranks any
  // per-dependency list, and it is the louder outage.
  if (isUnreachable) {
    return (
      <div
        role="alert"
        data-testid="health-unreachable-banner"
        className="flex items-start gap-2 border-b border-[var(--status-broken-bd)]/40 bg-[var(--status-broken-bg)]/10 px-4 py-2 text-sm text-[var(--status-broken)]"
      >
        <AlertTriangle className="mt-0.5 h-4 w-4 flex-shrink-0 text-[var(--status-broken)]" />
        <div>
          <strong className="font-semibold">Can't reach the TestLookup backend.</strong>{' '}
          Health status is unknown, not healthy — pages may show stale data or
          none at all. Retrying automatically.
        </div>
      </div>
    )
  }

  if (!isDegraded) return null

  // A critical store (PostgreSQL / MongoDB / Redis) being unreachable is a
  // different, louder outage than an optional dependency degrading: it loses
  // the pages' own data, not a feature. The optional-degradation copy below
  // reassures that pages "will show data from PostgreSQL where possible" —
  // which is false during a critical outage and self-contradictory when
  // PostgreSQL is the very store that is down. Lead with the critical failure,
  // as an alert, and drop the fallback promise.
  if (criticalUnavailable.length > 0) {
    const criticalList = criticalUnavailable.map(label).join(', ')
    return (
      <div
        role="alert"
        data-testid="health-critical-banner"
        className="flex items-start gap-2 border-b border-[var(--status-broken-bd)]/40 bg-[var(--status-broken-bg)]/10 px-4 py-2 text-sm text-[var(--status-broken)]"
      >
        <AlertTriangle className="mt-0.5 h-4 w-4 flex-shrink-0 text-[var(--status-broken)]" />
        <div>
          <strong className="font-semibold">Critical services unreachable:</strong> {criticalList}.
          Core data is unavailable — pages may be empty or fail to load until these recover.
        </div>
      </div>
    )
  }

  const list = unavailable.map(label).join(', ')

  return (
    <div
      role="status"
      className="flex items-start gap-2 border-b border-[var(--status-broken-bd)]/40 bg-[var(--status-broken-bg)]/10 px-4 py-2 text-sm text-[var(--status-broken)]"
    >
      <AlertTriangle className="mt-0.5 h-4 w-4 flex-shrink-0 text-[var(--status-broken)]" />
      <div>
        <strong className="font-semibold">Degraded:</strong> {list} unreachable.
        Pages relying on these will show data from PostgreSQL where possible.
      </div>
    </div>
  )
}
