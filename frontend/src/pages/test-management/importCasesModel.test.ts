/** W13 TCL-04: the Import section's copy and helpers. */
import { describe, expect, it } from 'vitest'
import {
  IMPORT_TEMPLATE_HEADERS,
  UTF8_BOM,
  applyLabel,
  duplicateTarget,
  filterRows,
  headerReportLines,
  importToast,
  outcomeChips,
  outcomeLabel,
  resultLine,
  serverSentence,
  summaryLine,
  templateCsv,
} from './importCasesModel'

describe('importCasesModel', () => {
  it('words the dry-run summary with both plural forms', () => {
    expect(summaryLine({ rows: 20, create: 17, duplicate: 0, invalid: 3 })).toBe('20 rows: 17 to create · 0 duplicates · 3 invalid')
    expect(summaryLine({ rows: 1, create: 0, duplicate: 1, invalid: 0 })).toBe('1 row: 0 to create · 1 duplicate · 0 invalid')
    expect(summaryLine({ rows: 2000, create: 2000, duplicate: 0, invalid: 0 })).toBe('2,000 rows: 2,000 to create · 0 duplicates · 0 invalid')
  })

  it('words the result line, the toast and the apply button', () => {
    expect(resultLine({ rows: 20, create: 17, duplicate: 0, invalid: 3, created: 17 })).toBe('17 cases imported as Draft. 3 rows were not imported.')
    expect(resultLine({ rows: 2, create: 2, duplicate: 0, invalid: 0, created: 1 })).toBe('1 case imported as Draft. 1 row was not imported.')
    expect(resultLine({ rows: 3, create: 3, duplicate: 0, invalid: 0, created: 3 })).toBe('3 cases imported as Draft.')
    expect(importToast(17)).toBe('17 test cases imported')
    expect(importToast(1)).toBe('1 test case imported')
    expect(applyLabel(17)).toBe('Import 17 cases')
    expect(applyLabel(1)).toBe('Import 1 case')
  })

  it('labels outcomes and builds the chips in order with counts', () => {
    expect(['create', 'duplicate', 'invalid', 'created', 'other'].map(outcomeLabel)).toEqual(['Create', 'Duplicate', 'Invalid', 'Created', 'other'])
    const rows = [
      { row: 2, outcome: 'create' }, { row: 3, outcome: 'invalid' }, { row: 4, outcome: 'create' },
    ]
    expect(outcomeChips(rows, false)).toEqual([
      { id: 'all', label: 'All', count: 3 }, { id: 'create', label: 'Create', count: 2 },
      { id: 'duplicate', label: 'Duplicate', count: 0 }, { id: 'invalid', label: 'Invalid', count: 1 },
    ])
    expect(outcomeChips(rows, true).map((chip) => chip.id)).toEqual(['all', 'created', 'duplicate', 'invalid'])
    expect(filterRows(rows, 'invalid').map((r) => r.row)).toEqual([3])
    expect(filterRows(rows, 'all')).toHaveLength(3)
  })

  it('reports ignored and unknown headers, and names a duplicate target', () => {
    expect(headerReportLines({ recognised: ['Title'], ignored: ['Status', 'Version'], unknown: ['Owner'] })).toEqual({
      ignored: 'Ignored columns: Status, Version', unknown: 'Unknown columns: Owner',
    })
    expect(headerReportLines({ recognised: ['Title'], ignored: [], unknown: [] })).toEqual({ ignored: null, unknown: null })
    expect(duplicateTarget({ case_id: '1a2b3c4d-1111-2222-3333-444455556666', title: 'Login works' })).toBe('TC-1a2b3c Login works')
    expect(duplicateTarget({ row: 7 })).toBe('row 7')
    expect(duplicateTarget(null)).toBeNull()
  })

  it('builds the template from the one header list (the server parity test reads it)', () => {
    expect(UTF8_BOM.charCodeAt(0)).toBe(0xfeff)
    expect(templateCsv()).toBe(`${UTF8_BOM}${IMPORT_TEMPLATE_HEADERS.join(',')}\r\n`)
    expect(IMPORT_TEMPLATE_HEADERS).toHaveLength(15)
    expect(IMPORT_TEMPLATE_HEADERS[0]).toBe('Title')
  })

  it('reads the server sentence of a failed write', () => {
    const err = (detail: unknown) => ({ response: { status: 422, data: { detail } } })
    expect(serverSentence(err('The file is larger than 5 MB.'), 'fallback')).toBe('The file is larger than 5 MB.')
    expect(serverSentence(err([{ loc: ['body'], msg: "The saved view's statuses is not valid.", type: 'saved_case_view' }]), 'fallback'))
      .toBe("The saved view's statuses is not valid.")
    expect(serverSentence(err([{ loc: ['body', 'x'], msg: 'Value error, Bad thing', type: 'value_error' }]), 'fallback')).toBe('Bad thing')
    expect(serverSentence(err({ message: 'Stale' }), 'fallback')).toBe('Stale')
    expect(serverSentence(new Error('network'), 'fallback')).toBe('fallback')
    expect(serverSentence(err([]), 'fallback')).toBe('fallback')
  })
})
