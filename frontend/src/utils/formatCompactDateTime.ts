import { format } from 'date-fns'

/**
 * Compact wall-clock stamp for dense table cells: 'Aug 28, 14:32'.
 *
 * Distinct from `formatDateTime` ('MMM dd') — this uses a non-padded day so the
 * cell stays narrow for single-digit dates ('Aug 8', not 'Aug 08'). Full-length
 * `toLocaleString()` values were what pushed the Started/End columns off-screen
 * on /intelligence, /runs and /live; callers pair this with `isoTooltip` so the
 * exact instant is still one hover away.
 *
 * Missing/unparseable input returns the em-dash placeholder rather than
 * 'Invalid Date'.
 */
export const formatCompactDateTime = (d?: string | Date | null): string => {
  if (!d) return '—'
  const date = new Date(d)
  if (Number.isNaN(date.getTime())) return '—'
  return format(date, 'MMM d, HH:mm')
}
