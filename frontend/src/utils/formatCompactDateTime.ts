// No date-fns here: the eager TopBar uses this, and date-fns' `format` pulls
// its whole token engine (~19 KB raw) onto every route for one fixed pattern.
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
const pad2 = (n: number) => String(n).padStart(2, '0')

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
  // date-fns 'MMM d, HH:mm', local time: 'Aug 8, 09:05'.
  return `${MONTHS[date.getMonth()]} ${date.getDate()}, ${pad2(date.getHours())}:${pad2(date.getMinutes())}`
}
