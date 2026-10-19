import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const stepState: { data: unknown; isLoading: boolean } = { data: undefined, isLoading: false }

vi.mock('@/hooks/useRuns', () => ({
  useTestSteps: () => stepState,
}))

import TestStepsPanel from './TestStepsPanel'
import { normalizeTestCaseDetail } from '@/utils/testCaseDetail'

describe('TestStepsPanel', () => {
  beforeEach(() => {
    stepState.data = undefined
    stepState.isLoading = false
  })

  it('renders an honest empty state for sparse detail without making optional arrays crash', () => {
    render(<TestStepsPanel detail={normalizeTestCaseDetail({ id: 't1', test_name: 'Sparse' })} />)
    expect(screen.getByText(/No granular steps captured/i)).toBeInTheDocument()
  })

  it('renders nested metadata, safe links, attachments, and masked parameters', () => {
    const detail = normalizeTestCaseDetail({
      id: 't1',
      test_name: 'Checkout',
      steps: [
        {
          id: 'root',
          name: 'Checkout flow',
          status: 'PASSED',
          parameters: [{ name: 'password', value: 'secret-value', masked: true }],
          links: [
            { url: 'https://example.com/trace/1', name: 'Trace', type: 'evidence' },
            { url: 'javascript:alert(1)', name: 'Unsafe' },
          ],
          attachments: [{ id: 'att-1', name: 'screenshot.png', media_type: 'image/png' }],
          steps: [{ id: 'child', name: 'Submit', status: 'FAILED', error_message: '500 response' }],
        },
      ],
    })

    render(<TestStepsPanel detail={detail} />)

    expect(screen.getByText('Checkout flow')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Expand step' }))
    expect(screen.getByText('screenshot.png')).toBeInTheDocument()
    expect(screen.queryByText('secret-value')).not.toBeInTheDocument()
    expect(screen.getByText('Masked')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Trace' })).toHaveAttribute('href', 'https://example.com/trace/1')
    expect(screen.queryByText('Unsafe')).not.toBeInTheDocument()

    expect(screen.getByText('Submit')).toBeInTheDocument()
    expect(screen.getByText('500 response')).toBeInTheDocument()
  })

  // W29a UIX-07 / §0.27: the failed step read `--color-danger`, which no theme
  // defines, so its row tint vanished and its message inherited the body colour.
  it('tints a failed step and writes its message with the danger token, which every theme defines', () => {
    const detail = normalizeTestCaseDetail({
      id: 't1',
      test_name: 'Checkout',
      steps: [{ id: 'bad', name: 'Pay', status: 'FAILED', error_message: 'card declined' }],
    })
    render(<TestStepsPanel detail={detail} />)
    const message = screen.getByText('card declined')
    expect(message.className).toContain('text-[var(--ui-danger)]')
    expect(message.className).not.toContain('--color-danger')
    const row = screen.getByText('Pay').closest('div.rounded-md') as HTMLElement
    expect(row.className).toContain('border-[var(--ui-danger)]')
    expect(row.className).not.toContain('--color-danger')
  })

  it('normalizes a malformed legacy payload and still renders valid attachments', () => {
    stepState.data = {
      steps: [{ id: 'legacy', name: 'Legacy step', status: 'PASSED', steps: null, attachments: null }],
      attachments: [{ id: 'run-att', name: 'log.txt', media_type: 'text/plain' }],
    }

    render(<TestStepsPanel />)
    expect(screen.getByText('Legacy step')).toBeInTheDocument()
    expect(screen.getByText('log.txt')).toBeInTheDocument()
  })
})
