/**
 * W03 AIH-02/04: the diff component renders the model's sections in order (provenance banner FIRST), links a test
 * row to the `to` side's test, keeps a removed test as plain text unless the `from` run is named, and renders AI /
 * CI text as text: a test name, cluster label or excerpt of `<img src=x onerror=alert(1)>` creates no element.
 */
import { render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'

import AIDigestDiff from './AIDigestDiff'
import { crossRunDiff, HOSTILE, identicalDiff, versionsDiff } from './aiHistory.fixtures'

function renderDiff(props: Partial<Parameters<typeof AIDigestDiff>[0]> = {}) {
  return render(
    <MemoryRouter>
      <AIDigestDiff diff={versionsDiff()} fromLabel="v1 · rules" toLabel="v2 · LLM llama3.1" runId="run-2" {...props} />
    </MemoryRouter>,
  )
}

const sectionIds = (root: HTMLElement) =>
  Array.from(root.querySelectorAll('[data-ai-diff-section]')).map((el) => el.getAttribute('data-ai-diff-section'))

describe('AIDigestDiff', () => {
  it('renders every section in order, the provenance banner first', () => {
    const { container } = renderDiff()
    const root = container.querySelector('[data-ai-diff]') as HTMLElement
    expect(root).toHaveAttribute('data-ai-diff-mode', 'versions')
    expect(sectionIds(root)).toEqual(['provenance', 'verdict', 'categories', 'clusters', 'tests', 'actions'])
    const banner = root.querySelector('[data-ai-diff-section="provenance"]') as HTMLElement
    expect(banner).toHaveAttribute('role', 'note')
    expect(banner).toHaveTextContent('The analysis engine changed between these versions (rules -> llm).')
    expect(within(root).getByText('Verdict CONDITIONAL_GO -> NO_GO')).toBeInTheDocument()
    expect(root.querySelector('[data-ai-diff-category="PRODUCT_BUG"]')).toHaveTextContent('PRODUCT_BUG +3')
    expect(within(root).getByText('v1 · rules')).toBeInTheDocument()
  })

  it('links a changed or added test to the `to` run, a removed one only when the `from` run is named', () => {
    const { container, unmount } = renderDiff()
    const pay = container.querySelector('[data-ai-diff-row="test"][data-test-fingerprint="fp-pay"]') as HTMLElement
    expect(pay.tagName).toBe('A')
    expect(pay).toHaveAttribute('href', '/runs/run-2/tests/tc-pay')
    expect(pay).toHaveTextContent('INFRASTRUCTURE -> PRODUCT_BUG (confidence 40 -> 85)')
    const added = container.querySelector('[data-test-fingerprint="fp-new"]') as HTMLElement
    expect(added).toHaveAttribute('href', '/runs/run-2/tests/tc-new')
    const gone = container.querySelector('[data-test-fingerprint="fp-gone"]') as HTMLElement
    expect(gone.tagName).toBe('SPAN')
    unmount()
    const linked = renderDiff({ fromRunId: 'run-1' })
    expect(linked.container.querySelector('[data-test-fingerprint="fp-gone"]')).toHaveAttribute('href', '/runs/run-1/tests/old-tc-gone')
  })

  it('puts root-cause-only rewording under its own subhead, after the material changes', () => {
    const { container } = renderDiff()
    const groups = Array.from(container.querySelectorAll('[data-ai-diff-section="tests"] [data-ai-diff-group]')).map((g) => g.getAttribute('data-ai-diff-group'))
    expect(groups).toEqual(['added', 'removed', 'changed', 'reworded'])
    const reworded = container.querySelector('[data-ai-diff-group="reworded"]') as HTMLElement
    expect(within(reworded).getByText(/Reworded root cause/)).toBeInTheDocument()
    expect(reworded.querySelector('[data-test-fingerprint="fp-reworded"]')).not.toBeNull()
  })

  it('labels the cross-run mode: new root causes, resolved, persisting; new failing tests', () => {
    const { container } = renderDiff({ diff: crossRunDiff(), mode: 'cross_run' })
    const root = container.querySelector('[data-ai-diff]') as HTMLElement
    expect(root).toHaveAttribute('data-ai-diff-mode', 'cross_run')
    expect(within(root).getByText(/^New root causes/)).toBeInTheDocument()
    expect(within(root).getAllByText(/^Resolved/).length).toBeGreaterThan(0)
    expect(within(root).getByText(/^Persisting/)).toBeInTheDocument()
    expect(within(root).getByText(/^New failing/)).toBeInTheDocument()
    expect(within(root).getByText('Verdict GO -> NO_GO')).toBeInTheDocument()
  })

  it('says "No differences" (identical) when there is nothing to show', () => {
    const { container } = renderDiff({ diff: identicalDiff() })
    expect(container.querySelector('[data-ai-diff-empty]')).toHaveTextContent(/^No differences: the two analyses are identical/)
    expect(container.querySelectorAll('[data-ai-diff-section]')).toHaveLength(0)
  })

  it('caps each group at maxRows with a "+N more" line', () => {
    const { container } = renderDiff({ maxRows: 1 })
    const changed = container.querySelector('[data-ai-diff-group="changed"]') as HTMLElement
    expect(changed.querySelectorAll('[data-ai-diff-row="test"]')).toHaveLength(1)
    expect(changed).toHaveTextContent('+1 more')
  })

  it('XSS: a hostile test name, cluster label and action render as text, never as an element', () => {
    const diff = versionsDiff({ actions: { added: [HOSTILE], removed: [] } })
    const { container } = renderDiff({ diff })
    expect(container.querySelector('img')).toBeNull()
    expect(container.querySelector('[onerror]')).toBeNull()
    // The removed cluster's label, the changed test's name and the added action are all the literal characters.
    expect(screen.getAllByText(HOSTILE, { exact: false }).length).toBeGreaterThanOrEqual(2)
    expect(container.innerHTML).toContain('&lt;img src=x onerror=alert(1)&gt;')
  })
})
