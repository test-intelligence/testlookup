/**
 * W23d AGT-14 — the `HTTP exchanges` tab's panel and its model (contract §4.4
 * G-D): rows collapsed by default; an opened row shows both header lists (a
 * whole-value `[REDACTED]` as a badge) and the pretty body; captured content
 * is React text (a `<script>` body, an `"><img onerror>` header); the empty,
 * run-cap, dropped and load-error sentences verbatim; Retry refetches.
 */
import { fireEvent, render, screen, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import HttpExchangesPanel from './HttpExchangesPanel'
import {
  BODY_NOT_CAPTURED,
  EXCHANGES_LOAD_ERROR,
  EXCHANGE_PANEL_SENTENCES,
  RUN_CAP_SENTENCE,
  formatExchangeDuration,
  panelSentences,
  prettyBody,
  summaryLine,
} from './httpExchangesModel'
import { useHttpExchanges } from '@/hooks/useHttpExchanges'
import type { HttpExchange, HttpExchangeList } from '@/types/httpExchanges'

vi.mock('@/hooks/useHttpExchanges', () => ({ useHttpExchanges: vi.fn() }))

const SCRIPT_BODY = '<script>alert(1)</script>'
const IMG_HEADER = '"><img src=x onerror=alert(1)>'

function exchange(over: Partial<HttpExchange> = {}): HttpExchange {
  return {
    seq: 0,
    method: 'GET',
    endpoint: '/api/products/{id}',
    url: 'https://shop.example.test/api/products/41?access_token=[REDACTED]&page=2',
    host: 'shop.example.test',
    status: 200,
    duration_ms: 184,
    started_at: null,
    error: null,
    request: { headers: { Accept: 'application/json', Authorization: '[REDACTED]' }, body: null, body_truncated: false, body_bytes: 0 },
    response: {
      headers: { 'Content-Type': 'application/json', 'Set-Cookie': '[REDACTED]' },
      body: '{"id":41,"name":"Lamp","price":12.5}',
      body_truncated: false,
      body_bytes: 36,
    },
    response_schema_state: 'parsed',
    source: 'json_batch',
    captured_at: '2026-10-30T09:15:00Z',
    redacted: true,
    ...over,
  } as HttpExchange
}

function list(over: Partial<HttpExchangeList> = {}): HttpExchangeList {
  return { items: [exchange()], total: 1, dropped: 0, run_cap_reached: false, ...over } as HttpExchangeList
}

const mutate = vi.fn()

function answer(state: { data?: unknown; error?: unknown; isLoading?: boolean }) {
  vi.mocked(useHttpExchanges).mockReturnValue({ data: undefined, error: undefined, isLoading: false, mutate, ...state } as never)
}

function renderPanel() {
  return render(<HttpExchangesPanel runId="run-1" testId="test-1" />)
}

const rows = () => Array.from(document.querySelectorAll('[data-http-exchange]')) as HTMLElement[]

beforeEach(() => {
  mutate.mockReset()
  vi.mocked(useHttpExchanges).mockReset()
})

describe('HttpExchangesPanel — the list', () => {
  it('asks for this test\'s exchanges, enabled (the tab is open)', () => {
    answer({ data: list() })
    renderPanel()
    expect(useHttpExchanges).toHaveBeenCalledWith('run-1', 'test-1', true)
  })

  it('shows one collapsed row per exchange: method · endpoint (full URL as title) · status · duration · Redacted', () => {
    answer({ data: list({ items: [exchange(), exchange({ seq: 1, method: 'POST', endpoint: '/api/cart', status: 201, redacted: false })], total: 2 }) })
    renderPanel()
    expect(screen.getByText(`2 exchanges · ${EXCHANGE_PANEL_SENTENCES.redacted}`)).toBeInTheDocument()
    expect(rows()).toHaveLength(2)
    const first = rows()[0].querySelector('[data-http-exchange-summary]') as HTMLElement
    expect(first).toHaveTextContent('GET/api/products/{id}200184 msRedacted')
    expect(within(first).getByText('/api/products/{id}')).toHaveAttribute('title', exchange().url)
    expect(rows()[1]).not.toHaveTextContent('Redacted')
    // Collapsed by default: no header list, no body.
    for (const row of rows()) {
      expect(row.querySelector('[data-disclosure]')).toHaveAttribute('data-open', 'false')
      expect(row.querySelector('[data-http-headers]')).toBeNull()
    }
  })

  it('opening a row shows Request and Response: headers with [REDACTED] as a badge, the URL, the pretty JSON body', () => {
    answer({ data: list() })
    renderPanel()
    fireEvent.click(within(rows()[0]).getByRole('button', { expanded: false }))
    const request = document.querySelector('[data-http-side="request"]') as HTMLElement
    const response = document.querySelector('[data-http-side="response"]') as HTMLElement
    expect(within(request).getByText(exchange().url)).toBeInTheDocument()
    expect(within(request).getByText('Authorization')).toBeInTheDocument()
    const badge = within(request).getByText('[REDACTED]')
    expect(badge).toHaveAttribute('data-redacted', '')
    expect(badge.className).toContain('badge')
    expect(within(request).getByText(BODY_NOT_CAPTURED)).toBeInTheDocument()
    expect(within(response).getByText('[REDACTED]')).toHaveAttribute('data-redacted', '')
    expect(response.querySelector('[data-http-body]')?.textContent).toBe(JSON.stringify({ id: 41, name: 'Lamp', price: 12.5 }, null, 2))
  })

  it('renders captured content as text: a <script> body and an "><img onerror> header create no element', () => {
    answer({
      data: list({
        items: [exchange({
          request: { headers: { 'X-Note': IMG_HEADER }, body: SCRIPT_BODY, body_truncated: false, body_bytes: 25 },
          response: { headers: {}, body: SCRIPT_BODY, body_truncated: false, body_bytes: 25 },
        })],
      }),
    })
    const { container } = renderPanel()
    fireEvent.click(within(rows()[0]).getByRole('button', { expanded: false }))
    expect(container.querySelector('script')).toBeNull()
    expect(container.querySelector('img')).toBeNull()
    expect(screen.getByText(IMG_HEADER)).toBeInTheDocument()
    expect(screen.getAllByText(SCRIPT_BODY)).toHaveLength(2)
  })

  it('a cut body says it was cut to 16 KB', () => {
    answer({ data: list({ items: [exchange({ response: { headers: {}, body: '{"items":[1,2', body_truncated: true, body_bytes: 70_000 } })] }) })
    renderPanel()
    fireEvent.click(within(rows()[0]).getByRole('button', { expanded: false }))
    const response = document.querySelector('[data-http-side="response"]') as HTMLElement
    expect(within(response).getByText(EXCHANGE_PANEL_SENTENCES.truncated)).toBeInTheDocument()
    // A cut JSON body no longer parses: shown as it came.
    expect(response.querySelector('[data-http-body]')?.textContent).toBe('{"items":[1,2')
  })

  it('dropped exchanges are counted at the list\'s end', () => {
    answer({ data: list({ dropped: 3 }) })
    renderPanel()
    expect(screen.getByText(EXCHANGE_PANEL_SENTENCES.dropped.replace('{dropped}', '3'))).toBeInTheDocument()
  })
})

describe('HttpExchangesPanel — absence and failure are sentences, never a blank', () => {
  it('total 0 shows the "none" and "how" sentences verbatim', () => {
    answer({ data: list({ items: [], total: 0 }) })
    renderPanel()
    expect(document.querySelector('[data-http-exchanges]')).toHaveAttribute('data-http-exchanges', 'empty')
    expect(screen.getByText('No HTTP exchanges were captured for this test.')).toBeInTheDocument()
    expect(screen.getByText(EXCHANGE_PANEL_SENTENCES.how)).toBeInTheDocument()
    expect(screen.queryByText(RUN_CAP_SENTENCE)).toBeNull()
  })

  it('run_cap_reached adds the run-cap sentence', () => {
    answer({ data: list({ items: [], total: 0, run_cap_reached: true }) })
    renderPanel()
    expect(screen.getByText(RUN_CAP_SENTENCE)).toBeInTheDocument()
  })

  it('a 503 reads "Could not load the HTTP exchanges." (never "none captured"), and Retry refetches', () => {
    answer({ error: Object.assign(new Error('Request failed with status code 503'), { response: { status: 503 } }) })
    renderPanel()
    expect(screen.getByText(EXCHANGES_LOAD_ERROR)).toBeInTheDocument()
    expect(EXCHANGES_LOAD_ERROR).toBe('Could not load the HTTP exchanges.')
    expect(screen.queryByText(EXCHANGE_PANEL_SENTENCES.none)).toBeNull()
    expect(screen.queryByText(/status code/)).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(mutate).toHaveBeenCalledTimes(1)
  })

  it('an answer without an items array is a load error, not a crash', () => {
    answer({ data: { total: 1 } })
    renderPanel()
    expect(screen.getByText(EXCHANGES_LOAD_ERROR)).toBeInTheDocument()
  })

  it('a hook that resolved to null (its parser refused the answer) is a load error, never a spinner forever', () => {
    answer({ data: null })
    renderPanel()
    expect(screen.getByText(EXCHANGES_LOAD_ERROR)).toBeInTheDocument()
  })

  it('while loading: a spinner line', () => {
    answer({ isLoading: true })
    renderPanel()
    expect(document.querySelector('[data-http-exchanges]')).toHaveAttribute('data-http-exchanges', 'loading')
  })
})

describe('httpExchangesModel', () => {
  it('prettyBody: JSON objects / arrays indented, anything else as it came, never throws', () => {
    expect(prettyBody('{"a":1}')).toBe('{\n  "a": 1\n}')
    expect(prettyBody('[1,2]')).toBe('[\n  1,\n  2\n]')
    expect(prettyBody('token=[REDACTED]')).toBe('token=[REDACTED]')
    expect(prettyBody('"just a string"')).toBe('"just a string"')
    expect(prettyBody('{"cut":')).toBe('{"cut":')
    expect(prettyBody(null)).toBeNull()
  })

  it('formatExchangeDuration: ms under a second, s from one, nothing when not measured', () => {
    expect(formatExchangeDuration(184)).toBe('184 ms')
    expect(formatExchangeDuration(1234)).toBe('1.2 s')
    expect(formatExchangeDuration(null)).toBe('')
    expect(formatExchangeDuration(-1)).toBe('')
  })

  it('summaryLine: no status reads as the transport error, else "No response"; no endpoint falls back to the URL', () => {
    expect(summaryLine(exchange({ status: null, error: 'ConnectTimeout' })).status).toBe('ConnectTimeout')
    expect(summaryLine(exchange({ status: null, error: null })).status).toBe('No response')
    expect(summaryLine(exchange({ endpoint: '' })).endpoint).toBe(exchange().url)
  })

  it('panelSentences: header singular / plural; an empty list never has a header', () => {
    expect(panelSentences(list()).header).toBe(`1 exchange · ${EXCHANGE_PANEL_SENTENCES.redacted}`)
    expect(panelSentences(list({ items: [], total: 0 })).header).toBeNull()
    expect(panelSentences(list({ items: [], total: 0 })).empty).toEqual([EXCHANGE_PANEL_SENTENCES.none, EXCHANGE_PANEL_SENTENCES.how])
  })
})
