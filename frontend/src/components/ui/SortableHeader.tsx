/**
 * SortableHeader — clickable table header cell with sort direction indicator.
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

  return (
    <th
      className={`th cursor-pointer select-none hover:text-[var(--color-text-secondary)] transition-colors ${className ?? ''}`}
      onClick={() => onSort(sortKey)}
    >
      <span className={`inline-flex items-center gap-1 ${alignClass}`}>
        {label}
        {active ? (
          dir === 'asc' ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />
        ) : (
          <ChevronsUpDown className="h-3 w-3 opacity-30" />
        )}
      </span>
    </th>
  )
}
