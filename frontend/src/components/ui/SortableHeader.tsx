/**
 * SortableHeader — a sortable table header cell, kept for its callers: since W29b (UIX-06) it IS the one sort
 * control, `ThSort` from `components/ui/Table`, with its props unchanged.
 *
 * The interactive target is a real <button> inside the <th>, not the cell itself: a button is keyboard-operable for
 * free (Enter/Space activate it). The <th> keeps its columnheader semantics and carries aria-sort, so a screen
 * reader announces the current sort direction that the chevron only shows visually. The label keeps the header's
 * uppercase (`.th-sort` inherits it; a button's UA style reset it before).
 */
import type { SortDir } from '@/hooks/useTableSort'
import { ThSort } from './Table'

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
  return (
    <ThSort label={label} sortKey={sortKey} currentKey={currentKey} dir={dir} onSort={onSort} className={className} align={align} />
  )
}
