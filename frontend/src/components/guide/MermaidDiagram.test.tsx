/**
 * The Mermaid renderer.
 *
 * Mermaid is mocked rather than run for real. jsdom has no layout engine — no
 * `getBBox`, no text measurement — so real mermaid fails there regardless of
 * whether this component is correct, and every test would pass through the
 * fallback path and prove nothing about the drawing path. The mock lets both
 * outcomes be asserted deliberately. That the real library draws is verified
 * against the deployment in `tests/probe-docs-render.spec.ts`.
 */
import { act, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import MermaidDiagram, { RENDER_TIMEOUT_MS } from './MermaidDiagram'

const renderDiagram = vi.fn()
const initialize = vi.fn()

vi.mock('mermaid', () => ({
  default: {
    initialize: (config: unknown) => initialize(config),
    render: (id: string, source: string) => renderDiagram(id, source),
  },
}))

const SOURCE = 'flowchart LR\n  A[Run] --> B[Analysis]'
const SOURCE_TWO = `flowchart LR
  A[Run] --> E[Second]`

beforeEach(() => {
  renderDiagram.mockReset()
  initialize.mockReset()
})

describe('MermaidDiagram', () => {
  it('draws the diagram as a picture', async () => {
    renderDiagram.mockResolvedValue({ svg: '<svg><text>Analysis</text></svg>' })

    const { container } = render(<MermaidDiagram source={SOURCE} />)

    await waitFor(() => expect(container.querySelector('svg')).not.toBeNull())
    expect(container.querySelector('svg')?.textContent).toContain('Analysis')
    expect(renderDiagram).toHaveBeenCalledWith(expect.any(String), SOURCE)
  })

  it('gives the picture an accessible role and label', async () => {
    renderDiagram.mockResolvedValue({ svg: '<svg><text>Analysis</text></svg>' })
    render(<MermaidDiagram source={SOURCE} />)
    await waitFor(() => expect(screen.getByRole('img')).toBeInTheDocument())
    expect(screen.getByRole('img')).toHaveAttribute('aria-label', expect.stringMatching(/Diagram/))
  })

  it('shows the source and says so when the diagram cannot be drawn', async () => {
    // The failure mode worth avoiding is a blank space: the reader cannot tell
    // a broken diagram from a page that simply has nothing there.
    renderDiagram.mockRejectedValue(new Error('Parse error on line 2'))

    const { container } = render(<MermaidDiagram source={SOURCE} />)

    await waitFor(() => expect(screen.getByText(/could not be drawn/i)).toBeInTheDocument())
    expect(container.textContent, 'the reader should still get the source').toContain('flowchart LR')
  })

  it('does not leak the underlying error text to the reader', async () => {
    renderDiagram.mockRejectedValue(new Error('Parse error on line 2'))
    const { container } = render(<MermaidDiagram source={SOURCE} />)
    await waitFor(() => expect(screen.getByText(/could not be drawn/i)).toBeInTheDocument())
    expect(container.textContent).not.toContain('Parse error on line 2')
  })

  it('recovers on a later render after a failure', async () => {
    // Regression: the fallback used to REPLACE the host element rather than
    // hide it. The host is the render target, so once it was unmounted the ref
    // was null and every redraw — a theme change, a new diagram — returned
    // early at the null check. One parse failure was permanent.
    renderDiagram.mockRejectedValueOnce(new Error('boom'))
    renderDiagram.mockResolvedValue({ svg: '<svg><text>Recovered</text></svg>' })

    const { container, rerender } = render(<MermaidDiagram source={SOURCE} />)
    await waitFor(() => expect(screen.getByText(/could not be drawn/i)).toBeInTheDocument())

    rerender(<MermaidDiagram source={'flowchart LR\n  A[Run] --> C[Report]'} />)

    await waitFor(() => expect(container.querySelector('svg')).not.toBeNull())
    expect(container.querySelector('svg')?.textContent).toContain('Recovered')
    expect(screen.queryByText(/could not be drawn/i)).not.toBeInTheDocument()
  })

  it('clears a stale diagram when a later render fails', async () => {
    renderDiagram.mockResolvedValueOnce({ svg: '<svg><text>First</text></svg>' })
    renderDiagram.mockRejectedValue(new Error('boom'))

    const { container, rerender } = render(<MermaidDiagram source={SOURCE} />)
    await waitFor(() => expect(container.querySelector('svg')).not.toBeNull())

    rerender(<MermaidDiagram source={'flowchart LR\n  A[Run] --> D[Other]'} />)

    // Showing the fallback underneath a picture of something else would be
    // worse than showing nothing.
    await waitFor(() => expect(screen.getByText(/could not be drawn/i)).toBeInTheDocument())
    expect(container.textContent).not.toContain('First')
  })

  it('measures labels in a concrete font, never "inherit"', async () => {
    // The defect this pins: mermaid computes each node box by measuring its
    // label in a detached element. Under fontFamily 'inherit' that element
    // resolves the font differently from the finished SVG, so every box came
    // out ~12% too narrow and the labels were visually cut off — while
    // textContent still held the full label, so every content assertion in
    // this file and in the live probes passed on an unreadable diagram.
    renderDiagram.mockResolvedValue({ svg: '<svg><text>Analysis</text></svg>' })
    render(<MermaidDiagram source={SOURCE} />)

    await waitFor(() => expect(initialize).toHaveBeenCalled())
    const config = initialize.mock.calls[0][0] as { fontFamily?: string }
    expect(config.fontFamily, 'inherit makes mermaid mis-measure every label').not.toBe('inherit')
    expect(config.fontFamily, 'a real font stack is required').toMatch(/\w/)
  })

  it('gives each render attempt its own id', async () => {
    // Two renders of one instance can overlap: a theme toggle or a quick
    // navigation re-runs the effect while the previous render is in flight.
    // With a shared id they collide inside mermaid and NEITHER settles — the
    // component sits in 'pending' forever and the reader gets a blank box.
    // Measured in the CI harness: 6 of 13 diagrams stuck, no error, no
    // fallback, until the id was made unique per attempt.
    renderDiagram.mockResolvedValue({ svg: '<svg><text>x</text></svg>' })

    const { rerender } = render(<MermaidDiagram source={SOURCE} />)
    await waitFor(() => expect(renderDiagram).toHaveBeenCalledTimes(1))

    rerender(<MermaidDiagram source={SOURCE_TWO} />)
    await waitFor(() => expect(renderDiagram).toHaveBeenCalledTimes(2))

    const [firstId] = renderDiagram.mock.calls[0]
    const [secondId] = renderDiagram.mock.calls[1]
    expect(secondId, 'two overlapping renders must not share a mermaid id').not.toBe(firstId)
  })

  it('shows the source rather than waiting forever if mermaid never answers', async () => {
    // 'pending' must not be terminal. A render that neither resolves nor
    // rejects would otherwise leave an empty bordered box on the page for good
    // — the exact outcome the fallback exists to prevent.
    vi.useFakeTimers()
    try {
      renderDiagram.mockReturnValue(new Promise(() => {}))
      render(<MermaidDiagram source={SOURCE} />)

      // Let the dynamic import settle, then run out the clock. act() so the
      // state change from the timeout is flushed before asserting.
      await act(async () => {
        await vi.advanceTimersByTimeAsync(RENDER_TIMEOUT_MS + 1_000)
      })

      expect(screen.getByText(/could not be drawn/i)).toBeInTheDocument()
    } finally {
      vi.useRealTimers()
    }
  })
})
