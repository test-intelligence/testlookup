/**
 * VIZ-104 (K3) — the sparkline as it is DRAWN: nothing for fewer than 2
 * measured points, a line broken at a gap, a middle line for a flat series,
 * an accessible name with latest / min / max, hostile label text kept as text,
 * the end dot on the newest point, and colours from the tokens only.
 */
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import Sparkline from './Sparkline'
import { SPARKLINE_HEIGHT, SPARKLINE_INSET, SPARKLINE_TONE_COLOR } from './Sparkline.model'

const linePath = (container: HTMLElement) => container.querySelector('path[data-part="line"]')?.getAttribute('d') ?? ''
/** The one element `selector` must find: fails loudly instead of asserting non-null. */
function one<T extends Element = HTMLElement>(container: ParentNode, selector: string): T {
  const found = container.querySelector<T>(selector)
  if (!found) throw new Error(`nothing matches ${selector}`)
  return found
}
const endDot = (container: HTMLElement) => one(container, '[data-part="end-dot"]')

describe('Sparkline', () => {
  it.each([
    ['no points', []],
    ['one point', [42]],
    ['one measured point among gaps', [null, 42, null]],
  ])('renders nothing for %s — the caller keeps its caption', (_name, series) => {
    const { container } = render(<Sparkline series={series} label="Pass rate" />)
    expect(container.firstChild).toBeNull()
    expect(container.querySelector('svg')).toBeNull()
  })

  it('is one image whose accessible name is the label plus count, latest, min and max', () => {
    render(<Sparkline series={[81, 97.5, 92.4]} label="Avg pass rate" format={(v) => `${v}%`} />)
    const img = screen.getByRole('img')
    expect(img).toHaveAccessibleName('Avg pass rate: 3 points, latest 92.4%, min 81%, max 97.5%')
    // The drawing is hidden: the name is the one thing assistive tech reads.
    expect(img.querySelector('[aria-hidden]')).not.toBeNull()
    expect(screen.getAllByRole('img')).toHaveLength(1)
  })

  it('breaks the drawn line at a null — two subpaths, not one across the gap', () => {
    const { container } = render(<Sparkline series={[80, 90, null, 70, 85]} label="Pass rate" />)
    const d = linePath(container)
    expect(d.match(/M /g)).toHaveLength(2)
    expect(screen.getByRole('img')).toHaveAccessibleName(/4 points.*1 not measured$/)
  })

  it('draws a flat series as a middle line, with no NaN anywhere in the svg', () => {
    const { container } = render(<Sparkline series={[3, 3, 3]} label="Executions" area />)
    const mid = SPARKLINE_HEIGHT / 2
    expect(linePath(container)).toBe(`M 0 ${mid} L 50 ${mid} L 100 ${mid}`)
    expect(container.innerHTML).not.toMatch(/NaN|Infinity/)
    expect(endDot(container).style.top).toBe(`${mid}px`)
  })

  it('puts the end dot on the newest point (the right end), as a round element, not a stretched circle', () => {
    const { container } = render(<Sparkline series={[90, 60, 75]} label="Pass rate" domain={[0, 100]} />)
    expect(container.querySelector('circle')).toBeNull()
    const dot = endDot(container)
    expect(dot.style.left).toBe('100%')
    expect(dot.style.borderRadius).toBe('50%')
    expect(screen.getByRole('img')).toHaveAccessibleName(/latest 75,/)
  })

  it('keeps hostile label text as text: in the accessible name, never parsed as markup', () => {
    const hostile = '<img src=x onerror="alert(1)"><script>alert(2)</script>'
    const { container } = render(<Sparkline series={[1, 2]} label={hostile} />)
    expect(screen.getByRole('img')).toHaveAccessibleName(`${hostile}: 2 points, latest 2, min 1, max 2`)
    expect(container.querySelector('script')).toBeNull()
    expect(container.querySelector('img')).toBeNull()
  })

  it('colours the line and the dot from the tone token, and the stroke does not scale with the stretch', () => {
    const { container } = render(<Sparkline series={[1, 2, 3]} label="x" tone="bad" />)
    const line = one<SVGPathElement>(container, 'path[data-part="line"]')
    expect(line.getAttribute('stroke')).toBe('var(--status-failed)')
    expect(line.getAttribute('vector-effect')).toBe('non-scaling-stroke')
    expect(endDot(container).style.background).toBe('var(--status-failed)')
    for (const color of Object.values(SPARKLINE_TONE_COLOR)) expect(color).toMatch(/^var\(--[a-z-]+\)$/)
  })

  it('draws the area only when asked, filled from a gradient with a url-safe id', () => {
    const { container, rerender } = render(<Sparkline series={[1, 2, 3]} label="x" />)
    expect(container.querySelector('path[data-part="area"]')).toBeNull()
    rerender(<Sparkline series={[1, 2, 3]} label="x" area />)
    const area = one<SVGPathElement>(container, 'path[data-part="area"]')
    const id = one(container, 'linearGradient').id
    expect(id).toMatch(/^spark-[A-Za-z0-9_-]+$/)
    expect(area.getAttribute('fill')).toBe(`url(#${id})`)
  })

  it('gives two sparklines on one page two gradient ids', () => {
    const { container } = render(
      <>
        <Sparkline series={[1, 2]} label="a" area />
        <Sparkline series={[2, 1]} label="b" area />
      </>,
    )
    const ids = [...container.querySelectorAll('linearGradient')].map((g) => g.id)
    expect(new Set(ids).size).toBe(2)
  })

  it('holds its height and insets the dot by its radius, so a wider font cannot move it (no text is drawn)', () => {
    const { container } = render(<Sparkline series={[0, 10]} label="x" height={40} />)
    const img = screen.getByRole('img')
    expect(img.style.height).toBe('40px')
    expect(img.style.paddingInline).toBe(`${SPARKLINE_INSET}px`)
    expect(endDot(container).style.top).toBe(`${SPARKLINE_INSET}px`)
    expect(container.querySelector('text')).toBeNull()
  })
})
