/**
 * VIZ-106 fix round (B0 finding 2): a frame's toolbar wraps UNDER its title
 * when the two do not fit on one line, instead of squeezing the title to a
 * sliver (Summary's four-button "Results by suite" frame at 375 px left its
 * title a 2 px column). jsdom lays nothing out, so this holds the CSS
 * contract that does it; the geometry is proved in Chromium on Windows and
 * Linux (`rollout-responsive.spec.ts`, and the committed baselines at
 * 1280 px, byte-identical: a frame wide enough is laid out as before).
 *
 *  - the header is a WRAPPING flex row;
 *  - the title column grows from an 8rem flex-basis: the toolbar wraps only
 *    when 8rem of title and the toolbar cannot share the line, and wherever
 *    they can, the title grows to the width `flex-1` gave it;
 *  - the toolbar never exceeds the header's width, and wraps its own buttons
 *    when a wrapped toolbar is still too wide.
 */
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import ChartFrame from './ChartFrame'

function frame() {
  render(
    <ChartFrame
      title="Results by suite"
      headingLevel={3}
      takeaway="Tests by status in each suite"
      state={{ status: 'ready', data: { meta: null, series: null }, meta: null, revalidating: false }}
      toolbar={
        <>
          <button type="button">Tests</button>
          <button type="button">Runs</button>
        </>
      }
    >
      <div>plot</div>
    </ChartFrame>,
  )
  const toolbar = screen.getByRole('toolbar')
  const header = toolbar.parentElement as HTMLElement
  const title = screen.getByRole('heading', { level: 3, name: 'Results by suite' }).parentElement as HTMLElement
  return { header, title, toolbar }
}

describe('ChartFrame header on the page: the toolbar wraps under the title, never squeezes it', () => {
  it('is a wrapping row', () => {
    const { header } = frame()
    expect(header).toHaveClass('flex', 'flex-wrap', 'items-start', 'gap-3')
  })

  it('the title asks for 8rem before it grows, and may shrink below its content (min-w-0)', () => {
    const { title, header } = frame()
    expect(title.parentElement).toBe(header)
    expect(title).toHaveClass('min-w-0', 'flex-[1_1_8rem]')
    // Not `flex-1`: a 0 basis never wraps the toolbar, whatever the width.
    expect(title).not.toHaveClass('flex-1')
  })

  it('the toolbar keeps its own width beside the title, but never more than the header, and wraps its buttons', () => {
    const { toolbar } = frame()
    expect(toolbar).toHaveClass('shrink-0', 'max-w-full', 'flex-wrap')
  })
})
