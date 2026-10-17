/**
 * W27 PAN-01: a row's form or detail opens in a full-width row right under
 * it; its content has zero intrinsic width, so it can never widen the table.
 */
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { InlineRowSection } from './InlineRowSection'

describe('InlineRowSection', () => {
  it('is a <tr><td colSpan> spanning every column, with a zero-intrinsic-width wrapper', () => {
    render(
      <table>
        <tbody>
          <tr><td>a</td><td>b</td><td>c</td></tr>
          <InlineRowSection colSpan={3} testId="mute-row" dataAttributes={{ 'data-row-form': 'mute' }}>
            <p>The form</p>
          </InlineRowSection>
        </tbody>
      </table>,
    )
    const tr = screen.getByTestId('mute-row')
    expect(tr.tagName).toBe('TR')
    expect(tr).toHaveAttribute('data-in-page-row', '')
    expect(tr).toHaveAttribute('data-row-form', 'mute')
    // The row right after its row.
    expect(tr.previousElementSibling?.textContent).toBe('abc')

    const cells = tr.querySelectorAll(':scope > td')
    expect(cells).toHaveLength(1)
    expect(cells[0]).toHaveAttribute('colspan', '3')

    const wrapper = cells[0].firstElementChild as HTMLElement
    expect(wrapper.style.width).toBe('0px')
    expect(wrapper.style.minWidth).toBe('100%')
    expect(wrapper).toContainElement(screen.getByText('The form'))
  })
})
