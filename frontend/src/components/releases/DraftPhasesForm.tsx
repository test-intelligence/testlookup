/**
 * Draft from a PRD… (W11 REL-07): paste a PRD (or choose a .md / .txt /
 * .docx), get a DRAFT of the release's test phases, edit it, then create
 * them — in the page (an `InlineFormSection` at the top of "Release Phases").
 *
 * The server's heading parser always drafts; the AI model does when the
 * deployment allows it, and any model failure is the rules draft with its
 * reason on the badge ("Drafted by rules — the AI model did not answer in
 * time"), never an error. Nothing is saved before Create, which sends ONE
 * bulk request. The PRD text lives in this form's state only: never the URL,
 * never storage, never a log. Every drafted name and description is React
 * TEXT (an input's value), never HTML.
 */
import { useState } from 'react'
import toast from 'react-hot-toast'
import { InlineFormSection } from '@/components/ui/inPage/InlineFormSection'
import type { InlineSlot } from '@/components/ui/inPage/useInlineSlot'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import { extractErrorMessage } from '@/services/apiErrors'
import { releasesService } from '@/services/releasesService'
import type { PhaseDraftResult, Release, ReleasePhasesCreated } from '@/types/releases'
import {
  bulkBody,
  createLabel,
  createdToast,
  draftBadge,
  emptyRow,
  MAX_DAYS,
  MIN_DAYS,
  NO_PHASES_DRAFTED,
  PHASE_TYPE_OPTIONS,
  rowsFromDraft,
  validateRows,
  type DraftRow,
} from './draftPhasesModel'

function failureText(err: unknown, fallback: string): string {
  const e = err as { message?: string; response?: { data?: { detail?: unknown } } }
  return extractErrorMessage(e?.response?.data?.detail, e?.message || fallback)
}

export interface DraftPhasesFormProps {
  release: Pick<Release, 'id' | 'name'>
  slot?: InlineSlot
  onDone: (result: ReleasePhasesCreated) => void
  onCancel: () => void
}

