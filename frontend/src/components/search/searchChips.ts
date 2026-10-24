/**
 * W12 SRCH-04 — the filter chips' one operation. Pure.
 *
 * The server reports each applied filter with `[start, end)` offsets into the
 * STRIPPED query it parsed (`parsed.filters[*]`), and `q.slice(start, end)` is
 * the filter's own source token (`raw`). Removing a chip cuts exactly that
 * token out and joins what is left with one space, so the rest of the query —
 * quoted values included — is kept as typed.
 */
import type { SearchParsedFilter } from '@/types/search'

export function removeFilter(q: string, filter: Pick<SearchParsedFilter, 'start' | 'end' | 'raw'>): string {
  const stripped = q.trim()
  let { start, end } = filter
  // The offsets belong to the query the server parsed; anything else (a box
  // edited since) is matched by the token itself, and an absent token leaves
  // the query as it is.
  if (!(Number.isInteger(start) && Number.isInteger(end) && start >= 0 && end <= stripped.length && start < end
    && (!filter.raw || stripped.slice(start, end) === filter.raw))) {
    const at = filter.raw ? stripped.indexOf(filter.raw) : -1
    if (at < 0) return stripped
    start = at
    end = at + filter.raw.length
  }
  const before = stripped.slice(0, start).trimEnd()
  const after = stripped.slice(end).trimStart()
  return before && after ? `${before} ${after}` : `${before}${after}`
}
