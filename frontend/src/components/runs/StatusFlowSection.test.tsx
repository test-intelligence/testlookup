/**
 * VIZ-507: the run-compare status flows (no flag since Phase D, S5): the
 * frame, its takeaway, a table fallback of every transition, and a flow click
 * that filters the per-test table.
 */
import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { StatusTransition } from '@/components/charts/statusSankeyModel'

// The engine is ECharts on a canvas: here a stand-in that offers each flow as a button.
vi.mock('@/components/charts/StatusSankey', () => ({
  default: ({ model, onSelect }: { model: { links: { before: string; after: string }[] }; onSelect: (f: unknown) => void }) => (
    <div data-testid="sankey">
      {model.links.map((link) => (
        <button key={`${link.before}-${link.after}`} type="button" onClick={() => onSelect({ before: link.before, after: link.after })}>
          {`${link.before}->${link.after}`}
        </button>
      ))}
    </div>
  ),
}))

import StatusFlowSection, { NO_FLOWS_REASON, STATUS_FLOW_TITLE } from './StatusFlowSection'

const FLOWS: StatusTransition[] = [
  { before: 'passed', after: 'passed', count: 10 },
  { before: 'passed', after: 'failed', count: 2 },
  { before: 'absent', after: 'passed', count: 1 },
]

describe('StatusFlowSection (VIZ-507)', () => {
  it('draws the flows with the regressions in the takeaway, and a click filters the table to that flow', () => {
    const onSelect = vi.fn()
    render(<StatusFlowSection transitions={FLOWS} onSelectClassification={onSelect} />)
    expect(screen.getByRole('heading', { name: STATUS_FLOW_TITLE })).toBeInTheDocument()
    expect(screen.getByText(/2 tests went from passed to failing/)).toBeInTheDocument()
    fireEvent.click(screen.getByText('passed->failed'))
    expect(onSelect).toHaveBeenCalledWith('new_failure', 'passed → failed')
    fireEvent.click(screen.getByText('passed->passed'))
    expect(onSelect).toHaveBeenLastCalledWith(null, 'passed → passed')
  })

  it('the table fallback lists every transition with its count', () => {
    render(<StatusFlowSection transitions={FLOWS} onSelectClassification={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: /View as table/ }))
    const table = screen.getByRole('table')
    expect(table.textContent).toContain('New')
    expect(table.textContent).toContain('10')
  })

  it('no per-test results on either side: says so instead of drawing', () => {
    render(<StatusFlowSection transitions={[]} onSelectClassification={vi.fn()} />)
    expect(screen.getByText(new RegExp(NO_FLOWS_REASON.slice(0, 30)))).toBeInTheDocument()
    expect(screen.queryByTestId('sankey')).toBeNull()
  })
})
