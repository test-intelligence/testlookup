/**
 * One open form per page section — a table, a detail pane, the area under a
 * page header (W27 PAN-01).
 *
 * The overlay's backdrop used to stop a second action while a form was half
 * typed. In the page nothing stops it, so the section's slot does: opening
 * another form here, or running a guarded action (switching the detail pane
 * to another item, closing the pane), while the open form has typed input
 * asks first, inline, in that form (`Discard your changes to "…"?`).
 *
 *  - Cancel / X / Escape discard silently, as the modals did: close().
 *  - A clean slot (or nothing open) replaces at once.
 *  - [Discard changes] closes the open form and then does what was asked;
 *    [Keep editing] keeps the form and its text.
 *
 * guard(action) never closes a CLEAN form: when the action must take the
 * form away too (another item in the pane), the action calls close() itself.
 *
 * The form reports "typed into" through setDirty(true) (InlineFormSection does
 * it from the input / change events inside it); dirty resets on open(),
 * close(), and when the form unmounts (setDirty(false)).
 */
import { useCallback, useMemo, useRef, useState } from 'react'

export interface InlineSlot<K extends string = string> {
  readonly openKey: K | null
  readonly dirty: boolean
  /** A replacement or a guarded action waits for the discard answer (the open form shows the question). */
  readonly asking: boolean
  /** Open `key` here (a no-op when `key` is already open). Clean (or nothing open): at once. Dirty: asks first;
   *  [Discard changes] then opens. */
  open(key: K): void
  /** Close WITHOUT asking (Cancel, X, Escape, after a successful save): what the modal's Cancel did. */
  close(): void
  /** Run `action` after the same check (switching the detail pane to another item, closing the pane). */
  guard(action: () => void): void
  /** InlineFormSection reports user edits; reset to false on open(), close() and unmount. */
  setDirty(dirty: boolean): void
  answer(discard: boolean): void
}

interface SlotState<K extends string> {
  openKey: K | null
  dirty: boolean
  asking: boolean
}

export function useInlineSlot<K extends string = string>(): InlineSlot<K> {
  const [state, setState] = useState<SlotState<K>>({ openKey: null, dirty: false, asking: false })
  // What [Discard changes] does: the replacement or the guarded action that asked.
  const pending = useRef<(() => void) | null>(null)

  const close = useCallback(() => {
    pending.current = null
    setState({ openKey: null, dirty: false, asking: false })
  }, [])

  const open = useCallback(
    (key: K) => {
      if (key === state.openKey) return
      const replace = () => setState({ openKey: key, dirty: false, asking: false })
      if (state.dirty) {
        pending.current = replace
        setState((s) => (s.asking ? s : { ...s, asking: true }))
        return
      }
      pending.current = null
      replace()
    },
    [state.dirty, state.openKey],
  )

  const guard = useCallback(
    (action: () => void) => {
      if (state.dirty) {
        pending.current = () => {
          setState({ openKey: null, dirty: false, asking: false })
          action()
        }
        setState((s) => (s.asking ? s : { ...s, asking: true }))
        return
      }
      action()
    },
    [state.dirty],
  )

  const setDirty = useCallback((dirty: boolean) => {
    if (!dirty) pending.current = null
    setState((s) => {
      // Nothing typed any more: there is nothing to ask about either.
      const next = dirty ? { ...s, dirty: true } : { ...s, dirty: false, asking: false }
      return next.dirty === s.dirty && next.asking === s.asking ? s : next
    })
  }, [])

  const answer = useCallback((discard: boolean) => {
    const next = pending.current
    pending.current = null
    if (discard && next) next()
    else setState((s) => (s.asking ? { ...s, asking: false } : s))
  }, [])

  return useMemo(
    () => ({
      openKey: state.openKey,
      dirty: state.dirty,
      asking: state.asking,
      open,
      close,
      guard,
      setDirty,
      answer,
    }),
    [state, open, close, guard, setDirty, answer],
  )
}
