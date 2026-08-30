import { AlertTriangle } from 'lucide-react'
import { useSystemHealth } from '@/hooks/useSystemHealth'

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
  const { unavailable, isDegraded, isUnreachable } = useSystemHealth()

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
