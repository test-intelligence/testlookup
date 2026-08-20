/**
 * SortableHeader — sortable table header cell with a sort-direction indicator.
 *
 * The interactive target is a real <button> inside the <th>, not the cell
 * itself: a button is keyboard-operable for free (Enter/Space activate it), so
 * keyboard users can sort — the bare <th onClick> it replaced was mouse-only.
 * The <th> keeps its columnheader semantics and carries aria-sort, so a screen
 * reader announces the current sort direction that the chevron only shows
 * visually.
 */
import { ChevronUp, ChevronDown, ChevronsUpDown } from 'lucide-react'
import type { SortDir } from '@/hooks/useTableSort'

interface Props {
  label: string
  sortKey: string
  currentKey: string
  dir: SortDir
  onSort: (key: string) => void
  className?: string
  align?: 'left' | 'right' | 'center'
}

export default function SortableHeader({ label, sortKey, currentKey, dir, onSort, className, align = 'left' }: Props) {
  const active = currentKey === sortKey
  const alignClass = align === 'right' ? 'justify-end' : align === 'center' ? 'justify-center' : 'justify-start'
  const ariaSort = active ? (dir === 'asc' ? 'ascending' : 'descending') : 'none'

  return (
    <th scope="col" aria-sort={ariaSort} className={`th ${className ?? ''}`}>
      <button
        type="button"
        onClick={() => onSort(sortKey)}
        className={`inline-flex w-full items-center gap-1 bg-transparent cursor-pointer select-none hover:text-[var(--color-text-secondary)] transition-colors ${alignClass}`}
      >
        {label}
        {active ? (
          dir === 'asc' ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />
        ) : (
          <ChevronsUpDown className="h-3 w-3 opacity-30" />
        )}
      </button>
    </th>
  )
}
