/**
 * A list and the open item's detail, side by side in the page (W27 PAN-01):
 * the /test-management split, shared.
 *
 *  - Nothing open ('list'): the list alone, and the row container carries no
 *    class and no style, so a page's closed state is exactly what it was —
 *    or, with a `rail`, the list beside its right rail in the host's grid.
 *  - Open ('split'): the list keeps `minmax(0, 1fr)`, the detail takes
 *    `detailTrack` (default `minmax(440px, 46%)`); a right rail is folded (not
 *    rendered) and the detail takes its column, so the list is never
 *    squeezed below its width.
 *  - Expanded: the detail takes the row; the list wrapper is `hidden` but
 *    stays MOUNTED, so its scroll position, page, filters and typed text are
 *    there when the list comes back.
 *
 * `rowProps` / `listProps` land on the row container and the list wrapper
 * as given (a host's `className` is used as is), so a host moving onto this
 * keeps its exact DOM.
 */
import type { HTMLAttributes, ReactNode } from 'react'

export type MasterDetailState = 'list' | 'split' | 'expanded'

/** The /test-management split (TestManagementPage's case panel track). */
export const DETAIL_TRACK = 'minmax(440px, 46%)'

type DivProps = HTMLAttributes<HTMLDivElement> & { [data: `data-${string}`]: string | undefined }

export interface MasterDetailProps {
  /** The list, rendered inside the list wrapper; unchanged (no wrapper class, no style) while nothing is open. */
  list: ReactNode
  /** The open item's <DetailPane>, or null. */
  detail: ReactNode | null
  /** The detail takes the row; the list wrapper gets `hidden` and stays mounted (its scroll, page, filters stay). */
  expanded?: boolean
  /** Default DETAIL_TRACK. */
  detailTrack?: string
  /** Attributes of the row container (TM passes data-cases-layout). */
  rowProps?: DivProps
  /** Attributes of the list wrapper (TM passes data-primary, its className and style: its DOM stays identical). */
  listProps?: DivProps
  /** Default 'gap-4'. */
  gapClassName?: string
  /** A right rail shown while nothing is open; folded (not rendered) while a detail is open, the detail taking its
   *  column. Closed: grid `minmax(0, 1fr) ${railTrack}` with the host's gap, no items-start; open: the split. */
  rail?: ReactNode
  /** /releases: '340px'. */
  railTrack?: string
}

export function MasterDetail({
  list,
  detail,
  expanded = false,
  detailTrack = DETAIL_TRACK,
  rowProps,
  listProps,
  gapClassName = 'gap-4',
  rail,
  railTrack,
}: MasterDetailProps) {
  const open = detail !== null && detail !== undefined && detail !== false
  const state: MasterDetailState = open ? (expanded ? 'expanded' : 'split') : 'list'
  const showRail = !open && rail !== undefined && rail !== null && rail !== false

  let className: string | undefined
  let gridTemplateColumns: string | undefined
  if (state === 'split') {
    className = `grid ${gapClassName} items-start`
    gridTemplateColumns = `minmax(0, 1fr) ${detailTrack}`
  } else if (showRail) {
    className = `grid ${gapClassName}`
    gridTemplateColumns = `minmax(0, 1fr) ${railTrack ?? 'auto'}`
  }

  return (
    <div
      data-master-detail={state}
      {...rowProps}
      className={className}
      style={gridTemplateColumns ? { gridTemplateColumns } : undefined}
    >
      <div {...listProps} hidden={state === 'expanded'} className={listProps?.className ?? 'min-w-0'}>
        {list}
      </div>
      {showRail && rail}
      {open && detail}
    </div>
  )
}

export default MasterDetail
