/**
 * useTableSort — reusable client-side table sorting hook.
 *
 * Usage:
 *   const { sorted, sortKey, sortDir, toggleSort } = useTableSort(items, 'created_at', 'desc')
 *   <SortableHeader label="Date" sortKey="created_at" current={sortKey} dir={sortDir} onSort={toggleSort} />
 */
import { useMemo, useState } from 'react'

export type SortDir = 'asc' | 'desc'

export interface SortState {
  key: string
  dir: SortDir
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function useTableSort<T extends Record<string, any>>(
  items: T[],
  defaultKey: string = '',
  defaultDir: SortDir = 'asc',
) {
  const [sortKey, setSortKey] = useState(defaultKey)
  const [sortDir, setSortDir] = useState<SortDir>(defaultDir)

  const toggleSort = (key: string) => {
    if (sortKey === key) {
      setSortDir(d => (d === 'asc' ? 'desc' : 'asc'))
    } else {
      setSortKey(key)
      setSortDir('asc')
    }
  }

  const sorted = useMemo(() => {
    if (!sortKey) return items
    return [...items].sort((a, b) => {
      const av = a[sortKey]
      const bv = b[sortKey]
      if (av == null && bv == null) return 0
      if (av == null) return 1
      if (bv == null) return -1
      let cmp = 0
      if (typeof av === 'number' && typeof bv === 'number') {
        cmp = av - bv
      } else if (typeof av === 'string' && typeof bv === 'string') {
        cmp = av.localeCompare(bv, undefined, { sensitivity: 'base' })
      } else {
        cmp = String(av).localeCompare(String(bv))
      }
      return sortDir === 'desc' ? -cmp : cmp
    })
  }, [items, sortKey, sortDir])

  return { sorted, sortKey, sortDir, toggleSort }
}
