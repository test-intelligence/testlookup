/**
 * A button label that swaps between two or more words WITHOUT changing the
 * button's width — "View as table" / "Hide table", "Show 100%" / "Show counts".
 *
 * Every label is laid out in the same grid cell, so the cell is as wide as the
 * WIDEST of them whichever one shows. The others are `visibility: hidden` —
 * they keep their space but are not painted, not in the accessible name and
 * not in `innerText` — and `aria-hidden` on top, so no reader ever hears two.
 *
 * Why it matters: a chart frame's toolbar never yields width to the title
 * (it is `shrink-0` beside a `flex-1` title). A toggle whose label changed
 * width re-wrapped a title near the limit every time it was pressed, and the
 * plot under the title jumped by a line (baseline review A, D1).
 */
export interface SwapLabelProps {
  /** Every label the control can show. Order is irrelevant to the layout. */
  labels: readonly string[]
  /** The one showing now: an element of `labels`. */
  current: string
}

export default function SwapLabel({ labels, current }: SwapLabelProps) {
  return (
    <span data-swap-label="" className="inline-grid justify-items-center">
      {labels.map((label) => {
        const showing = label === current
        return (
          <span
            key={label}
            aria-hidden={showing ? undefined : true}
            data-swap-label-current={showing ? '' : undefined}
            className={showing ? 'col-start-1 row-start-1' : 'invisible col-start-1 row-start-1'}
          >
            {label}
          </span>
        )
      })}
    </span>
  )
}
