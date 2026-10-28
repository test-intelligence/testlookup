/**
 * The keep-last floor field of the retention policy form (W22a ACT-04):
 * `Always keep the last N runs (0–1000)`. The scheduled purge never deletes
 * the newest N runs of the project, and always keeps the latest passing run of
 * the project and of each suite (even at 0). Explicit deletions are not
 * limited by it.
 *
 * Controlled: the page owns the value (a string, so typing is never fought),
 * validates it and sends `keep_last_runs` only when it changed.
 */
import {
  FLOOR_FIELD_HELP,
  FLOOR_FIELD_LABEL,
  KEEP_LAST_RUNS_MAX,
  KEEP_LAST_RUNS_MIN,
} from './holdsModel'

export interface RetentionFloorFieldProps {
  value: string
  error?: string | null
  onChange: (value: string) => void
}

export default function RetentionFloorField({ value, error, onChange }: RetentionFloorFieldProps) {
  return (
    <div data-retention-floor="" className="grid gap-3 border-t border-[var(--color-border)] pt-3 sm:grid-cols-2">
      <label htmlFor="retention-keep_last_runs" className="block text-xs">
        <span className="text-[var(--color-text-muted)]">{FLOOR_FIELD_LABEL}</span>
        <input
          id="retention-keep_last_runs"
          type="number"
          inputMode="numeric"
          min={KEEP_LAST_RUNS_MIN}
          max={KEEP_LAST_RUNS_MAX}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          aria-invalid={Boolean(error)}
          className={`mt-1 w-full rounded border bg-[var(--color-bg-secondary)] px-2 py-1.5 text-sm ${
            error ? 'border-[var(--ui-danger-bd)]' : 'border-[var(--color-border)]'
          }`}
        />
        {error ? (
          <span className="mt-1 block text-[var(--ui-danger-ink)]">{error}</span>
        ) : (
          <span className="mt-1 block text-[var(--color-text-faint)]">{FLOOR_FIELD_HELP}</span>
        )}
      </label>
    </div>
  )
}
