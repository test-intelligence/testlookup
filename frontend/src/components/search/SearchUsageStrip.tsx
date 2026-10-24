/**
 * W12 SRCH-05 — today's search usage, under the verdict ribbon on /search, for
 * a QA lead or an admin (the page mounts it only for them, so other roles send
 * no request): Searches today, Zero-result rate, p95 latency, from
 * `GET /search/usage?days=1`. A day with no search reads "Not measured yet",
 * never "0 %". No query text exists behind these numbers (a keyed hash).
 */
import MetricCard from '@/components/ui/MetricCard'
import type { SearchUsage } from '@/types/search'
import { tileValues } from './usageModel'

export default function SearchUsageStrip({
  usage, loading, failed,
}: {
  usage: SearchUsage | undefined
  loading: boolean
  failed: boolean
}) {
  const tiles = tileValues(usage, failed && !usage)
  return (
    <section aria-label="Search usage" data-search-usage="" className="mb-3.5 grid grid-cols-3 gap-3">
      {tiles.map((tile) => (
        <MetricCard
          key={tile.key}
          size="sm"
          title={tile.title}
          loading={loading && !usage && !failed}
          metric={{ value: tile.value }}
          caption={tile.caption}
          data-usage-tile={tile.key}
        />
      ))}
    </section>
  )
}
