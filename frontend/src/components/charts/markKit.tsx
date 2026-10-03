/**
 * The runtime of mark activation for cursor charts (Wave 3), as ONE value a
 * host hands its chart with its handler: `<BarChart onMarkActivate={...}
 * markKit={MARK_KIT} />`.
 *
 * Why a value and not an import inside the chart: `ChartCursor` and
 * `BarChart` sit in the first-visit bundle of every page that draws a bar or
 * a day strip, flag on or off. If they imported the intent rules, the
 * cursor's Enter and pointer behaviour and the buttons themselves, every one
 * of those pages would download code that only a flag-on, lazily loaded
 * section (the Failures drill ladder) ever runs. The host that activates is
 * lazy, so this module rides in ITS chunk (`sectionOnlyModules.test.ts`
 * keeps it out of every page's static closure).
 */
import type { KeyboardEvent as ReactKeyboardEvent, ReactNode } from 'react'
import {
  availableIntents,
  keyboardIntent,
  pointerIntent,
  type ChartMark,
  type CursorActivation,
  type CursorActivationContext,
  type MarkIntent,
  type MarkKit,
  type PointerModifiers,
} from './marks'
import { MarkActions } from './MarkActions'

/**
 * The chart a readout button belongs to: the cursor's surface, which renders
 * the readout inside itself. Focused before the action runs (as Escape on a
 * button does), so a panel the action opens returns focus to the chart.
 */
const cursorSurfaceOf = (button: HTMLElement): HTMLElement | null => button.closest<HTMLElement>('[data-chart-cursor]')

/** The cursor's Enter, pointer and readout behaviour for one render of an activatable chart. */
function cursorActivation({ points, index, onMarkActivate, markIntents, move, dismiss }: CursorActivationContext): CursorActivation {
  const intentsAt = (at: number): MarkIntent[] => {
    const mark = points[at]?.mark
    return mark ? availableIntents(mark, { onMarkActivate, markIntents }) : []
  }
  return {
    onKeyDown(event: ReactKeyboardEvent<HTMLElement>) {
      if (event.target !== event.currentTarget) {
        // A key pressed on one of the readout's buttons is the button's own
        // (Enter and Space press it). Only Escape is ours: it closes the
        // readout and hands focus back to the chart, so focus is never left
        // on a button that is about to disappear.
        if (event.key !== 'Escape') return true
        dismiss()
        event.currentTarget.focus()
        event.preventDefault()
        event.stopPropagation()
        return true
      }
      // A modified key is the reader's (the cursor ignores it too).
      if (event.altKey || event.ctrlKey || event.metaKey || event.key !== 'Enter') return false
      // Enter is the first intent on offer, Shift+Enter the filter (OD-2).
      const mark = index >= 0 ? points[index]?.mark : undefined
      const intent = keyboardIntent(intentsAt(index), { shiftKey: event.shiftKey })
      if (mark && intent) {
        onMarkActivate(mark, intent)
        event.preventDefault()
        event.stopPropagation()
      }
      return true
    },
    activatePointer(at: number, modifiers: PointerModifiers = {}) {
      const mark = points[at]?.mark
      if (!mark) return
      const intent = pointerIntent(intentsAt(at), modifiers)
      if (intent) onMarkActivate(mark, intent)
      // A touch tap has no modifier to choose with: it selects, and the readout offers the buttons.
      else if (modifiers.pointerType === 'touch' && intentsAt(at).length > 0) move(at)
    },
    activateMark(mark: ChartMark, modifiers: PointerModifiers = {}) {
      const intent = pointerIntent(availableIntents(mark, { onMarkActivate, markIntents }), modifiers)
      if (intent) onMarkActivate(mark, intent)
      return intent !== null
    },
    readout(body: ReactNode, at: number) {
      const mark = points[at]?.mark
      if (!mark) return body
      return (
        <>
          {body}
          <MarkActions mark={mark} intents={intentsAt(at)} onActivate={onMarkActivate} returnFocus={cursorSurfaceOf} />
        </>
      )
    },
  }
}

export const MARK_KIT: MarkKit = { availableIntents, pointerIntent, keyboardIntent, Actions: MarkActions, cursor: cursorActivation }
