import { describe, expect, it } from 'vitest'
import { normalizeTestCaseDetail, normalizeSteps } from './testCaseDetail'

describe('test-case detail normalization', () => {
  it('keeps sparse responses valid and distinguishes missing steps from an empty supplied list', () => {
    const missing = normalizeTestCaseDetail({ id: 't1', test_name: 'Sparse test', status: 'PASSED' })
    const empty = normalizeTestCaseDetail({ id: 't2', test_name: 'Empty test', steps: null, attachments: [] })

    expect(missing.steps).toBeNull()
    expect(missing.steps_present).toBeNull()
    expect(empty.steps).toEqual([])
    expect(empty.steps_present).toBe(false)
    expect(empty.attachments).toEqual([])
  })

  it('normalizes nested children and drops malformed nodes without dropping the parent', () => {
    const steps = normalizeSteps([
      {
        id: 'parent',
        name: 'Checkout',
        status: 'passed',
        children: [
          null,
          { name: 'Submit order', status: 'failed', expected_value: '201', actual_value: '500' },
        ],
      },
    ])

    expect(steps).toHaveLength(1)
    expect(steps[0].steps).toHaveLength(1)
    expect(steps[0].steps[0]).toMatchObject({
      name: 'Submit order',
      status: 'FAILED',
      expected: '201',
      actual: '500',
      parent_step_id: 'parent',
      depth: 1,
    })
  })

  it('does not retain masked parameter values and preserves provenance from an envelope', () => {
    const detail = normalizeTestCaseDetail({
      id: 'execution-1',
      detail: {
        contract: 'test-case-detail',
        schema_version: 1,
        test_name: 'Login',
        definition: {
          parameters: [
            { name: 'password', value: 'do-not-leak', masked: true },
            { name: 'browser', value: 'chromium' },
          ],
        },
        provenance: {
          format: 'allure',
          source_file: 'result.json',
          warnings: ['one optional field was malformed'],
        },
      },
    })

    expect(detail.source).toBe('enriched')
    expect(detail.contract).toBe('test-case-detail')
    expect(detail.schema_version).toBe(1)
    expect(detail.definition?.parameters).toEqual([
      expect.objectContaining({ name: 'password', value: null, display_value: 'Masked', masked: true }),
      expect.objectContaining({ name: 'browser', value: 'chromium', masked: false }),
    ])
    expect(JSON.stringify(detail)).not.toContain('do-not-leak')
    expect(detail.provenance).toMatchObject({ format: 'allure', source_file: 'result.json' })
  })

  it('adapts the additive backend shape without losing legacy-compatible fields', () => {
    const detail = normalizeTestCaseDetail({
      id: 'execution-2',
      test_name: 'Search works',
      contract_version: '1',
      identity: {
        source_uuid: 'source-uuid',
        source_history_id: 'source-history',
        source_test_case_id: 'source-test-case',
        test_fingerprint: 'fingerprint-1',
      },
      classification: {
        suite_name: 'Search',
        package_name: 'tests.search',
        component_name: 'indexing',
      },
      provenance: { parser_format: 'junit-xml', source_test_run_id: 'run-1' },
      execution: { status: 'PASSED', is_flaky_run: true },
      steps: [{ id: 's1', name: 'query', status: 'PASSED', parameters: { q: 'testlookup' } }],
    })

    expect(detail.schema_version).toBe(1)
    expect(detail.identity).toMatchObject({
      uuid: 'source-uuid',
      history_id: 'source-history',
      test_case_id: 'source-test-case',
      fingerprint: 'fingerprint-1',
    })
    expect(detail.classification).toMatchObject({
      suite: { name: 'Search' },
      package_or_module: 'tests.search',
    })
    expect(detail.classification?.components).toEqual([
      expect.objectContaining({ name: 'indexing' }),
    ])
    expect(detail.provenance).toMatchObject({ format: 'junit-xml', source_test_run_id: 'run-1' })
    expect(detail.execution).toMatchObject({ is_flaky: true })
    expect(detail.steps?.[0].parameters).toEqual([
      expect.objectContaining({ name: 'q', value: 'testlookup' }),
    ])
  })
})
