/**
 * W03 AIH-04: the Analysis tab's "History" control (contract §9.3). The header line names the version shown
 * (`v3 · LLM llama3.1 · about 2 hours ago`, a `deterministic` chip when no model wrote it); the first select picks
 * the version to view ("Current" = the live analysis), the second opens the diff against another version or the
 * current data. Selecting is all it does: the caller writes the URL (`aiHistoryUrl.ts`).
 */
import { History } from 'lucide-react'
import type { AIVersionSummary } from '@/types/aiHistory'
import { FIRST_ANALYSIS_TEXT, versionLabel } from './aiDigestDiff.model'

export interface AIVersionPickerProps {
  /** Newest first (as listed). */
  versions: AIVersionSummary[]
  latestVersion: number | null
  /** The viewed version; null = Current. */
  selected: number | null
  onSelect: (version: number | null) => void
  /** The open diff's other side; null = none. */
  compare: number | 'live' | null
  onCompare: (version: number | 'live' | null) => void
  /** The list is still loading: the selects are disabled. */
  loading?: boolean
}

export default function AIVersionPicker({
  versions,
  latestVersion,
  selected,
  onSelect,
  compare,
  onCompare,
  loading = false,
}: AIVersionPickerProps) {
  const ordered = [...versions].sort((a, b) => b.version - a.version)
  if (!loading && ordered.length === 0) {
    return (
      <div data-ai-version-picker="" className="flex items-center gap-2 text-[12px] text-[var(--color-text-muted)]">
        <History aria-hidden className="h-3.5 w-3.5" />
        <span data-ai-version-empty="">
          {FIRST_ANALYSIS_TEXT}: earlier analyses appear here after the run is analysed again.
        </span>
      </div>
    )
  }
  const shown = ordered.find((v) => v.version === (selected ?? latestVersion)) ?? ordered[0] ?? null
  // Comparing the current data with itself says nothing: "Current data" is offered only while a stored version is viewed.
  const compareOptions = ordered.filter((v) => v.version !== selected)
  return (
    <div data-ai-version-picker="" className="flex flex-wrap items-center gap-x-3 gap-y-1.5 text-[12px]">
      <span className="inline-flex items-center gap-1.5 text-[var(--color-text-muted)]">
        <History aria-hidden className="h-3.5 w-3.5" />
        <span className="font-semibold uppercase tracking-wide text-[11px]">AI history</span>
      </span>
      <span data-ai-version-meta="" className="font-medium text-[var(--color-text)]">
        {shown ? versionLabel(shown) : 'Loading versions…'}
        {selected == null && shown && <> <span className="font-normal text-[var(--color-text-muted)]">(current)</span></>}
      </span>
      {shown?.deterministic && (
        <span
          data-ai-version-deterministic=""
          className="rounded-full border border-[var(--color-border)] px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-[var(--color-text-muted)]"
        >
          deterministic
        </span>
      )}
      <select
        data-ai-version-select=""
        aria-label="AI analysis version"
        disabled={loading}
        value={selected == null ? '' : String(selected)}
        onChange={(event) => {
          const value = Number(event.target.value)
          onSelect(event.target.value === '' || !Number.isInteger(value) ? null : value)
        }}
        className="max-w-[320px] rounded border border-[var(--color-border)] bg-[var(--color-bg-card)] px-2 py-1 text-[12px] text-[var(--color-text)] disabled:opacity-50"
      >
        <option value="">Current</option>
        {ordered.map((v) => (
          <option key={v.version_id} value={String(v.version)}>{versionLabel(v)}</option>
        ))}
      </select>
      <select
        data-ai-compare-select=""
        aria-label="Compare with"
        disabled={loading || (compareOptions.length === 0 && selected == null)}
        value={compare == null ? '' : String(compare)}
        onChange={(event) => {
          const raw = event.target.value
          const value = Number(raw)
          onCompare(raw === '' ? null : raw === 'live' ? 'live' : Number.isInteger(value) ? value : null)
        }}
        className="max-w-[320px] rounded border border-[var(--color-border)] bg-[var(--color-bg-card)] px-2 py-1 text-[12px] text-[var(--color-text)] disabled:opacity-50"
      >
        <option value="">Compare with…</option>
        {compareOptions.map((v) => (
          <option key={v.version_id} value={String(v.version)}>{versionLabel(v)}</option>
        ))}
        {selected != null && <option value="live">Current data</option>}
      </select>
    </div>
  )
}
