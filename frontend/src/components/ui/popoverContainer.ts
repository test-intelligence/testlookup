/**
 * Where a `position: fixed` popover should be portalled (VIZ-109).
 *
 *  1. The FULLSCREEN element, when it holds the anchor: only the fullscreen
 *     element's subtree is painted in full screen, so a popover portalled to
 *     <body> opens invisibly.
 *  2. Else the nearest `aria-modal="true"` dialog: portalled outside it, the
 *     popover's controls sit outside the dialog's focus trap, and the dialog's
 *     Tab handling would pull focus straight back out of it.
 *  3. Else <body> — out of every `overflow: hidden` card and stacking context.
 */
export function resolvePopoverContainer(anchor: HTMLElement | null): HTMLElement {
  const fullscreen = typeof document !== 'undefined' ? document.fullscreenElement : null
  if (anchor && fullscreen instanceof HTMLElement && fullscreen.contains(anchor)) return fullscreen
  const dialog = anchor?.closest<HTMLElement>('[aria-modal="true"]')
  return dialog ?? document.body
}
