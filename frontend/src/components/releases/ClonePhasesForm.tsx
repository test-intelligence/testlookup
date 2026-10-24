/**
 * Copy phases from… (W11 REL-05): copy another release's phases into a
 * release that has none, in the page (an `InlineFormSection` at the top of
 * the release's "Release Phases", W27's in-page rule).
 *
 * The sources are the same project's releases that have phases, from the
 * list the page already holds (no request of its own). The preview shows the
 * phases as they will land: name · type · days, the planned dates moved so
 * the first phase starts on "First phase starts" (asked only when a source
 * phase has a date). Every copied phase starts Pending. A refusal (the
 * release gained phases meanwhile, a source that is gone) is the server's
 * sentence, shown here as text.
 */
import { useMemo, useState } from 'react'
import toast from 'react-hot-toast'
import { InlineFormSection } from '@/components/ui/inPage/InlineFormSection'
import type { InlineSlot } from '@/components/ui/inPage/useInlineSlot'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import { extractErrorMessage } from '@/services/apiErrors'
import { releasesService } from '@/services/releasesService'
import type { Release, ReleasePhasesCreated } from '@/types/releases'
import { useNow } from '@/hooks/useNow'
import {
  cloneSources,
  copiedToast,
  defaultFirstStart,
  hasDatedPhases,
  shiftTo,
  shiftedPreview,
  type ReleaseWithCutoff,
} from './clonePhasesModel'
import { PHASE_TYPE_OPTIONS } from './draftPhasesModel'

export const NO_CLONE_SOURCE = 'No other release of this project has phases to copy.'

function typeLabel(value: string): string {
  return PHASE_TYPE_OPTIONS.find(o => o.value === value)?.label ?? value
}

function failureText(err: unknown): string {
  const e = err as { message?: string; response?: { data?: { detail?: unknown } } }
  return extractErrorMessage(e?.response?.data?.detail, e?.message || 'The phases could not be copied.')
}

export interface ClonePhasesFormProps {
  /** The release that receives the phases (it has none). */
  release: ReleaseWithCutoff
  /** The release list the page holds; the sources are picked from it. */
  releases: readonly Release[]
  slot?: InlineSlot
  onDone: (result: ReleasePhasesCreated) => void
  onCancel: () => void
}

export default function ClonePhasesForm({ release, releases, slot, onDone, onCancel }: ClonePhasesFormProps) {
  const now = useNow()
  const sources = useMemo(() => cloneSources(release, releases), [release, releases])
  const [sourceId, setSourceId] = useState<string>(() => sources[0]?.release.id ?? '')
  const source = sources.find(s => s.release.id === sourceId)?.release ?? null
  const [firstStart, setFirstStart] = useState<string>(() =>
    source ? defaultFirstStart(release, source, new Date(now)) : '')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const dated = source ? hasDatedPhases(source) : false
  const preview = useMemo(() => (source ? shiftedPreview(source, dated ? firstStart : null) : []), [source, dated, firstStart])
  const idBase = `clone-phases-${release.id}`

  function chooseSource(id: string) {
    setSourceId(id)
    setError(null)
    const next = sources.find(s => s.release.id === id)?.release
    if (next) setFirstStart(defaultFirstStart(release, next, new Date(now)))
  }

  async function copy() {
    if (!source) return
    setSaving(true)
    setError(null)
    try {
      const result = await releasesService.clonePhases(release.id, {
        from_release_id: source.id,
        shift_to: shiftTo(source, firstStart),
      })
      toast.success(copiedToast(result.created, source.name))
      onDone(result)
    } catch (err: unknown) {
      setError(failureText(err))
    } finally {
      setSaving(false)
    }
  }

  return (
    <InlineFormSection
      title="Copy phases"
      titleId="clone-phases-title"
      headingLevel={3}
      closeLabel="Close copy phases"
      onClose={onCancel}
      canClose={!saving}
      slot={slot}
      busy={saving}
      dataAttributes={{ 'data-clone-phases-form': release.id }}
      footer={(
        // One wrapping row: a button that does not fit moves to the next line whole, never breaking its text.
        <div className="flex w-full flex-wrap justify-end gap-2">
          <button type="button" onClick={onCancel} disabled={saving} className="btn-secondary btn-sm whitespace-nowrap">Cancel</button>
          <button
            type="button"
            onClick={() => void copy()}
            disabled={saving || !source || (dated && !firstStart)}
            className="btn-primary btn-sm inline-flex items-center gap-2 whitespace-nowrap"
          >
            {saving && <LoadingSpinner size="sm" />}
            Copy phases
          </button>
        </div>
      )}
    >
      {sources.length === 0 ? (
        <p className="text-sm text-[var(--color-text-muted)]">{NO_CLONE_SOURCE}</p>
      ) : (
        <div className="space-y-3">
          <div>
            <label htmlFor={`${idBase}-source`} className="mb-1 block text-xs text-[var(--color-text-muted)]">From release</label>
            <select
              id={`${idBase}-source`}
              value={sourceId}
              onChange={e => chooseSource(e.target.value)}
              className="input w-full"
            >
              {sources.map(s => <option key={s.release.id} value={s.release.id}>{s.label}</option>)}
            </select>
          </div>
          {dated && (
            <div>
              <label htmlFor={`${idBase}-start`} className="mb-1 block text-xs text-[var(--color-text-muted)]">First phase starts</label>
              <input
                id={`${idBase}-start`}
                type="date"
                value={firstStart}
                onChange={e => setFirstStart(e.target.value)}
                className="input w-full"
              />
            </div>
          )}
          {/* Two lines a phase: the FULL name (it wraps, never truncates: the pane is ~380 px) with its type and
              days, then the moved dates. No `m-0` here: it would cancel the section's gap under the list. */}
          <ol aria-label="Phases to copy" className="list-none space-y-1.5 p-0 text-xs">
            {preview.map(row => (
              <li key={row.id} data-clone-preview-row="" className="min-w-0 text-[var(--color-text-secondary)]">
                <div className="min-w-0 break-words">
                  <span className="font-medium text-[var(--color-text)]" title={row.name}>{row.name}</span>
                  <span> · {typeLabel(row.phaseType)}</span>
                  {row.days != null && <span> · {row.days} day{row.days === 1 ? '' : 's'}</span>}
                </div>
                {(row.start || row.end) && (
                  <div data-clone-preview-dates="" className="tabular-nums text-[var(--color-text-muted)]">
                    {row.start ?? '?'} → {row.end ?? '?'}
                  </div>
                )}
              </li>
            ))}
          </ol>
          <p className="text-xs text-[var(--color-text-muted)]">Every copied phase starts as Pending. Dates are UTC days.</p>
        </div>
      )}
      {error && (
        <p role="alert" data-clone-phases-error="" className="mt-3 text-sm text-[var(--ui-danger-ink)]">{error}</p>
      )}
    </InlineFormSection>
  )
}
