import { FlaskConical } from 'lucide-react'

/**
 * Small "Experimental" badge rendered next to a page header when the
 * feature is flagged as experimental in the OSS stability labels.
 *
 * Usage:
 *   <PageHeader title="Quarantine" actions={<ExperimentalBadge />} />
 *
 * Or inline:
 *   <div className="flex items-center gap-2">
 *     <h2>LLM Cost Budget</h2>
 *     <ExperimentalBadge />
 *   </div>
 */
export default function ExperimentalBadge() {
  return (
    <span
      className="inline-flex items-center gap-1 text-[10px] px-2 py-0.5 rounded-full border border-amber-500/40 text-amber-400 bg-amber-500/10"
      title="This feature is experimental and may change or be removed. It is off by default and must be enabled via Settings &gt; Feature Flags."
    >
      <FlaskConical className="h-3 w-3" />
      Experimental
    </span>
  )
}
