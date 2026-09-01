import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import TestCaseDetailSummary from './TestCaseDetailSummary'
import { normalizeTestCaseDetail } from '@/utils/testCaseDetail'

describe('TestCaseDetailSummary', () => {
  it('shows identity, classification, provenance, and only safe links', () => {
    const detail = normalizeTestCaseDetail({
      id: 'execution-1',
      contract: 'test-case-detail',
      schema_version: 1,
      test_name: 'Checkout accepts a valid card',
      canonical_test_case_id: 'logical-1',
      identity: {
        test_case_id: 'allure-1',
        history_id: 'history-1',
        full_name: 'checkout.CartTest.validCard',
      },
      classification: {
        suite: { parent: 'Checkout', name: 'Cart' },
        service: { name: 'checkout-service', source: 'mapping', confidence: 'high' },
        components: [{ name: 'payments', source: 'label' }],
        framework: 'playwright',
        tags: ['smoke'],
      },
      provenance: { format: 'allure', parser_version: '2026.08', source_file: 'result.json' },
      links: [
        { url: 'https://example.com/issue/1', name: 'Issue' },
        { url: 'javascript:alert(1)', name: 'Unsafe issue' },
      ],
    })

    render(<TestCaseDetailSummary detail={detail} />)

    expect(screen.getByText('test-case-detail v1')).toBeInTheDocument()
    expect(screen.getByText('logical-1')).toBeInTheDocument()
    expect(screen.getByText('history-1')).toBeInTheDocument()
    expect(screen.getByText(/checkout-service · mapping/)).toBeInTheDocument()
    expect(screen.getByText('payments')).toBeInTheDocument()
    expect(screen.getByText('result.json')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Issue/ })).toHaveAttribute('href', 'https://example.com/issue/1')
    expect(screen.queryByText('Unsafe issue')).not.toBeInTheDocument()
  })

  it('states when provenance and links were not supplied', () => {
    const detail = normalizeTestCaseDetail({ id: 't1', test_name: 'Minimal' })
    render(<TestCaseDetailSummary detail={detail} />)

    expect(screen.getByText(/Provenance was not supplied/i)).toBeInTheDocument()
    expect(screen.getByText(/No safe links supplied/i)).toBeInTheDocument()
    expect(screen.getByText(/legacy run response/i)).toBeInTheDocument()
  })
})
