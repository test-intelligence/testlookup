/**
 * The tooltip BODY (VIZ-601): the title and the label / value rows every chart
 * tooltip and every keyboard readout draws, and the box they sit in. The React
 * twin of `buildTooltipNode` (`tooltip.ts`).
 *
 * Split out of `ChartTooltip.tsx` in Wave 2.5 (VIZ-104) for one reason: that
 * module value-imports Recharts (`usePlotArea`, for `PinnedTip`), so anything
 * importing the body from it carried Recharts' store and hooks too. The
 * keyboard cursor draws this body, and `DayStrip` (no chart engine at all)
 * uses the cursor, so every page with a day strip fetched about 60 kB gzip of
 * Recharts it never ran. `ChartTooltip` re-exports everything here, so no
 * importer changes.
 *
 * Every string is a React text child: a test named `<img src=x onerror=…>`
 * from an ingested CI file is shown as those characters and never parsed.
 */
import type { CSSProperties } from 'react'
import {
  TIP_CLASS,
  TIP_LINE_SWATCH,
  TIP_STYLE,
  TOOLTIP_NODE_ATTRIBUTE,
  rowLabelText,
  safeDataAttributes,
  type TooltipContent,
  type TooltipRow,
} from './tooltip'
import { RECHARTS_TOOLTIP_STYLE } from './tokens'

/** The box around the content: the card, its border and its padding (Overview's tooltip, BarChart's shell). */
export const TIP_BOX_STYLE: CSSProperties = {
  ...RECHARTS_TOOLTIP_STYLE,
  padding: '4px 8px',
  boxSizing: 'border-box',
}

function Swatch({ row }: { row: TooltipRow }) {
  if (!row.color) return null
  if (row.mark === 'line') {
    return (
      <svg
        aria-hidden="true"
        focusable="false"
        width={TIP_LINE_SWATCH}
        height={10}
        className={TIP_CLASS.swatch}
        style={TIP_STYLE.line}
      >
        <line
          x1={1}
          y1={5}
          x2={TIP_LINE_SWATCH - 1}
          y2={5}
          stroke={row.color}
          strokeWidth={2}
          strokeDasharray={row.dash}
        />
      </svg>
    )
  }
  return <span aria-hidden="true" className={TIP_CLASS.swatch} style={{ ...TIP_STYLE.swatch, backgroundColor: row.color }} />
}

function Row({ row }: { row: TooltipRow }) {
  const note = row.kind === 'note'
  const data = Object.fromEntries(safeDataAttributes(row.data))
  const label = rowLabelText(row)
  return (
    <div className={note ? TIP_CLASS.note : TIP_CLASS.row} data-tip-kind={row.kind ?? 'value'} style={note ? TIP_STYLE.note : TIP_STYLE.row} {...data}>
      {!note && <Swatch row={row} />}
      {label && (
        <span className={TIP_CLASS.label} style={TIP_STYLE.label}>
          {label}
        </span>
      )}
      <span className={TIP_CLASS.value} style={note ? TIP_STYLE.noteValue : TIP_STYLE.value} data-tip-value="">
        {row.value}
      </span>
      {row.detail && (
        <span className={TIP_CLASS.detail} style={TIP_STYLE.detail}>
          ({row.detail})
        </span>
      )}
    </div>
  )
}

const rowKey = (row: TooltipRow, index: number) => `${row.key ?? `${row.kind ?? 'value'}:${row.label}`}#${index}`

export interface ChartTooltipBodyProps {
  content: TooltipContent
  /** Mark it as THE tooltip (`data-chart-tooltip`). The keyboard readout draws the same body unmarked. */
  marked?: boolean
  style?: CSSProperties
}

/** The content, drawn: the React twin of `buildTooltipNode`. */
export function ChartTooltipBody({ content, marked = false, style }: ChartTooltipBodyProps) {
  const mark = marked ? { [TOOLTIP_NODE_ATTRIBUTE]: '' } : {}
  return (
    <div className={TIP_CLASS.body} style={{ ...TIP_STYLE.body, ...style }} {...mark}>
      <TipRows content={content} />
    </div>
  )
}

/** The title and rows, for a box that is itself the `[data-chart-tooltip]` element. */
export function TipRows({ content }: { content: TooltipContent }) {
  return (
    <>
      {content.title !== undefined && (
        <div className={TIP_CLASS.title} style={TIP_STYLE.title}>
          {content.title}
        </div>
      )}
      {content.rows.map((row, index) => (
        <Row key={rowKey(row, index)} row={row} />
      ))}
    </>
  )
}
