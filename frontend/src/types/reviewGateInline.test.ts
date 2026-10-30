/**
 * W23c AGT-10 / AGT-11: the defensive parsers and the editor template.
 *
 * - `parseReviewGate`: an unknown `source` (or a missing boolean) is unreadable → null; `measured: false`
 *   drops every number (absence is never shown as 0), and so does a measured block without its counts;
 * - `parseInvocation`: an answer from before W23c (no `input_kind` / `input_sha256` / `input`) reads as a
 *   stored-run invocation; an unreadable review block fails closed (not accepted);
 * - `inlineTemplate`: one failure that the server's closed model would accept (within
 *   `INLINE_FAILURES_MAX` and every cap), keyed by the schema's properties; '' for an agent without inline
 *   input.
 */
import { describe, expect, it } from 'vitest'
import {
  CONFIDENCE_RANGE,
  FAILURE_CATEGORIES,
  INLINE_FAILURES_MAX,
  INLINE_INPUT_MAX_BYTES,
  LOG_EXCERPT_MAX,
  ROOT_CAUSE_MAX,
  SUITE_NAME_MAX,
  TEMPLATE_CONFIDENCE,
  TEST_NAME_MAX,
  inlineTemplate,
  parseInvocation,
} from '@/types/agentInvoke'
import { GATE_SOURCES, GATED_CHANNEL_CODES, parseReviewGate } from '@/types/reviewGate'
import {
  RG_CHANNELS,
  RG_INPUT_SHA,
  gateDate,
  gateEnv,
  gateNotMeasured,
  gateScheduled,
  gateUnset,
  invocation,
  invocationAwaitingReview,
  summaryDetail,
} from '@/test/reviewGateInlineMocks'

describe('parseReviewGate', () => {
  it('reads each of the four sources as the server sent them', () => {
    for (const answer of [gateUnset(), gateScheduled(), gateDate(), gateEnv()]) {
      expect(parseReviewGate(JSON.parse(JSON.stringify(answer)))).toEqual(answer)
    }
    expect(GATE_SOURCES).toEqual(['env', 'date', 'unset', 'scheduled'])
  })

  it('refuses an unknown source and a missing boolean', () => {
    expect(parseReviewGate({ ...gateUnset(), source: 'tomorrow' })).toBeNull()
    expect(parseReviewGate({ ...gateUnset(), source: undefined })).toBeNull()
    const { enforced: _enforced, ...withoutEnforced } = gateUnset()
    expect(parseReviewGate(withoutEnforced)).toBeNull()
    expect(parseReviewGate(null)).toBeNull()
    expect(parseReviewGate([])).toBeNull()
  })

  it('drops every number when the audit read was not measured', () => {
    const notMeasured = {
      ...gateNotMeasured(),
      would_refuse: { measured: false, window_days: 30, since: '2026-10-01T00:00:00Z', decisions: 42, runs: 12, by_channel: [] },
    }
    const parsed = parseReviewGate(notMeasured)
    expect(parsed?.would_refuse).toEqual({
      measured: false, window_days: 30, since: null, decisions: null, runs: null, by_channel: null,
    })
  })

  it('treats a measured block without its counts as not measured (never 0)', () => {
    const parsed = parseReviewGate({ ...gateUnset(), would_refuse: { measured: true, window_days: 30 } })
    expect(parsed?.would_refuse.measured).toBe(false)
    expect(parsed?.would_refuse.decisions).toBeNull()
    expect(parseReviewGate({ ...gateUnset(), would_refuse: undefined })?.would_refuse.runs).toBeNull()
  })

  it('keeps every channel row and skips malformed ones', () => {
    const parsed = parseReviewGate({
      ...gateUnset(),
      channels: [...RG_CHANNELS, { channel: 'x' }, 'bad'],
      would_refuse: { ...gateUnset().would_refuse, by_channel: [{ channel: 'report_pdf', decisions: 3 }, { channel: 'y' }] },
    })
    expect(parsed?.channels.map((c) => c.channel)).toEqual([...GATED_CHANNEL_CODES])
    expect(parsed?.would_refuse.by_channel).toEqual([{ channel: 'report_pdf', decisions: 3 }])
  })
})

