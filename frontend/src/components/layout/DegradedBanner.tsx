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
  const { unavailable, isDegraded } = useSystemHealth()
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
