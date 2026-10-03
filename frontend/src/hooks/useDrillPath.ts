/**
 * The drill path and the open rows panel in the address bar (C5 URL encoding,
 * `contracts/viz/README.md`; VIZ-602).
 *
 *   drill   repeatable `drill=<dimension>~<value>`, top level first
 *   rows    repeatable: FIRST `rows=by~<owner>` (the section that opened the
 *           panel), then `rows=<dimension>~<value>`, the open rows panel's
 *           selectors; absent = closed
 *
 * The owner entry exists because one page can hold several rows hosts that
 * read the one `rows` key (Suite detail: the test x run heatmap and the
 * scatter both open a panel on a lone `test` selector). The selectors alone
 * cannot say whose panel it is; the owner can, so exactly one host opens its
 * panel, for a click and for a pasted link alike. A `rows` list without a
 * valid owner first is not applied (it is read as an invalid level, with the
 * notice): no host could claim it.
 *
 * Reading never throws (a URL is user input). Each entry is split on its FIRST
 * `~` (`test~a~b` is dimension `test`, value `a~b`); `URLSearchParams` does
 * every bit of percent-encoding, nothing here escapes or unescapes. A list is
 * read level by level and TRUNCATED at the first level that breaks a C5 rule
 * (no `~`, an unknown dimension, a repeated dimension, a status outside the
 * vocabulary, an empty or over-long value, a fifth level); and while the
 * encoded `drill` + `rows` parameters are longer than `MAX_DRILL_URL_CHARS`,
 * the deepest drill level is dropped (then the deepest rows selector). What
 * was dropped is returned as sentences for the page's notice.
 *
 * Writing: a drill step and opening the rows panel PUSH a history entry (Back
 * walks up the levels and closes the panel); closing the panel REPLACES (a
 * filter change keeps replacing too, VIZ-306). Only `drill` and `rows` are
 * touched: every other key, its order and its repetition stay as they were.
 * The path is never written into a saved view (plan F17).
 */
import { useCallback, useMemo } from 'react'
import { useSearchParams } from 'react-router-dom'
import { validateDrillPath, type DrillLevel } from '@/lib/viz/contracts'

export const DRILL_URL_KEY = 'drill'
export const ROWS_URL_KEY = 'rows'
/** Past this many characters of encoded `drill` + `rows`, the deepest drill level goes (C5). */
export const MAX_DRILL_URL_CHARS = 6000
/** The separator between a dimension and its value. */
export const DRILL_SEPARATOR = '~'
/** The pseudo-dimension of the `rows` owner entry, `rows=by~<owner>` (never a C5 dimension). */
export const ROWS_OWNER_DIMENSION = 'by'
/** A rows owner: a host's section id (`scatter-suite`, `heatmap-test_run`, ...). */
const ROWS_OWNER_RE = /^[a-z][a-z0-9_-]{0,63}$/

/** Whether `owner` can be written as (and read back from) a `rows` owner entry. */
export function isRowsOwner(owner: unknown): owner is string {
  return typeof owner === 'string' && ROWS_OWNER_RE.test(owner)
}

export const DRILL_DROP_WORDS = {
  invalid: 'Part of the drill-down in this link was not valid, so the view opens at the level above it.',
  tooLong: 'The drill-down in this link was too long, so its deepest level was dropped.',
} as const

export interface DrillState {
  /** The validated drill path, top level first. `[]` at the root. */
  path: DrillLevel[]
  /** The open rows panel's validated selectors. `[]` = closed. Hosts read `ownedRows(state, owner)`. */
  rows: DrillLevel[]
  /** The host that opened the rows panel (`rows=by~<owner>`); `null` when closed. */
  rowsOwner: string | null
  /** What the URL named that was not applied, as sentences for the page's notice. `[]` when nothing. */
  dropped: string[]
  /** Go down: append `levels` below the current path (a history PUSH; closes the rows panel). */
  drill: (levels: readonly DrillLevel[]) => void
  /** Go up: keep the first `depth` levels, `0` = the root (a history PUSH; closes the rows panel). */
  truncate: (depth: number) => void
  /** Open `owner`'s rows panel on `selectors` (a history PUSH, so Back closes it). */
  openRows: (owner: string, selectors: readonly DrillLevel[]) => void
  /** Close the rows panel (a history REPLACE). */
  closeRows: () => void
}

