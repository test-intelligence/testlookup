/**
 * RetentionScheduleField — the purge window and the pause of the Retention
 * policy card (W22b ACT-06).
 *
 * Row 1: `Purge window (UTC)`, a select of the 24 hours, saved with the rest
 * of the form by `Save policy` (sent only when it changed), with its
 * inherited marker. Row 2: the status line — `Next scheduled purge …`,
 * `Paused until …` (+ a `Paused` chip) or `Scheduled purging is off for this
 * project.` — then `Pause 7 days` / `Resume`, which send their OWN PUT at
 * once (`{pause_days: 7}` / `{resume: true}`), are disabled while the form has
 * unsaved changes, and hand the saved policy back.
 *
 * Rendered by the page only when the server reports the window (an older
 * server shows nothing new). In-page only, React text only.
 */
import { useState } from 'react'
import { isAxiosError } from 'axios'
import toast from 'react-hot-toast'
import { updateRetentionPolicy } from '@/hooks/useRetentionPolicy'
import type { RetentionPolicy } from '@/types/retention'
import InheritedMarker from './InheritedMarker'
import { toneStyle, WARNING_TONE } from './exportModel'
import {
  DIRTY_TITLE,
  HOURS,
  PAUSE_DAYS_DEFAULT,
  PAUSE_LABEL,
  PAUSED_CHIP_LABEL,
  RESUME_LABEL,
  scheduleLine,
  WINDOW_FIELD_HELP,
  WINDOW_FIELD_LABEL,
  type FieldMode,
} from './scheduleModel'

export interface RetentionScheduleFieldProps {
  projectId: string
  /** The SAVED policy: sources, inherit values, pause, next purge. */
  policy: RetentionPolicy
  /** The form's window hour ('0' … '23'). */
  value: string
  /** The form's mode for the window (inherit / own); undefined without sources. */
  mode?: FieldMode
  /** The form has unsaved changes: the pause controls wait for Save / discard. */
  dirty: boolean
  onChange: (value: string) => void
  onOverride: () => void
  onReset: () => void
  onSaved: (saved: RetentionPolicy) => void
}

export default function RetentionScheduleField({
  projectId,
  policy,
  value,
  mode,
  dirty,
  onChange,
  onOverride,
  onReset,
  onSaved,
}: RetentionScheduleFieldProps) {
  const [busy, setBusy] = useState(false)
  const line = scheduleLine(policy)
  const paused = line.state === 'paused'
  const inherited = mode === 'inherit'

  async function send(body: { pause_days: number } | { resume: true }) {
    if (busy || dirty) return
    setBusy(true)
    try {
      const saved = await updateRetentionPolicy(projectId, body)
      onSaved(saved)
      toast.success(paused ? 'Scheduled purging resumed' : 'Scheduled purging paused')
    } catch (err) {
      // Axios failures already toast the server's sentence via the shared interceptor.
      if (!isAxiosError(err)) toast.error(`Failed to save: ${(err as Error).message}`)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div data-retention-schedule="" className="space-y-2 border-t border-[var(--color-border)] pt-3">
      <label htmlFor="retention-purge_window_start_utc" className="text-xs block">
        <span id="retention-purge-window-label" className="text-[var(--color-text-muted)]">
          {WINDOW_FIELD_LABEL}
        </span>
        <select
          id="retention-purge_window_start_utc"
          aria-labelledby="retention-purge-window-label"
          value={value}
          disabled={inherited}
          onChange={(e) => onChange(e.target.value)}
          className="mt-1 block w-32 px-2 py-1.5 text-sm bg-[var(--color-bg-secondary)] border border-[var(--color-border)] rounded disabled:cursor-not-allowed disabled:opacity-60"
        >
          {HOURS.map((hour) => (
            <option key={hour.value} value={String(hour.value)}>
              {hour.label}
            </option>
          ))}
        </select>
        <span className="mt-1 block text-[var(--color-text-faint)]">{WINDOW_FIELD_HELP}</span>
        <InheritedMarker
          field="purge_window_start_utc"
          policy={policy}
          mode={mode}
          onOverride={onOverride}
          onReset={onReset}
        />
      </label>
      <div data-retention-schedule-status="" className="flex flex-wrap items-center gap-x-3 gap-y-2 text-xs">
        {paused && (
          <span
            data-retention-paused=""
            className="shrink-0 rounded-full border px-2 py-0.5 text-xs"
            style={toneStyle(WARNING_TONE)}
          >
            {PAUSED_CHIP_LABEL}
          </span>
        )}
        {line.text && <span className="text-[var(--color-text)]">{line.text}</span>}
        <button
          type="button"
          onClick={() => void send(paused ? { resume: true } : { pause_days: PAUSE_DAYS_DEFAULT })}
          disabled={busy || dirty}
          title={dirty ? DIRTY_TITLE : undefined}
          className="btn-secondary btn-sm shrink-0 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {paused ? RESUME_LABEL : PAUSE_LABEL}
        </button>
      </div>
    </div>
  )
}