export default function DraftPhasesForm({ release, slot, onDone, onCancel }: DraftPhasesFormProps) {
  const [text, setText] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [drafting, setDrafting] = useState(false)
  const [result, setResult] = useState<PhaseDraftResult | null>(null)
  const [rows, setRows] = useState<DraftRow[]>([])
  const [firstStart, setFirstStart] = useState('')
  const [creating, setCreating] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const idBase = `draft-phases-${release.id}`
  const busy = drafting || creating
  const blocked = result ? validateRows(rows) : null

  async function draft() {
    setDrafting(true)
    setError(null)
    try {
      const answer = await releasesService.draftPhases(release.id, file ? { file } : { text })
      setResult(answer)
      setRows(rowsFromDraft(answer.phases ?? []))
    } catch (err: unknown) {
      setError(failureText(err, 'The PRD could not be drafted.'))
    } finally {
      setDrafting(false)
    }
  }

  async function create() {
    if (!result || blocked) return
    setCreating(true)
    setError(null)
    try {
      const created = await releasesService.createPhasesBulk(release.id, bulkBody(rows, result, firstStart || null))
      toast.success(createdToast(created.created))
      onDone(created)
    } catch (err: unknown) {
      setError(failureText(err, 'The phases could not be created.'))
    } finally {
      setCreating(false)
    }
  }

  function chooseFile(next: File | null) {
    setFile(next)
    if (next) setText('')
    setError(null)
  }

  function update(key: string, patch: Partial<DraftRow>) {
    setRows(current => current.map(row => (row.key === key ? { ...row, ...patch } : row)))
  }

  const inputFooter = (
    <>
      <button type="button" onClick={onCancel} disabled={busy} className="btn-secondary btn-sm">Cancel</button>
      <button
        type="button"
        onClick={() => void draft()}
        disabled={busy || (!file && text.trim() === '')}
        className="btn-primary btn-sm inline-flex items-center gap-2"
      >
        {drafting && <LoadingSpinner size="sm" />}
        {drafting ? 'Drafting…' : 'Draft phases'}
      </button>
    </>
  )

  const previewFooter = (
    <>
      <button type="button" onClick={onCancel} disabled={busy} className="btn-secondary btn-sm">Cancel</button>
      <button
        type="button"
        onClick={() => { setResult(null); setRows([]); setError(null) }}
        disabled={busy}
        className="btn-secondary btn-sm"
      >
        Back to the text
      </button>
      <button
        type="button"
        onClick={() => void create()}
        disabled={busy || blocked !== null}
        className="btn-primary btn-sm inline-flex items-center gap-2"
      >
        {creating && <LoadingSpinner size="sm" />}
        {createLabel(rows.length)}
      </button>
    </>
  )

  return (
    <InlineFormSection
      title="Draft phases from a PRD"
      titleId="draft-phases-title"
      headingLevel={3}
      closeLabel="Close draft phases"
      onClose={onCancel}
      canClose={!busy}
      slot={slot}
      busy={busy}
      dataAttributes={{ 'data-draft-phases-form': release.id }}
      footer={result ? previewFooter : inputFooter}
    >
      {!result ? (
        <div className="space-y-3">
          <div>
            <label htmlFor={`${idBase}-text`} className="mb-1 block text-xs text-[var(--color-text-muted)]">PRD text</label>
            <textarea
              id={`${idBase}-text`}
              rows={12}
              value={text}
              onChange={e => { setText(e.target.value); if (file) setFile(null) }}
              placeholder="Paste the PRD. Its ## headings become the phases."
              className="input w-full resize-y font-mono text-xs"
            />
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <label htmlFor={`${idBase}-file`} className="btn-secondary btn-sm cursor-pointer">Choose file</label>
            <input
              id={`${idBase}-file`}
              type="file"
              accept=".md,.txt,.docx"
              className="sr-only"
              onChange={e => chooseFile(e.target.files?.[0] ?? null)}
            />
            {file ? (
              <span data-draft-file="" className="min-w-0 truncate text-xs text-[var(--color-text-secondary)]" title={file.name}>{file.name}</span>
            ) : (
              <span className="text-xs text-[var(--color-text-muted)]">.md, .txt or .docx, up to 200 KB</span>
            )}
          </div>
        </div>
      ) : (
        <div className="space-y-3">
          <p data-draft-badge="" className="m-0 inline-flex rounded-md border border-[var(--color-border)] px-2 py-0.5 text-xs font-medium text-[var(--color-text-secondary)]">
            {draftBadge(result)}
          </p>
          {(result.warnings ?? []).length > 0 && (
            <ul aria-label="Draft warnings" className="m-0 list-none space-y-1 p-0">
              {(result.warnings ?? []).map(w => (
                <li key={w} className="text-xs text-[var(--ui-warning-ink)]">{w}</li>
              ))}
            </ul>
          )}
          {rows.length === 0 && <p className="text-sm text-[var(--color-text-muted)]">{NO_PHASES_DRAFTED}</p>}
          {rows.length > 0 && (
            <div className="space-y-2" data-draft-rows="">
              <div aria-hidden="true" className="grid grid-cols-[minmax(0,1fr)_140px_72px_auto] gap-2 text-xs text-[var(--color-text-muted)]">
                <span>Name</span><span>Type</span><span>Days</span><span />
              </div>
              {rows.map((row, i) => (
                <div key={row.key} data-draft-row="" className="space-y-1 rounded-md border border-[var(--color-border)] p-2">
                  <div className="grid grid-cols-[minmax(0,1fr)_140px_72px_auto] items-center gap-2">
                    <input
                      aria-label={`Name of phase ${i + 1}`}
                      value={row.name}
                      maxLength={255}
                      onChange={e => update(row.key, { name: e.target.value })}
                      className="input w-full"
                    />
                    <select
                      aria-label={`Type of phase ${i + 1}`}
                      value={row.phaseType}
                      onChange={e => update(row.key, { phaseType: e.target.value })}
                      className="input w-full"
                    >
                      {PHASE_TYPE_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                    </select>
                    <input
                      aria-label={`Days of phase ${i + 1}`}
                      type="number"
                      min={MIN_DAYS}
                      max={MAX_DAYS}
                      value={row.days}
                      onChange={e => update(row.key, { days: e.target.value })}
                      className="input w-full"
                    />
                    <button
                      type="button"
                      aria-label={`Remove phase ${i + 1}`}
                      onClick={() => setRows(current => current.filter(r => r.key !== row.key))}
                      className="btn-secondary btn-sm"
                    >
                      Remove
                    </button>
                  </div>
                  <textarea
                    aria-label={`Description of phase ${i + 1}`}
                    value={row.description}
                    rows={2}
                    maxLength={4000}
                    onChange={e => update(row.key, { description: e.target.value })}
                    className="input w-full resize-y text-xs"
                  />
                </div>
              ))}
            </div>
          )}
          <div className="flex flex-wrap items-end justify-between gap-3">
            <button type="button" onClick={() => setRows(current => [...current, emptyRow()])} className="btn-secondary btn-sm">
              Add a row
            </button>
            <div>
              <label htmlFor={`${idBase}-start`} className="mb-1 block text-xs text-[var(--color-text-muted)]">First phase starts (optional)</label>
              <input
                id={`${idBase}-start`}
                type="date"
                value={firstStart}
                onChange={e => setFirstStart(e.target.value)}
                className="input"
              />
            </div>
          </div>
          {firstStart && (
            <p className="text-xs text-[var(--color-text-muted)]">
              The phases get consecutive windows of their Days (a phase without Days counts 5), as UTC days.
            </p>
          )}
          {blocked && <p data-draft-blocked="" className="text-sm text-[var(--ui-warning-ink)]">{blocked}</p>}
        </div>
      )}
      {error && <p role="alert" data-draft-phases-error="" className="mt-3 text-sm text-[var(--ui-danger-ink)]">{error}</p>}
    </InlineFormSection>
  )
}
