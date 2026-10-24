/**
 * W12 SRCH-04 — the applied filters of the last search, as removable chips,
 * and what the server did NOT apply, in its own words.
 *
 * Inside the command bar, between the input and the Mode / Scope row. Absent
 * until the answer's `parsed` has a filter or a note, so a plain query keeps
 * the bar's height. A chip is the filter's key and value as React text
 * (`font-mono text-xs`; the full source token in its title); its × removes
 * that token from the query and re-runs it. Each `parsed.notes` line renders
 * verbatim: an ignored token (unknown key, bad value, …) in muted ink, an
 * unsupported filter or an unresolved release in warning ink. **Syntax** (a
 * closed `Disclosure`, in the page) lists the eight keys.
 */
import { X } from 'lucide-react'
import Disclosure from '@/components/ui/Disclosure'
import { Table, Td, Th } from '@/components/ui/Table'
import type { SearchParsed, SearchParsedFilter } from '@/types/search'
import { SEARCH_SYNTAX, SYNTAX_NOTES, appliesToLabel } from './searchSyntax'

export function SyntaxHelp() {
  return (
    <Disclosure title="Syntax" summary="8 keys" className="ml-auto max-w-full">
      <div data-search-syntax="" className="w-[37.5rem] max-w-full">
        <Table density="dense">
          <thead>
            <tr>
              <Th className="w-[90px]">Key</Th>
              <Th className="w-[220px]">Example</Th>
              <Th>Applies to</Th>
            </tr>
          </thead>
          <tbody>
            {SEARCH_SYNTAX.map((row) => (
              <tr key={row.key} data-syntax-key={row.key}>
                <Td className="font-mono text-xs">{row.key}:</Td>
                <Td className="font-mono text-xs">{row.example}</Td>
                <Td className="min-w-0 text-xs">{appliesToLabel(row)}</Td>
              </tr>
            ))}
          </tbody>
        </Table>
        <ul className="m-0 mt-2 list-none space-y-0.5 p-0 text-xs text-[var(--color-text-muted)]">
          {SYNTAX_NOTES.map((note) => <li key={note}>{note}</li>)}
        </ul>
      </div>
    </Disclosure>
  )
}

export default function SearchChipsRow({
  parsed, onRemove,
}: {
  parsed?: SearchParsed | null
  onRemove: (filter: SearchParsedFilter) => void
}) {
  const filters = parsed?.filters ?? []
  const notes = parsed?.notes ?? []
  if (filters.length === 0 && notes.length === 0) return null
  const ignored = new Set((parsed?.ignored_tokens ?? []).map((token) => token.message))
  return (
    <div data-search-chips="" className="mt-3 border-t border-[var(--color-border)] pt-3">
      <div className="flex flex-wrap items-center gap-1.5">
        {filters.length > 0 && <span className="eyebrow mr-1">Filters</span>}
        {filters.map((filter) => (
          <span
            key={`${filter.start}-${filter.raw}`}
            data-search-chip={filter.key}
            title={filter.raw}
            className="inline-flex max-w-[260px] items-center gap-1 rounded-full border border-[var(--color-border)] bg-[var(--color-bg-secondary)] py-0.5 pl-2 pr-1 font-mono text-xs text-[var(--color-text)]"
          >
            <span className="min-w-0 truncate">{filter.key}: {filter.value}</span>
            <button
              type="button"
              aria-label={`Remove ${filter.key}: ${filter.value}`}
              onClick={() => onRemove(filter)}
              className="inline-flex h-4 w-4 flex-none items-center justify-center rounded-full text-[var(--color-text-muted)] hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text)]"
            >
              <X aria-hidden="true" className="h-3 w-3" />
            </button>
          </span>
        ))}
        <SyntaxHelp />
      </div>
      {notes.length > 0 && (
        <ul data-search-notes="" className="m-0 mt-2 list-none space-y-0.5 p-0">
          {notes.map((note, index) => (
            <li
              key={`${index}-${note}`}
              className={ignored.has(note) ? 'text-xs text-[var(--color-text-muted)]' : 'text-xs text-[var(--ui-warning-ink)]'}
            >
              {note}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
