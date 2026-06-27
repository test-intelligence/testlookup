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

export function useTableSort<T>(
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
      // sortKey is a runtime string, so index through an indexable view of the
      // row; the comparisons below narrow each value with `typeof` at runtime.
      const av = (a as Record<string, unknown>)[sortKey]
      const bv = (b as Record<string, unknown>)[sortKey]
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