describe('parseInvocation', () => {
  it('reads an answer from before W23c as a stored-run invocation', () => {
    const parsed = parseInvocation(JSON.parse(JSON.stringify(invocation())))
    expect(parsed).not.toBeNull()
    expect(parsed?.input_kind).toBe('stored_run')
    expect(parsed?.input_sha256).toBeNull()
    expect(parsed?.input).toBeNull()
    expect(parsed?.status).toBe('in_progress')
  })

  it('keeps the inline fields, the redacted stored input and the review block', () => {
    const parsed = parseInvocation(JSON.parse(JSON.stringify(invocationAwaitingReview())))
    expect(parsed?.input_kind).toBe('inline')
    expect(parsed?.input_sha256).toBe(RG_INPUT_SHA)
    expect(JSON.stringify(parsed?.input)).toContain('[REDACTED]')
    expect(parsed?.review.state).toBe('pending_review')
    expect(parsed?.links.review).toBeDefined()
  })

  it('refuses an unreadable answer and fails closed on the review block', () => {
    expect(parseInvocation(null)).toBeNull()
    expect(parseInvocation({ ...invocation(), status: 'queued' })).toBeNull()
    expect(parseInvocation({ ...invocation(), id: 7 })).toBeNull()
    const parsed = parseInvocation({ ...invocation(), review: { state: 'approved-ish' }, input_sha256: 'NOT-HEX' })
    expect(parsed?.review.state).toBe('pending_review')
    expect(parsed?.input_sha256).toBeNull()
  })
})

describe('inlineTemplate', () => {
  it('is one failure the closed model accepts, within every cap', () => {
    const text = inlineTemplate(summaryDetail())
    const value = JSON.parse(text) as { schema_version: number; failures: Array<Record<string, unknown>> }
    expect(value.schema_version).toBe(1)
    expect(value.failures.length).toBeGreaterThanOrEqual(1)
    expect(value.failures.length).toBeLessThanOrEqual(INLINE_FAILURES_MAX)
    expect(new TextEncoder().encode(text).length).toBeLessThan(INLINE_INPUT_MAX_BYTES)
    const failure = value.failures[0]
    expect(Object.keys(failure).sort()).toEqual(
      ['confidence_score', 'failure_category', 'log_excerpt', 'root_cause', 'suite_name', 'test_name'],
    )
    expect(failure.confidence_score).toBe(TEMPLATE_CONFIDENCE)
    expect(failure.confidence_score).toBeGreaterThanOrEqual(CONFIDENCE_RANGE[0])
    expect(failure.confidence_score).toBeLessThanOrEqual(CONFIDENCE_RANGE[1])
    expect(FAILURE_CATEGORIES).toContain(failure.failure_category)
    expect(String(failure.test_name).length).toBeLessThanOrEqual(TEST_NAME_MAX)
    expect(String(failure.suite_name).length).toBeLessThanOrEqual(SUITE_NAME_MAX)
    expect(String(failure.root_cause).length).toBeLessThanOrEqual(ROOT_CAUSE_MAX)
    expect(String(failure.log_excerpt).length).toBeLessThanOrEqual(LOG_EXCERPT_MAX)
  })

  it('drops a key the server schema does not declare (the model is closed)', () => {
    const schema = summaryDetail().inline_input_json_schema as {
      $defs: { InlineFailureV1: { properties: Record<string, unknown> } }
    }
    delete schema.$defs.InlineFailureV1.properties.log_excerpt
    const failure = JSON.parse(inlineTemplate(summaryDetail({ inline_input_json_schema: schema }))).failures[0]
    expect(failure).not.toHaveProperty('log_excerpt')
    expect(failure).toHaveProperty('test_name')
  })

  it('is empty for an agent that does not accept inline input', () => {
    expect(inlineTemplate(summaryDetail({ inline_input_supported: false }))).toBe('')
    expect(inlineTemplate(null)).toBe('')
  })
})
