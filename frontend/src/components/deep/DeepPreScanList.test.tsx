/**
 * W14 AGT-07 (`W14-contract.md` §2.5 test 5): the pre-scan's signature groups. A representative error is test
 * output: rendered as TEXT (markup in it is shown, never parsed); a group's cluster label likewise.
 */
import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { PRESCAN_FIXTURE } from '@/test/agentOpsMocks'
import type { PreScanGroup } from '@/types/agentOps'
import DeepPreScanList from './DeepPreScanList'

const HOSTILE = '<img src=x onerror=alert(1)> TypeError: boom'
const hostile: PreScanGroup = {
  signature_id: 'sig-hostile', representative_error: HOSTILE, members: 1, member_result_ids: ['r-1'],
  members_truncated: false, suites: ['<b>suite</b>'], cluster_id: 'c-9', cluster_label: '<script>x()</script>',
}

describe('DeepPreScanList', () => {
  it('one row per group: the error, the count, the suites and the cluster it landed in', () => {
    render(<DeepPreScanList groups={PRESCAN_FIXTURE.groups} />)
    const rows = screen.getAllByRole('listitem')
    expect(rows).toHaveLength(3)
    expect(rows[0]).toHaveTextContent('TimeoutError: page.goto exceeded 30000 ms')
    expect(rows[0]).toHaveTextContent('7 failures')
    expect(rows[0]).toHaveTextContent('checkout, cart')
    expect(rows[0]).toHaveTextContent('cluster Navigation timeouts')
    // A group the last analysis did not cluster names no cluster.
    expect(rows[2]).not.toHaveTextContent('cluster')
  })

  it('renders a representative error, a suite and a cluster label holding markup as text', () => {
    const { container } = render(<DeepPreScanList groups={[hostile]} onToggle={vi.fn()} />)
    expect(container.querySelector('img')).toBeNull()
    expect(container.querySelector('script')).toBeNull()
    expect(container.querySelector('b')).toBeNull()
    expect(screen.getByText(HOSTILE)).toBeInTheDocument()
    expect(screen.getByText('cluster <script>x()</script>')).toBeInTheDocument()
    expect(screen.getByText('<b>suite</b>')).toBeInTheDocument()
  })

  it('with onToggle each row is a checkbox named by its error; without, the rows are read-only', () => {
    const onToggle = vi.fn()
    const { unmount } = render(<DeepPreScanList groups={PRESCAN_FIXTURE.groups} selected={['sig-assert']} onToggle={onToggle} />)
    const box = screen.getByRole('checkbox', { name: 'Select 3 failures: AssertionError: expected 200 to equal 500' })
    expect(box).toBeChecked()
    fireEvent.click(screen.getByRole('checkbox', { name: /^Select 7 failures/ }))
    expect(onToggle).toHaveBeenCalledWith('sig-timeout')
    unmount()

    render(<DeepPreScanList groups={PRESCAN_FIXTURE.groups} />)
    expect(screen.queryByRole('checkbox')).toBeNull()
    expect(within(screen.getAllByRole('listitem')[1]).getByText('3 failures')).toBeInTheDocument()
  })

  it('a group with no message says so', () => {
    render(<DeepPreScanList groups={[{ ...hostile, representative_error: null }]} />)
    expect(screen.getByText('No error message')).toBeInTheDocument()
  })
})