/** One level as its URL value: `suite~payments`. */
export function encodeDrillLevel({ dimension, value }: DrillLevel): string {
  return `${dimension}${DRILL_SEPARATOR}${value}`
}

/** One URL value as a level (split on the FIRST `~`), or `null` when it has none. Not validated here. */
export function decodeDrillLevel(raw: string): { dimension: string; value: string } | null {
  const at = raw.indexOf(DRILL_SEPARATOR)
  if (at < 0) return null
  return { dimension: raw.slice(0, at), value: raw.slice(at + 1) }
}

/**
 * The longest valid prefix of `raws` (C5 rules, `validateDrillPath`), and
 * whether anything after it was dropped.
 */
export function readDrillLevels(raws: readonly string[]): { levels: DrillLevel[]; truncated: boolean } {
  const levels: DrillLevel[] = []
  for (const raw of raws) {
    const level = decodeDrillLevel(raw)
    if (!level || !validateDrillPath({ path: [...levels, level] }).ok) return { levels, truncated: true }
    levels.push(level as DrillLevel)
  }
  return { levels, truncated: false }
}

function encodedLength(path: readonly DrillLevel[], rows: readonly DrillLevel[], owner: string | null): number {
  return writeDrillParams('', { path, rows, rowsOwner: owner ?? undefined }).toString().length
}

export interface ParsedDrillParams {
  path: DrillLevel[]
  rows: DrillLevel[]
  /** The rows panel's owner; `null` whenever `rows` is empty. */
  rowsOwner: string | null
  dropped: string[]
}

/** The owner entry and the selectors after it; a list without a valid owner first is not applied. */
function readRows(raws: readonly string[]): { owner: string | null; levels: DrillLevel[]; truncated: boolean } {
  if (raws.length === 0) return { owner: null, levels: [], truncated: false }
  const first = decodeDrillLevel(raws[0])
  if (!first || first.dimension !== ROWS_OWNER_DIMENSION || !isRowsOwner(first.value)) {
    return { owner: null, levels: [], truncated: true }
  }
  const { levels, truncated } = readDrillLevels(raws.slice(1))
  return levels.length ? { owner: first.value, levels, truncated } : { owner: null, levels, truncated: true }
}

/** No rows selection (one identity, so a closed panel's selectors never look new). */
const NO_ROWS: readonly DrillLevel[] = Object.freeze([])

/**
 * The open rows panel's selectors when `owner` opened it, else `[]`: each host
 * asks with its own id, so one URL never opens two panels. The state's own
 * array is returned (stable while the URL's `rows` do not change).
 */
export function ownedRows(state: { rows: readonly DrillLevel[]; rowsOwner: string | null }, owner: string): readonly DrillLevel[] {
  return state.rowsOwner === owner && state.rows.length > 0 ? state.rows : NO_ROWS
}

/** The drill state a query string carries. Never throws. */
export function parseDrillParams(search: URLSearchParams | string | null | undefined): ParsedDrillParams {
  // `URLSearchParams` decodes any string without throwing (a bad escape stays literal).
  const params = search instanceof URLSearchParams ? search : new URLSearchParams(search ?? '')
  const drill = readDrillLevels(params.getAll(DRILL_URL_KEY))
  const rowsRead = readRows(params.getAll(ROWS_URL_KEY))
  const path = drill.levels
  const rows = rowsRead.levels
  const dropped: string[] = []
  if (drill.truncated || rowsRead.truncated) dropped.push(DRILL_DROP_WORDS.invalid)
  let shortened = false
  while (encodedLength(path, rows, rowsRead.owner) > MAX_DRILL_URL_CHARS && (path.length || rows.length)) {
    if (path.length) path.pop()
    else rows.pop()
    shortened = true
  }
  if (shortened) dropped.push(DRILL_DROP_WORDS.tooLong)
  return { path, rows, rowsOwner: rows.length ? rowsRead.owner : null, dropped }
}

