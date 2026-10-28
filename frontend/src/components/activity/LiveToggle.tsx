/**
 * The /activity Live toggle and its status line (W21d ACT-02).
 *
 * The toggle is a plain pressed / not-pressed button, LAST in the feed's
 * filter row: an 8 px dot (the accent while the stream is live, muted
 * otherwise) and the word Live. The status line is its own line under the
 * row, shown only while Live is on and NOT streaming — Connecting…,
 * Reconnecting…, the unavailable sentence, or a refusal's own sentence — so a
 * missing stream is always said out loud, never shown as a quiet project.
 * Server sentences render as React text.
 */
import { clsx } from 'clsx'

import type { LiveState } from '@/hooks/useActivityLive'
import { LIVE_STATUS_TEXT, STREAM_ERROR_SENTENCES, type LiveStatus } from '@/types/activityLive'

/** What the status line says for a status (null = no line: off, or live). */
function liveStatusSentence(status: LiveStatus, message: string | null): string | null {
  if (status === 'off' || status === 'live') return null
  if (status === 'unavailable') return message ?? STREAM_ERROR_SENTENCES.live_unavailable
  if (status === 'refused') return message ?? LIVE_STATUS_TEXT.refused
  return LIVE_STATUS_TEXT[status]
}

export default function LiveToggle({
  on,
  status,
  onToggle,
}: {
  on: boolean
  status: LiveStatus
  onToggle: (next: boolean) => void
}) {
  const streaming = on && status === 'live'
  return (
    <button
      type="button"
      aria-pressed={on}
      data-activity-live=""
      data-live-status={on ? status : 'off'}
      title={on ? 'Stop streaming new activity' : 'Show new activity as it happens'}
      onClick={() => onToggle(!on)}
      className={clsx(
        // Compact (px-1.5 / gap-1, ≈ 53 px): /activity sits in the settings column, 892 px at 1440, and
        // measured on DejaVu Sans the one-line filter row keeps ≥ 40 px to spare with it (at 1280, 732 px,
        // the row already wraps and the toggle sits on its second line).
        'btn-secondary inline-flex items-center gap-1 px-1.5 py-1.5 font-medium',
        on && 'bg-[var(--color-accent-muted)]',
      )}
    >
      <span
        aria-hidden="true"
        className="h-2 w-2 shrink-0 rounded-full"
        style={{ background: streaming ? 'var(--color-accent)' : 'var(--color-text-muted)' }}
      />
      Live
    </button>
  )
}

/** The one line under the filter row while Live is on and not streaming. */
export function LiveStatusLine({ status, message }: LiveState) {
  const sentence = liveStatusSentence(status, message)
  if (!sentence) return null
  return (
    <p role="status" data-activity-live-status={status} className="-mt-4 text-xs text-[var(--color-text-muted)]">
      {sentence}
    </p>
  )
}
