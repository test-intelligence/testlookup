/**
 * TRND-10: the Customise panel is generic — it draws a chart's `CustomiseSpec`
 * (text and select fields, each with its `data-customise` id), lists every
 * unavailable option with its reason, and hands field changes back. The suite
 * series' spec keeps every DOM hook it had (VIZ-604).
 */
import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import ChartCustomisePanel from './ChartCustomisePanel'
import { DEFAULT_SERIES_CONFIG, seriesCustomiseSpec, type CustomiseSpec } from './chartCustomiseModel'

const panel = () => screen.getByRole('complementary', { name: /^Customise / })

describe('ChartCustomisePanel', () => {
  it('draws the suite series spec with every data-customise hook it always had', () => {
    render(
      <ChartCustomisePanel chartTitle="Pass rate by suite" spec={seriesCustomiseSpec(DEFAULT_SERIES_CONFIG)} onField={vi.fn()} onReset={vi.fn()} onClose={vi.fn()} />,
    )
    const ids = [...panel().querySelectorAll('[data-customise]')].map((el) => el.getAttribute('data-customise'))
    expect(ids).toEqual(['title', 'metric', 'series', 'bucket', 'topN', 'chartType', 'scale', 'reset'])
    expect(within(panel()).getByLabelText('Metric')).toHaveValue('pass_rate')
    expect(within(panel()).getByLabelText(/^Lines/)).toHaveValue('7')
    expect(within(panel()).getByLabelText('Title')).toHaveAttribute('placeholder', 'Pass rate by suite')
    // A default chart: nothing to reset.
    expect(within(panel()).getByRole('button', { name: 'Reset to default' })).toBeDisabled()
  })

  it('lists unavailable options disabled in their select AND with their reasons', () => {
    render(
      <ChartCustomisePanel chartTitle="Pass rate by suite" spec={seriesCustomiseSpec(DEFAULT_SERIES_CONFIG)} onField={vi.fn()} onReset={vi.fn()} onClose={vi.fn()} />,
    )
    const chartType = within(panel()).getByLabelText('Chart type') as HTMLSelectElement
    const pie = [...chartType.options].find((o) => o.textContent === 'Pie (unavailable)')
    expect(pie?.disabled).toBe(true)
    const reasons = panel().querySelector('[data-customise-reasons]') as HTMLElement
    expect(reasons).toHaveTextContent(/Pie: A pie shows the parts of one whole/)
    expect(reasons).toHaveTextContent(/Stacked area: Rates cannot be stacked/)
    expect(reasons).toHaveTextContent(/Log: A log scale exaggerates/)
    expect(reasons).toHaveTextContent(/More than 7: More than 7 lines/)
  })

  it('draws any chart’s fields and hands each change back by field id', () => {
    const spec: CustomiseSpec = {
      fields: [
        { id: 'title', label: 'Title', kind: 'text', value: '', placeholder: 'Compare', maxLength: 80 },
        { id: 'by', label: 'Compare by', kind: 'select', value: 'suite', options: [{ value: 'suite', label: 'Suite' }, { value: 'release', label: 'Release' }] },
      ],
      unavailable: [{ control: 'lines', label: 'More than 8 lines', reason: 'narrow the selection' }],
      isDefault: false,
    }
    const onField = vi.fn()
    const onReset = vi.fn()
    const onClose = vi.fn()
    render(<ChartCustomisePanel chartTitle="Compare" spec={spec} onField={onField} onReset={onReset} onClose={onClose} />)
    fireEvent.change(within(panel()).getByLabelText('Compare by'), { target: { value: 'release' } })
    expect(onField).toHaveBeenLastCalledWith('by', 'release')
    fireEvent.change(within(panel()).getByLabelText('Title'), { target: { value: 'My compare' } })
    expect(onField).toHaveBeenLastCalledWith('title', 'My compare')
    expect(panel().querySelector('[data-customise-reasons]')).toHaveTextContent('More than 8 lines: narrow the selection')
    fireEvent.click(within(panel()).getByRole('button', { name: 'Reset to default' }))
    expect(onReset).toHaveBeenCalled()
    fireEvent.click(within(panel()).getByRole('button', { name: 'Close customise panel' }))
    expect(onClose).toHaveBeenCalled()
  })

  it('renders a hostile chart title and field label as text', () => {
    const spec: CustomiseSpec = { fields: [{ id: 'title', label: '<b>x</b>', kind: 'text', value: '<img src=x>' }], unavailable: [], isDefault: true }
    render(<ChartCustomisePanel chartTitle="<img src=x onerror=alert(1)>" spec={spec} onField={vi.fn()} onReset={vi.fn()} onClose={vi.fn()} />)
    expect(panel().querySelector('img, b')).toBeNull()
    expect(panel()).toHaveTextContent('<img src=x onerror=alert(1)>')
  })
})
