/**
 * A full-width row under a table row, for that row's form or detail (W27
 * PAN-01). The precedents are the /failures detail row (W07) and the
 * quarantine decision form row.
 *
 * Its content can never widen the table: the inner wrapper has ZERO
 * intrinsic width (`width: 0; min-width: 100%`), so the table's column sizing
 * ignores the content's min-content width, and the wrapper then fills the
 * cell. A form inside wraps to the table's width instead of pushing it.
 *
 * `colSpan` is the table's column count, so the row spans every column.
 */
import type { ReactNode } from 'react'

export interface InlineRowSectionProps {
  colSpan: number
  children: ReactNode
  testId?: string
  dataAttributes?: { [data: `data-${string}`]: string }
}

export function InlineRowSection({ colSpan, children, testId, dataAttributes }: InlineRowSectionProps) {
  return (
    <tr
      data-in-page-row=""
      data-testid={testId}
      {...dataAttributes}
      style={{ borderBottom: '1px solid var(--color-border)' }}
    >
      <td colSpan={colSpan} style={{ padding: 12, background: 'var(--color-bg)' }}>
        <div style={{ width: 0, minWidth: '100%' }}>{children}</div>
      </td>
    </tr>
  )
}

export default InlineRowSection
