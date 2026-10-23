/**
 * The ONE table (W29b UIX-06). The look lives in `index.css` — `.th` (the label style on the sunken header row),
 * `.th-sort`, `.th-num` / `.td-num`, `.td`, `.table-row` (grey hover, `[data-selected]`) and
 * `table[data-density]` — so the tables that still write those classes by hand look the same; these components
 * only apply them.
 *
 * - `Table density`: `comfortable` (px-4 py-3, the `.th` / `.td` default), `dense` (px-3 py-2) or `compact`
 *   (px-2 py-1.5): the paddings the page tables already used, so an adopting row never grows.
 * - `Th` / `ThSort` / `Td` take `numeric` on a NUMBER column (count, percentage, duration, money): right-aligned,
 *   tabular figures, in the header AND the cells.
 * - `ThSort` is the one sort control: a real `<button>` inside the `<th>` (keyboard-operable), `aria-sort` on the
 *   `<th>`, the three lucide chevrons (never a text arrow), and the header's case kept (`.th-sort` inherits it; the
 *   UA stylesheet resets a button's `text-transform`). A header with no sort handler is a plain `Th`.
 * - `Tr selected` is the item open in the page (accent-muted + the 3 px accent bar); `interactive={false}` drops
 *   the pointer cursor of a row nothing opens.
 *
 * React 19: `ref` is an ordinary prop, so `<Tr ref={…}>` reaches the `<tr>` through the spread.
 */
import type { ComponentPropsWithRef, ReactNode } from 'react'
import { clsx } from 'clsx'
import { ChevronDown, ChevronUp, ChevronsUpDown } from 'lucide-react'
import type { SortDir } from '@/hooks/useTableSort'

export type TableDensity = 'comfortable' | 'dense' | 'compact'

export type TableProps = ComponentPropsWithRef<'table'> & { density?: TableDensity }

export function Table({ density, className, ...rest }: TableProps) {
  return (
    <table data-density={density ?? 'comfortable'} className={clsx('w-full border-collapse text-sm', className)} {...rest} />
  )
}

export type ThProps = ComponentPropsWithRef<'th'> & { numeric?: boolean }

/** A column header (`scope="col"` unless given: `"colgroup"` for a group header). */
export function Th({ numeric, className, scope = 'col', ...rest }: ThProps) {
  return <th scope={scope} className={clsx('th', numeric && 'th-num', className)} {...rest} />
}

export interface ThSortProps {
  label: ReactNode
  sortKey: string
  currentKey: string | null
  dir: SortDir
  onSort: (key: string) => void
  numeric?: boolean
  className?: string
  /** The header's `title` (on the `<th>`). */
  title?: string
  /** The button's content alignment; a `numeric` header is right-aligned by `.th-num` without it. */
  align?: 'left' | 'right' | 'center'
}

const JUSTIFY = { left: 'justify-start', right: 'justify-end', center: 'justify-center' } as const

export function ThSort({ label, sortKey, currentKey, dir, onSort, numeric, className, title, align }: ThSortProps) {
  const active = currentKey === sortKey
  const ariaSort = active ? (dir === 'asc' ? 'ascending' : 'descending') : 'none'
  const Icon = active ? (dir === 'asc' ? ChevronUp : ChevronDown) : ChevronsUpDown
  return (
    <th scope="col" aria-sort={ariaSort} title={title} className={clsx('th', numeric && 'th-num', className)}>
      <button type="button" className={clsx('th-sort', align && JUSTIFY[align])} onClick={() => onSort(sortKey)}>
        {label}
        <Icon
          aria-hidden="true"
          data-sort-icon={active ? dir : 'none'}
          className={clsx('h-3 w-3 shrink-0', !active && 'opacity-30')}
        />
      </button>
    </th>
  )
}

export type TdProps = ComponentPropsWithRef<'td'> & { numeric?: boolean }

export function Td({ numeric, className, ...rest }: TdProps) {
  return <td className={clsx('td', numeric && 'td-num', className)} {...rest} />
}

export type TrProps = ComponentPropsWithRef<'tr'> & { selected?: boolean; interactive?: boolean }

export function Tr({ selected, interactive, className, ...rest }: TrProps) {
  return (
    <tr
      data-selected={selected ? '' : undefined}
      className={clsx('table-row', interactive === false && 'cursor-default', className)}
      {...rest}
    />
  )
}
