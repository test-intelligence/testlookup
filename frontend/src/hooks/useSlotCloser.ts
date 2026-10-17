/**
 * W27a PAN-04: close a section's open form only while it is still THAT form.
 *
 * A modal's backdrop stopped a second action while a request was in flight.
 * In the page nothing does: a user can open another row's form while the
 * first one is still saving, and the first one's `onClose` (called once its
 * request answers) would then close the form that replaced it. The closer
 * reads the slot as last rendered and closes only when `key` is the open one.
 */
import { useCallback, useLayoutEffect, useRef } from 'react'
import type { InlineSlot } from '@/components/ui/inPage/useInlineSlot'

export function useSlotCloser<K extends string>(slot: InlineSlot<K>): (key: K) => void {
  const latest = useRef(slot)
  useLayoutEffect(() => {
    latest.current = slot
  })
  return useCallback((key: K) => {
    if (latest.current.openKey === key) latest.current.close()
  }, [])
}
