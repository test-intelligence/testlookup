import { useState } from 'react'

import { appSettingsService, type EmbeddingIndex } from '@/services/appSettingsService'
import {
  EMBEDDING_SAVE_WARNING,
  EMBEDDING_STATE_UNREADABLE,
  REBUILD_NOT_STARTED,
  refusalSentence,
  statusLines,
} from './embeddingIndexModel'

const ACTION_LABELS = { reembed: 'Re-embed now', retry: 'Retry' } as const

interface EmbeddingIndexStatusProps {
  /** The latest embedding index (`useEmbeddingIndex().index`); null = the server could not read it. */
  index: EmbeddingIndex | null
  /** The last poll failed: the state shown is the last one known. */
  pollFailed?: boolean
  /** Re-embed now / Retry are ADMIN actions (`POST /settings/ai/embedding-index/rebuild`). */
  isAdmin: boolean
  /** The form's embedder differs from the saved one: Save will re-embed (admin only). */
  warning?: boolean
  /** A changed embedder's 422 sentence from Save. */
  saveRefusal?: string | null
  /** Show a rebuild's answer (it is `building`, so the poll starts). */
  onIndex: (next: EmbeddingIndex) => void
}

/**
 * The Embedding card's state block (W12 SRCH-02, contract §2.4): what is in use, what is configured but not
 * applied, a re-embed's progress per family, a failed build's reason, and the admin's Re-embed now / Retry.
 * Every string is React text (model names and `last_error` come from the server); in-page only.
 */
export default function EmbeddingIndexStatus({
  index,
  pollFailed = false,
  isAdmin,
  warning = false,
  saveRefusal = null,
  onIndex,
}: EmbeddingIndexStatusProps) {
  const [starting, setStarting] = useState(false)
  const [refusal, setRefusal] = useState<string | null>(null)
  const lines = statusLines(index)
  const action = isAdmin ? lines.action : null

  async function rebuild() {
    setStarting(true)
    setRefusal(null)
    try {
      onIndex(await appSettingsService.rebuildEmbeddingIndex())
    } catch (err) {
      setRefusal(refusalSentence(err, [409, 422, 503]) ?? REBUILD_NOT_STARTED)
    } finally {
      setStarting(false)
    }
  }

  const shownRefusal = saveRefusal ?? refusal
  return (
    <div className="space-y-3">
      {warning && isAdmin && (
        <p className="text-xs text-[var(--ui-warning-ink)]" data-embedding-save-warning="">
          {EMBEDDING_SAVE_WARNING}
        </p>
      )}
      <div data-embedding-index="" data-state={lines.kind} className="space-y-1 border-t border-[var(--color-border)] pt-3">
        <p className={`text-sm break-words ${lines.kind === 'failed' ? 'text-[var(--ui-danger-ink)]' : 'text-[var(--color-text)]'}`}>
          {/* Each embedder name is one unit: a narrow card moves it to the next line whole, never splits it at a
              hyphen (`nomic-embed-` / `text:v1.5`). The parts join to exactly `lines.lead`. */}
          {lines.leadParts.map((part, i) =>
            part.nowrap ? (
              <span key={i} className="whitespace-nowrap" data-embedder-name="">{part.text}</span>
            ) : (
              part.text
            ),
          )}
        </p>
        {lines.since && <p className="text-xs text-[var(--color-text-muted)]">{lines.since}</p>}
        {lines.families && <p className="text-xs text-[var(--color-text-muted)] break-words">{lines.families}</p>}
        {lines.stalled && (
          <p className="text-xs text-[var(--ui-warning-ink)] break-words" data-embedding-stalled="">{lines.stalled}</p>
        )}
        {lines.note && <p className="text-xs text-[var(--color-text-muted)] break-words">{lines.note}</p>}
        {pollFailed && lines.kind !== 'unreadable' && (
          <p className="text-xs text-[var(--color-text-muted)]">{EMBEDDING_STATE_UNREADABLE} The last known state is shown.</p>
        )}
        {action && (
          <div className="pt-1">
            <button type="button" className="btn-secondary btn-sm" disabled={starting} onClick={() => { void rebuild() }}>
              {ACTION_LABELS[action]}
            </button>
          </div>
        )}
        {shownRefusal && (
          <p className="text-xs text-[var(--ui-danger-ink)] break-words" data-embedding-refusal="">
            {shownRefusal}
          </p>
        )}
      </div>
    </div>
  )
}