/**
 * `base` with its `drill` and `rows` keys replaced (`undefined` leaves a key
 * alone, `[]` removes it). Every other key keeps its value, order and repetition.
 * Selectors are written after their `rowsOwner` entry; selectors without a
 * valid owner are not written at all (no host could claim them).
 */
export function writeDrillParams(
  base: URLSearchParams | string,
  { path, rows, rowsOwner }: { path?: readonly DrillLevel[]; rows?: readonly DrillLevel[]; rowsOwner?: string },
): URLSearchParams {
  const next = new URLSearchParams(base)
  if (path !== undefined) {
    next.delete(DRILL_URL_KEY)
    for (const level of path) next.append(DRILL_URL_KEY, encodeDrillLevel(level))
  }
  if (rows !== undefined) {
    next.delete(ROWS_URL_KEY)
    if (rows.length && isRowsOwner(rowsOwner)) {
      next.append(ROWS_URL_KEY, `${ROWS_OWNER_DIMENSION}${DRILL_SEPARATOR}${rowsOwner}`)
      for (const level of rows) next.append(ROWS_URL_KEY, encodeDrillLevel(level))
    }
  }
  return next
}

const copy = (levels: readonly DrillLevel[]): DrillLevel[] => levels.map(({ dimension, value }) => ({ dimension, value }))

/** The page's drill path and rows panel, read from and written to the URL. */
export function useDrillPath(): DrillState {
  const [searchParams, setSearchParams] = useSearchParams()
  // Keyed on the two keys' values only (as JSON: a value may hold any
  // character), so an unrelated filter change does not hand the host a new,
  // equal path.
  const drillRaw = JSON.stringify(searchParams.getAll(DRILL_URL_KEY))
  const rowsRaw = JSON.stringify(searchParams.getAll(ROWS_URL_KEY))
  const parsed = useMemo(() => {
    const only = new URLSearchParams()
    for (const value of JSON.parse(drillRaw) as string[]) only.append(DRILL_URL_KEY, value)
    for (const value of JSON.parse(rowsRaw) as string[]) only.append(ROWS_URL_KEY, value)
    return parseDrillParams(only)
  }, [drillRaw, rowsRaw])

  const write = useCallback(
    (next: { path?: readonly DrillLevel[]; rows?: readonly DrillLevel[]; rowsOwner?: string }, replace: boolean) => {
      setSearchParams((current) => writeDrillParams(current, next), { replace })
    },
    [setSearchParams],
  )

  const drill = useCallback(
    (levels: readonly DrillLevel[]) => {
      // A step that would break a rule (a repeated dimension, a fifth level)
      // stops where the valid path stops, exactly as a URL would be read.
      const { levels: path } = readDrillLevels([...parsed.path, ...levels].map(encodeDrillLevel))
      write({ path, rows: [] }, false)
    },
    [parsed.path, write],
  )
  const truncate = useCallback(
    (depth: number) => write({ path: parsed.path.slice(0, Math.max(0, Math.trunc(depth))), rows: [] }, false),
    [parsed.path, write],
  )
  const openRows = useCallback(
    (owner: string, selectors: readonly DrillLevel[]) => {
      const { levels } = readDrillLevels(selectors.map(encodeDrillLevel))
      write({ rows: levels, rowsOwner: owner }, false)
    },
    [write],
  )
  const closeRows = useCallback(() => write({ rows: [] }, true), [write])

  return useMemo(
    () => ({
      path: copy(parsed.path),
      rows: copy(parsed.rows),
      rowsOwner: parsed.rowsOwner,
      dropped: parsed.dropped,
      drill,
      truncate,
      openRows,
      closeRows,
    }),
    [closeRows, drill, openRows, parsed, truncate],
  )
}
